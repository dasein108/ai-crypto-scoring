from __future__ import annotations

from dataclasses import asdict

from token_research.chains import get_blockscout_base_url
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _gini(values: list[float]) -> float | None:
    """Compute the Gini coefficient for a list of non-negative balances.

    Uses the unbiased formula
        G = (sum_i (2i - n - 1) * x_i) / (n * sum_i x_i)
    on the ascending-sorted list. Returns None for degenerate inputs.
    """
    cleaned = [v for v in values if v is not None and v >= 0]
    n = len(cleaned)
    if n < 2:
        return None
    total = sum(cleaned)
    if total <= 0:
        return None
    sorted_values = sorted(cleaned)
    cumulative = 0.0
    for i, value in enumerate(sorted_values, start=1):
        cumulative += (2 * i - n - 1) * value
    return round(cumulative / (n * total), 4)


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics = {
        "top_holders": [],
        "concentration": {
            "top10_share": None,
            "top20_share": None,
            "gini": None,
            "top_holders_gini": None,
            "nakamoto_51": None,
        },
    }
    explorer_base = get_blockscout_base_url(identity.chain)
    if identity.token_address and explorer_base and not config.offline:
        try:
            holders = blockscout.get_token_holders(identity.chain, identity.token_address, config, page=1, offset=20)
            metrics["top_holders"] = holders
            total_supply = _safe_float(
                blockscout.get_token_info(identity.chain, identity.token_address, config).get("total_supply")
            )
            if total_supply and holders:
                top10_amount = sum(_safe_float(item.get("value")) or 0.0 for item in holders[:10])
                top20_amount = sum(_safe_float(item.get("value")) or 0.0 for item in holders[:20])
                metrics["concentration"]["top10_share"] = top10_amount / total_supply
                metrics["concentration"]["top20_share"] = top20_amount / total_supply
                metrics["concentration"]["nakamoto_51"] = _nakamoto_count(holders, total_supply, 0.51)
            if holders:
                balances = [_safe_float(item.get("value")) or 0.0 for item in holders]
                metrics["concentration"]["top_holders_gini"] = _gini(balances)
            coverage_gaps.append(
                CoverageGap(
                    metric=CoverageMetric.GINI,
                    reason=(
                        "Full-distribution Gini requires every holder's balance; only the top-N snapshot is "
                        "available today. top_holders_gini is reported as an upper-bound approximation."
                    ),
                    suggested_source="Dune or Footprint full holder reconstruction",
                )
            )
            if not holders:
                coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOP_HOLDERS, reason="No holder snapshot returned.", suggested_source="Blockscout"))
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.BLOCKSCOUT_UNAVAILABLE, message=str(exc), severity="warning"))
    elif config.offline:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOP_HOLDERS, reason="Offline mode enabled.", suggested_source="Disable offline mode to query Blockscout."))
    elif not explorer_base:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOP_HOLDERS, reason="No Blockscout instance configured for this chain.", suggested_source="Blockscout or SQL indexers"))
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOP_HOLDERS, reason="No token address available for holder lookup.", suggested_source="Resolve token address first."))

    return CommandResult(
        command="holders",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )


def _nakamoto_count(holders: list[dict[str, object]], total_supply: float, threshold: float) -> int | None:
    if total_supply <= 0:
        return None
    running = 0.0
    for index, holder in enumerate(holders, start=1):
        running += _safe_float(holder.get("value")) or 0.0
        if running / total_supply >= threshold:
            return index
    return None
