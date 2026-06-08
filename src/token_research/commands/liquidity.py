from __future__ import annotations

from dataclasses import asdict

from token_research import contract_registry
from token_research.chains import get_blockscout_base_url, normalize_chain_name
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout, dexscreener
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# Constant-product slippage approximation, applied via the 50/50-pool model.
# For x*y=k with a balanced pool of total USD TVL T, the token-side reserve
# value is T/2 in USD. Selling Δ_usd into that side gives slippage
# s = Δ / (T/2 + Δ). Solving for Δ at target s: Δ = s * T / (2 * (1 - s)).
#
# Why not the per-token-amount formula? DexScreener `liquidity.base` for v3/v4
# pools reports only the currently *active* tick liquidity, not the full pool
# TVL — so token_liquidity × price can be << liquidity_usd, leading to absurdly
# small depth estimates on CLMM pools. The 50/50 USD model is a conservative
# but robust approximation that works on both v2 and concentrated AMMs.
_SWAP_FEE_FRACTION = 0.003  # ~30 bps blended v2/v3 default


def _slippage_depth_for_pool(pool: dict, slippage_target: float) -> float | None:
    """USD value of the largest sell that keeps execution within `slippage_target`."""
    liquidity_usd = _safe_float(pool.get("liquidity_usd"))
    if liquidity_usd is None or liquidity_usd <= 0:
        return None
    if slippage_target <= 0 or slippage_target >= 1:
        return None
    raw = liquidity_usd * slippage_target / (2.0 * (1.0 - slippage_target))
    return raw * (1.0 - _SWAP_FEE_FRACTION)


def _aggregate_slippage_depth(summaries: list[dict]) -> dict:
    """Aggregate per-pool slippage depth at standard thresholds.

    Returns USD amounts that can be sold across the full pool set without any
    single pool's marginal slippage exceeding the threshold. Also surfaces the
    deepest single-pool exit for sanity-checking the routing assumption.
    """
    thresholds = (("max_sell_usd_2pct", 0.02), ("max_sell_usd_5pct", 0.05), ("max_sell_usd_10pct", 0.10))
    out: dict = {th_key: 0.0 for th_key, _ in thresholds}
    out["per_pool"] = []
    deepest_5pct = 0.0
    for pool in summaries:
        per_pool: dict = {
            "pair_address": pool.get("pair_address"),
            "dex_id": pool.get("dex_id"),
            "liquidity_usd": pool.get("liquidity_usd"),
        }
        for th_key, target in thresholds:
            value = _slippage_depth_for_pool(pool, target)
            if value is not None:
                out[th_key] += value
                per_pool[th_key] = round(value, 2)
                if target == 0.05 and value > deepest_5pct:
                    deepest_5pct = value
        if any(per_pool.get(th_key) is not None for th_key, _ in thresholds):
            out["per_pool"].append(per_pool)
    for th_key, _ in thresholds:
        out[th_key] = round(out[th_key], 2) if out[th_key] else None
    out["deepest_pool_5pct_usd"] = round(deepest_5pct, 2) if deepest_5pct else None
    out["per_pool"] = out["per_pool"][:5]
    return out


