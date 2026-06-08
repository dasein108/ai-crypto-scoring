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


# How deep into the top-holders list we attempt contract fingerprinting.
# Each candidate takes ~1 RPC call for eth_getCode + up to ~5 for fingerprinting.
MAX_HOLDERS_TO_INSPECT = 30


def _safe_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _discover_locked_contracts(
    chain: str,
    token_address: str,
    top_holders: list[dict],
    config: AppConfig,
    warnings: list[WarningItem],
) -> tuple[list[dict], list[dict], int, int]:
    """Fingerprint top-holder addresses for known lock patterns.

    Returns (vesting_contracts, timelock_balances, contract_holders_inspected,
    non_contract_holders_skipped).
    """
    vesting_contracts: list[dict] = []
    timelock_balances: list[dict] = []
    contracts_inspected = 0
    non_contract_skipped = 0

    for holder in top_holders[:MAX_HOLDERS_TO_INSPECT]:
        address = str(holder.get("address") or "")
        if not address:
            continue
        if contract_registry.is_burn_address(address):
            # Burn addresses are classified by the liquidity / holders modules,
            # not as vesting.
            continue
        try:
            if not rpc.is_contract(chain, address, config):
                non_contract_skipped += 1
                continue
        except ProviderError as exc:
            warnings.append(WarningItem(code=WarningCode.RPC_UNAVAILABLE, message=str(exc), severity="info"))
            continue
        contracts_inspected += 1

        # 1. OpenZeppelin VestingWallet (start/duration/released)
        vesting = rpc.inspect_vesting_wallet(chain, address, token_address, config)
        if vesting and vesting.get("token_balance"):
            vesting_contracts.append(vesting)
            continue

        # 2. OpenZeppelin TokenTimelock (releaseTime/token)
        timelock = rpc.inspect_token_timelock(chain, address, token_address, config)
        if timelock and timelock.get("token_balance"):
            vesting_contracts.append(timelock)
            continue

        # 3. Known locker contract registered in contract_registry
        known_label = contract_registry.known_locker_label(chain, address)
        if known_label:
            timelock_balances.append(
                {
                    "address": address,
                    "label": known_label,
                    "type": "registered_locker",
                    "token_balance": _safe_int(holder.get("value")),
                }
            )
            continue

        # 4. Verified-name fallback: Blockscout exposes a verified contract
        # name + token name + public tags, any of which can indicate vesting/
        # lock semantics. Check every hint, not just the contract name — a
        # TransparentUpgradeableProxy wrapping a "StakedPendle" token needs
        # the token name to fire.
        hints = _collect_address_hints(chain, address, config)
        matched_vesting = next(
            (h for h in hints if contract_registry.name_suggests_vesting(h)), None
        )
        if matched_vesting:
            vesting_contracts.append(
                {
                    "contract": address,
                    "type": "heuristic_name_match",
                    "verified_name": matched_vesting,
                    "name_hints": hints,
                    "token_balance": _safe_int(holder.get("value")),
                    "confidence": "low",
                }
            )
            continue

        # 5. Generic contract holder — record without asserting lock semantics.
        # Down-stream scoring treats these as weak signals only.
        timelock_balances.append(
            {
                "address": address,
                "label": hints[0] if hints else None,
                "name_hints": hints,
                "type": "contract_balance",
                "token_balance": _safe_int(holder.get("value")),
            }
        )

    return vesting_contracts, timelock_balances, contracts_inspected, non_contract_skipped


def _collect_address_hints(chain: str, address: str, config: AppConfig) -> list[str]:
    """Return every verified/tag hint Blockscout has for the address.

    Used for keyword-based fingerprinting; the caller scans every hint rather
    than betting on a single "primary" name. Returns [] on any failure.
    """
    try:
        info = blockscout.get_address_info(chain, address, config)
    except ProviderError:
        return []
    return blockscout.collect_name_hints(info)


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)
    metrics: dict[str, object] = {
        "vesting_contracts": [],
        "timelock_balances": [],
        "locked_onchain_total": None,
        "contract_holders_inspected": 0,
        "non_contract_holders_skipped": 0,
    }

    if not identity.token_address:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.LOCKED_ONCHAIN_TOTAL,
                reason="No token address available.",
                suggested_source="Resolve token address first.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)

    if config.offline:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.LOCKED_ONCHAIN_TOTAL,
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
                metric=CoverageMetric.LOCKED_ONCHAIN_TOTAL,
                reason="No RPC URL configured for this chain.",
                suggested_source="Set TOKEN_RESEARCH_ETH_RPC_URL / BASE_RPC_URL / ARB_RPC_URL / OP_RPC_URL.",
            )
        )
        return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)
    if not explorer_base:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.LOCKED_ONCHAIN_TOTAL,
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

    vesting_contracts, timelock_balances, contracts_inspected, non_contract_skipped = _discover_locked_contracts(
        identity.chain, identity.token_address, top_holders, config, warnings
    )

    locked_total = 0
    for entry in vesting_contracts:
        balance = entry.get("token_balance")
        if isinstance(balance, int):
            locked_total += balance
    for entry in timelock_balances:
        if entry.get("type") == "registered_locker":
            balance = entry.get("token_balance")
            if isinstance(balance, int):
                locked_total += balance

    metrics["vesting_contracts"] = vesting_contracts
    metrics["timelock_balances"] = timelock_balances
    metrics["locked_onchain_total"] = locked_total if locked_total > 0 else None
    metrics["contract_holders_inspected"] = contracts_inspected
    metrics["non_contract_holders_skipped"] = non_contract_skipped

    if not vesting_contracts and contracts_inspected > 0:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.VESTING_CONTRACTS,
                reason=(
                    f"Inspected {contracts_inspected} contract holders but none matched OZ VestingWallet or "
                    "TokenTimelock patterns. Custom vesting implementations require project-specific heuristics."
                ),
                suggested_source="Project documentation or governance forum.",
            )
        )
    if contracts_inspected == 0 and top_holders:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.VESTING_CONTRACTS,
                reason="No contract addresses found among top holders.",
                suggested_source="Extend holder scan depth or inspect known team addresses manually.",
            )
        )

    return _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config)


def _build(query, chain, address, include_premium, identity, metrics, warnings, coverage_gaps, config):
    return CommandResult(
        command="locks",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
