from __future__ import annotations

from dataclasses import asdict

from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import dune
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "netflows": [],
        "destination_buckets": ["cex", "dex", "bridge", "treasury", "unknown"],
    }

    can_query = identity.token_address and not config.offline and config.dune_api_key
    if can_query:
        try:
            rows = dune.query_token_flows(identity.token_address, config)
            netflows = []
            for row in rows:
                netflows.append({
                    "category": row.get("destination_category", "unknown"),
                    "inflow_raw": row.get("inflow_raw"),
                    "outflow_raw": row.get("outflow_raw"),
                    "net_raw": (row.get("inflow_raw") or 0) - (row.get("outflow_raw") or 0),
                    "tx_count": row.get("tx_count"),
                })
            metrics["netflows"] = netflows
            if not netflows:
                coverage_gaps.append(CoverageGap(metric=CoverageMetric.NETFLOWS, reason="No transfer data returned from Dune.", suggested_source="Dune"))
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DUNE_UNAVAILABLE, message=str(exc), severity="warning"))
            coverage_gaps.append(CoverageGap(metric=CoverageMetric.NETFLOWS, reason="Dune query failed.", suggested_source="Dune"))
    elif config.offline:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.NETFLOWS, reason="Offline mode enabled.", suggested_source="Disable offline mode."))
    elif not config.dune_api_key:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.NETFLOWS, reason="Dune API key not configured.", suggested_source="Set TOKEN_RESEARCH_DUNE_API_KEY."))
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.NETFLOWS, reason="No token address available.", suggested_source="Resolve token address first."))

    return CommandResult(
        command="flows",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
