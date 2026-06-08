from __future__ import annotations

from dataclasses import asdict

from token_research import contract_registry
from token_research.chains import get_blockscout_base_url
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers import blockscout, rpc
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


MAX_HOLDERS_TO_INSPECT = 30


def _discover_staking_contracts(
    chain: str,
    token_address: str,
    top_holders: list[dict],
    config: AppConfig,
    warnings: list[WarningItem],
) -> tuple[list[dict], int]:
    staking_contracts: list[dict] = []
    contracts_inspected = 0

    for holder in top_holders[:MAX_HOLDERS_TO_INSPECT]:
        address = str(holder.get("address") or "")
        if not address or contract_registry.is_burn_address(address):
            continue
        try:
            if not rpc.is_contract(chain, address, config):
                continue
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.RPC_UNAVAILABLE, message=str(exc), severity="info"))
            continue
        contracts_inspected += 1

        # 1. ERC-4626 vault whose asset() matches the token
        vault = rpc.inspect_erc4626_vault(chain, address, token_address, config)
        if vault:
            staking_contracts.append(vault)
            continue

        # 2. Synthetix-style StakingRewards whose stakingToken() matches the token
        pool = rpc.inspect_generic_staking(chain, address, token_address, config)
        if pool:
            staking_contracts.append(pool)
            continue

        # 2b. veCRV-style vote-escrow lock whose token() matches
        ve = rpc.inspect_ve_lock(chain, address, token_address, config)
        if ve:
            staking_contracts.append(ve)
            continue

        # 3. Verified-name fallback: scan every hint Blockscout surfaces
        # (verified name, implementation_name, token.name, public_tags). A
        # proxy may be called TransparentUpgradeableProxy while its underlying
        # token.name is "StakedPendle" — we need to match on the latter.
        hints = _collect_address_hints(chain, address, config)
        matched = next(
            (h for h in hints if contract_registry.name_suggests_staking(h)), None
        )
        if matched:
            staking_contracts.append(
                {
                    "contract": address,
                    "type": "heuristic_name_match",
                    "verified_name": matched,
                    "name_hints": hints,
                    "token_balance": _safe_int(holder.get("value")),
                    "confidence": "low",
                }
            )

    return staking_contracts, contracts_inspected


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _collect_address_hints(chain: str, address: str, config: AppConfig) -> list[str]:
    try:
        info = blockscout.get_address_info(chain, address, config)
    except ProviderError:
        return []
    return blockscout.collect_name_hints(info)


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "staking_contracts": [],
        "staked_total": None,
        "contract_holders_inspected": 0,
    }

    if not identity.token_address:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.STAKED_TOTAL,
                reason="No token address available.",
                suggested_source="Resolve token address first.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    if config.offline:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.STAKED_TOTAL,
                reason="Offline mode enabled.",
                suggested_source="Disable offline mode.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    has_rpc = rpc._rpc_url_for_chain(identity.chain, config) is not None
    explorer_base = get_blockscout_base_url(identity.chain)

    if not has_rpc:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.STAKED_TOTAL,
                reason="No RPC URL configured for this chain.",
                suggested_source="Set TOKEN_RESEARCH_ETH_RPC_URL / BASE_RPC_URL / ARB_RPC_URL / OP_RPC_URL.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)
    if not explorer_base:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.STAKED_TOTAL,
                reason="No Blockscout instance for this chain — cannot enumerate top holders to fingerprint.",
                suggested_source="Dune holder snapshot or alternate explorer.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    try:
        top_holders = blockscout.get_token_holders(
            identity.chain, identity.token_address, config, page=1, offset=MAX_HOLDERS_TO_INSPECT
        )
    except ProviderError as exc:
        warnings.append(WarningItem(code=WarningCode.BLOCKSCOUT_UNAVAILABLE, message=str(exc), severity="warning"))
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    staking_contracts, contracts_inspected = _discover_staking_contracts(
        identity.chain, identity.token_address, top_holders, config, warnings
    )

    staked_total = 0
    for entry in staking_contracts:
        balance = entry.get("token_balance")
        if isinstance(balance, int):
            staked_total += balance

    metrics["staking_contracts"] = staking_contracts
    metrics["staked_total"] = staked_total if staked_total > 0 else None
    metrics["contract_holders_inspected"] = contracts_inspected

    if not staking_contracts and contracts_inspected > 0:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.STAKING_CONTRACTS,
                reason=(
                    f"Inspected {contracts_inspected} contract holders but none matched ERC-4626 "
                    "or Synthetix-style StakingRewards patterns."
                ),
                suggested_source="Protocol docs or DeFiLlama protocol adapters.",
            )
        )

    return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)


def _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config):
    return CommandResult(
        command="staking",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
