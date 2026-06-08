from __future__ import annotations

from dataclasses import asdict

from token_research.chains import get_blockscout_base_url
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics = {
        "symbol_verified": False,
        "decimals": None,
        "name_verified": False,
        "chain_supported": True,
        "explorer": get_blockscout_base_url(identity.chain),
    }
    if identity.token_address and not config.offline and metrics["explorer"]:
        try:
            token_info = blockscout.get_token_info(identity.chain, identity.token_address, config)
            if token_info:
                metrics.update(
                    {
                        "symbol_verified": bool(token_info.get("symbol")),
                        "decimals": token_info.get("decimals"),
                        "name_verified": bool(token_info.get("name")),
                        "token_info": {
                            "name": token_info.get("name"),
                            "symbol": token_info.get("symbol"),
                            "decimals": token_info.get("decimals"),
                            "circulating_market_cap": token_info.get("circulating_market_cap"),
                            "exchange_rate": token_info.get("exchange_rate"),
                            "holders": token_info.get("holders"),
                        },
                    }
                )
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.BLOCKSCOUT_UNAVAILABLE, message=str(exc), severity="warning"))
    elif not metrics["explorer"]:
        coverage_gaps.append(
            CoverageGap(metric=CoverageMetric.IDENTITY_VERIFICATION, reason="No Blockscout instance configured for this chain.", suggested_source="Blockscout or RPC")
        )

    return CommandResult(
        command="identity",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
