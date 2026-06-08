from __future__ import annotations

from dataclasses import asdict

from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import defillama, dexscreener
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "spot_price_usd": None,
        "market_cap": None,
        "fdv": None,
        "best_pair": None,
        "defillama_price": None,
        "defillama_confidence": None,
    }
    if identity.token_address and not config.offline:
        # Primary: DexScreener
        try:
            pairs = dexscreener.sort_pairs_by_liquidity(dexscreener.get_token_pairs(identity.chain, identity.token_address, config))
            if pairs:
                best_pair = dexscreener.summarize_pair(pairs[0], token_address=identity.token_address)
                metrics.update(
                    {
                        "spot_price_usd": best_pair["price_usd"],
                        "market_cap": best_pair["market_cap"],
                        "fdv": best_pair["fdv"],
                        "best_pair": best_pair,
                    }
                )
            else:
                coverage_gaps.append(CoverageGap(metric=CoverageMetric.SPOT_PRICE_USD, reason="No pairs returned for resolved token.", suggested_source="DexScreener"))
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEXSCREENER_UNAVAILABLE, message=str(exc), severity="warning"))

        # Secondary: DeFiLlama price as cross-validation or fallback
        try:
            llama = defillama.get_current_price(identity.chain, identity.token_address, config)
            if llama:
                metrics["defillama_price"] = llama.get("price")
                metrics["defillama_confidence"] = llama.get("confidence")
                if metrics["spot_price_usd"] is None and llama.get("price") is not None:
                    metrics["spot_price_usd"] = llama["price"]
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.DEFILLAMA_UNAVAILABLE, message=str(exc), severity="info"))
    elif config.offline:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.SPOT_PRICE_USD, reason="Offline mode enabled.", suggested_source="Disable offline mode to query DexScreener."))
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.SPOT_PRICE_USD, reason="No token address available for price lookup.", suggested_source="Resolve token address first."))

    return CommandResult(
        command="price",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
