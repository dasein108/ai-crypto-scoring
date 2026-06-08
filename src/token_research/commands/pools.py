from __future__ import annotations

from dataclasses import asdict

from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import dexscreener
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics = {"pools": []}
    if identity.token_address and not config.offline:
        try:
            pairs = dexscreener.sort_pairs_by_liquidity(dexscreener.get_token_pairs(identity.chain, identity.token_address, config))
            metrics["pools"] = [dexscreener.summarize_pair(pair, token_address=identity.token_address) for pair in pairs[:10]]
            if not metrics["pools"]:
                coverage_gaps.append(CoverageGap(metric=CoverageMetric.POOLS, reason="No pools returned for resolved token.", suggested_source="DexScreener"))
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEXSCREENER_UNAVAILABLE, message=str(exc), severity="warning"))
    elif config.offline:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.POOLS, reason="Offline mode enabled.", suggested_source="Disable offline mode to query DexScreener."))
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.POOLS, reason="No token address available for pool discovery.", suggested_source="Resolve token address first."))

    return CommandResult(
        command="pools",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
