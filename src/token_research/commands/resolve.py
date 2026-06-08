from __future__ import annotations

from dataclasses import asdict

from token_research.config import AppConfig
from token_research.models import CommandResult, dataclass_list
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, candidates, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    return CommandResult(
        command="resolve",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity=asdict(identity),
        metrics={
            "candidates": candidates,
        },
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
