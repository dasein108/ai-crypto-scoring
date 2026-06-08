from __future__ import annotations

from dataclasses import asdict

from token_research.commands import price as price_cmd
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout, defillama
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


# Outliers / sanity thresholds for filtered APY metrics.
# Pools below MIN_POOL_TVL_USD contribute noise to "best APY" (a $100 pool
# with 1000% APY is not a useful signal), and pools with apy == 0 represent
# pure-collateral positions that should not drag the weighted average down
# for users trying to understand realistic lending returns.
MIN_POOL_TVL_USD = 10_000_000
MEANINGFUL_MIN_APY = 0.001  # 0.1% — anything below is effectively collateral


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _annualize_30d(value: float | None) -> float | None:
    if value is None:
        return None
    # 30d × 12 is a simple annualization; good enough for "is the protocol
    # generating meaningful revenue" without pretending to be forward-looking.
    return value * 12


def _weighted_avg_apy(pools: list[dict]) -> float | None:
    """TVL-weighted average APY across pools (excludes pools with no TVL)."""
    total_tvl = 0.0
    weighted = 0.0
    for pool in pools:
        tvl = _safe_float(pool.get("tvlUsd")) or 0.0
        apy = _safe_float(pool.get("apy"))
        if tvl > 0 and apy is not None:
            total_tvl += tvl
            weighted += apy * tvl
    if total_tvl <= 0:
        return None
    return round(weighted / total_tvl, 3)


def _meaningful_pools(pools: list[dict]) -> list[dict]:
    """Filter out zero-APY collateral positions and sub-$10M noise pools."""
    out: list[dict] = []
    for pool in pools:
        tvl = _safe_float(pool.get("tvlUsd")) or 0.0
        apy = _safe_float(pool.get("apy")) or 0.0
        if tvl >= MIN_POOL_TVL_USD and apy >= MEANINGFUL_MIN_APY:
            out.append(pool)
    return out


def _apy_percentile(pools: list[dict], fraction: float) -> float | None:
    """Return the APY at ``fraction`` of the sorted distribution, pools only.

    Used for robust "best realistic APY" — p95 excludes the 5% tail of
    micro-TVL pools that DeFiLlama surfaces with triple-digit numbers.
    """
    apys = sorted([a for a in (_safe_float(p.get("apy")) for p in pools) if a is not None])
    if not apys:
        return None
    idx = int(len(apys) * fraction)
    return round(apys[min(idx, len(apys) - 1)], 3)


def _classify_revenue_model(fees_30d: float | None, revenue_30d: float | None) -> str:
    """Distinguish 'by design' zero revenue from 'no data'.

    - ``fees_pass_through``: meaningful fees but zero protocol revenue —
      the protocol deliberately routes all fees to LPs/borrowers (Morpho V1,
      Uniswap V2 without the fee switch, etc.).
    - ``protocol_capture``: revenue > 0, some portion of fees flows to the
      protocol / token holders.
    - ``unknown``: we have neither fees nor revenue data.
    """
    has_fees = fees_30d is not None and fees_30d > 0
    has_rev = revenue_30d is not None and revenue_30d > 0
    if has_rev:
        return "protocol_capture"
    if has_fees and (revenue_30d == 0 or revenue_30d is None):
        return "fees_pass_through" if has_fees else "unknown"
    return "unknown"


def _age_band_from_listed_at(listed_at: float | None) -> str | None:
    """Map DeFiLlama listedAt (Unix timestamp) to a CPD age band."""
    if not listed_at or listed_at <= 0:
        return None
    import time
    return _age_band_from_unix(time.time() - float(listed_at))


def _age_band_from_unix(age_seconds: float) -> str | None:
    if age_seconds is None or age_seconds < 0:
        return None
    years = age_seconds / (365.25 * 86400)
    if years < 1:
        return "0-1"
    if years < 2:
        return "1-2"
    if years < 3:
        return "2-3"
    if years < 5:
        return "3-5"
    return ">5"


