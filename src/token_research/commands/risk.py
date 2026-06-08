"""risk command — implements the Revenue-vs-Risk (CPD 2.0) model end-to-end.

Auto-derives CPD sub-scores from existing pipeline data where possible:
- chain_tier: by chain name (Ethereum=I, major L2s=II, experimental=III)
- protocol_tier: by DeFiLlama TVL band
- dapp_tier: by chain footprint + yield pool count
- stage: live if we have on-chain presence
- code: 'open_source_old' if Blockscout returns verified contract metadata
- primitive_a/b: from DeFiLlama category (Lending→Deposits, Dexs→Pools, …)

Anything we can't infer falls through to CLI overrides, which the caller
passes as kwargs on top of the usual positional arguments. Missing inputs
score at 0 (neutral) inside the CPD model.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from token_research.commands import identity as identity_cmd
from token_research.commands import liquidity as liquidity_cmd
from token_research.commands import yields as yields_cmd
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout, defillama
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources
from token_research.risk_model import (
    CpdInputs,
    RiskInputs,
    combine_revenue_risk,
    compute_cpd,
    compute_risk,
    infer_chain_tier,
)


# --- DeFiLlama TVL → protocol tier + dapp tier -------------------------------

def _infer_protocol_tier(tvl_usd: float | None) -> str | None:
    if tvl_usd is None:
        return None
    if tvl_usd >= 1_000_000_000:
        return "I"
    if tvl_usd >= 100_000_000:
        return "II"
    return "III"


def _infer_dapp_tier(chains_count: int | None, pool_count: int | None) -> str | None:
    """Heuristic dapp-level tier from cross-chain presence and yield footprint.

    Pool count is weighted more heavily than chain count because a protocol
    with 700 pools on 2 chains (e.g. Morpho: Ethereum + Base) is clearly a
    top-tier dapp, while a protocol on 5 chains with 3 pools is not.
    """
    if chains_count is None and pool_count is None:
        return None
    chains = chains_count or 0
    pools = pool_count or 0
    # Tier I thresholds: either 100+ pools (top-tier ecosystem) OR strong
    # multi-chain presence with meaningful pool count.
    if pools >= 100 or (chains >= 3 and pools >= 20):
        return "I"
    # Tier II: any meaningful presence — pools in double digits or 1+ chain.
    if pools >= 10 or chains >= 1:
        return "II"
    return "III"


# --- DeFiLlama category → DeFi primitive level -------------------------------

# Mapping from the DeFiLlama "category" field onto our primitive 1..10 scale.
# Not every category has a clean match — unknowns get "other/custom" = 10.
_CATEGORY_TO_PRIMITIVE: dict[str, int] = {
    "liquid staking": 1,
    "liquid staking derivatives": 1,
    "lending": 2,
    "cdp": 2,
    "rwa": 2,
    "rwa lending": 2,
    "dexs": 3,
    "dex aggregator": 3,
    "yield aggregator": 4,
    "yield": 5,
    "farm": 5,
    "leveraged farming": 6,
    "services": 5,
    "basis trading": 6,
    "derivatives": 6,
    "options": 7,
    "options vault": 7,
    "prediction market": 7,
    "synthetics": 8,
    "indexes": 8,
    "structured products": 9,
}


def _infer_primitives(category: str | None) -> tuple[int, int]:
    """Return (primitive_a, primitive_b) based on the protocol category.

    When we only know one category we repeat it on both axes — the DeFi
    matrix then produces a "solo primitive" multiplier M[n, n] = n/10.
    """
    if not category:
        return 10, 10
    key = category.strip().lower()
    primitive = _CATEGORY_TO_PRIMITIVE.get(key, 10)
    return primitive, primitive


# --- Code-band inference -----------------------------------------------------

def _infer_code_band(identity_result: dict, config: AppConfig, identity) -> str | None:
    """Use Blockscout's token-info / address-info to guess at a code band.

    If Blockscout returns verified contract metadata (non-empty `name` or
    `verified` flag), we tag it as 'open_source_old' as a conservative
    default. Callers can override with --cpd-code.
    """
    if not identity.token_address or config.offline:
        return None
    try:
        info = blockscout.get_address_info(identity.chain, identity.token_address, config)
    except ProviderError:
        return None
    if not info:
        return None
    has_verified_name = bool(info.get("name") or info.get("implementation_name"))
    verified = info.get("is_verified") or has_verified_name
    if verified:
        return "open_source_old"
    return "proprietary_old"


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

_CONVICTION_MULTIPLIER: dict[str, float] = {"low": 0.5, "medium": 1.0, "high": 1.5}


def _compute_position_sizing(
    fund_aum_usd: float,
    max_pos_pct: float,
    conviction: str,
    final_risk: int,
    slippage_depth: dict | None,
) -> dict:
    """Translate the abstract risk number into a concrete position cap in USD.

    Three caps are applied; the binding one wins:
      1. Mandate cap: fund_aum * max_pos_pct
      2. Risk-discounted cap: mandate_cap * (11 - final_risk) / 10 * conviction_mult
      3. Liquidity cap: deepest single-pool 5%-slippage exit (most conservative);
         falls back to aggregate 5% depth when single-pool figure is missing.

    Conservative by design — a fund manager can always loosen via flags.
    """
    conviction_key = conviction.lower() if isinstance(conviction, str) else "medium"
    conviction_mult = _CONVICTION_MULTIPLIER.get(conviction_key, 1.0)
    risk = max(1, min(10, int(final_risk)))

    mandate_cap = fund_aum_usd * max_pos_pct
    risk_discount = (11 - risk) / 10.0
    risk_cap = mandate_cap * risk_discount * conviction_mult

    liquidity_cap: float | None = None
    if isinstance(slippage_depth, dict):
        liquidity_cap = slippage_depth.get("deepest_pool_5pct_usd") or slippage_depth.get("max_sell_usd_5pct")

    candidates = [("mandate_cap", mandate_cap), ("risk_cap", risk_cap)]
    if liquidity_cap:
        candidates.append(("liquidity_cap", float(liquidity_cap)))
    binding_label, binding_value = min(candidates, key=lambda kv: kv[1])

    return {
        "fund_aum_usd": fund_aum_usd,
        "max_pos_pct": max_pos_pct,
        "conviction": conviction_key,
        "conviction_multiplier": conviction_mult,
        "final_risk": risk,
        "risk_discount": round(risk_discount, 3),
        "mandate_cap_usd": round(mandate_cap, 2),
        "risk_cap_usd": round(risk_cap, 2),
        "liquidity_cap_usd": round(liquidity_cap, 2) if liquidity_cap else None,
        "max_position_usd": round(binding_value, 2),
        "binding_constraint": binding_label,
    }


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    trade_size_pct: float = 0.10,
    nesting_depth: int = 1,
    cpd_chain_tier: str | None = None,
    cpd_protocol_tier: str | None = None,
    cpd_dapp_tier: str | None = None,
    cpd_stage: str | None = None,
    cpd_age_band: str | None = None,
    cpd_code_band: str | None = None,
    cpd_hacks_band: str | None = None,
    primitive_a: int | None = None,
    primitive_b: int | None = None,
    fund_aum_usd: float | None = None,
    max_pos_pct: float = 0.05,
    conviction: str = "medium",
):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)

    # Pull yields + identity so we can infer CPD + primitives automatically.
    yields_result = yields_cmd.run(query, chain, address, config, include_premium).to_dict()
    identity_result = identity_cmd.run(query, chain, address, config, include_premium).to_dict()

    ym = yields_result.get("metrics") or {}
    protocol_category = ym.get("protocol_category")
    tvl_usd = ym.get("tvl_usd")
    # chains_count lives only in score.raw_inputs, but yields carries
    # pool_count directly. For chains_count we re-inspect the DefiLlama
    # protocol via find_protocol — too expensive, so approximate from
    # yield pools distinct chains instead.
    pool_count = ym.get("yield_pool_count")
    pools = ym.get("yield_pools") or []
    distinct_chains = len({p.get("chain") for p in pools if isinstance(p, dict) and p.get("chain")})
    # Protocol age band now comes from DeFiLlama listedAt via the yields
    # command — removes the coverage gap for age_band.
    inferred_age = ym.get("protocol_age_band")

    # Auto-derive every CPD input, then let the caller's explicit kwargs win.
    inferred_chain = infer_chain_tier(identity.chain)
    inferred_protocol = _infer_protocol_tier(tvl_usd)
    inferred_dapp = _infer_dapp_tier(distinct_chains or None, pool_count)
    inferred_code = _infer_code_band(identity_result, config, identity)

    # --- Hacks auto-inference from DeFiLlama --------------------------------
    inferred_hacks_band: str | None = None
    hacks_detail: list[dict] = []
    protocol_slug = ym.get("protocol_slug")
    protocol_name = ym.get("protocol_name")
    if not cpd_hacks_band and not config.offline:
        hacks_detail = defillama.find_hacks_for_protocol(protocol_name, protocol_slug, config)
        hack_count = len(hacks_detail)
        if hack_count == 0:
            inferred_hacks_band = "0"
        elif hack_count == 1:
            inferred_hacks_band = "0-1"
        elif hack_count == 2:
            inferred_hacks_band = "2"
        else:
            inferred_hacks_band = ">2"

    # --- Audit list from DeFiLlama ------------------------------------------
    audit_list: list[dict] = []
    if not config.offline:
        llama_proto = defillama.find_protocol(identity.symbol, identity.project_name, config)
        if llama_proto:
            audit_list = defillama.get_protocol_audits(llama_proto)

    cpd_inputs = CpdInputs(
        chain_tier=cpd_chain_tier or inferred_chain,  # type: ignore[arg-type]
        protocol_tier=cpd_protocol_tier or inferred_protocol,  # type: ignore[arg-type]
        dapp_tier=cpd_dapp_tier or inferred_dapp,  # type: ignore[arg-type]
        stage=cpd_stage or "release",  # type: ignore[arg-type]
        age_band=cpd_age_band or inferred_age,  # type: ignore[arg-type]
        code_band=cpd_code_band or inferred_code,  # type: ignore[arg-type]
        hacks_band=cpd_hacks_band or inferred_hacks_band or "0",  # type: ignore[arg-type]
    )
    cpd_result = compute_cpd(cpd_inputs)

    # Primitives: use explicit overrides, else infer from DeFiLlama category,
    # else "other/custom".
    if primitive_a is None or primitive_b is None:
        inferred_a, inferred_b = _infer_primitives(protocol_category)
        primitive_a = primitive_a or inferred_a
        primitive_b = primitive_b or inferred_b

    risk_inputs = RiskInputs(
        trade_size_pct=trade_size_pct,
        nesting_depth=nesting_depth,
        cpd_risk_input=cpd_result.risk_input,
        primitive_a=primitive_a,
        primitive_b=primitive_b,
    )
    risk_result = compute_risk(risk_inputs)

    # Pull yield APYs for the revenue-vs-risk comparison.
    # Prefer the "meaningful" (filtered) versions when available — they
    # exclude zero-APY collateral positions and sub-$10M noise pools.
    # Fall back to the unfiltered values only if filtering produced nothing.
    weighted_apy = ym.get("weighted_avg_apy_meaningful") or ym.get("weighted_avg_apy")
    best_apy = ym.get("best_apy_meaningful") or ym.get("apy_p95") or ym.get("best_apy")
    # Observed vs linear vs conservative risk-adjusted yields.
    rr_weighted = combine_revenue_risk(weighted_apy, risk_result.final_risk)
    rr_best = combine_revenue_risk(best_apy, risk_result.final_risk)

    metrics: dict[str, Any] = {
        "cpd": {
            "inputs": cpd_result.inputs,
            "inputs_source": {
                "chain_tier": "override" if cpd_chain_tier else "inferred" if inferred_chain else "unknown",
                "protocol_tier": "override" if cpd_protocol_tier else "inferred" if inferred_protocol else "unknown",
                "dapp_tier": "override" if cpd_dapp_tier else "inferred" if inferred_dapp else "unknown",
                "stage": "override" if cpd_stage else "default:release",
                "age_band": "override" if cpd_age_band else "inferred" if inferred_age else "unknown",
                "code_band": "override" if cpd_code_band else "inferred" if inferred_code else "unknown",
                "hacks_band": "override" if cpd_hacks_band else "inferred" if inferred_hacks_band else "default:0",
            },
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
        "revenue_vs_risk": {
            "weighted_avg_apy_percent": weighted_apy,
            "best_apy_percent": best_apy,
            "risk_score": risk_result.final_risk,
            "linear_adjusted": {
                "weighted": rr_weighted.risk_adjusted_apy,
                "best": rr_best.risk_adjusted_apy,
            },
            "conservative_adjusted": {
                "weighted": rr_weighted.risk_adjusted_apy_conservative,
                "best": rr_best.risk_adjusted_apy_conservative,
            },
            "apy_per_unit_risk": rr_weighted.apy_per_unit_risk,
            "verdict_weighted": rr_weighted.verdict,
            "verdict_best": rr_best.verdict,
        },
        "security": {
            "hacks": hacks_detail,
            "hacks_count": len(hacks_detail),
            "hacks_band_source": "override" if cpd_hacks_band else "inferred" if inferred_hacks_band else "default:0",
            "audits": audit_list,
            "audits_count": len(audit_list),
        },
        "position_sizing": None,
        "source_summary": {
            "protocol_category": protocol_category,
            "tvl_usd": tvl_usd,
            "mcap_usd": ym.get("mcap_usd"),
            "mcap_source": ym.get("mcap_source"),
            "revenue_model": ym.get("revenue_model"),
            "protocol_age_band": ym.get("protocol_age_band"),
            "yield_pool_count": pool_count,
            "meaningful_pool_count": ym.get("meaningful_pool_count"),
            "distinct_yield_chains": distinct_chains,
            "apy_headline": weighted_apy,
            "apy_best": best_apy,
        },
    }

    # Position sizing — only when caller passes --fund-aum. Pulls slippage
    # depth from the liquidity command so the liquidity cap is grounded in
    # actual pool reserves rather than just total $TVL.
    if fund_aum_usd is not None and fund_aum_usd > 0:
        slippage_depth = None
        try:
            liq = liquidity_cmd.run(query, chain, address, config, include_premium).to_dict()
            slippage_depth = (liq.get("metrics") or {}).get("slippage_depth")
        except Exception:
            slippage_depth = None
        metrics["position_sizing"] = _compute_position_sizing(
            fund_aum_usd=fund_aum_usd,
            max_pos_pct=max_pos_pct,
            conviction=conviction,
            final_risk=risk_result.final_risk,
            slippage_depth=slippage_depth,
        )

    if protocol_category is None:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.PRIMITIVE_INFERENCE,
                reason="No DeFiLlama category available — primitive defaulted to 'other/custom' (10).",
                suggested_source="Provide --primitive-a/--primitive-b overrides based on the actual product type.",
            )
        )
    if cpd_inputs.age_band is None:
        coverage_gaps.append(
            CoverageGap(
                metric="cpd.age_band",
                reason="No DeFiLlama listedAt for this protocol — age could not be auto-derived.",
                suggested_source="Pass --cpd-age with one of 0-1, 1-2, 2-3, 3-5, >5.",
            )
        )
    if cpd_inputs.hacks_band == "0" and cpd_hacks_band is None and inferred_hacks_band is None:
        warnings.append(
            WarningItem(
                code=WarningCode.HACKS_DEFAULT,
                message="Hacks band defaulted to '0' (clean) — DeFiLlama hacks lookup unavailable. Override with --cpd-hacks if the project has incidents.",
                severity="info",
            )
        )

    return CommandResult(
        command="risk",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
            "trade_size_pct": trade_size_pct,
            "nesting_depth": nesting_depth,
            "cpd_chain_tier": cpd_chain_tier,
            "cpd_protocol_tier": cpd_protocol_tier,
            "cpd_dapp_tier": cpd_dapp_tier,
            "cpd_stage": cpd_stage,
            "cpd_age_band": cpd_age_band,
            "cpd_code_band": cpd_code_band,
            "cpd_hacks_band": cpd_hacks_band,
            "primitive_a": primitive_a,
            "primitive_b": primitive_b,
            "fund_aum_usd": fund_aum_usd,
            "max_pos_pct": max_pos_pct,
            "conviction": conviction,
        },
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
