from __future__ import annotations

from dataclasses import asdict

from token_research.commands import compare, holders, liquidity, locks, staking, supply, unlocks, yields
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, dataclass_list
from token_research.providers import defillama
from token_research.providers.http import ProviderError
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


# Default pillar weights (must sum to 1.0).
# `token_capture` was added as the VC-lens "does the token actually accrue
# protocol economics?" pillar — distinct from `fundamental_support` which
# measures protocol size/quality regardless of token capture mechanism.
PILLAR_WEIGHTS = {
    "supply_pressure": 0.22,
    "ownership_quality": 0.22,
    "liquidity_quality": 0.22,
    "fundamental_support": 0.08,
    "governance_commitment": 0.13,
    "token_capture": 0.13,
}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _score_supply_pressure(
    supply_result: dict,
    compare_result: dict | None = None,
    yields_result: dict | None = None,
    unlocks_result: dict | None = None,
) -> tuple[float | None, dict]:
    """Score supply pressure from structural overhang + revenue absorption.

    Key inputs (optional — scoring degrades gracefully):
      - total_supply_tokens — basic data availability signal
      - protocol_owned_pct (from compare.supply) — how much supply is held
        in DAO treasuries, wrappers, multisigs that could be distributed.
        Higher = worse (dilution risk).
      - revenue_model (from yields) — fees_pass_through has no organic buy
        pressure; protocol_capture partially offsets future issuance.
      - overhang_ratio (protocol_owned / effective_circulating) — stress
        metric: Morpho ~2.17×, ONDO ~1.50×.
    """
    metrics = supply_result.get("metrics") or {}
    raw: dict = {}
    total = metrics.get("total_supply_adjusted")
    if total is None:
        return None, raw
    raw["total_supply_adjusted"] = total

    # Pull structural overhang from compare if available.
    #
    # Prefers `effective_overhang_pct_of_supply` and `effective_overhang_ratio`
    # (which count concentrated EOA top-10 as structural supply too — KAITO fix).
    # Falls back to the legacy protocol_owned_pct / effective_circulating_pct
    # calculation when compare's new fields are absent.
    protocol_owned_pct = None
    overhang_ratio = None
    overhang_pct_for_penalty = None
    if compare_result:
        sup_block = (compare_result.get("metrics") or {}).get("supply") or {}
        protocol_owned_pct = sup_block.get("protocol_owned_pct_of_supply")
        effective_pct = sup_block.get("effective_circulating_pct_of_supply")
        eff_overhang_pct = sup_block.get("effective_overhang_pct_of_supply")
        eff_overhang_ratio = sup_block.get("effective_overhang_ratio")

        # Prefer effective overhang when available.
        if eff_overhang_pct is not None and eff_overhang_ratio is not None:
            overhang_pct_for_penalty = eff_overhang_pct
            overhang_ratio = eff_overhang_ratio
            raw["overhang_source"] = "effective_overhang"
            raw["effective_overhang_pct_of_supply"] = eff_overhang_pct
            raw["effective_overhang_ratio"] = eff_overhang_ratio
        elif protocol_owned_pct is not None and effective_pct is not None and effective_pct > 0:
            overhang_pct_for_penalty = protocol_owned_pct
            overhang_ratio = protocol_owned_pct / effective_pct
            raw["overhang_source"] = "legacy_protocol_owned"
            raw["overhang_ratio"] = round(overhang_ratio, 3)

        if protocol_owned_pct is not None:
            raw["protocol_owned_pct_of_supply"] = protocol_owned_pct

    # Pull revenue model — determines whether fees absorb new supply.
    revenue_model = None
    if yields_result:
        rm = (yields_result.get("metrics") or {}).get("revenue_model")
        if rm:
            revenue_model = rm
            raw["revenue_model"] = rm

    if overhang_pct_for_penalty is None:
        raw["note"] = "Baseline score; compare.supply not available for overhang calculation."
        return 65.0, raw

    score = 75.0

    # Overhang penalty — more structural supply = more potential dilution.
    # Uses effective_overhang_pct (combines contract-held + concentrated EOA)
    # when available, falling back to legacy protocol_owned_pct otherwise.
    if overhang_pct_for_penalty >= 65:
        score -= 25  # Morpho 68.43% class — severe structural overhang
    elif overhang_pct_for_penalty >= 55:
        score -= 20  # ONDO 60.04% class
    elif overhang_pct_for_penalty >= 35:
        score -= 10  # Pendle 35.46% class
    elif overhang_pct_for_penalty >= 15:
        score -= 5
    # <15%: no penalty (distributed is good)

    # Revenue model offset — protocols that capture fees have organic buy
    # pressure that absorbs new supply into circulation.
    if revenue_model == "protocol_capture":
        score += 10
    elif revenue_model == "fees_pass_through":
        score -= 5

    # Overhang ratio — how much "unreleased" supply vs float.
    if overhang_ratio is not None:
        if overhang_ratio >= 2.0:
            score -= 5  # Morpho-class — material near-term dilution
        elif overhang_ratio >= 1.5:
            score -= 3  # ONDO-class

    # Cliff-overhang penalty — concrete upcoming dilution events from
    # `unlocks.cliff_overhang.horizons`. A 30%-of-supply unlock in 90 days
    # is fundamentally different from "60% sits in a multisig forever",
    # so this penalty fires on top of the structural overhang above.
    if unlocks_result:
        cliff = (unlocks_result.get("metrics") or {}).get("cliff_overhang") or {}
        horizons = cliff.get("horizons") or {}
        pct_90 = (horizons.get("90d") or {}).get("pct_of_total_supply")
        pct_180 = (horizons.get("180d") or {}).get("pct_of_total_supply")
        pct_365 = (horizons.get("365d") or {}).get("pct_of_total_supply")
        cliff_penalty = 0.0
        if pct_90 is not None:
            raw["cliff_unlock_pct_90d"] = pct_90
            if pct_90 >= 30:
                cliff_penalty += 30
            elif pct_90 >= 15:
                cliff_penalty += 15
            elif pct_90 >= 5:
                cliff_penalty += 5
        if pct_180 is not None:
            raw["cliff_unlock_pct_180d"] = pct_180
            if pct_180 >= 30:
                cliff_penalty += 10
            elif pct_180 >= 15:
                cliff_penalty += 5
        if pct_365 is not None:
            raw["cliff_unlock_pct_365d"] = pct_365
            if pct_365 >= 40:
                cliff_penalty += 5
        if cliff_penalty > 0:
            raw["cliff_penalty"] = cliff_penalty
            score -= cliff_penalty

    score = max(0.0, min(100.0, score))
    return round(score, 1), raw


