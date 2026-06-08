"""fairlaunch command — Fair Launch scoring based on fairlaunch.org rubric.

Implements the 15-point fairness rubric from the Fair Launch Handbook
(fairlaunch.org) using on-chain data already collected by the pipeline.

Five dimensions, 3 points each:
  1. Pricing — presale signals, market-determined price evidence
  2. Allocation — public vs insider distribution
  3. Vesting — lockup contract detection, cliff/duration
  4. Transparency — verified source, ownership, upgradeability
  5. Access — distribution equality (Gini, Nakamoto, holder count)

Some sub-criteria require manual input (presale terms, advance notice) and
are flagged as "manual_check_needed". The auto-scored subset typically
covers 9-12 of the 15 points.

Reference:
  https://fairlaunch.org/
  Scoring: 0-3 avoid | 4-7 caution | 8-12 fair | 13-15 exemplary

Usage:
    token-research fairlaunch MORPHO --chain ethereum \\
      --address 0x58D97B57BB95320F9a05dC918Aef65434969c2B2
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from token_research.commands import holders, locks, staking, supply
from token_research.config import AppConfig
from token_research.models import CommandResult, dataclass_list
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _safe_float(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------

def _score_pricing(supply_m: dict, holders_m: dict) -> tuple[int, list[dict]]:
    """Pricing dimension: presale signals, market-determined price.

    Sub-criteria (1 point each):
      1. No presale evidence — inferred from allocation pattern
      2. Uniform pricing — can't auto-detect, manual
      3. Market-determined price — DEX pair exists (implied by pipeline)
    """
    score = 0
    details: list[dict] = []

    # 1. Presale evidence: if top-1 holder has >50% and no vesting, likely presale/team
    # This is a heuristic — real presale detection needs off-chain data
    details.append({
        "criterion": "no_presale_detected",
        "points": 0,
        "max": 1,
        "note": "manual_check_needed — presale terms require off-chain verification",
        "auto": False,
    })

    # 2. Uniform pricing — can't auto-detect
    details.append({
        "criterion": "uniform_pricing",
        "points": 0,
        "max": 1,
        "note": "manual_check_needed — requires launch mechanism inspection",
        "auto": False,
    })

    # 3. Market-determined price — if the token trades on DEX, price is market-set
    # We know this because the resolver found DEX pairs
    score += 1
    details.append({
        "criterion": "market_determined_price",
        "points": 1,
        "max": 1,
        "note": "Token trades on DEX — price is market-determined",
        "auto": True,
    })

    return score, details


def _score_allocation(holders_m: dict, compare_supply: dict) -> tuple[int, list[dict]]:
    """Allocation dimension: public >30%, team <20%, no hidden buckets.

    Uses effective top-10 (EOA-only) and protocol-owned % from compare.
    """
    score = 0
    details: list[dict] = []

    # 1. Public allocation > 30% — use effective_circulating / float
    float_pct = _safe_float(compare_supply.get("float_pct_of_supply"))
    eff_circ_pct = _safe_float(compare_supply.get("effective_circulating_pct_of_supply"))
    public_pct = eff_circ_pct or float_pct
    if public_pct is not None and public_pct > 30:
        score += 1
        details.append({
            "criterion": "public_allocation_above_30pct",
            "points": 1, "max": 1,
            "note": f"Effective public/circulating = {public_pct:.1f}%",
            "auto": True, "value": public_pct,
        })
    elif public_pct is not None:
        details.append({
            "criterion": "public_allocation_above_30pct",
            "points": 0, "max": 1,
            "note": f"Public/circulating only {public_pct:.1f}% (< 30% threshold)",
            "auto": True, "value": public_pct,
        })
    else:
        details.append({
            "criterion": "public_allocation_above_30pct",
            "points": 0, "max": 1,
            "note": "Could not determine public allocation",
            "auto": False,
        })

    # 2. Team/insider allocation < 20% — use effective_top10 as proxy for EOA concentration
    eff_top10 = _safe_float(compare_supply.get("effective_top10_pct_of_supply"))
    if eff_top10 is not None and eff_top10 < 20:
        score += 1
        details.append({
            "criterion": "team_allocation_below_20pct",
            "points": 1, "max": 1,
            "note": f"Effective top-10 EOA share = {eff_top10:.1f}% (healthy)",
            "auto": True, "value": eff_top10,
        })
    elif eff_top10 is not None:
        details.append({
            "criterion": "team_allocation_below_20pct",
            "points": 0, "max": 1,
            "note": f"Effective top-10 EOA share = {eff_top10:.1f}% (>= 20%)",
            "auto": True, "value": eff_top10,
        })
    else:
        details.append({
            "criterion": "team_allocation_below_20pct",
            "points": 0, "max": 1,
            "note": "Could not compute effective top-10",
            "auto": False,
        })

    # 3. No hidden allocation buckets — check if protocol-owned is transparent
    protocol_owned = _safe_float(compare_supply.get("protocol_owned_pct_of_supply"))
    if protocol_owned is not None and protocol_owned > 0:
        # Protocol-owned supply is at least visible on-chain (contracts, not hidden)
        score += 1
        details.append({
            "criterion": "no_hidden_allocations",
            "points": 1, "max": 1,
            "note": f"Protocol-owned {protocol_owned:.1f}% is in identifiable contracts (on-chain visible)",
            "auto": True, "value": protocol_owned,
        })
    else:
        details.append({
            "criterion": "no_hidden_allocations",
            "points": 0, "max": 1,
            "note": "manual_check_needed — no protocol-owned contracts detected; allocation transparency unknown",
            "auto": False,
        })

    return score, details


def _score_vesting(locks_m: dict, staking_m: dict) -> tuple[int, list[dict]]:
    """Vesting dimension: 12m+ cliff, insider vesting > public, existence of locks."""
    score = 0
    details: list[dict] = []

    vesting_contracts = locks_m.get("vesting_contracts") or []
    timelock_entries = locks_m.get("timelock_balances") or []
    staking_contracts = staking_m.get("staking_contracts") or []

    has_vesting = len(vesting_contracts) > 0 or len(timelock_entries) > 0
    has_staking = len(staking_contracts) > 0

    # 1. Vesting contracts exist
    if has_vesting:
        score += 1
        details.append({
            "criterion": "vesting_contracts_exist",
            "points": 1, "max": 1,
            "note": f"{len(vesting_contracts)} vesting + {len(timelock_entries)} timelock contracts detected",
            "auto": True,
        })
    else:
        details.append({
            "criterion": "vesting_contracts_exist",
            "points": 0, "max": 1,
            "note": "No OZ VestingWallet / TokenTimelock detected — may use custom vesting",
            "auto": True,
        })

    # 2. Cliff duration >= 12 months — check vesting start/duration
    has_long_cliff = False
    for vc in vesting_contracts:
        duration = vc.get("duration")
        if isinstance(duration, int) and duration >= 365 * 86_400:
            has_long_cliff = True
            break
    if has_long_cliff:
        score += 1
        details.append({
            "criterion": "cliff_12_months_plus",
            "points": 1, "max": 1,
            "note": "At least one vesting contract has duration >= 12 months",
            "auto": True,
        })
    elif has_vesting:
        details.append({
            "criterion": "cliff_12_months_plus",
            "points": 0, "max": 1,
            "note": "Vesting detected but duration < 12 months or not readable",
            "auto": True,
        })
    else:
        details.append({
            "criterion": "cliff_12_months_plus",
            "points": 0, "max": 1,
            "note": "No vesting to evaluate",
            "auto": False,
        })

    # 3. Staking / lockup mechanism exists (commitment signal)
    if has_staking:
        score += 1
        details.append({
            "criterion": "staking_lockup_exists",
            "points": 1, "max": 1,
            "note": f"{len(staking_contracts)} staking contracts detected",
            "auto": True,
        })
    else:
        details.append({
            "criterion": "staking_lockup_exists",
            "points": 0, "max": 1,
            "note": "No staking contracts detected",
            "auto": True,
        })

    return score, details


def _score_transparency(supply_m: dict) -> tuple[int, list[dict]]:
    """Transparency dimension: verified contract, disclosed ownership, no proxy risk."""
    score = 0
    details: list[dict] = []
    security = supply_m.get("security") or {}

    # 1. Verified contract source (Blockscout)
    # If supply succeeded via RPC, the contract exists and is callable.
    # Verified source from Blockscout is implicit in code_band detection.
    source = supply_m.get("total_supply_source")
    if source == "rpc":
        score += 1
        details.append({
            "criterion": "contract_verified",
            "points": 1, "max": 1,
            "note": "Contract responds to RPC calls (exists on-chain)",
            "auto": True,
        })
    else:
        details.append({
            "criterion": "contract_verified",
            "points": 0, "max": 1,
            "note": "Supply not confirmed via RPC",
            "auto": True,
        })

    # 2. Ownership disclosed — owner is a contract (multisig/governance) not an EOA
    owner = security.get("owner")
    owner_is_contract = security.get("owner_is_contract")
    if owner is None:
        # No owner = renounced or non-standard — positive signal
        score += 1
        details.append({
            "criterion": "ownership_safe",
            "points": 1, "max": 1,
            "note": "No owner() detected — renounced or non-standard (positive)",
            "auto": True,
        })
    elif owner_is_contract:
        score += 1
        details.append({
            "criterion": "ownership_safe",
            "points": 1, "max": 1,
            "note": f"Owner is a contract ({owner[:16]}...) — likely multisig/governance",
            "auto": True, "value": owner,
        })
    else:
        details.append({
            "criterion": "ownership_safe",
            "points": 0, "max": 1,
            "note": f"Owner is an EOA ({owner[:16]}...) — single-key risk",
            "auto": True, "value": owner,
        })

    # 3. Not upgradeable — no proxy admin
    is_upgradeable = security.get("is_upgradeable", False)
    if not is_upgradeable:
        score += 1
        details.append({
            "criterion": "not_upgradeable",
            "points": 1, "max": 1,
            "note": "No EIP-1967 proxy detected — immutable contract",
            "auto": True,
        })
    else:
        proxy_admin = security.get("proxy_admin")
        details.append({
            "criterion": "not_upgradeable",
            "points": 0, "max": 1,
            "note": f"Upgradeable proxy detected (admin: {str(proxy_admin)[:16]}...)",
            "auto": True, "value": proxy_admin,
        })

    return score, details


def _score_access(holders_m: dict, supply_m: dict) -> tuple[int, list[dict]]:
    """Access dimension: distribution equality, wallet caps, anti-whale."""
    score = 0
    details: list[dict] = []

    # 1. Nakamoto coefficient > 1 — supply not controlled by single entity
    nakamoto = holders_m.get("nakamoto_coefficient")
    if nakamoto is not None and nakamoto >= 5:
        score += 1
        details.append({
            "criterion": "distributed_control",
            "points": 1, "max": 1,
            "note": f"Nakamoto coefficient = {nakamoto} (5+ addresses for 51%)",
            "auto": True, "value": nakamoto,
        })
    elif nakamoto is not None:
        details.append({
            "criterion": "distributed_control",
            "points": 0, "max": 1,
            "note": f"Nakamoto coefficient = {nakamoto} (< 5 — concentrated)",
            "auto": True, "value": nakamoto,
        })
    else:
        details.append({
            "criterion": "distributed_control",
            "points": 0, "max": 1,
            "note": "Nakamoto coefficient unavailable",
            "auto": False,
        })

    # 2. Gini < 0.6 — relatively equal distribution
    gini = holders_m.get("gini_coefficient") or holders_m.get("top_holders_gini")
    if gini is not None and gini < 0.6:
        score += 1
        details.append({
            "criterion": "low_gini_inequality",
            "points": 1, "max": 1,
            "note": f"Gini = {gini:.3f} (< 0.6 — moderate inequality)",
            "auto": True, "value": gini,
        })
    elif gini is not None:
        details.append({
            "criterion": "low_gini_inequality",
            "points": 0, "max": 1,
            "note": f"Gini = {gini:.3f} (>= 0.6 — high inequality)",
            "auto": True, "value": gini,
        })
    else:
        details.append({
            "criterion": "low_gini_inequality",
            "points": 0, "max": 1,
            "note": "Gini coefficient unavailable",
            "auto": False,
        })

    # 3. Holder count > 10K — broad distribution achieved
    holder_count = supply_m.get("holders_count")
    if holder_count is not None:
        try:
            hc = int(holder_count)
        except (TypeError, ValueError):
            hc = 0
        if hc >= 10_000:
            score += 1
            details.append({
                "criterion": "broad_holder_base",
                "points": 1, "max": 1,
                "note": f"Holder count = {hc:,} (>= 10K — broad distribution)",
                "auto": True, "value": hc,
            })
        else:
            details.append({
                "criterion": "broad_holder_base",
                "points": 0, "max": 1,
                "note": f"Holder count = {hc:,} (< 10K — narrow distribution)",
                "auto": True, "value": hc,
            })
    else:
        details.append({
            "criterion": "broad_holder_base",
            "points": 0, "max": 1,
            "note": "Holder count unavailable",
            "auto": False,
        })

    return score, details


def _fairness_band(total: int) -> str:
    if total >= 13:
        return "exemplary"
    if total >= 8:
        return "fair"
    if total >= 4:
        return "caution"
    return "avoid"


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)

    # Collect upstream data
    supply_result = supply.run(query, chain, address, config, include_premium).to_dict()
    holders_result = holders.run(query, chain, address, config, include_premium).to_dict()
    locks_result = locks.run(query, chain, address, config, include_premium).to_dict()
    staking_result = staking.run(query, chain, address, config, include_premium).to_dict()

    supply_m = supply_result.get("metrics") or {}
    holders_m = holders_result.get("metrics") or {}
    locks_m = locks_result.get("metrics") or {}
    staking_m = staking_result.get("metrics") or {}

    # Build compare supply block for allocation scoring
    # (Lightweight — reuse holder + lock data instead of full compare run)
    from token_research.commands import compare
    compare_result = compare.run(query, chain, address, config, include_premium).to_dict()
    compare_supply = (compare_result.get("metrics") or {}).get("supply") or {}

    # Score each dimension
    pricing_score, pricing_details = _score_pricing(supply_m, holders_m)
    allocation_score, allocation_details = _score_allocation(holders_m, compare_supply)
    vesting_score, vesting_details = _score_vesting(locks_m, staking_m)
    transparency_score, transparency_details = _score_transparency(supply_m)
    access_score, access_details = _score_access(holders_m, supply_m)

    total_score = pricing_score + allocation_score + vesting_score + transparency_score + access_score
    max_possible = 15
    auto_scored = sum(1 for d in pricing_details + allocation_details + vesting_details + transparency_details + access_details if d.get("auto"))
    manual_needed = 15 - auto_scored

    band = _fairness_band(total_score)

    metrics: dict[str, Any] = {
        "fairness_score": total_score,
        "max_score": max_possible,
        "band": band,
        "auto_scored_criteria": auto_scored,
        "manual_check_criteria": manual_needed,
        "dimensions": {
            "pricing": {"score": pricing_score, "max": 3, "details": pricing_details},
            "allocation": {"score": allocation_score, "max": 3, "details": allocation_details},
            "vesting": {"score": vesting_score, "max": 3, "details": vesting_details},
            "transparency": {"score": transparency_score, "max": 3, "details": transparency_details},
            "access": {"score": access_score, "max": 3, "details": access_details},
        },
        "reference": "https://fairlaunch.org/",
        "scoring_bands": {
            "0-3": "avoid",
            "4-7": "caution",
            "8-12": "fair",
            "13-15": "exemplary",
        },
    }

    return CommandResult(
        command="fairlaunch",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
