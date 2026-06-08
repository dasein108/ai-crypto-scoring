from __future__ import annotations


from token_research.cache import persist_report
from token_research.config import AppConfig
from token_research.format_utils import format_usd as _fmt_usd
from token_research.models import DeepReportResult
from token_research.providers.registry import build_source_refs
from token_research.commands import (
    compare,
    fairlaunch,
    flows,
    holders,
    identity,
    labels,
    pools,
    price,
    liquidity,
    locks,
    relations,
    resolve,
    risk,
    score,
    staking,
    supply,
    unlocks,
    yields,
)


def _score_band(score_value: float | None) -> str:
    if score_value is None:
        return "unscored"
    if score_value >= 75:
        return "strong"
    if score_value >= 55:
        return "solid"
    if score_value >= 35:
        return "mixed"
    return "weak"


def _build_narrative(modules: dict[str, dict], all_gaps: list[dict]) -> list[str]:
    """Generate an analytical summary grounded in the collected module metrics.

    Each line is a self-contained claim that a reviewer can verify against the
    corresponding module output. Lines are intentionally short so the Markdown
    renderer can drop them into a bullet list without reformatting.
    """
    lines: list[str] = []
    resolve_mod = modules.get("resolve", {}) or {}
    identity_dict = resolve_mod.get("resolved_identity") or {}
    symbol = identity_dict.get("symbol") or "token"
    chain = identity_dict.get("chain") or "?"
    token_addr = identity_dict.get("token_address")

    lines.append(
        f"{symbol} on {chain}: {'resolved to ' + token_addr if token_addr else 'no on-chain address resolved'}."
    )

    score_mod = modules.get("score", {}) or {}
    score_metrics = score_mod.get("metrics", {}) or {}
    composite = score_metrics.get("composite_score")
    coverage_ratio = score_metrics.get("coverage_ratio")
    pillars = score_metrics.get("pillars") or {}
    band = _score_band(composite)
    if composite is not None:
        scored = [k for k, v in pillars.items() if v is not None]
        lines.append(
            f"Composite {composite}/100 ({band}); coverage {coverage_ratio} across pillars "
            f"[{', '.join(scored)}]."
        )
    else:
        lines.append("Composite score unavailable — insufficient pillar coverage.")

    # Liquidity one-liner
    liq_metrics = (modules.get("liquidity") or {}).get("metrics") or {}
    total_liq = liq_metrics.get("total_dex_liquidity_usd")
    lp_locked = liq_metrics.get("lp_locked_percent")
    if total_liq is not None:
        liq_line = f"DEX liquidity {_fmt_usd(total_liq)}"
        if lp_locked is not None:
            liq_line += f", LP {lp_locked}% locked"
        venues = liq_metrics.get("liquidity_by_dex") or {}
        if venues:
            liq_line += f" across {len(venues)} venue(s)"
        lines.append(liq_line + ".")

    # Holder concentration
    holders_metrics = (modules.get("holders") or {}).get("metrics") or {}
    concentration = holders_metrics.get("concentration") or {}
    top10 = concentration.get("top10_share")
    nakamoto = concentration.get("nakamoto_51")
    if top10 is not None:
        line = f"Top-10 holders control {top10 * 100:.1f}% of supply"
        if nakamoto is not None:
            line += f"; Nakamoto-51 = {nakamoto}"
        lines.append(line + ".")

    # On-chain custody (locks + staking)
    locks_metrics = (modules.get("locks") or {}).get("metrics") or {}
    staking_metrics = (modules.get("staking") or {}).get("metrics") or {}
    vesting_count = len(locks_metrics.get("vesting_contracts") or [])
    staking_count = len(staking_metrics.get("staking_contracts") or [])
    if vesting_count or staking_count:
        lines.append(
            f"On-chain custody: {vesting_count} vesting/timelock contract(s), "
            f"{staking_count} staking contract(s) fingerprinted among top holders."
        )

    # Unlock posture
    unlocks_metrics = (modules.get("unlocks") or {}).get("metrics") or {}
    upcoming = unlocks_metrics.get("upcoming_onchain_events") or []
    curated = unlocks_metrics.get("curated_schedule") or []
    if upcoming or curated:
        lines.append(
            f"Unlock posture: {len(curated)} curated event(s), {len(upcoming)} projected on-chain event(s) in the next 90 days."
        )

    # Flow posture
    flows_metrics = (modules.get("flows") or {}).get("metrics") or {}
    netflows = flows_metrics.get("netflows") or []
    if netflows:
        categories = sorted(
            ((row.get("category"), float(row.get("net_raw") or 0)) for row in netflows if isinstance(row, dict)),
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        top = ", ".join(f"{cat}:{'+' if net > 0 else ''}{net:.0f}" for cat, net in categories[:3] if cat and cat != "unknown")
        if top:
            lines.append(f"Recent net flows (last 7d): {top}.")

    # Risk / reward (CPD 2.0)
    risk_metrics = (modules.get("risk") or {}).get("metrics") or {}
    cpd = risk_metrics.get("cpd") or {}
    rk = risk_metrics.get("risk") or {}
    rv = risk_metrics.get("revenue_vs_risk") or {}
    if cpd.get("tier") and rk.get("final_risk"):
        ven = rv.get("verdict_weighted") or "n/a"
        parts = [
            f"CPD {cpd['tier']} ({cpd.get('total_score')}/{cpd.get('max_score')})",
            f"risk {rk['final_risk']}/10",
        ]
        if rv.get("weighted_avg_apy_percent") is not None:
            parts.append(f"APY {rv['weighted_avg_apy_percent']}% → risk-adj {rv.get('conservative_adjusted', {}).get('weighted')}%")
        parts.append(f"verdict: {ven}")
        lines.append("Revenue vs risk: " + " | ".join(parts) + ".")

    # Cross-metric ratios
    compare_metrics = (modules.get("compare") or {}).get("metrics") or {}
    cbits: list[str] = []
    cs = compare_metrics.get("supply") or {}
    cf = compare_metrics.get("fees") or {}
    cm = compare_metrics.get("market") or {}
    if cs.get("custody_pct_of_supply") is not None:
        cbits.append(f"custody {cs['custody_pct_of_supply']}%")
    if cs.get("float_pct_of_supply") is not None:
        cbits.append(f"float {cs['float_pct_of_supply']}%")
    if cm.get("liquidity_to_mcap") is not None:
        cbits.append(f"liq/mcap {cm['liquidity_to_mcap']:.3f}")
    if cf.get("fees_to_tvl") is not None:
        cbits.append(f"fees/tvl {cf['fees_to_tvl']:.4f}")
    if cf.get("implied_staker_apy_if_fees_distributed") is not None:
        cbits.append(f"implied staker APY {cf['implied_staker_apy_if_fees_distributed']}%")
    if cbits:
        lines.append("Cross-metric ratios: " + "; ".join(cbits) + ".")

    # Protocol yield
    yields_metrics = (modules.get("yields") or {}).get("metrics") or {}
    fees_ratio = yields_metrics.get("fees_to_mcap_ratio")
    best_apy = yields_metrics.get("best_apy")
    pool_count = yields_metrics.get("yield_pool_count") or 0
    if fees_ratio is not None or pool_count:
        bits = []
        if fees_ratio is not None:
            bits.append(f"fees/mcap {fees_ratio * 100:.1f}%")
        ann_fees = yields_metrics.get("annualized_fees_usd")
        if ann_fees:
            bits.append(f"ann. fees ~${ann_fees / 1_000_000:.1f}M")
        if pool_count:
            bits.append(f"{pool_count} yield pools")
        if best_apy is not None:
            bits.append(f"best APY {best_apy:.2f}%")
        if bits:
            lines.append("Protocol yield: " + "; ".join(bits) + ".")

    # Coverage snapshot
    if all_gaps:
        by_metric: dict[str, int] = {}
        for gap in all_gaps:
            if isinstance(gap, dict):
                metric = str(gap.get("metric") or "")
                by_metric[metric] = by_metric.get(metric, 0) + 1
        top_gaps = sorted(by_metric.items(), key=lambda item: item[1], reverse=True)[:4]
        lines.append(
            "Open coverage gaps: " + ", ".join(f"{m}({c})" for m, c in top_gaps) + "."
        )

    return lines


def _build_evidence_summary(modules: dict[str, dict]) -> list[str]:
    """One bullet per source with what it contributed."""
    summary: list[str] = []
    mappings = [
        ("resolve", "DexScreener", "token identity + chain classification"),
        ("identity", "Blockscout", "contract metadata"),
        ("price", "DexScreener + DeFiLlama", "spot price"),
        ("pools", "DexScreener", "pool discovery"),
        ("supply", "RPC + Blockscout", "total supply and decimals"),
        ("holders", "Blockscout", "top-N holder snapshot + concentration metrics"),
        ("flows", "Dune", "categorized netflows"),
        ("labels", "Blockscout + Dune", "address tags and public labels"),
        ("liquidity", "DexScreener + Blockscout", "DEX liquidity and LP lock analysis"),
        ("locks", "RPC (OZ VestingWallet + TokenTimelock)", "vesting contract fingerprinting"),
        ("staking", "RPC (ERC-4626 + StakingRewards)", "staking contract fingerprinting"),
        ("unlocks", "Tokenomist + on-chain projection", "unlock schedule + flow validation"),
        ("relations", "holders + labels + locks + staking + flows", "entity graph with evidence"),
        ("yields", "DeFiLlama fees + revenue + yields.llama.fi", "protocol fee/revenue and APY pools"),
        ("compare", "derived from supply/holders/liquidity/locks/staking/yields", "cross-metric ratios (custody%, float%, fees/TVL, implied staker APY)"),
        ("risk", "CPD 2.0 model", "quality tier + final risk 1..10 + revenue-vs-risk view"),
        ("score", "5-pillar composite", "weighted score with coverage penalty"),
    ]
    for module_name, source, purpose in mappings:
        mod = modules.get(module_name) or {}
        metrics = mod.get("metrics") or {}
        has_data = any(
            value not in (None, [], {}, 0)
            for value in metrics.values()
            if not isinstance(value, str)
        )
        status = "ok" if has_data else "empty"
        summary.append(f"{module_name} [{status}] — {source}: {purpose}")
    return summary


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    persist: bool,
):
    resolve_module = resolve.run(query, chain, address, config, include_premium).to_dict()
    modules = {
        "resolve": resolve_module,
        "identity": identity.run(query, chain, address, config, include_premium).to_dict(),
        "price": price.run(query, chain, address, config, include_premium).to_dict(),
        "pools": pools.run(query, chain, address, config, include_premium).to_dict(),
        "supply": supply.run(query, chain, address, config, include_premium).to_dict(),
        "holders": holders.run(query, chain, address, config, include_premium).to_dict(),
        "flows": flows.run(query, chain, address, config, include_premium).to_dict(),
        "labels": labels.run(query, chain, address, config, include_premium).to_dict(),
        "locks": locks.run(query, chain, address, config, include_premium).to_dict(),
        "staking": staking.run(query, chain, address, config, include_premium).to_dict(),
        "liquidity": liquidity.run(query, chain, address, config, include_premium).to_dict(),
        "unlocks": unlocks.run(query, chain, address, config, include_premium).to_dict(),
        "relations": relations.run(query, chain, address, config, include_premium).to_dict(),
        "yields": yields.run(query, chain, address, config, include_premium).to_dict(),
        "compare": compare.run(query, chain, address, config, include_premium).to_dict(),
        "risk": risk.run(query, chain, address, config, include_premium).to_dict(),
        "fairlaunch": fairlaunch.run(query, chain, address, config, include_premium).to_dict(),
        "score": score.run(query, chain, address, config, include_premium).to_dict(),
    }

    # Aggregate gaps across modules and dedupe (same metric+reason often
    # appears from every module via resolve_with_sources).
    seen_gap_keys: set[tuple[str, str]] = set()
    all_gaps: list[dict] = []
    for module in modules.values():
        for gap in module.get("coverage_gaps", []) or []:
            if not isinstance(gap, dict):
                continue
            key = (str(gap.get("metric", "")), str(gap.get("reason", "")))
            if key in seen_gap_keys:
                continue
            seen_gap_keys.add(key)
            all_gaps.append(gap)
    score_metrics = (modules.get("score") or {}).get("metrics") or {}
    composite = score_metrics.get("composite_score")
    score_status = "computed" if composite is not None else "insufficient_coverage"

    result = DeepReportResult(
        command="deep-report",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
            "persist": persist,
        },
        resolved_identity=resolve_module["resolved_identity"],
        metrics={"modules": modules},
        sources=build_source_refs(config, include_premium),
        warnings=resolve_module.get("warnings", []),
        coverage_gaps=all_gaps,
        scores={
            "status": score_status,
            "pillars": score_metrics.get("pillars") or {},
            "composite_score": composite,
            "coverage_ratio": score_metrics.get("coverage_ratio"),
        },
        narrative=_build_narrative(modules, all_gaps),
        evidence_summary=_build_evidence_summary(modules),
    )

    payload = result.to_dict()
    if persist:
        resolved = resolve_module["resolved_identity"]
        slug = f"{resolved['chain']}-{str(resolved['symbol']).lower()}-deep-report"
        path = persist_report(config, slug=slug, payload=payload)
        payload["saved_to"] = str(path)
    return payload