def _score_ownership_quality(
    holders_result: dict, compare_result: dict | None = None
) -> tuple[float | None, dict]:
    """Score ownership quality from holders command metrics.

    Lower concentration = higher score. When a compare result is available,
    the pillar prefers the EOA-adjusted "effective_top10_share" over the raw
    top-10 so a DAO treasury or wrapper contract doesn't tank the score the
    same way a whale EOA would. Nakamoto-51 still reflects the raw view
    because it's the genuinely "worst-case single-point-of-failure" signal.
    """
    metrics = holders_result.get("metrics") or {}
    concentration = metrics.get("concentration") or {}
    raw: dict = {}

    raw_top10 = concentration.get("top10_share")
    if raw_top10 is None:
        return None, raw
    raw["top10_share_raw"] = raw_top10

    # Prefer the EOA-adjusted top-10 when compare has computed one; it comes
    # back as a percentage (e.g. 25.5), so divide by 100 to keep the formula.
    eff_pct = None
    if compare_result:
        cmetrics = compare_result.get("metrics") or {}
        sup = cmetrics.get("supply") or {}
        eff_raw = sup.get("effective_top10_pct_of_supply")
        if eff_raw is not None:
            eff_pct = eff_raw / 100.0

    top10_for_scoring = eff_pct if eff_pct is not None else raw_top10
    raw["top10_share_used"] = top10_for_scoring
    raw["top10_is_effective"] = eff_pct is not None
    if eff_pct is not None:
        raw["protocol_owned_pct_of_supply"] = sup.get("protocol_owned_pct_of_supply")

    # Score: low concentration is better
    score = _clamp((1.0 - top10_for_scoring) * 100.0)

    nakamoto = concentration.get("nakamoto_51")
    if nakamoto is not None:
        raw["nakamoto_51_raw"] = nakamoto
        # Bonus for high nakamoto count (more holders needed for 51%)
        if nakamoto >= 10:
            score = _clamp(score + 10)
        elif nakamoto <= 2:
            # Only apply the -15 penalty if the effective top-10 is ALSO
            # highly concentrated. A nakamoto_51 = 1 driven by a DAO wrapper
            # is not the same risk as one driven by a whale EOA.
            if top10_for_scoring >= 0.5:
                score = _clamp(score - 15)
            else:
                raw["nakamoto_penalty_skipped"] = "raw_nakamoto_low_but_effective_top10_ok"

    gini = concentration.get("top_holders_gini")
    if gini is not None:
        raw["top_holders_gini"] = gini
        if gini < 0.3:
            score = _clamp(score + 5)
        elif gini > 0.7 and not raw.get("top10_is_effective"):
            # Skip gini penalty when we already substituted effective top-10
            # — otherwise protocol-owned holders get double-penalised.
            score = _clamp(score - 10)

    return round(score, 1), raw


