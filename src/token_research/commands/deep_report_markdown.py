"""Markdown renderer for deep-report JSON payloads.

Sole consumer is ``commands.deep_report``. Kept as its own module because
the renderer is ~700 lines of section helpers that would otherwise bloat
``deep_report.py`` (which is already the aggregator).
"""

from __future__ import annotations

from typing import Any

from token_research.format_utils import format_number as _fmt_num



def _h(level: int, text: str) -> str:
    return f"{'#' * level} {text}"


def _bullet(text: str) -> str:
    return f"- {text}"


def _metrics(module: dict | None) -> dict:
    """Safe accessor for ``module["metrics"]`` returning an empty dict on None."""
    if not module:
        return {}
    return module.get("metrics") or {}


def deep_report_to_markdown(payload: dict[str, Any]) -> str:
    """Render a deep-report payload as a human-readable Markdown dossier.

    Accepts the full DeepReportResult dict. Sections are ordered for a
    top-down reading: identity → verdict → metrics → coverage → evidence.
    """
    lines: list[str] = []

    ident = payload.get("resolved_identity") or {}
    symbol = ident.get("symbol") or "?"
    project = ident.get("project_name") or symbol
    chain = ident.get("chain") or "?"
    token_addr = ident.get("token_address") or "—"

    lines.append(_h(1, f"{project} ({symbol}) — token research dossier"))
    lines.append("")
    lines.append(f"*Generated: {payload.get('generated_at', '')}*")
    lines.append("")

    # Identity table
    lines.append(_h(2, "Identity"))
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Symbol | `{symbol}` |")
    lines.append(f"| Project | {project} |")
    lines.append(f"| Chain | `{chain}` |")
    lines.append(f"| Address | `{token_addr}` |")
    lines.append(f"| Query type | {ident.get('query_type', '—')} |")
    lines.append("")

    # Verdict / composite score
    scores = payload.get("scores") or {}
    pillars = scores.get("pillars") or {}
    composite = scores.get("composite_score")
    coverage_ratio = scores.get("coverage_ratio")
    lines.append(_h(2, "Verdict"))
    lines.append("")
    lines.append(f"**Composite score:** {composite if composite is not None else 'n/a'}/100")
    if coverage_ratio is not None:
        lines.append(f"**Pillar coverage:** {coverage_ratio}")
    lines.append("")
    lines.append("| Pillar | Score |")
    lines.append("| --- | --- |")
    for name, value in pillars.items():
        lines.append(f"| {name} | {value if value is not None else '—'} |")
    lines.append("")

    # Narrative
    narrative = payload.get("narrative") or []
    if narrative:
        lines.append(_h(2, "Narrative"))
        lines.append("")
        for line in narrative:
            lines.append(_bullet(line))
        lines.append("")

    # Module highlights — buffer to a sub-list so we can skip the heading
    # entirely when all module renderers short-circuit (e.g. offline runs).
    modules = (payload.get("metrics") or {}).get("modules") or {}
    metrics_block: list[str] = []
    _render_price(metrics_block, modules.get("price"))
    _render_supply(metrics_block, modules.get("supply"))
    _render_liquidity(metrics_block, modules.get("liquidity"))
    _render_holders(metrics_block, modules.get("holders"))
    _render_locks(metrics_block, modules.get("locks"))
    _render_staking(metrics_block, modules.get("staking"))
    _render_unlocks(metrics_block, modules.get("unlocks"))
    _render_flows(metrics_block, modules.get("flows"))
    _render_yields(metrics_block, modules.get("yields"))
    _render_risk(metrics_block, modules.get("risk"))
    _render_compare(metrics_block, modules.get("compare"))
    _render_relations(metrics_block, modules.get("relations"))
    if metrics_block:
        lines.append(_h(2, "Key metrics"))
        lines.append("")
        lines.extend(metrics_block)

    # Warnings
    warnings = payload.get("warnings") or []
    if warnings:
        lines.append(_h(2, "Warnings"))
        lines.append("")
        for w in warnings:
            if isinstance(w, dict):
                lines.append(_bullet(f"**{w.get('severity', 'info')}** `{w.get('code', '')}` — {w.get('message', '')}"))
        lines.append("")

    # Coverage gaps
    gaps = payload.get("coverage_gaps") or []
    if gaps:
        lines.append(_h(2, "Coverage gaps"))
        lines.append("")
        lines.append("| Metric | Reason | Suggested source |")
        lines.append("| --- | --- | --- |")
        for g in gaps:
            if isinstance(g, dict):
                lines.append(
                    f"| `{g.get('metric', '')}` | {g.get('reason', '')} | {g.get('suggested_source', '')} |"
                )
        lines.append("")

    # Evidence summary
    evidence = payload.get("evidence_summary") or []
    if evidence:
        lines.append(_h(2, "Evidence summary"))
        lines.append("")
        for line in evidence:
            lines.append(_bullet(line))
        lines.append("")

    # Sources
    sources = payload.get("sources") or []
    if sources:
        lines.append(_h(2, "Sources"))
        lines.append("")
        lines.append("| Name | Access | Enabled | Notes |")
        lines.append("| --- | --- | --- | --- |")
        for s in sources:
            if isinstance(s, dict):
                enabled = "yes" if s.get("enabled") else "no"
                lines.append(
                    f"| {s.get('name', '')} | {s.get('access', '')} | {enabled} | {s.get('notes', '')} |"
                )
        lines.append("")

    saved = payload.get("saved_to")
    if saved:
        lines.append(f"*Report JSON persisted to `{saved}`.*")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_price(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    price = metrics.get("price_usd")
    if price is None:
        return
    lines.append(_h(3, "Price"))
    lines.append("")
    lines.append(_bullet(f"USD: ${_fmt_num(price, 6)}"))
    source = metrics.get("source")
    if source:
        lines.append(_bullet(f"Source: {source}"))
    lines.append("")


def _render_supply(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    total = metrics.get("total_supply_adjusted")
    if total is None:
        return
    lines.append(_h(3, "Supply"))
    lines.append("")
    lines.append(_bullet(f"Total supply: {_fmt_num(total)} (source: {metrics.get('total_supply_source', '—')})"))
    if metrics.get("decimals") is not None:
        lines.append(_bullet(f"Decimals: {metrics['decimals']}"))
    if metrics.get("holders_count") is not None:
        lines.append(_bullet(f"Holders count (vendor): {metrics['holders_count']}"))
    lines.append("")


def _render_liquidity(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    total = metrics.get("total_dex_liquidity_usd")
    if total is None:
        return
    lines.append(_h(3, "Liquidity"))
    lines.append("")
    lines.append(_bullet(f"Total DEX liquidity: ${_fmt_num(total)}"))
    by_dex = metrics.get("liquidity_by_dex") or {}
    if by_dex:
        top = list(by_dex.items())[:5]
        lines.append(_bullet("Top venues: " + ", ".join(f"{k} (${_fmt_num(v)})" for k, v in top)))
    lp_locked = metrics.get("lp_locked_percent")
    if lp_locked is not None:
        lines.append(_bullet(f"LP locked: {lp_locked}% / unlocked: {metrics.get('lp_unlocked_percent')}%"))
    lines.append("")


def _render_holders(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    concentration = metrics.get("concentration") or {}
    top10 = concentration.get("top10_share")
    if top10 is None:
        return
    lines.append(_h(3, "Holders"))
    lines.append("")
    lines.append(_bullet(f"Top-10 share: {top10 * 100:.2f}%"))
    if concentration.get("top20_share") is not None:
        lines.append(_bullet(f"Top-20 share: {concentration['top20_share'] * 100:.2f}%"))
    if concentration.get("nakamoto_51") is not None:
        lines.append(_bullet(f"Nakamoto-51: {concentration['nakamoto_51']}"))
    if concentration.get("top_holders_gini") is not None:
        lines.append(_bullet(f"Top-holders Gini: {concentration['top_holders_gini']}"))
    lines.append("")


def _render_locks(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    vesting = metrics.get("vesting_contracts") or []
    timelocks = metrics.get("timelock_balances") or []
    inspected = metrics.get("contract_holders_inspected") or 0
    if not (vesting or timelocks or inspected):
        return
    lines.append(_h(3, "Locks & vesting"))
    lines.append("")
    lines.append(_bullet(f"Contract holders inspected: {inspected}"))
    lines.append(_bullet(f"Vesting contracts: {len(vesting)}"))
    lines.append(_bullet(f"Other contract balances: {len(timelocks)}"))
    for v in vesting[:5]:
        if isinstance(v, dict):
            lines.append(
                _bullet(
                    f"`{v.get('contract', '')}` — {v.get('type', 'vesting')} (confidence: {v.get('confidence', 'high')})"
                )
            )
    lines.append("")


def _render_staking(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    contracts = metrics.get("staking_contracts") or []
    if not contracts:
        return
    lines.append(_h(3, "Staking"))
    lines.append("")
    lines.append(_bullet(f"Staking contracts fingerprinted: {len(contracts)}"))
    for c in contracts[:5]:
        if isinstance(c, dict):
            lines.append(
                _bullet(
                    f"`{c.get('contract', '')}` — {c.get('type', 'staking')} (confidence: {c.get('confidence', 'high')})"
                )
            )
    lines.append("")


def _render_unlocks(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    curated = metrics.get("curated_schedule") or []
    onchain = metrics.get("onchain_schedule") or []
    upcoming = metrics.get("upcoming_onchain_events") or []
    if not (curated or onchain or upcoming):
        return
    lines.append(_h(3, "Unlocks"))
    lines.append("")
    lines.append(_bullet(f"Curated events: {len(curated)}"))
    lines.append(_bullet(f"On-chain schedules projected: {len(onchain)}"))
    lines.append(_bullet(f"Projected events in next 90d: {len(upcoming)}"))
    validation = metrics.get("validation_windows") or []
    if validation:
        lines.append(_bullet(f"Validation windows: {len(validation)}"))
    lines.append("")


def _render_flows(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    netflows = metrics.get("netflows") or []
    if not netflows:
        return
    lines.append(_h(3, "Flows (7d)"))
    lines.append("")
    lines.append("| Category | Net | Tx count |")
    lines.append("| --- | --- | --- |")
    for row in netflows:
        if isinstance(row, dict):
            lines.append(
                f"| {row.get('category', 'unknown')} | {_fmt_num(row.get('net_raw'))} | {row.get('tx_count', '—')} |"
            )
    lines.append("")


def _render_yields(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    protocol_slug = metrics.get("protocol_slug")
    fees = metrics.get("protocol_fees") or {}
    revenue = metrics.get("protocol_revenue") or {}
    pools = metrics.get("yield_pools") or []
    if not protocol_slug and not pools:
        return
    lines.append(_h(3, "Protocol yield"))
    lines.append("")
    if protocol_slug:
        name = metrics.get("protocol_name") or protocol_slug
        category = metrics.get("protocol_category")
        cat_str = f" ({category})" if category else ""
        lines.append(_bullet(f"DeFiLlama protocol: **{name}**{cat_str} — slug `{protocol_slug}`"))
    age_band = metrics.get("protocol_age_band")
    if age_band:
        lines.append(_bullet(f"Protocol age band (from DeFiLlama listedAt): **{age_band}**"))

    mcap = metrics.get("mcap_usd")
    mcap_source = metrics.get("mcap_source") or "unknown"
    if mcap is not None:
        lines.append(_bullet(f"Market cap: ${_fmt_num(mcap)} (source: `{mcap_source}`)"))

    ann_fees = metrics.get("annualized_fees_usd")
    ann_rev = metrics.get("annualized_revenue_usd")
    fees_ratio = metrics.get("fees_to_mcap_ratio")
    rev_ratio = metrics.get("revenue_to_mcap_ratio")
    rev_model = metrics.get("revenue_model") or "unknown"

    if fees.get("total24h") is not None or fees.get("total30d") is not None:
        lines.append(
            _bullet(
                f"Fees: 24h ${_fmt_num(fees.get('total24h'))} • "
                f"7d ${_fmt_num(fees.get('total7d'))} • "
                f"30d ${_fmt_num(fees.get('total30d'))}"
            )
        )
    if ann_fees is not None:
        extra = f" (fees/mcap {fees_ratio * 100:.1f}%)" if fees_ratio is not None else ""
        lines.append(_bullet(f"Annualized fees: ~${_fmt_num(ann_fees)}{extra}"))
    if revenue.get("total24h") is not None or revenue.get("total30d") is not None:
        lines.append(
            _bullet(
                f"Revenue: 24h ${_fmt_num(revenue.get('total24h'))} • "
                f"7d ${_fmt_num(revenue.get('total7d'))} • "
                f"30d ${_fmt_num(revenue.get('total30d'))}"
            )
        )
    if ann_rev is not None:
        extra = f" (revenue/mcap {rev_ratio * 100:.1f}%)" if rev_ratio is not None else ""
        lines.append(_bullet(f"Annualized revenue: ~${_fmt_num(ann_rev)}{extra}"))
    lines.append(_bullet(f"Revenue model: **`{rev_model}`**"))

    if pools:
        best = metrics.get("best_apy")
        wavg = metrics.get("weighted_avg_apy")
        pool_tvl = metrics.get("total_yield_tvl_usd")
        lines.append(
            _bullet(
                f"All yield pools: {metrics.get('yield_pool_count')} — "
                f"raw best APY {best}% • TVL-weighted avg {wavg}% • total pool TVL ${_fmt_num(pool_tvl)}"
            )
        )
        best_mf = metrics.get("best_apy_meaningful")
        wavg_mf = metrics.get("weighted_avg_apy_meaningful")
        mf_count = metrics.get("meaningful_pool_count") or 0
        mf_tvl = metrics.get("meaningful_yield_tvl_usd")
        if mf_count:
            lines.append(
                _bullet(
                    f"**Meaningful pools** (TVL ≥ $10M, APY > 0): {mf_count} — "
                    f"best APY {best_mf}% • TVL-weighted avg **{wavg_mf}%** • total TVL ${_fmt_num(mf_tvl)}"
                )
            )
        p95 = metrics.get("apy_p95")
        if p95 is not None:
            lines.append(_bullet(f"APY p95 (robust max): {p95}%"))
        lines.append("")
        lines.append("| Pool | Chain | Symbol | APY | TVL |")
        lines.append("| --- | --- | --- | --- | --- |")
        for pool in pools[:8]:
            lines.append(
                f"| {pool.get('project','—')} | {pool.get('chain','—')} | {pool.get('symbol','—')} | "
                f"{pool.get('apy','—')}% | ${_fmt_num(pool.get('tvl_usd'))} |"
            )
    lines.append("")


def _render_risk(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    cpd = metrics.get("cpd") or {}
    risk = metrics.get("risk") or {}
    rv = metrics.get("revenue_vs_risk") or {}
    if not cpd and not risk:
        return

    lines.append(_h(3, "Revenue vs risk (CPD 2.0)"))
    lines.append("")

    # CPD
    if cpd:
        lines.append(
            _bullet(
                f"**CPD score:** {cpd.get('total_score','—')}/{cpd.get('max_score','—')} "
                f"({(cpd.get('percent_of_max') or 0) * 100:.1f}%) → **{cpd.get('tier','—')}** "
                f"(risk input {cpd.get('risk_input','—')})"
            )
        )
        sub = cpd.get("sub_scores") or {}
        if sub:
            lines.append("")
            lines.append("| CPD dimension | Score |")
            lines.append("| --- | --- |")
            for k in ("chain", "protocol", "dapp", "stage", "age", "code", "hacks"):
                if k in sub:
                    lines.append(f"| {k} | {sub[k]:+d} |")
            lines.append("")

    # Risk formula
    if risk:
        lines.append(
            _bullet(
                f"**Final risk:** {risk.get('final_risk','—')}/10 "
                f"(base {risk.get('base_risk','—')} × (1 + {risk.get('defi_multiplier','—')}))"
            )
        )
        lines.append(
            _bullet(
                f"Trade size {(risk.get('trade_size_pct') or 0) * 100:.0f}% • "
                f"nesting {risk.get('nesting_depth','—')} • "
                f"primitives: {risk.get('primitive_a_name','—')} × {risk.get('primitive_b_name','—')}"
            )
        )

    # Revenue vs risk
    if rv:
        lines.append("")
        lines.append("**Risk-adjusted yield**")
        lines.append("")
        lines.append("| View | APY | Verdict |")
        lines.append("| --- | --- | --- |")
        w_apy = rv.get("weighted_avg_apy_percent")
        b_apy = rv.get("best_apy_percent")
        w_linear = (rv.get("linear_adjusted") or {}).get("weighted")
        b_linear = (rv.get("linear_adjusted") or {}).get("best")
        w_cons = (rv.get("conservative_adjusted") or {}).get("weighted")
        b_cons = (rv.get("conservative_adjusted") or {}).get("best")
        if w_apy is not None:
            lines.append(f"| Observed (weighted) | {w_apy}% | — |")
            lines.append(f"| Linear-adjusted (weighted) | {w_linear}% | {rv.get('verdict_weighted','—')} |")
            lines.append(f"| Conservative (weighted) | {w_cons}% | — |")
        if b_apy is not None:
            lines.append(f"| Observed (best pool) | {b_apy}% | — |")
            lines.append(f"| Linear-adjusted (best) | {b_linear}% | {rv.get('verdict_best','—')} |")
            lines.append(f"| Conservative (best) | {b_cons}% | — |")

    lines.append("")


def _render_compare(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    supply = metrics.get("supply") or {}
    market = metrics.get("market") or {}
    fees = metrics.get("fees") or {}
    custody = metrics.get("custody") or {}
    yield_ = metrics.get("yield") or {}
    notes = metrics.get("notes") or []

    # Skip section entirely when there's no meaningful data.
    if not any(v is not None for v in list(supply.values()) + list(market.values()) + list(fees.values())):
        return

    lines.append(_h(3, "Cross-metric comparison"))
    lines.append("")

    # Supply composition table
    sup_rows: list[tuple[str, str]] = []
    if supply.get("total_supply_tokens") is not None:
        sup_rows.append(("Total supply (tokens)", _fmt_num(supply["total_supply_tokens"])))
    if supply.get("total_supply_usd") is not None:
        sup_rows.append(("Total supply (USD)", f"${_fmt_num(supply['total_supply_usd'])}"))
    if supply.get("locked_onchain_tokens") is not None:
        sup_rows.append(
            (
                "Locked on-chain",
                f"{_fmt_num(supply['locked_onchain_tokens'])} tokens "
                f"({supply.get('locked_pct_of_supply','—')}%)",
            )
        )
    if supply.get("staked_onchain_tokens") is not None:
        sup_rows.append(
            (
                "Staked on-chain",
                f"{_fmt_num(supply['staked_onchain_tokens'])} tokens "
                f"({supply.get('staked_pct_of_supply','—')}%)",
            )
        )
    if supply.get("custody_tokens") is not None:
        sup_rows.append(
            (
                "Fingerprinted custody (vest+stake)",
                f"{_fmt_num(supply['custody_tokens'])} tokens "
                f"({supply.get('custody_pct_of_supply','—')}%)",
            )
        )
    if supply.get("protocol_owned_tokens") is not None:
        sup_rows.append(
            (
                "**Protocol-owned total** (incl. wrappers, safes, non-FP contracts)",
                f"**{_fmt_num(supply['protocol_owned_tokens'])} tokens "
                f"({supply.get('protocol_owned_pct_of_supply','—')}%)**",
            )
        )
    if supply.get("float_tokens_est") is not None:
        sup_rows.append(
            (
                "Float (excl. fingerprinted custody)",
                f"{_fmt_num(supply['float_tokens_est'])} tokens "
                f"({supply.get('float_pct_of_supply','—')}%)",
            )
        )
    if supply.get("effective_circulating_tokens") is not None:
        sup_rows.append(
            (
                "**Effective circulating** (excl. all protocol-owned)",
                f"**{_fmt_num(supply['effective_circulating_tokens'])} tokens "
                f"({supply.get('effective_circulating_pct_of_supply','—')}%)**",
            )
        )
    if supply.get("top10_pct_of_supply") is not None:
        sup_rows.append(("Top-10 holder share (raw)", f"{supply['top10_pct_of_supply']}%"))
    if supply.get("effective_top10_pct_of_supply") is not None:
        sup_rows.append(
            (
                "**Effective top-10 share** (EOAs + non-protocol contracts only)",
                f"**{supply['effective_top10_pct_of_supply']}%**",
            )
        )

    if sup_rows:
        lines.append("**Supply composition**")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for k, v in sup_rows:
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # Market / liquidity ratios
    mkt_rows: list[tuple[str, str]] = []
    if market.get("mcap_usd") is not None:
        mkt_rows.append(("Market cap", f"${_fmt_num(market['mcap_usd'])}"))
    if market.get("tvl_usd") is not None:
        mkt_rows.append(("Protocol TVL", f"${_fmt_num(market['tvl_usd'])}"))
    if market.get("dex_liquidity_usd") is not None:
        mkt_rows.append(("DEX liquidity", f"${_fmt_num(market['dex_liquidity_usd'])}"))
    if market.get("lp_locked_percent") is not None:
        mkt_rows.append(("LP locked %", f"{market['lp_locked_percent']}%"))
    if market.get("yield_pool_tvl_usd") is not None:
        mkt_rows.append(("Yield pool TVL", f"${_fmt_num(market['yield_pool_tvl_usd'])}"))
    if market.get("mcap_to_tvl") is not None:
        mkt_rows.append(("mcap / TVL", f"{market['mcap_to_tvl']:.4f}"))
    if market.get("liquidity_to_mcap") is not None:
        mkt_rows.append(("DEX liq / mcap", f"{market['liquidity_to_mcap']:.4f}"))
    if market.get("liquidity_to_float_usd") is not None:
        mkt_rows.append(("DEX liq / float", f"{market['liquidity_to_float_usd']:.4f}"))
    if market.get("yield_pool_to_protocol_tvl") is not None:
        mkt_rows.append(("Yield pool TVL / protocol TVL", f"{market['yield_pool_to_protocol_tvl']:.4f}"))

    if mkt_rows:
        lines.append("**Market & liquidity depth**")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for k, v in mkt_rows:
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # Fees vs custody/mcap/TVL
    fee_rows: list[tuple[str, str]] = []
    if fees.get("annualized_fees_usd") is not None:
        fee_rows.append(("Annualized fees", f"${_fmt_num(fees['annualized_fees_usd'])}"))
    if fees.get("annualized_revenue_usd") is not None:
        fee_rows.append(("Annualized revenue", f"${_fmt_num(fees['annualized_revenue_usd'])}"))
    if fees.get("revenue_share_of_fees") is not None:
        fee_rows.append(("Revenue share of fees", f"{fees['revenue_share_of_fees'] * 100:.1f}%"))
    if fees.get("fees_to_mcap") is not None:
        fee_rows.append(("Fees / mcap", f"{fees['fees_to_mcap'] * 100:.2f}%"))
    if fees.get("fees_to_tvl") is not None:
        fee_rows.append(("Fees / TVL (capital efficiency)", f"{fees['fees_to_tvl'] * 100:.2f}%"))
    if fees.get("revenue_to_mcap") is not None:
        fee_rows.append(("Revenue / mcap", f"{fees['revenue_to_mcap'] * 100:.2f}%"))
    if fees.get("revenue_to_tvl") is not None:
        fee_rows.append(("Revenue / TVL", f"{fees['revenue_to_tvl'] * 100:.2f}%"))
    if fees.get("fees_to_custody_usd") is not None:
        fee_rows.append(("Fees / custody USD", f"{fees['fees_to_custody_usd'] * 100:.2f}%"))
    if fees.get("implied_staker_apy_if_fees_distributed") is not None:
        fee_rows.append(
            (
                "**Implied staker APY** (if all fees distributed)",
                f"**{fees['implied_staker_apy_if_fees_distributed']}%**",
            )
        )

    if fee_rows:
        lines.append("**Fees vs mcap / TVL / custody**")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for k, v in fee_rows:
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # Yield observed vs implied
    y_rows: list[tuple[str, str]] = []
    if yield_.get("pool_count") is not None:
        y_rows.append(("Yield pool count", str(yield_["pool_count"])))
    if yield_.get("best_apy_percent") is not None:
        y_rows.append(("Observed best APY", f"{yield_['best_apy_percent']}%"))
    if yield_.get("weighted_avg_apy_percent") is not None:
        y_rows.append(("Observed TVL-weighted APY", f"{yield_['weighted_avg_apy_percent']}%"))
    if yield_.get("implied_staker_apy_percent") is not None:
        y_rows.append(("Implied staker APY (fees/staked)", f"{yield_['implied_staker_apy_percent']}%"))
    if yield_.get("market_apy_vs_implied_apy") is not None:
        y_rows.append(("Market APY / implied APY", f"{yield_['market_apy_vs_implied_apy']:.3f}"))

    if y_rows:
        lines.append("**Yield observed vs implied from fees**")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for k, v in y_rows:
            lines.append(f"| {k} | {v} |")
        lines.append("")

    # Custody comparisons
    cust_rows: list[tuple[str, str]] = []
    if custody.get("locked_tokens_vs_staked") is not None:
        cust_rows.append(("Locked / staked", f"{custody['locked_tokens_vs_staked']:.3f}"))
    if custody.get("custody_vs_float") is not None:
        cust_rows.append(("Custody / float", f"{custody['custody_vs_float']:.3f}"))
    if custody.get("custody_vs_dex_liquidity_usd") is not None:
        cust_rows.append(("Custody USD / DEX liquidity", f"{custody['custody_vs_dex_liquidity_usd']:.3f}"))
    lp_vs = custody.get("lp_locked_vs_token_custody_pct") or {}
    if isinstance(lp_vs, dict) and (lp_vs.get("lp_locked_percent") is not None or lp_vs.get("token_custody_percent") is not None):
        cust_rows.append(
            (
                "LP locked % vs token custody %",
                f"LP {lp_vs.get('lp_locked_percent','—')}% / tokens {lp_vs.get('token_custody_percent','—')}%",
            )
        )

    if cust_rows:
        lines.append("**Custody vs float vs liquidity**")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("| --- | --- |")
        for k, v in cust_rows:
            lines.append(f"| {k} | {v} |")
        lines.append("")

    if notes:
        lines.append("**Notes on estimation limits**")
        lines.append("")
        for n in notes:
            lines.append(_bullet(n))
        lines.append("")


def _render_relations(lines: list[str], module: dict | None) -> None:
    if not module:
        return
    metrics = _metrics(module)
    entities = metrics.get("entities") or []
    clusters = metrics.get("clusters") or []
    relations = metrics.get("relations") or []
    if not entities:
        return
    lines.append(_h(3, "Relations graph"))
    lines.append("")
    lines.append(_bullet(f"Entities: {len(entities)}"))
    lines.append(_bullet(f"Clusters: {len(clusters)}"))
    lines.append(_bullet(f"Relations: {len(relations)}"))
    if clusters:
        lines.append("")
        lines.append("| Cluster | Members | Share of top holders |")
        lines.append("| --- | --- | --- |")
        for c in clusters[:8]:
            if isinstance(c, dict):
                share = c.get("share_of_top_holders")
                share_str = f"{share * 100:.1f}%" if isinstance(share, (int, float)) else "—"
                lines.append(f"| {c.get('name', '—')} | {len(c.get('members') or [])} | {share_str} |")
    lines.append("")
