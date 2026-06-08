"""signal command — composite buy/sell score from pipeline metrics + trend deltas.

Applies the trader-framework scoring formula documented in
`docs/trend_signals.md`. The score combines:

- **Structural supply pressure** (overhang_ratio)
- **Fundamental yield** (fees_to_mcap, weighted by revenue_model)
- **Exit liquidity** (liquidity_to_float_usd)
- **Flow direction** (CEX net if flows available)
- **Accumulation momentum** (effective_top10 delta from trend)
- **Fee momentum** (fees_to_mcap delta from trend)
- **Whale events** (new top-30 entries detected by trend)

Produces a single `signal_score` in the range [-10, +10] and a verdict
string. Designed to be the "one number" trading decision support for
portfolio managers running the full pipeline on a watchlist.

Usage:
    token-research signal MORPHO --chain ethereum --address 0x58D...
    token-research signal ONDO --chain ethereum --address 0xfABA... --window 14

Requires that `trend` has been run at least once before for delta signals;
works without trend history but produces a structural-only score.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from token_research.commands import compare, flows as flows_cmd, trend
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _clamp(value: float, low: float = -10.0, high: float = 10.0) -> float:
    return max(low, min(high, value))


def _score_overhang(
    protocol_owned_pct: float | None,
    effective_pct: float | None,
    effective_overhang_ratio: float | None = None,
) -> tuple[float, str]:
    """Supply overhang: -2 for severe, +2 for fully distributed.

    Prefers the new `effective_overhang_ratio` from compare (which counts
    concentrated EOA top-10 as structural supply pressure in addition to
    contract-held tokens). Falls back to the legacy protocol_owned /
    effective_circulating ratio when the new field is unavailable.

    The KAITO-class fix: tokens with investor/team allocations held in EOAs
    rather than multisigs (82% EOA top-10, 13% contract) used to score
    +2 "distributed" under the old formula. Under the new one, same token
    scores -2 "severe" (ratio 3.03×) which matches reality.
    """
    # Prefer the effective-overhang ratio when available.
    if effective_overhang_ratio is not None:
        r = effective_overhang_ratio
        if r <= 0.3:
            return 2.0, f"distributed (eff_overhang {r:.2f}x)"
        if r <= 0.8:
            return 1.0, f"healthy (eff_overhang {r:.2f}x)"
        if r <= 1.5:
            return 0.0, f"moderate (eff_overhang {r:.2f}x)"
        if r <= 2.5:
            return -1.5, f"elevated (eff_overhang {r:.2f}x)"
        return -2.0, f"severe (eff_overhang {r:.2f}x)"

    # Legacy fallback: protocol_owned / effective_circulating.
    # Guard against effective_pct ≤ 0 (happens when contract-detection
    # overcounts, e.g., staking wrappers + rebasing tokens where held >
    # total supply — USUAL case). Treat as no_data rather than producing
    # a nonsensical negative ratio.
    if protocol_owned_pct is None or effective_pct is None or effective_pct <= 0:
        return 0.0, "no_data"
    ratio = protocol_owned_pct / effective_pct
    if ratio <= 0.3:
        return 2.0, f"distributed (overhang {ratio:.2f}x)"
    if ratio <= 0.8:
        return 1.0, f"healthy (overhang {ratio:.2f}x)"
    if ratio <= 1.5:
        return 0.0, f"moderate (overhang {ratio:.2f}x)"
    if ratio <= 2.5:
        return -1.5, f"elevated (overhang {ratio:.2f}x)"
    return -2.0, f"severe (overhang {ratio:.2f}x)"


def _score_fee_yield(fees_to_mcap: float | None, revenue_model: str | None) -> tuple[float, str]:
    """Fundamental yield. Weighted down for fees_pass_through (optionality only)."""
    if fees_to_mcap is None:
        return 0.0, "no_data"
    # Raw fee yield tier
    if fees_to_mcap >= 0.15:
        raw = 2.0
    elif fees_to_mcap >= 0.08:
        raw = 1.5
    elif fees_to_mcap >= 0.04:
        raw = 1.0
    elif fees_to_mcap >= 0.01:
        raw = 0.5
    else:
        raw = 0.0
    # Revenue model adjustment
    if revenue_model == "protocol_capture":
        # Real cash flow — full credit
        adjusted = raw
        note = f"capture ({fees_to_mcap*100:.2f}%/yr)"
    elif revenue_model == "fees_pass_through":
        # Optionality only — half credit
        adjusted = raw * 0.5
        note = f"pass_through optionality ({fees_to_mcap*100:.2f}%/yr × 0.5)"
    else:
        adjusted = raw * 0.75
        note = f"unknown model ({fees_to_mcap*100:.2f}%/yr × 0.75)"
    return adjusted, note


def _score_liquidity_to_float(lq_float: float | None) -> tuple[float, str]:
    """Exit liquidity as a size constraint. Negative only when dangerously thin."""
    if lq_float is None:
        return 0.0, "no_data"
    if lq_float >= 0.05:
        return 1.0, f"deep ({lq_float*100:.2f}%)"
    if lq_float >= 0.02:
        return 0.5, f"normal ({lq_float*100:.2f}%)"
    if lq_float >= 0.005:
        return -0.5, f"thin ({lq_float*100:.2f}%)"
    return -1.0, f"very thin ({lq_float*100:.2f}%)"


def _score_cex_flows(netflows_7d: dict) -> tuple[float, str]:
    """CEX direction over the 7-day flow window.

    Positive CEX net = more inbound to exchanges = sell intent.
    Negative CEX net = outflow to self-custody = hold intent.
    """
    cex_net = _safe_float((netflows_7d or {}).get("cex"))
    if cex_net is None:
        return 0.0, "no_data"
    # Normalize the signal direction: negative cex_net → bullish (+), positive cex_net → bearish (−)
    if cex_net <= 0:
        return 1.0, f"off-exchange (net {cex_net:.0f})"
    return -1.0, f"exchange-inbound (net {cex_net:.0f})"


def _score_accumulation_delta(trend_deltas: dict) -> tuple[float, str]:
    """Effective top-10 share trend — smart-money concentration vs dispersion."""
    eff10 = (trend_deltas.get("supply") or {}).get("effective_top10_pct_of_supply")
    if not eff10 or eff10.get("delta_pct") is None:
        return 0.0, "no_trend_data"
    d = eff10["delta_pct"]
    if d >= 10:
        return 2.0, f"strong accumulation ({d:+.1f}%)"
    if d >= 3:
        return 1.0, f"accumulation ({d:+.1f}%)"
    if d <= -10:
        return -2.0, f"strong distribution ({d:+.1f}%)"
    if d <= -3:
        return -1.0, f"distribution ({d:+.1f}%)"
    return 0.0, f"flat ({d:+.1f}%)"


def _score_fee_momentum(trend_deltas: dict) -> tuple[float, str]:
    """fees_to_mcap delta — fundamental momentum relative to price."""
    f2m = (trend_deltas.get("fees") or {}).get("fees_to_mcap")
    if not f2m or f2m.get("delta_pct") is None:
        return 0.0, "no_trend_data"
    d = f2m["delta_pct"]
    if d >= 20:
        return 1.5, f"strong fee momentum ({d:+.1f}%)"
    if d >= 5:
        return 1.0, f"fee momentum ({d:+.1f}%)"
    if d <= -20:
        return -1.5, f"fee decay ({d:+.1f}%)"
    if d <= -5:
        return -0.5, f"fee softening ({d:+.1f}%)"
    return 0.0, f"stable ({d:+.1f}%)"


def _score_whale_events(holder_changes: dict) -> tuple[float, str]:
    """New top-30 entries and departures."""
    new_whales = holder_changes.get("new_in_top30") or []
    departed = holder_changes.get("departed_from_top30") or []
    grew = holder_changes.get("grew_significantly") or []
    shrunk = holder_changes.get("shrunk_significantly") or []

    score = 0.0
    notes = []
    # New whales are bullish; departed are bearish
    score += 0.3 * len(new_whales) - 0.3 * len(departed)
    # Top-30 holders adding tokens are bullish; shrinking are bearish
    score += 0.2 * len(grew) - 0.2 * len(shrunk)
    score = _clamp(score, -2.0, 2.0)
    if new_whales:
        notes.append(f"{len(new_whales)} new whale(s)")
    if departed:
        notes.append(f"{len(departed)} exited top-30")
    if grew:
        notes.append(f"{len(grew)} grew ≥10%")
    if shrunk:
        notes.append(f"{len(shrunk)} shrunk ≥10%")
    if not notes:
        notes.append("no movement")
    return score, " · ".join(notes)


def _verdict(score: float, coverage: float) -> str:
    """Map numeric score + coverage ratio to a verdict string.

    Coverage < 0.5 means we're missing half the signals (usually because
    trend has no baseline). Lower the confidence of any verdict.
    """
    # If coverage is very low, downgrade verdict confidence.
    low_confidence = coverage < 0.5
    if score >= 4:
        return "strong_accumulate" if not low_confidence else "accumulate_low_confidence"
    if score >= 1.5:
        return "accumulate"
    if score >= -1.5:
        return "neutral"
    if score >= -4:
        return "reduce"
    return "strong_reduce" if not low_confidence else "reduce_low_confidence"


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    window_days: int = 30,
):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)

    # Pull current structural state from compare.
    compare_result = compare.run(query, chain, address, config, include_premium).to_dict()
    cm = compare_result.get("metrics") or {}
    sup = cm.get("supply") or {}
    mkt = cm.get("market") or {}
    fees_block = cm.get("fees") or {}

    # Pull trend deltas — run trend with no_save so we don't pollute the store.
    trend_result = trend.run(
        query, chain, address, config, include_premium,
        window_days=window_days, no_save=True,
    ).to_dict()
    trend_metrics = trend_result.get("metrics") or {}
    trend_deltas = trend_metrics.get("deltas") or {}
    holder_changes = trend_metrics.get("holder_changes") or {}
    has_baseline = trend_deltas.get("has_baseline")

    # Flows — for CEX direction. Try/fail gracefully.
    netflows_7d: dict = {}
    try:
        flows_result = flows_cmd.run(query, chain, address, config, include_premium).to_dict()
        nf = (flows_result.get("metrics") or {}).get("netflows") or []
        for row in nf:
            if isinstance(row, dict):
                cat = str(row.get("category") or "unknown")
                netflows_7d[cat] = _safe_float(row.get("net_raw")) or 0
    except Exception as exc:
        warnings.append(WarningItem(code=WarningCode.FLOWS_UNAVAILABLE, message=str(exc), severity="info"))

    # Pull yields so we have revenue_model
    rev_model = None
    try:
        from token_research.commands import yields as yields_cmd
        y_result = yields_cmd.run(query, chain, address, config, include_premium).to_dict()
        rev_model = (y_result.get("metrics") or {}).get("revenue_model")
    except Exception:
        pass

    # --- Score components ---
    components: dict[str, dict] = {}
    total = 0.0
    coverage_count = 0
    coverage_total = 0

    def _record(name: str, score: float, note: str, weight: float = 1.0):
        nonlocal total, coverage_count, coverage_total
        components[name] = {"score": round(score, 3), "weight": weight, "note": note}
        total += score * weight
        coverage_total += 1
        if note != "no_data" and note != "no_trend_data":
            coverage_count += 1

    # 1. Structural overhang (weight 1.5 — dominant mid-term force)
    # Prefers effective_overhang_ratio (KAITO-class fix) when compare.supply
    # has computed it; falls back to the legacy ratio otherwise.
    s, n = _score_overhang(
        sup.get("protocol_owned_pct_of_supply"),
        sup.get("effective_circulating_pct_of_supply"),
        effective_overhang_ratio=_safe_float(sup.get("effective_overhang_ratio")),
    )
    _record("overhang", s, n, weight=1.5)

    # 2. Fundamental yield (weight 1.5)
    s, n = _score_fee_yield(fees_block.get("fees_to_mcap"), rev_model)
    _record("fee_yield", s, n, weight=1.5)

    # 3. Exit liquidity (weight 1.0)
    s, n = _score_liquidity_to_float(mkt.get("liquidity_to_float_usd"))
    _record("exit_liquidity", s, n, weight=1.0)

    # 4. CEX flows (weight 1.5 — near-term directional)
    s, n = _score_cex_flows(netflows_7d)
    _record("cex_flows", s, n, weight=1.5)

    # 5. Accumulation delta (weight 2.0 — highest trader signal)
    s, n = _score_accumulation_delta(trend_deltas)
    _record("accumulation_trend", s, n, weight=2.0)

    # 6. Fee momentum (weight 1.0)
    s, n = _score_fee_momentum(trend_deltas)
    _record("fee_momentum", s, n, weight=1.0)

    # 7. Whale events (weight 1.5)
    s, n = _score_whale_events(holder_changes if has_baseline else {})
    _record("whale_events", s, n, weight=1.5)

    total = _clamp(total)
    coverage_ratio = coverage_count / coverage_total if coverage_total else 0.0
    verdict_str = _verdict(total, coverage_ratio)

    if not has_baseline:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.TREND_DELTAS,
                reason=(
                    f"No historical snapshot within {window_days} days. "
                    "Accumulation / fee-momentum / whale-event signals are "
                    "unavailable until you run `trend` at least twice."
                ),
                suggested_source="Run `trend` on a cron.",
            )
        )

    metrics: dict[str, Any] = {
        "signal_score": round(total, 3),
        "verdict": verdict_str,
        "coverage_ratio": round(coverage_ratio, 3),
        "has_trend_baseline": bool(has_baseline),
        "components": components,
        "inputs_snapshot": {
            "protocol_owned_pct": sup.get("protocol_owned_pct_of_supply"),
            "effective_circulating_pct": sup.get("effective_circulating_pct_of_supply"),
            "effective_top10_pct": sup.get("effective_top10_pct_of_supply"),
            "effective_overhang_pct": sup.get("effective_overhang_pct_of_supply"),
            "effective_overhang_ratio": sup.get("effective_overhang_ratio"),
            "fees_to_mcap": fees_block.get("fees_to_mcap"),
            "revenue_model": rev_model,
            "liquidity_to_float_usd": mkt.get("liquidity_to_float_usd"),
            "cex_net_7d": netflows_7d.get("cex"),
            "dex_net_7d": netflows_7d.get("dex"),
        },
    }

    return CommandResult(
        command="signal",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
            "window_days": window_days,
        },
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