def _score_liquidity_quality(liquidity_result: dict) -> tuple[float | None, dict]:
    """Score liquidity quality from liquidity command metrics.

    Higher DEX liquidity and better distribution = higher score.
    """
    metrics = liquidity_result.get("metrics") or {}
    raw: dict = {}

    total_liq = metrics.get("total_dex_liquidity_usd")
    if total_liq is None:
        return None, raw
    raw["total_dex_liquidity_usd"] = total_liq

    # Score based on liquidity depth thresholds
    if total_liq >= 10_000_000:
        score = 90.0
    elif total_liq >= 1_000_000:
        score = 70.0 + (total_liq - 1_000_000) / 9_000_000 * 20.0
    elif total_liq >= 100_000:
        score = 40.0 + (total_liq - 100_000) / 900_000 * 30.0
    elif total_liq >= 10_000:
        score = 15.0 + (total_liq - 10_000) / 90_000 * 25.0
    else:
        score = total_liq / 10_000 * 15.0

    # Penalize high venue concentration
    by_dex = metrics.get("liquidity_by_dex") or {}
    if by_dex and total_liq > 0:
        max_venue = max(by_dex.values()) if by_dex else 0
        venue_concentration = max_venue / total_liq
        raw["top_venue_concentration"] = round(venue_concentration, 3)
        if venue_concentration > 0.95:
            score = _clamp(score - 10)

    # LP durability bonus — pool has a measurable locked share.
    lp_locked_pct = metrics.get("lp_locked_percent")
    if lp_locked_pct is not None:
        raw["lp_locked_percent"] = lp_locked_pct
        if lp_locked_pct >= 90:
            score = _clamp(score + 10)
        elif lp_locked_pct >= 50:
            score = _clamp(score + 5)
        elif lp_locked_pct < 5:
            score = _clamp(score - 5)

    # Exit-ability bonus/penalty from slippage depth at 5%. A token can have
    # high `total_dex_liquidity_usd` but distribute it badly across thin pools,
    # so a fund-sized exit still moves the price. Reward depth that supports
    # mid-six-figure exits, penalize when even $50K hits the 5% slippage wall.
    slippage = metrics.get("slippage_depth") or {}
    depth_5pct = slippage.get("max_sell_usd_5pct") if isinstance(slippage, dict) else None
    if depth_5pct is not None:
        raw["max_sell_usd_5pct"] = depth_5pct
        if depth_5pct >= 1_000_000:
            score = _clamp(score + 5)
        elif depth_5pct >= 250_000:
            score = _clamp(score + 2)
        elif depth_5pct < 50_000:
            score = _clamp(score - 10)
        elif depth_5pct < 150_000:
            score = _clamp(score - 5)

    return round(_clamp(score), 1), raw