def _compute_lp_lock_enrichment(
    top_pool: dict, fallback_chain: str, config: AppConfig, warnings: list
) -> dict | None:
    """Analyze the LP token holders of a pool to estimate locked vs unlocked share.

    Only works for fungible v2-style pair tokens on chains where Blockscout is
    configured. Returns None if the pool is not inspectable.
    """
    pair_address = top_pool.get("pair_address")
    if not pair_address:
        return None
    raw_chain = top_pool.get("chain") or fallback_chain
    normalized = normalize_chain_name(str(raw_chain))
    if not get_blockscout_base_url(normalized):
        return None
    try:
        lp_holders = blockscout.get_token_holders(normalized, pair_address, config, page=1, offset=50)
    except ProviderError as exc:
        warnings.append(WarningItem(code=WarningCode.LP_HOLDERS_UNAVAILABLE, message=str(exc), severity="info"))
        return None
    if not lp_holders:
        return None

    total = 0.0
    locked = 0.0
    breakdown: dict[str, float] = {}
    locked_entries: list[dict] = []

    for holder in lp_holders:
        value = _safe_float(holder.get("value")) or 0.0
        total += value
        address = str(holder.get("address") or "")
        if not address:
            continue
        classification, label = contract_registry.classify_lp_holder(normalized, address)
        if classification in {"burn", "locker"}:
            locked += value
            key = label or classification
            breakdown[key] = breakdown.get(key, 0.0) + value
            locked_entries.append(
                {
                    "address": address,
                    "classification": classification,
                    "label": label,
                    "lp_balance": value,
                }
            )

    if total <= 0:
        return None

    locked_pct = locked / total
    return {
        "pair_address": pair_address,
        "chain": normalized,
        "holders_inspected": len(lp_holders),
        "lp_locked_percent": round(locked_pct * 100, 2),
        "lp_unlocked_percent": round((1 - locked_pct) * 100, 2),
        "locked_by_holder": sorted(
            locked_entries, key=lambda item: item["lp_balance"], reverse=True
        ),
        "locked_breakdown": {
            key: round(value, 4) for key, value in sorted(breakdown.items(), key=lambda i: i[1], reverse=True)
        },
    }


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "total_dex_liquidity_usd": None,
        "top_pools": [],
        "liquidity_by_dex": {},
        "lp_locked_percent": None,
        "lp_unlocked_percent": None,
        "lp_lock_analysis": None,
        "slippage_depth": None,
    }
    if identity.token_address and not config.offline:
        try:
            pairs = dexscreener.sort_pairs_by_liquidity(dexscreener.get_token_pairs(identity.chain, identity.token_address, config))
            summaries = [dexscreener.summarize_pair(pair, token_address=identity.token_address) for pair in pairs]
            total_liquidity = sum(item["liquidity_usd"] or 0.0 for item in summaries)
            by_dex: dict[str, float] = {}
            for item in summaries:
                dex_id = str(item["dex_id"] or "unknown")
                by_dex[dex_id] = by_dex.get(dex_id, 0.0) + float(item["liquidity_usd"] or 0.0)
            metrics.update(
                {
                    "total_dex_liquidity_usd": round(total_liquidity, 2),
                    "top_pools": summaries[:10],
                    "liquidity_by_dex": {key: round(value, 2) for key, value in sorted(by_dex.items(), key=lambda item: item[1], reverse=True)},
                }
            )
            if not summaries:
                coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOTAL_DEX_LIQUIDITY_USD, reason="No pools returned for resolved token.", suggested_source="DexScreener"))
            else:
                metrics["slippage_depth"] = _aggregate_slippage_depth(summaries)

            lp_analysis = None
            if summaries:
                lp_analysis = _compute_lp_lock_enrichment(summaries[0], identity.chain, config, warnings)
            if lp_analysis:
                metrics["lp_lock_analysis"] = lp_analysis
                metrics["lp_locked_percent"] = lp_analysis["lp_locked_percent"]
                metrics["lp_unlocked_percent"] = lp_analysis["lp_unlocked_percent"]
            else:
                coverage_gaps.append(
                    CoverageGap(
                        metric=CoverageMetric.LP_LOCKED_PERCENT,
                        reason="LP holder inspection unavailable (non-v2 pool, missing Blockscout, or no holders).",
                        suggested_source="Mobula LP durability or extended locker registry.",
                    )
                )
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEXSCREENER_UNAVAILABLE, message=str(exc), severity="warning"))
    elif config.offline:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOTAL_DEX_LIQUIDITY_USD, reason="Offline mode enabled.", suggested_source="Disable offline mode to query DexScreener."))
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOTAL_DEX_LIQUIDITY_USD, reason="No token address available for liquidity discovery.", suggested_source="Resolve token address first."))

    return CommandResult(
        command="liquidity",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
