"""chain-report command — analyzes non-EVM L1s via DefiLlama chain endpoints.

The token-centric pipeline assumes an EVM contract address as the anchor,
which doesn't exist for native L1 tokens like HBAR, ATOM, SOL, TIA, TON.
This command provides partial coverage using chain-level data instead:

- DefiLlama /chains entry (name, gecko_id, tokenSymbol, TVL, chain id)
- Historical chain TVL trajectory via /v2/historicalChainTvl/{chain}
- Chain-level fees (if indexed by DefiLlama)
- Protocols deployed on the chain (filtered from /protocols)
- Yield pools on the chain (filtered from yields.llama.fi/pools)
- Native token price via coingecko id

It does NOT cover:
- On-chain holder distribution (needs chain-native indexer)
- Native staking APY (chain-native, not in yields.llama.fi)
- Treasury / foundation holdings
- Governance model details

See `docs/hbar_analysis.md` for a worked example of the gaps.

Usage:
    token-research chain-report Hedera
    token-research chain-report Solana --min-pool-tvl 1000000
    token-research chain-report Sui --trade-size 0.05 --nesting 1
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from token_research.format_utils import iso_from_unix
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, ResolvedIdentity, WarningCode, WarningItem, dataclass_list
from token_research.providers import defillama
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.risk_model import CpdInputs, RiskInputs, compute_cpd, compute_risk


# --- CPD defaults for L1 analysis --------------------------------------------

# L1 native tokens are categorically different from DeFi protocol tokens.
# The CPD model was designed for the latter; these defaults exist to produce
# a sensible tier assessment without pretending the DeFi framework maps
# cleanly onto chain-level tokens. Users should override with --cpd-* flags.

# Known well-established L1s get a higher chain_tier by default. For unknowns
# we default to Tier II (treated as "established L1" — anything the user
# analyzes via chain-report is by definition on DefiLlama, so it exists).
_CHAIN_SEED_TIER: dict[str, str] = {
    "ethereum": "I",
    "solana": "II",
    "bsc": "II",
    "binance": "II",
    "hedera": "II",
    "tron": "II",
    "avalanche": "II",
    "polkadot": "II",
    "cosmos": "II",
    "cardano": "II",
    "algorand": "III",
    "near": "II",
    "aptos": "III",
    "sui": "III",
    "ton": "II",
    "celestia": "III",
    "sei": "III",
}


def _infer_chain_tier(chain_name: str) -> str:
    key = (chain_name or "").strip().lower()
    return _CHAIN_SEED_TIER.get(key, "II")


def _infer_protocol_tier_from_tvl(tvl_usd: float | None) -> str:
    """Use chain TVL as a proxy for protocol tier on L1 queries."""
    if tvl_usd is None:
        return "III"
    if tvl_usd >= 5_000_000_000:
        return "I"
    if tvl_usd >= 500_000_000:
        return "II"
    return "III"


def _infer_dapp_tier(pool_count: int, protocol_count: int) -> str:
    if pool_count >= 50 or protocol_count >= 50:
        return "I"
    if pool_count >= 10 or protocol_count >= 10:
        return "II"
    return "III"


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _tvl_trajectory(history: list[dict]) -> dict:
    """Compute earliest / peak / current / 30d / 1y deltas from a TVL series."""
    if not history:
        return {}
    valid = [h for h in history if isinstance(h, dict) and h.get("tvl") is not None]
    if not valid:
        return {}
    first = valid[0]
    last = valid[-1]
    peak = max(valid, key=lambda h: h.get("tvl", 0))
    out: dict[str, Any] = {
        "earliest_date": iso_from_unix(first.get("date"), date_only=True),
        "earliest_tvl": first.get("tvl"),
        "peak_date": iso_from_unix(peak.get("date"), date_only=True),
        "peak_tvl": peak.get("tvl"),
        "current_date": iso_from_unix(last.get("date"), date_only=True),
        "current_tvl": last.get("tvl"),
        "data_points": len(valid),
    }
    if peak.get("tvl"):
        out["drawdown_from_peak_pct"] = round(
            (last.get("tvl", 0) - peak.get("tvl", 0)) / peak.get("tvl", 1) * 100, 2
        )
    # 30-day change
    if len(valid) >= 30:
        t0 = valid[-30].get("tvl") or 0
        if t0:
            out["change_30d_pct"] = round((last.get("tvl", 0) - t0) / t0 * 100, 2)
    if len(valid) >= 365:
        t0 = valid[-365].get("tvl") or 0
        if t0:
            out["change_1y_pct"] = round((last.get("tvl", 0) - t0) / t0 * 100, 2)
    return out


def _summarize_protocols_on_chain(chain_name: str, config: AppConfig, top_n: int = 15) -> list[dict]:
    protos = defillama.get_protocols(config)
    hits = [
        p for p in protos
        if chain_name in (p.get("chains") or [])
        and (p.get("tvl") or 0) > 0
    ]
    hits.sort(key=lambda p: p.get("tvl") or 0, reverse=True)
    return [
        {
            "name": p.get("name"),
            "slug": p.get("slug"),
            "category": p.get("category"),
            "tvl_usd": p.get("tvl"),
            "chains": p.get("chains"),
        }
        for p in hits[:top_n]
    ]


def _summarize_pools_on_chain(chain_name: str, config: AppConfig, min_tvl_usd: float = 100_000) -> dict:
    """Summarize yield pools on the target chain."""
    pools = defillama.get_yield_pools(config)
    hits = [
        p for p in pools
        if str(p.get("chain", "")).lower() == chain_name.lower()
        and (p.get("tvlUsd") or 0) >= min_tvl_usd
    ]
    hits.sort(key=lambda p: p.get("tvlUsd") or 0, reverse=True)
    summaries = [
        {
            "project": p.get("project"),
            "symbol": p.get("symbol"),
            "apy": p.get("apy"),
            "apy_base": p.get("apyBase"),
            "apy_reward": p.get("apyReward"),
            "tvl_usd": p.get("tvlUsd"),
            "stablecoin": p.get("stablecoin"),
            "il_risk": p.get("ilRisk"),
        }
        for p in hits[:15]
    ]
    # TVL-weighted avg APY
    total_tvl = 0.0
    weighted = 0.0
    for p in hits:
        t = _safe_float(p.get("tvlUsd")) or 0.0
        a = _safe_float(p.get("apy"))
        if t > 0 and a is not None:
            total_tvl += t
            weighted += a * t
    return {
        "count": len(hits),
        "top": summaries,
        "total_tvl_usd": round(total_tvl, 2) if total_tvl else None,
        "weighted_avg_apy": round(weighted / total_tvl, 3) if total_tvl > 0 else None,
        "best_apy": max((p.get("apy") or 0 for p in hits), default=None) if hits else None,
    }


def run(
    query: str,
    chain: str | None,  # unused — chain-report always uses the query as the chain name
    address: str | None,  # unused
    config: AppConfig,
    include_premium: bool,
    *,
    trade_size_pct: float = 0.05,
    nesting_depth: int = 1,
    primitive_a: int | None = None,
    primitive_b: int | None = None,
    cpd_chain_tier: str | None = None,
    cpd_protocol_tier: str | None = None,
    cpd_dapp_tier: str | None = None,
    cpd_stage: str | None = None,
    cpd_age_band: str | None = None,
    cpd_code_band: str | None = None,
    cpd_hacks_band: str | None = None,
    min_pool_tvl: float = 100_000.0,
):
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    if config.offline:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.CHAIN_DIRECTORY,
                reason="Offline mode enabled.",
                suggested_source="Disable offline mode.",
            )
        )
        return _build_empty(query, chain, include_premium, config, warnings, coverage_gaps)

    # Resolve the chain.
    try:
        chain_entry = defillama.find_chain_by_name(query, config)
    except ProviderError as exc:
        warnings.append(WarningItem(code=WarningCode.DEFILLAMA_UNAVAILABLE, message=str(exc), severity="warning"))
        chain_entry = None

    if not chain_entry:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.CHAIN_DIRECTORY,
                reason=f"No DeFiLlama chain entry matches '{query}'.",
                suggested_source="Check spelling; DefiLlama uses capitalized chain names (Hedera, Solana, Sui).",
            )
        )
        return _build_empty(query, chain, include_premium, config, warnings, coverage_gaps)

    chain_name = str(chain_entry.get("name") or query)
    gecko_id = chain_entry.get("gecko_id")
    token_symbol = chain_entry.get("tokenSymbol") or chain_name
    current_tvl = _safe_float(chain_entry.get("tvl"))

    # Native price via coingecko id (if available).
    price_entry = {}
    if gecko_id:
        try:
            price_entry = defillama.get_chain_coin_price(str(gecko_id), config)
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.COIN_PRICE_UNAVAILABLE, message=str(exc), severity="info"))

    # Historical TVL trajectory.
    try:
        history = defillama.get_chain_historical_tvl(chain_name, config)
    except ProviderError:
        history = []
    trajectory = _tvl_trajectory(history)

    # Chain-level fees.
    try:
        fees_raw = defillama.get_chain_fees(chain_name, config)
    except ProviderError:
        fees_raw = {}
    fees_block = {
        "total24h": fees_raw.get("total24h"),
        "total7d": fees_raw.get("total7d"),
        "total14dto7d": fees_raw.get("total14dto7d"),
        "total48hto24h": fees_raw.get("total48hto24h"),
    } if fees_raw else {}

    # Protocols on chain + yield pools on chain.
    protocols_on_chain = _summarize_protocols_on_chain(chain_name, config)
    pools_summary = _summarize_pools_on_chain(chain_name, config, min_tvl_usd=min_pool_tvl)
    protocol_count = len(protocols_on_chain)

    # CPD assessment with L1-specific defaults.
    inferred_chain_tier = _infer_chain_tier(chain_name)
    inferred_protocol_tier = _infer_protocol_tier_from_tvl(current_tvl)
    inferred_dapp_tier = _infer_dapp_tier(pools_summary.get("count") or 0, protocol_count)

    cpd_inputs = CpdInputs(
        chain_tier=cpd_chain_tier or inferred_chain_tier,  # type: ignore[arg-type]
        protocol_tier=cpd_protocol_tier or inferred_protocol_tier,  # type: ignore[arg-type]
        dapp_tier=cpd_dapp_tier or inferred_dapp_tier,  # type: ignore[arg-type]
        stage=cpd_stage or "release",  # type: ignore[arg-type]
        age_band=cpd_age_band,  # type: ignore[arg-type]
        code_band=cpd_code_band,  # type: ignore[arg-type]
        hacks_band=cpd_hacks_band or "0",  # type: ignore[arg-type]
    )
    cpd_result = compute_cpd(cpd_inputs)

    # Primitives: native staking by default for L1 holdings.
    if primitive_a is None:
        primitive_a = 1  # Native coin staking
    if primitive_b is None:
        primitive_b = primitive_a
    risk_result = compute_risk(
        RiskInputs(
            trade_size_pct=trade_size_pct,
            nesting_depth=nesting_depth,
            cpd_risk_input=cpd_result.risk_input,
            primitive_a=primitive_a,
            primitive_b=primitive_b,
        )
    )

    # Price + mcap math (best-effort).
    price_usd = _safe_float(price_entry.get("price"))
    mcap_note = None
    if price_usd is None:
        mcap_note = "Native price unavailable — mcap not derivable."
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.PRICE_USD,
                reason="No DefiLlama coin price for this chain (gecko_id missing or failed).",
                suggested_source="CoinGecko or CoinMarketCap direct API.",
            )
        )

    # Compose the result.
    identity = ResolvedIdentity(
        query=query,
        normalized_query=query.strip(),
        query_type="project_name",
        symbol=str(token_symbol).upper(),
        project_name=chain_name,
        chain=chain_name.lower(),
        token_address=None,
    )

    metrics: dict[str, Any] = {
        "chain": {
            "name": chain_name,
            "gecko_id": gecko_id,
            "token_symbol": token_symbol,
            "chain_id": chain_entry.get("chainId"),
            "cmc_id": chain_entry.get("cmcId"),
            "tvl_usd": current_tvl,
        },
        "native_token": {
            "symbol": token_symbol,
            "price_usd": price_usd,
            "price_source": "defillama_coins" if price_usd else None,
            "price_timestamp": price_entry.get("timestamp"),
            "confidence": price_entry.get("confidence"),
            "note": mcap_note,
        },
        "trajectory": trajectory,
        "chain_fees": fees_block,
        "protocols_on_chain": {
            "count": protocol_count,
            "top": protocols_on_chain,
        },
        "yield_pools": pools_summary,
        "cpd": {
            "inputs": cpd_result.inputs,
            "sub_scores": cpd_result.sub_scores,
            "total_score": cpd_result.total_score,
            "max_score": cpd_result.max_score,
            "percent_of_max": cpd_result.percent_of_max,
            "tier": cpd_result.tier,
            "risk_input": cpd_result.risk_input,
            "explanation": cpd_result.explanation,
        },
        "risk": {
            "trade_size_pct": trade_size_pct,
            "nesting_depth": nesting_depth,
            "primitive_a": primitive_a,
            "primitive_b": primitive_b,
            "primitive_a_name": risk_result.primitive_a_name,
            "primitive_b_name": risk_result.primitive_b_name,
            "base_risk": risk_result.base_risk,
            "defi_multiplier": risk_result.defi_multiplier,
            "final_risk": risk_result.final_risk,
            "explanation": risk_result.explanation,
        },
    }

    # Honest gap list — document what this command can't see.
    coverage_gaps.extend(
        [
            CoverageGap(
                metric=CoverageMetric.ON_CHAIN_HOLDERS,
                reason="Non-EVM chains — holder analysis requires chain-native indexer (Hedera Mirror Node, Solana Indexer, etc.).",
                suggested_source="Chain-specific integrations.",
            ),
            CoverageGap(
                metric=CoverageMetric.NATIVE_STAKING_APY,
                reason="Native-chain staking APY is not indexed on yields.llama.fi.",
                suggested_source="Chain staking dashboards or native APIs.",
            ),
            CoverageGap(
                metric=CoverageMetric.TREASURY_HOLDINGS,
                reason="Foundation / council treasury positions require chain-native data.",
                suggested_source="Chain governance docs.",
            ),
        ]
    )

    return CommandResult(
        command="chain-report",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
            "trade_size_pct": trade_size_pct,
            "nesting_depth": nesting_depth,
        },
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )


def _build_empty(query, chain, include_premium, config, warnings, coverage_gaps):
    identity = ResolvedIdentity(
        query=query,
        normalized_query=query.strip(),
        query_type="project_name",
        symbol=query.upper(),
        project_name=query,
        chain=(chain or query.lower()),
        token_address=None,
    )
    return CommandResult(
        command="chain-report",
        input={"query": query, "chain": chain, "address": None, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics={
            "chain": None,
            "native_token": None,
            "trajectory": None,
            "chain_fees": None,
            "protocols_on_chain": None,
            "yield_pools": None,
            "cpd": None,
            "risk": None,
        },
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