def _score_fundamental_support(
    identity, yields_result: dict | None, config: AppConfig
) -> tuple[float | None, dict]:
    """Score fundamental support by matching the token to a DeFiLlama protocol.

    Uses TVL tier, mcap/TVL efficiency, chain diversity, 1-month TVL trend, and
    — when available — protocol fees/revenue yield to market cap.
    Returns None in offline mode or when no protocol match exists.
    """
    raw: dict = {}
    if config.offline:
        return None, raw
    try:
        protocol = defillama.find_protocol(identity.symbol, identity.project_name, config)
    except ProviderError:
        return None, raw
    if not protocol:
        return None, raw

    tvl = _safe_float(protocol.get("tvl"))
    # Prefer mcap from the yields command which already applies the
    # DexScreener fallback for protocols that DeFiLlama doesn't track
    # (Morpho V1, some V2 splits, etc.). Falling back to the protocol's
    # native mcap keeps backwards compatibility when yields data is missing.
    ym = (yields_result or {}).get("metrics") or {}
    mcap = _safe_float(ym.get("mcap_usd")) or _safe_float(protocol.get("mcap"))
    mcap_source = ym.get("mcap_source") if mcap and ym.get("mcap_usd") else "defillama_protocol"
    change_1m = _safe_float(protocol.get("change_1m"))
    chains = protocol.get("chains") or []
    chains_count = len(chains) if isinstance(chains, list) else 0

    raw.update(
        {
            "protocol_slug": protocol.get("slug"),
            "protocol_name": protocol.get("name"),
            "tvl": tvl,
            "mcap": mcap,
            "mcap_source": mcap_source,
            "chains_count": chains_count,
            "change_1m": change_1m,
        }
    )

    if tvl is None:
        return None, raw

    score = 0.0
    # TVL tier (0–35)
    if tvl >= 1_000_000_000:
        score += 35
    elif tvl >= 100_000_000:
        score += 28
    elif tvl >= 10_000_000:
        score += 18
    elif tvl >= 1_000_000:
        score += 10
    else:
        score += 5

    # Market efficiency (0–25) — lower mcap/TVL is better-supported
    if mcap and tvl > 0:
        ratio = mcap / tvl
        raw["mcap_to_tvl"] = round(ratio, 3)
        if ratio < 2:
            score += 25
        elif ratio < 5:
            score += 18
        elif ratio < 10:
            score += 10

    # Chain diversity (0–10)
    if chains_count >= 3:
        score += 10
    elif chains_count >= 1:
        score += 4

    # 1-month TVL trend (−10 to +10)
    if change_1m is not None:
        if change_1m > 10:
            score += 10
        elif change_1m > 0:
            score += 6
        elif change_1m < -20:
            score -= 10

    # Protocol yield: annualized fees/mcap ratio (0–20). Protocols that
    # generate meaningful fee revenue relative to their market cap are
    # fundamentally better supported than pure TVL parks.
    if yields_result:
        ymetrics = yields_result.get("metrics") or {}
        fees_ratio = _safe_float(ymetrics.get("fees_to_mcap_ratio"))
        rev_ratio = _safe_float(ymetrics.get("revenue_to_mcap_ratio"))
        raw["fees_to_mcap_ratio"] = fees_ratio
        raw["revenue_to_mcap_ratio"] = rev_ratio
        if fees_ratio is not None:
            # fees/mcap of 0.5 means 50% of mcap in annualized fees — extremely
            # productive. 0.05 is already respectable.
            if fees_ratio >= 0.5:
                score += 20
            elif fees_ratio >= 0.2:
                score += 15
            elif fees_ratio >= 0.05:
                score += 10
            elif fees_ratio >= 0.01:
                score += 5

    return round(_clamp(score), 1), raw