def _age_band_from_blockscout(chain: str, token_address: str, config: AppConfig) -> str | None:
    """Fallback: derive age from the token contract's creation tx on Blockscout.

    Used when DeFiLlama's `listedAt` is null (Ondo Yield Assets case). Resolves
    token_address → creation_transaction_hash → tx.timestamp → age band.
    Returns None on any transport / parse failure.
    """
    if not token_address:
        return None
    try:
        info = blockscout.get_address_info(chain, token_address, config)
    except ProviderError:
        return None
    tx_hash = info.get("creation_transaction_hash")
    if not tx_hash:
        return None
    try:
        tx = blockscout.get_transaction(chain, str(tx_hash), config)
    except ProviderError:
        return None
    ts_str = tx.get("timestamp")
    if not ts_str:
        return None
    # Blockscout returns ISO 8601 strings like "2022-05-10T12:34:56.000000Z".
    from datetime import datetime
    import time
    try:
        # Handle trailing "Z" and optional fractional seconds.
        cleaned = str(ts_str).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    now_ts = time.time()
    age_seconds = now_ts - dt.timestamp()
    return _age_band_from_unix(age_seconds)


def _resolve_mcap(
    protocol_mcap: float | None,
    identity,
    config: AppConfig,
    include_premium: bool,
    warnings: list,
) -> tuple[float | None, str]:
    """Pick the best mcap value we can find, preferring the DeFiLlama protocol
    entry and falling back to the DexScreener-sourced price command.

    Returns (mcap, source). ``source`` is one of: 'defillama_protocol',
    'dexscreener_best_pair', 'unknown'.
    """
    if protocol_mcap is not None and protocol_mcap > 0:
        return protocol_mcap, "defillama_protocol"
    # Fallback: DexScreener best-pair market_cap via the price command.
    try:
        price_result = price_cmd.run(
            identity.normalized_query or identity.symbol,
            identity.chain,
            identity.token_address,
            config,
            include_premium,
        ).to_dict()
    except Exception as exc:
        warnings.append(WarningItem(code=WarningCode.PRICE_FALLBACK_FAILED, message=str(exc), severity="info"))
        return None, "unknown"
    pm = price_result.get("metrics") or {}
    mcap = _safe_float(pm.get("market_cap"))
    if mcap is not None and mcap > 0:
        return mcap, "dexscreener_best_pair"
    fdv = _safe_float(pm.get("fdv"))
    if fdv is not None and fdv > 0:
        # FDV is a coarse upper bound, but better than None for risk ratios.
        return fdv, "dexscreener_fdv"
    return None, "unknown"


