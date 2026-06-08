"""Cross-metric comparison command.

Collects results from supply/holders/liquidity/locks/staking/yields/price and
derives ratios that surface structural questions the individual commands
can't answer on their own:

- What fraction of supply is actually tradeable (float) vs in custody?
- Does DEX liquidity cover circulating market cap?
- Is the protocol generating fees relative to its TVL / mcap / custody base?
- How does locked liquidity compare to locked tokens?
- What would the implied yield be if protocol fees were distributed to stakers?

Every ratio is derived — no network calls of its own — so this command is
cheap once the underlying ones have run.
"""

from __future__ import annotations

from dataclasses import asdict

from token_research.commands import holders, liquidity, locks, price, staking, supply, yields
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, dataclass_list
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    try:
        return round(float(numerator) / float(denominator), 6)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _pct(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value * 100, digits)


def _raw_to_tokens(raw: object, decimals: int | None) -> float | None:
    """Convert a uint256 raw balance to a decimal token amount."""
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if decimals is None:
        return None
    return n / (10 ** decimals)


def _compute_ratios(
    supply_result: dict,
    holders_result: dict,
    liquidity_result: dict,
    locks_result: dict,
    staking_result: dict,
    yields_result: dict,
    price_result: dict,
) -> tuple[dict, list[str]]:
    """Build the structured ratio/comparison payload and return (metrics, notes)."""
    notes: list[str] = []

    sup = supply_result.get("metrics") or {}
    hld = holders_result.get("metrics") or {}
    liq = liquidity_result.get("metrics") or {}
    lck = locks_result.get("metrics") or {}
    stk = staking_result.get("metrics") or {}
    yld = yields_result.get("metrics") or {}
    prc = price_result.get("metrics") or {}

    total_supply_tokens = _safe_float(sup.get("total_supply_adjusted"))
    decimals = sup.get("decimals") if isinstance(sup.get("decimals"), int) else None
    price_usd = (
        _safe_float(prc.get("spot_price_usd"))
        or _safe_float(prc.get("defillama_price"))
        or _safe_float(prc.get("price_usd"))
        or _safe_float(prc.get("price"))
    )

    # Custody (raw → tokens) — fingerprinted vesting + staking only.
    locked_raw = lck.get("locked_onchain_total")
    staked_raw = stk.get("staked_total")
    locked_tokens = _raw_to_tokens(locked_raw, decimals)
    staked_tokens = _raw_to_tokens(staked_raw, decimals)

    custody_tokens = None
    if locked_tokens is not None or staked_tokens is not None:
        custody_tokens = (locked_tokens or 0.0) + (staked_tokens or 0.0)

    locked_pct_of_supply = _ratio(locked_tokens, total_supply_tokens)
    staked_pct_of_supply = _ratio(staked_tokens, total_supply_tokens)
    custody_pct_of_supply = _ratio(custody_tokens, total_supply_tokens)

    # Protocol-owned total: include every contract-held balance, not just
    # fingerprinted vesting/staking. This covers DAO wrapper contracts,
    # treasury GnosisSafes, bridge proxies, and other "non-EOA, non-circulating"
    # supply that the ownership pillar otherwise treats as whale concentration.
    # Morpho's `Wrapper` contract holds ~57% of supply this way — not caught
    # by the OZ VestingWallet keyword but clearly not a whale.
    #
    # Build the fingerprinted-address set FIRST so the timelock loop knows
    # which entries are already represented in `custody_tokens`. Without this,
    # protocols where staking/vesting contracts also appear in
    # `timelock_balances` (e.g. SDT veSDT + sdSDT wrappers) double-count the
    # same physical supply: custody+contract_held > total_supply, and
    # effective_circulating goes negative.
    fingerprinted_addresses: set[str] = set()
    for entry in (lck.get("vesting_contracts") or []):
        if isinstance(entry, dict):
            a = str(entry.get("contract") or "").lower()
            if a:
                fingerprinted_addresses.add(a)
    for entry in (stk.get("staking_contracts") or []):
        if isinstance(entry, dict):
            a = str(entry.get("contract") or "").lower()
            if a:
                fingerprinted_addresses.add(a)

    timelock_entries = lck.get("timelock_balances") or []
    contract_held_tokens = 0.0
    # Start the protocol-owned address set with the fingerprinted ones so
    # "effective top-10" filtering excludes them too, then add timelock entries.
    contract_held_addresses: set[str] = set(fingerprinted_addresses)
    duplicates_skipped = 0
    duplicate_balance_skipped = 0.0
    for entry in timelock_entries:
        if not isinstance(entry, dict):
            continue
        addr = str(entry.get("address") or "").lower()
        if not addr:
            continue
        contract_held_addresses.add(addr)
        balance_raw = entry.get("token_balance")
        bal = _raw_to_tokens(balance_raw, decimals)
        if addr in fingerprinted_addresses:
            # Already counted inside custody_tokens (locked + staked totals).
            # Adding it again would double-count the same on-chain SDT.
            if bal:
                duplicates_skipped += 1
                duplicate_balance_skipped += bal
            continue
        if bal:
            contract_held_tokens += bal

    protocol_owned_tokens: float | None = None
    if custody_tokens is not None or contract_held_tokens > 0:
        protocol_owned_tokens = (custody_tokens or 0.0) + contract_held_tokens

    # Defensive clamp: if a protocol uses non-standard wrappers we couldn't
    # match (e.g. sdToken-style synthetic shares minted 1:1 against deposits),
    # protocol_owned can still exceed total_supply. Cap and surface a note so
    # downstream pillars don't compute negative effective_circulating.
    protocol_owned_clamped = False
    if (
        protocol_owned_tokens is not None
        and total_supply_tokens is not None
        and protocol_owned_tokens > total_supply_tokens
    ):
        notes.append(
            f"protocol_owned ({protocol_owned_tokens:,.0f}) exceeded total_supply "
            f"({total_supply_tokens:,.0f}) — likely a wrapper/synthetic-share contract "
            "double-counts underlying. Clamped to total_supply."
        )
        protocol_owned_tokens = total_supply_tokens
        protocol_owned_clamped = True

    if duplicates_skipped:
        notes.append(
            f"{duplicates_skipped} timelock balance(s) ({duplicate_balance_skipped:,.0f} tokens) "
            "skipped as duplicates of fingerprinted vesting/staking custody."
        )
    protocol_owned_pct_of_supply = _ratio(protocol_owned_tokens, total_supply_tokens)

    # "Float" (legacy): supply minus fingerprinted custody only.
    # "Effective circulating": supply minus ALL protocol-owned tokens.
    float_tokens = None
    if total_supply_tokens is not None:
        float_tokens = total_supply_tokens - (custody_tokens or 0.0)
    float_pct_of_supply = _ratio(float_tokens, total_supply_tokens)

    effective_circulating_tokens: float | None = None
    if total_supply_tokens is not None and protocol_owned_tokens is not None:
        effective_circulating_tokens = total_supply_tokens - protocol_owned_tokens
    effective_circulating_pct = _ratio(effective_circulating_tokens, total_supply_tokens)

    # USD-denominated float / mcap / tvl
    total_supply_usd = (
        total_supply_tokens * price_usd if total_supply_tokens is not None and price_usd is not None else None
    )
    float_usd = float_tokens * price_usd if float_tokens is not None and price_usd is not None else None
    custody_usd = (
        custody_tokens * price_usd if custody_tokens is not None and price_usd is not None else None
    )
    staked_usd = staked_tokens * price_usd if staked_tokens is not None and price_usd is not None else None

    mcap_usd = _safe_float(yld.get("mcap_usd")) or _safe_float(sup.get("circulating_supply_vendor"))
    tvl_usd = _safe_float(yld.get("tvl_usd"))
    dex_liq_usd = _safe_float(liq.get("total_dex_liquidity_usd"))
    yield_pool_tvl_usd = _safe_float(yld.get("total_yield_tvl_usd"))
    lp_locked_pct = _safe_float(liq.get("lp_locked_percent"))

    ann_fees = _safe_float(yld.get("annualized_fees_usd"))
    ann_rev = _safe_float(yld.get("annualized_revenue_usd"))

    top10_share = _safe_float((hld.get("concentration") or {}).get("top10_share"))

    # Healthy top-10 ceiling for concentration-as-float heuristic.
    # Any top-10 EOA share above this threshold is treated as structural
    # supply pressure (investor/team vested holdings sitting in EOAs).
    TOP10_HEALTH_THRESHOLD_PCT = 20.0

    # Effective top-10 share: rank top holders whose address is NOT in the
    # protocol-owned set, and compute the combined share of those top 10.
    # Requires us to already know total_supply and a raw holder list.
    top_holders = hld.get("top_holders") or []
    effective_top10_share: float | None = None
    non_protocol_holders_considered = 0
    if top_holders and decimals is not None and total_supply_tokens:
        non_protocol_balances: list[float] = []
        for h in top_holders:
            if not isinstance(h, dict):
                continue
            addr = str(h.get("address") or "").lower()
            if addr in contract_held_addresses:
                continue  # protocol-owned — exclude
            bal_raw = h.get("value")
            bal = _raw_to_tokens(bal_raw, decimals)
            if bal is not None:
                non_protocol_balances.append(bal)
        non_protocol_holders_considered = len(non_protocol_balances)
        if non_protocol_balances:
            # Top-10 of the NON-protocol holders, divided by total supply.
            non_protocol_balances.sort(reverse=True)
            top10_non_protocol = sum(non_protocol_balances[:10])
            effective_top10_share = top10_non_protocol / total_supply_tokens
            # Alternative: divide by effective_circulating instead of total.
            # Both useful; we surface the "share of total" view for parity
            # with the legacy top10_share.

    # --- Effective overhang — KAITO-class fix ---
    # The legacy `protocol_owned_pct / effective_circulating_pct` ratio
    # correctly captures DAO-treasury overhang (Morpho/ONDO class) but
    # misses tokens where investor/team allocations are held in EOAs
    # rather than multisig contracts (KAITO class: 82% effective top-10
    # in EOAs, only 13% in contracts).
    #
    # Effective overhang combines both forms of structural supply:
    #
    #     effective_overhang_pct =
    #         protocol_owned_pct + max(0, effective_top10_pct - 20)
    #
    # The 20% top-10 ceiling is the "reasonable healthy distribution"
    # threshold. Concentration above that is treated as structural
    # overhang regardless of whether it's in contracts or EOAs.
    #
    # Reference readings (unchanged for Morpho/ONDO/Pendle):
    #   Morpho  68.43 + max(0, 11.41-20) = 68.43%  ratio 2.17×  (unchanged)
    #   ONDO    60.04 + max(0, 14.31-20) = 60.04%  ratio 1.50×  (unchanged)
    #   Pendle  ~35  + max(0, 11.41-20) = 35%      ratio 0.55×  (unchanged)
    #   KAITO   12.96 + max(0, 82.25-20) = 75.21%  ratio 3.04×  (FIXED)
    # Note: protocol_owned_pct_of_supply and effective_top10_share are
    # fractions in [0, 1] because they come from _ratio(). Multiply by 100
    # to get percentages for the sum.
    effective_overhang_pct: float | None = None
    effective_overhang_ratio: float | None = None
    if protocol_owned_pct_of_supply is not None:
        eff10_pct = (effective_top10_share or 0.0) * 100.0
        excess_eoa_concentration = max(0.0, eff10_pct - TOP10_HEALTH_THRESHOLD_PCT)
        effective_overhang_pct = (protocol_owned_pct_of_supply * 100.0) + excess_eoa_concentration
        true_float_pct = 100.0 - effective_overhang_pct
        if true_float_pct > 0:
            effective_overhang_ratio = effective_overhang_pct / true_float_pct
        else:
            notes.append(
                "Full protocol custody (~100%): effective overhang ratio undefined — "
                "no tradeable float remains."
            )

    # --- Supply composition ---
    supply_block = {
        "total_supply_tokens": total_supply_tokens,
        "decimals": decimals,
        "price_usd": price_usd,
        "total_supply_usd": total_supply_usd,
        "locked_onchain_tokens": locked_tokens,
        "staked_onchain_tokens": staked_tokens,
        "custody_tokens": custody_tokens,
        "contract_held_tokens_total": round(contract_held_tokens, 4) if contract_held_tokens else None,
        "protocol_owned_tokens": protocol_owned_tokens,
        "float_tokens_est": float_tokens,
        "effective_circulating_tokens": effective_circulating_tokens,
        "locked_pct_of_supply": _pct(locked_pct_of_supply, 2),
        "staked_pct_of_supply": _pct(staked_pct_of_supply, 2),
        "custody_pct_of_supply": _pct(custody_pct_of_supply, 2),
        "protocol_owned_pct_of_supply": _pct(protocol_owned_pct_of_supply, 2),
        "float_pct_of_supply": _pct(float_pct_of_supply, 2),
        "effective_circulating_pct_of_supply": _pct(effective_circulating_pct, 2),
        "top10_pct_of_supply": _pct(top10_share, 2),
        "effective_top10_pct_of_supply": _pct(effective_top10_share, 2),
        "effective_top10_excludes_protocol": bool(contract_held_addresses),
        "non_protocol_holders_considered": non_protocol_holders_considered,
        # NEW: effective overhang — counts concentrated EOA top-10 as
        # structural supply in addition to protocol-owned contracts.
        "effective_overhang_pct_of_supply": round(effective_overhang_pct, 2) if effective_overhang_pct is not None else None,
        "effective_overhang_ratio": round(effective_overhang_ratio, 3) if effective_overhang_ratio is not None else None,
        "protocol_owned_clamped_to_supply": protocol_owned_clamped,
    }

    # --- Market / liquidity depth ---
    market_block = {
        "mcap_usd": mcap_usd,
        "tvl_usd": tvl_usd,
        "dex_liquidity_usd": dex_liq_usd,
        "lp_locked_percent": lp_locked_pct,
        "yield_pool_tvl_usd": yield_pool_tvl_usd,
        "mcap_to_tvl": _ratio(mcap_usd, tvl_usd),
        "liquidity_to_mcap": _ratio(dex_liq_usd, mcap_usd),
        "liquidity_to_supply_usd": _ratio(dex_liq_usd, total_supply_usd),
        "liquidity_to_float_usd": _ratio(dex_liq_usd, float_usd),
        "yield_pool_to_protocol_tvl": _ratio(yield_pool_tvl_usd, tvl_usd),
    }

    # --- Fees vs stakes / mcap / TVL ---
    fees_block = {
        "annualized_fees_usd": ann_fees,
        "annualized_revenue_usd": ann_rev,
        "revenue_share_of_fees": _ratio(ann_rev, ann_fees),
        "fees_to_mcap": _safe_float(yld.get("fees_to_mcap_ratio")),
        "fees_to_tvl": _ratio(ann_fees, tvl_usd),
        "revenue_to_mcap": _safe_float(yld.get("revenue_to_mcap_ratio")),
        "revenue_to_tvl": _ratio(ann_rev, tvl_usd),
        "fees_to_custody_usd": _ratio(ann_fees, custody_usd),
        "fees_to_staked_usd": _ratio(ann_fees, staked_usd),
        "implied_staker_apy_if_fees_distributed": _pct(_ratio(ann_fees, staked_usd), 2),
    }

    # --- Custody comparisons ---
    custody_block = {
        "locked_tokens_vs_staked": _ratio(locked_tokens, staked_tokens),
        "custody_vs_float": _ratio(custody_tokens, float_tokens),
        "custody_vs_dex_liquidity_usd": _ratio(custody_usd, dex_liq_usd),
        "lp_locked_vs_token_custody_pct": (
            {
                "lp_locked_percent": lp_locked_pct,
                "token_custody_percent": _pct(custody_pct_of_supply, 2),
            }
            if (lp_locked_pct is not None or custody_pct_of_supply is not None)
            else None
        ),
    }

    # --- Yield comparison ---
    yield_block = {
        "best_apy_percent": _safe_float(yld.get("best_apy")),
        "weighted_avg_apy_percent": _safe_float(yld.get("weighted_avg_apy")),
        "pool_count": yld.get("yield_pool_count"),
        "implied_staker_apy_percent": fees_block["implied_staker_apy_if_fees_distributed"],
        "market_apy_vs_implied_apy": _ratio(
            _safe_float(yld.get("weighted_avg_apy")),
            fees_block["implied_staker_apy_if_fees_distributed"],
        ),
    }

    # --- Notes explaining estimates + data gaps ---
    if protocol_owned_tokens and float_tokens and protocol_owned_tokens > float_tokens * 0.3:
        notes.append(
            f"{_pct(protocol_owned_pct_of_supply, 1)}% of supply is held by contracts "
            "(DAO treasury, wrappers, multisigs, vesting). Raw top_10_share overstates "
            "concentration — effective_top10 is the EOA-only view."
        )
    if total_supply_tokens is None:
        notes.append("total_supply missing — custody percentages and float estimates cannot be computed.")
    if price_usd is None:
        notes.append("price_usd missing — USD-denominated comparisons (float_usd, custody_usd, staker APY) are skipped.")
    if locked_tokens is None and staked_tokens is None:
        notes.append("No on-chain custody detected — float defaults to 100% of supply.")
    elif decimals is None:
        notes.append("decimals missing — locked/staked raw uint256 cannot be converted to token counts.")
    if ann_fees is None:
        notes.append("no fees data — fees/* ratios skipped.")
    if mcap_usd is None:
        notes.append("mcap missing — liquidity/mcap ratio skipped.")
    if tvl_usd is None:
        notes.append("protocol TVL missing — fees/TVL skipped.")

    metrics = {
        "supply": supply_block,
        "market": market_block,
        "fees": fees_block,
        "custody": custody_block,
        "yield": yield_block,
        "notes": notes,
    }
    return metrics, notes


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)

    supply_result = supply.run(query, chain, address, config, include_premium).to_dict()
    holders_result = holders.run(query, chain, address, config, include_premium).to_dict()
    liquidity_result = liquidity.run(query, chain, address, config, include_premium).to_dict()
    locks_result = locks.run(query, chain, address, config, include_premium).to_dict()
    staking_result = staking.run(query, chain, address, config, include_premium).to_dict()
    yields_result = yields.run(query, chain, address, config, include_premium).to_dict()
    price_result = price.run(query, chain, address, config, include_premium).to_dict()

    metrics, notes = _compute_ratios(
        supply_result,
        holders_result,
        liquidity_result,
        locks_result,
        staking_result,
        yields_result,
        price_result,
    )

    if all(
        block_value in (None, 0)
        for section in ("supply", "market", "fees")
        for block_value in (metrics[section].values() if isinstance(metrics[section], dict) else [])
        if not isinstance(block_value, dict)
    ):
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.COMPARE,
                reason="No comparable inputs — all upstream modules returned empty.",
                suggested_source="Run resolve/supply/yields commands first to understand what's missing.",
            )
        )

    return CommandResult(
        command="compare",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