def _score_token_capture(yields_result: dict | None) -> tuple[float | None, dict]:
    """Score how much protocol economics flow back to the token (VC accrual lens).

    Inputs from `yields`:
      - `revenue_model`: protocol_capture / fees_pass_through / unknown
      - `annualized_fees_usd` / `annualized_revenue_usd`
      - `fees_to_mcap_ratio` / `revenue_to_mcap_ratio`

    Two protocols can have identical TVL and fees but very different token
    accrual: one routes 100% of fees to LPs (zero token holder benefit),
    another buys back the token. `fundamental_support` already credits TVL
    + fees/mcap. This pillar isolates the *holder accrual* component so the
    distinction shows up in the composite, not just in the warning bus.
    """
    raw: dict = {}
    if not yields_result:
        return None, raw
    metrics = yields_result.get("metrics") or {}

    revenue_model = metrics.get("revenue_model")
    fees_to_mcap = _safe_float(metrics.get("fees_to_mcap_ratio"))
    rev_to_mcap = _safe_float(metrics.get("revenue_to_mcap_ratio"))
    fees_annual = _safe_float(metrics.get("annualized_fees_usd"))
    rev_annual = _safe_float(metrics.get("annualized_revenue_usd"))

    # Real-yield ratio: revenue / fees. 1.0 = all fees captured by the
    # protocol/token. 0 = pass-through. Falls back to None when no fees.
    real_yield_ratio: float | None = None
    if fees_annual and fees_annual > 0 and rev_annual is not None:
        real_yield_ratio = round(rev_annual / fees_annual, 4)
        raw["real_yield_ratio"] = real_yield_ratio

    raw["revenue_model"] = revenue_model
    raw["fees_to_mcap_ratio"] = fees_to_mcap
    raw["revenue_to_mcap_ratio"] = rev_to_mcap

    # Need at least one signal to score.
    if revenue_model in (None, "unknown") and fees_to_mcap is None and rev_to_mcap is None:
        return None, raw

    # Start at the model floor.
    if revenue_model == "protocol_capture":
        score = 60.0
    elif revenue_model == "fees_pass_through":
        score = 25.0  # the protocol generates fees but the token does not benefit
    else:
        score = 40.0  # neutral baseline (no clear signal)

    # Real-yield ratio bonus (only meaningful when revenue is captured).
    if real_yield_ratio is not None:
        if real_yield_ratio >= 0.5:
            score += 20  # protocol takes ≥50% of fees as revenue
        elif real_yield_ratio >= 0.2:
            score += 12
        elif real_yield_ratio >= 0.05:
            score += 5
        elif real_yield_ratio == 0:
            score -= 5  # confirmed zero capture, beyond the model floor

    # Revenue-yield-to-mcap is the strongest direct accrual signal.
    if rev_to_mcap is not None:
        if rev_to_mcap >= 0.20:
            score += 20  # 20%+ of mcap in annual revenue — exceptional accrual
        elif rev_to_mcap >= 0.05:
            score += 12
        elif rev_to_mcap >= 0.01:
            score += 5
    elif fees_to_mcap is not None and revenue_model == "fees_pass_through":
        # Acknowledge the protocol generates fees, even if no token capture.
        # Half-credit relative to the rev_to_mcap path.
        if fees_to_mcap >= 0.20:
            score += 8
        elif fees_to_mcap >= 0.05:
            score += 4

    return round(_clamp(score), 1), raw