def _summarize_pool(pool: dict) -> dict:
    """Return a compact subset of a yields.llama.fi pool entry for the report."""
    return {
        "pool": pool.get("pool"),
        "chain": pool.get("chain"),
        "project": pool.get("project"),
        "symbol": pool.get("symbol"),
        "apy": pool.get("apy"),
        "apy_base": pool.get("apyBase"),
        "apy_reward": pool.get("apyReward"),
        "tvl_usd": pool.get("tvlUsd"),
        "reward_tokens": pool.get("rewardTokens"),
        "stablecoin": pool.get("stablecoin"),
        "il_risk": pool.get("ilRisk"),
        "exposure": pool.get("exposure"),
        "url": pool.get("url"),
    }


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "protocol_slug": None,
        "protocol_name": None,
        "protocol_category": None,
        "tvl_usd": None,
        "mcap_usd": None,
        "protocol_fees": {"total24h": None, "total7d": None, "total30d": None},
        "protocol_revenue": {"total24h": None, "total7d": None, "total30d": None},
        "annualized_fees_usd": None,
        "annualized_revenue_usd": None,
        "fees_to_mcap_ratio": None,
        "revenue_to_mcap_ratio": None,
        "revenue_model": "unknown",        # fees_pass_through / protocol_capture / unknown
        "mcap_source": "unknown",          # defillama_protocol / dexscreener_best_pair / dexscreener_fdv
        "protocol_listed_at": None,
        "protocol_age_band": None,         # 0-1 / 1-2 / 2-3 / 3-5 / >5 (derived)
        "yield_pools": [],
        "yield_pool_count": 0,
        "best_apy": None,                  # raw max across all pools (may be noise)
        "best_apy_meaningful": None,       # max APY among TVL>=$10M AND APY>0 pools
        "apy_p95": None,                   # 95th percentile APY across all pools
        "weighted_avg_apy": None,          # TVL-weighted across all pools
        "weighted_avg_apy_meaningful": None,  # TVL-weighted across meaningful pools
        "meaningful_pool_count": 0,
        "total_yield_tvl_usd": None,
        "meaningful_yield_tvl_usd": None,
    }

    if config.offline:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.PROTOCOL_FEES,
                reason="Offline mode enabled.",
                suggested_source="Disable offline mode.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    # Match the token to a DeFiLlama protocol — same logic the score pillar
    # uses, so `yields` and `score.fundamental_support` share a source of truth.
    try:
        protocol = defillama.find_protocol(identity.symbol, identity.project_name, config)
    except ProviderError as exc:
        warnings.append(WarningItem(code=WarningCode.DEFILLAMA_UNAVAILABLE, message=str(exc), severity="warning"))
        protocol = None

    if not protocol:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.PROTOCOL_SLUG,
                reason="No DeFiLlama protocol matched this token's symbol or name.",
                suggested_source="DeFiLlama protocol directory; Token Terminal for non-DeFi tokens.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    slug = protocol.get("slug")
    metrics["protocol_slug"] = slug
    metrics["protocol_name"] = protocol.get("name")
    metrics["protocol_category"] = protocol.get("category")
    tvl = _safe_float(protocol.get("tvl"))
    metrics["tvl_usd"] = tvl

    # Protocol launch date → age band (gives risk.py a free --cpd-age).
    # Primary: DeFiLlama listedAt. Fallback: Blockscout contract creation tx
    # for protocols where DeFiLlama left listedAt null (ondo-yield-assets class).
    listed_at = _safe_float(protocol.get("listedAt"))
    metrics["protocol_listed_at"] = listed_at
    age_band = _age_band_from_listed_at(listed_at)
    if age_band is None and identity.token_address:
        age_band = _age_band_from_blockscout(identity.chain, identity.token_address, config)
        if age_band:
            metrics["protocol_age_band_source"] = "blockscout_creation_tx"
    else:
        metrics["protocol_age_band_source"] = "defillama_listedAt" if age_band else "unknown"
    metrics["protocol_age_band"] = age_band

    # Mcap with fallback chain: DeFiLlama protocol entry → DexScreener pair.
    # Resolves the "Morpho V1 has mcap=None" class of issues.
    protocol_mcap = _safe_float(protocol.get("mcap"))
    mcap, mcap_source = _resolve_mcap(protocol_mcap, identity, config, include_premium, warnings)
    metrics["mcap_usd"] = mcap
    metrics["mcap_source"] = mcap_source

    # --- Fees + revenue ---
    if slug:
        try:
            fees = defillama.get_protocol_fees(slug, config)
            if fees:
                metrics["protocol_fees"] = {
                    "total24h": fees.get("total24h"),
                    "total7d": fees.get("total7d"),
                    "total30d": fees.get("total30d"),
                    "total1y": fees.get("total1y"),
                    "change_1m": fees.get("change_1m"),
                }
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEFILLAMA_FEES_UNAVAILABLE, message=str(exc), severity="info"))

        try:
            revenue = defillama.get_protocol_revenue(slug, config)
            if revenue:
                metrics["protocol_revenue"] = {
                    "total24h": revenue.get("total24h"),
                    "total7d": revenue.get("total7d"),
                    "total30d": revenue.get("total30d"),
                    "total1y": revenue.get("total1y"),
                    "change_1m": revenue.get("change_1m"),
                }
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEFILLAMA_REVENUE_UNAVAILABLE, message=str(exc), severity="info"))

    fees30 = _safe_float((metrics["protocol_fees"] or {}).get("total30d"))
    rev30 = _safe_float((metrics["protocol_revenue"] or {}).get("total30d"))
    metrics["annualized_fees_usd"] = _annualize_30d(fees30)
    metrics["annualized_revenue_usd"] = _annualize_30d(rev30)
    metrics["revenue_model"] = _classify_revenue_model(fees30, rev30)

    if mcap and mcap > 0 and metrics["annualized_fees_usd"]:
        metrics["fees_to_mcap_ratio"] = round(float(metrics["annualized_fees_usd"]) / mcap, 4)
    if mcap and mcap > 0 and metrics["annualized_revenue_usd"]:
        metrics["revenue_to_mcap_ratio"] = round(float(metrics["annualized_revenue_usd"]) / mcap, 4)

    if not fees30 and not rev30:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.PROTOCOL_FEES,
                reason="DeFiLlama returned no fees/revenue data for this protocol.",
                suggested_source="Token Terminal, protocol dashboards",
            )
        )
    if metrics["revenue_model"] == "fees_pass_through":
        warnings.append(
            WarningItem(
                code=WarningCode.FEES_PASS_THROUGH,
                message=(
                    f"Protocol generates fees (${metrics['annualized_fees_usd']:,.0f}/yr) but "
                    "reports $0 revenue — by-design fee-switch-off model. Token holders do not "
                    "receive fees unless governance activates capture."
                ),
                severity="info",
            )
        )

    # --- Yield pools (staking/LP APY opportunities) ---
    try:
        pools = defillama.find_yield_pools_for_protocol(slug, identity.symbol, config)
    except ProviderError as exc:
        warnings.append(WarningItem(code=WarningCode.DEFILLAMA_YIELDS_UNAVAILABLE, message=str(exc), severity="warning"))
        pools = []

    metrics["yield_pool_count"] = len(pools)
    metrics["yield_pools"] = [_summarize_pool(p) for p in pools[:10]]

    if pools:
        apys = [a for a in (_safe_float(p.get("apy")) for p in pools) if a is not None]
        if apys:
            metrics["best_apy"] = round(max(apys), 3)
        metrics["weighted_avg_apy"] = _weighted_avg_apy(pools)
        metrics["apy_p95"] = _apy_percentile(pools, 0.95)
        total_tvl = sum((_safe_float(p.get("tvlUsd")) or 0.0) for p in pools)
        metrics["total_yield_tvl_usd"] = round(total_tvl, 2) if total_tvl else None

        # Filtered variants — exclude pure-collateral (APY≈0) and micro-TVL
        # outliers so "best APY" / "weighted avg" are usable for portfolio
        # decisions. For Morpho this is the difference between 1.75% (polluted)
        # and ~4% (realistic).
        meaningful = _meaningful_pools(pools)
        metrics["meaningful_pool_count"] = len(meaningful)
        if meaningful:
            mf_apys = [_safe_float(p.get("apy")) or 0.0 for p in meaningful]
            metrics["best_apy_meaningful"] = round(max(mf_apys), 3)
            metrics["weighted_avg_apy_meaningful"] = _weighted_avg_apy(meaningful)
            mf_total_tvl = sum((_safe_float(p.get("tvlUsd")) or 0.0) for p in meaningful)
            metrics["meaningful_yield_tvl_usd"] = round(mf_total_tvl, 2) if mf_total_tvl else None
    else:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.YIELD_POOLS,
                reason="No yield pools on DeFiLlama matched the protocol slug or symbol.",
                suggested_source="Token-specific staking docs",
            )
        )

    return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)


def _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config):
    return CommandResult(
        command="yields",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
