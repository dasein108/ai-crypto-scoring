from __future__ import annotations

from dataclasses import asdict

from token_research.chains import get_blockscout_base_url
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout, rpc
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "total_supply_raw": None,
        "decimals": None,
        "total_supply_adjusted": None,
        "total_supply_source": None,
        "circulating_supply_vendor": None,
        "holders_count": None,
        "exchange_rate": None,
        "token_type": None,
        "security": None,
    }

    if not identity.token_address:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOTAL_SUPPLY_ONCHAIN, reason="No token address available for supply lookup.", suggested_source="Resolve token address first."))
        return _build_result(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    if config.offline:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOTAL_SUPPLY_ONCHAIN, reason="Offline mode enabled.", suggested_source="Disable offline mode to query Blockscout or RPC."))
        return _build_result(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    # Primary: RPC eth_call for totalSupply and decimals (canonical on-chain truth)
    rpc_succeeded = False
    try:
        raw_supply = rpc.get_total_supply(identity.chain, identity.token_address, config)
        decimals = rpc.get_decimals(identity.chain, identity.token_address, config)
        if raw_supply is not None:
            metrics["total_supply_raw"] = raw_supply
            metrics["total_supply_source"] = "rpc"
            rpc_succeeded = True
            if decimals is not None:
                metrics["decimals"] = decimals
                metrics["total_supply_adjusted"] = raw_supply / (10 ** decimals)
    except ProviderError as exc:
        warnings.append(WarningItem(code=WarningCode.RPC_UNAVAILABLE, message=str(exc), severity="warning"))

    # Fallback / enrichment: Blockscout token info
    explorer_base = get_blockscout_base_url(identity.chain)
    if explorer_base:
        try:
            token_info = blockscout.get_token_info(identity.chain, identity.token_address, config)
            if token_info:
                metrics["circulating_supply_vendor"] = token_info.get("circulating_market_cap")
                metrics["holders_count"] = token_info.get("holders")
                metrics["exchange_rate"] = token_info.get("exchange_rate")
                metrics["token_type"] = token_info.get("type")
                if not rpc_succeeded and token_info.get("total_supply"):
                    metrics["total_supply_raw"] = token_info["total_supply"]
                    metrics["total_supply_source"] = "blockscout"
                    decimals_str = token_info.get("decimals")
                    if decimals_str is not None:
                        try:
                            dec = int(decimals_str)
                            metrics["decimals"] = dec
                            metrics["total_supply_adjusted"] = int(token_info["total_supply"]) / (10 ** dec)
                        except (ValueError, TypeError):
                            pass
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.BLOCKSCOUT_UNAVAILABLE, message=str(exc), severity="warning"))
    elif not rpc_succeeded:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOTAL_SUPPLY_ONCHAIN, reason="No RPC URL or Blockscout instance available for this chain.", suggested_source="Configure RPC URL or use a supported chain."))

    if metrics["total_supply_raw"] is None:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.TOTAL_SUPPLY_ONCHAIN, reason="Neither RPC nor Blockscout returned supply data.", suggested_source="RPC or Blockscout"))

    # Probe ownership / upgradeability / mint privilege
    if identity.token_address and not config.offline:
        try:
            metrics["security"] = rpc.probe_mint_privilege(identity.chain, identity.token_address, config)
        except Exception:
            pass

    return _build_result(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)


def _build_result(
    query: str,
    chain: str | None,
    address: str | None,
    include_premium: bool,
    identity: object,
    metrics: dict[str, object],
    warnings: list[WarningItem],
    coverage_gaps: list[CoverageGap],
    config: AppConfig,
) -> CommandResult:
    return CommandResult(
        command="supply",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