def _score_governance_commitment(
    locks_result: dict,
    staking_result: dict,
    supply_result: dict,
    compare_result: dict | None = None,
) -> tuple[float | None, dict]:
    """Score governance commitment from on-chain custody signals.

    Rewards, in order of evidence quality:
      1. Fingerprinted OZ VestingWallet / TokenTimelock / ERC-4626 vaults
      2. Fingerprinted Synthetix-style staking contracts
      3. **Generic protocol-owned supply** — GnosisSafe treasuries, DAO
         wrappers, and bridge proxies that hold a meaningful share of supply
         but don't match a known pattern. Morpho's `Wrapper` (57%), ONDO's
         foundation Safes (60%), and Pendle's vePENDLE all fall here.

    Before this change the pillar only credited (1)+(2), which is why Morpho
    and ONDO both scored 25/35 despite 60%+ of supply sitting in multisigs.
    """
    raw: dict = {}
    lmetrics = locks_result.get("metrics") or {}
    smetrics = staking_result.get("metrics") or {}
    supmetrics = supply_result.get("metrics") or {}

    vesting_contracts = lmetrics.get("vesting_contracts") or []
    timelock_balances = lmetrics.get("timelock_balances") or []
    staking_contracts = smetrics.get("staking_contracts") or []
    locked_total = lmetrics.get("locked_onchain_total")
    staked_total = smetrics.get("staked_total")
    contracts_inspected = (lmetrics.get("contract_holders_inspected") or 0) + (
        smetrics.get("contract_holders_inspected") or 0
    )

    # Generic protocol-owned share from compare (the fix): non-fingerprinted
    # contract holders still count as on-chain custody, just with lower
    # confidence than strict ABI matches.
    protocol_owned_pct = None
    if compare_result:
        sup = (compare_result.get("metrics") or {}).get("supply") or {}
        protocol_owned_pct = sup.get("protocol_owned_pct_of_supply")
        if protocol_owned_pct is not None:
            raw["protocol_owned_pct_of_supply"] = protocol_owned_pct

    # If we had nothing to inspect AND compare has no protocol-owned signal,
    # we can't score this pillar.
    if (
        contracts_inspected == 0
        and not vesting_contracts
        and not staking_contracts
        and not protocol_owned_pct
    ):
        return None, raw

    raw.update(
        {
            "vesting_contracts_count": len(vesting_contracts),
            "timelock_balances_count": len(timelock_balances),
            "staking_contracts_count": len(staking_contracts),
            "has_vesting_schedule": any(
                v.get("start") is not None and v.get("duration") is not None for v in vesting_contracts
            ),
            "has_token_timelock": any(v.get("type") == "token_timelock" for v in vesting_contracts),
            "locked_onchain_total": locked_total,
            "staked_total": staked_total,
            "contracts_inspected": contracts_inspected,
        }
    )

    score = 35.0  # baseline — we were able to look, but nothing fingerprinted

    if vesting_contracts:
        score += 15
        if raw["has_vesting_schedule"]:
            score += 10
        if len(vesting_contracts) >= 3:
            score += 5
    if staking_contracts:
        score += 15
        if len(staking_contracts) >= 3:
            score += 5

    # Fingerprinted custody share of supply.
    total_raw = supmetrics.get("total_supply_raw")
    total_int = None
    if isinstance(total_raw, int):
        total_int = total_raw
    elif isinstance(total_raw, str):
        try:
            total_int = int(total_raw)
        except ValueError:
            total_int = None

    custody = 0
    if isinstance(locked_total, int):
        custody += locked_total
    if isinstance(staked_total, int):
        custody += staked_total
    if total_int and total_int > 0 and custody > 0:
        share = custody / total_int
        raw["onchain_custody_share"] = round(share, 4)
        if share >= 0.25:
            score += 15
        elif share >= 0.10:
            score += 10
        elif share >= 0.02:
            score += 5

    # NEW: generic protocol-owned supply credit (non-fingerprinted multisigs).
    # Weighted lower than fingerprinted vesting because the governance
    # structure is less verifiable — the multisig could distribute at any
    # time. But a protocol that holds 60% of supply in treasury multisigs
    # IS demonstrating on-chain governance commitment, just without an OZ
    # vesting schedule to prove it.
    if protocol_owned_pct is not None:
        if protocol_owned_pct >= 50:
            score += 15  # Morpho/ONDO class: foundation holds majority
        elif protocol_owned_pct >= 25:
            score += 10  # Pendle class: meaningful treasury
        elif protocol_owned_pct >= 10:
            score += 5
        # <10%: no credit (too thin to matter)

        # If protocol-owned is material but fingerprinted custody is zero,
        # we previously penalised "many contracts inspected, none
        # fingerprinted". That penalty is now wrong — the multisigs ARE
        # the custody, just not in OZ form. Skip the penalty in that case.
        raw["generic_custody_credit_applied"] = True
    else:
        # Old behaviour: penalty for "nothing structured found".
        if contracts_inspected >= 5 and not vesting_contracts and not staking_contracts:
            score -= 10

    return round(_clamp(score), 1), raw


def run(query: str, chain: str | None, address: str | None, config: AppConfig, include_premium: bool):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)

    # Collect data from dependent commands
    supply_result = supply.run(query, chain, address, config, include_premium).to_dict()
    holders_result = holders.run(query, chain, address, config, include_premium).to_dict()
    liquidity_result = liquidity.run(query, chain, address, config, include_premium).to_dict()
    locks_result = locks.run(query, chain, address, config, include_premium).to_dict()
    staking_result = staking.run(query, chain, address, config, include_premium).to_dict()
    yields_result = yields.run(query, chain, address, config, include_premium).to_dict()
    # Compare aggregates the above into the effective-circulation view. It's
    # expensive only because of its internal locks/staking/holders calls;
    # those already ran above so the marginal cost here is minor.
    compare_result = compare.run(query, chain, address, config, include_premium).to_dict()
    # Unlocks projects upcoming cliff/linear unlocks across 90/180/365d
    # horizons. The score uses the resulting %-of-supply numbers directly.
    unlocks_result = unlocks.run(query, chain, address, config, include_premium).to_dict()

    pillars: dict[str, object] = {}
    raw_inputs: dict[str, object] = {}
    scored_count = 0

    # Supply pressure (compare.supply + yields.revenue_model + unlocks.cliff_overhang)
    sp_score, sp_raw = _score_supply_pressure(supply_result, compare_result, yields_result, unlocks_result)
    pillars["supply_pressure"] = sp_score
    raw_inputs["supply_pressure"] = sp_raw
    if sp_score is not None:
        scored_count += 1
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.SUPPLY_PRESSURE, reason="Supply data unavailable for scoring.", suggested_source="supply command"))

    # Ownership quality (prefers EOA-adjusted top-10 from compare)
    oq_score, oq_raw = _score_ownership_quality(holders_result, compare_result)
    pillars["ownership_quality"] = oq_score
    raw_inputs["ownership_quality"] = oq_raw
    if oq_score is not None:
        scored_count += 1
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.OWNERSHIP_QUALITY, reason="Holder concentration data unavailable.", suggested_source="holders command"))

    # Liquidity quality
    lq_score, lq_raw = _score_liquidity_quality(liquidity_result)
    pillars["liquidity_quality"] = lq_score
    raw_inputs["liquidity_quality"] = lq_raw
    if lq_score is not None:
        scored_count += 1
    else:
        coverage_gaps.append(CoverageGap(metric=CoverageMetric.LIQUIDITY_QUALITY, reason="Liquidity data unavailable.", suggested_source="liquidity command"))

    # Fundamental support — DeFiLlama protocol match + protocol yield
    fs_score, fs_raw = _score_fundamental_support(identity, yields_result, config)
    pillars["fundamental_support"] = fs_score
    raw_inputs["fundamental_support"] = fs_raw
    if fs_score is not None:
        scored_count += 1
    else:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.FUNDAMENTAL_SUPPORT,
                reason="No DeFiLlama protocol match for this token, or offline mode.",
                suggested_source="DeFiLlama protocol directory; Token Terminal for non-DeFi tokens.",
            )
        )

    # Token capture — does the token actually accrue protocol economics?
    tc_score, tc_raw = _score_token_capture(yields_result)
    pillars["token_capture"] = tc_score
    raw_inputs["token_capture"] = tc_raw
    if tc_score is not None:
        scored_count += 1
    else:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.TOKEN_CAPTURE,
                reason="No fees/revenue data available to determine token accrual mechanism.",
                suggested_source="DeFiLlama protocol fees/revenue endpoints; Token Terminal.",
            )
        )

    # Governance commitment — derived from locks + staking + supply + compare
    gc_score, gc_raw = _score_governance_commitment(
        locks_result, staking_result, supply_result, compare_result
    )
    pillars["governance_commitment"] = gc_score
    raw_inputs["governance_commitment"] = gc_raw
    if gc_score is not None:
        scored_count += 1
    else:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.GOVERNANCE_COMMITMENT,
                reason="No on-chain custody data available (RPC/explorer missing or offline).",
                suggested_source="Configure RPC and a supported chain explorer.",
            )
        )

    # Composite score: weighted average of available pillars
    composite = None
    if scored_count > 0:
        weighted_sum = 0.0
        weight_sum = 0.0
        for key, weight in PILLAR_WEIGHTS.items():
            value = pillars.get(key)
            if value is not None:
                weighted_sum += float(value) * weight
                weight_sum += weight
        if weight_sum > 0:
            composite = round(weighted_sum / weight_sum, 1)

    # Coverage penalty: reduce composite when data is incomplete
    coverage_ratio = scored_count / len(PILLAR_WEIGHTS)
    if composite is not None and coverage_ratio < 1.0:
        penalty = (1.0 - coverage_ratio) * 15.0
        composite = round(_clamp(composite - penalty), 1)

    metrics: dict[str, object] = {
        "pillars": pillars,
        "raw_inputs": raw_inputs,
        "composite_score": composite,
        "coverage_ratio": round(coverage_ratio, 2),
        "pillar_weights": PILLAR_WEIGHTS,
    }

    return CommandResult(
        command="score",
        input={"query": query, "chain": chain, "address": address, "include_premium": include_premium},
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
