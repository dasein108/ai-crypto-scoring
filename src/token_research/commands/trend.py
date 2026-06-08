"""trend command — snapshot + delta tracking for token metrics.

Snapshot layer on top of the existing compare / yields / holders / flows
commands. Stores small per-token-per-date JSON files under
`$TOKEN_RESEARCH_DATA_DIR/trends/<chain>-<symbol>/<YYYY-MM-DD>.json` and
diffs the current reading against the closest historical snapshot within
`--window` days.

What this turns into trader-usable signals:

- **Accumulation:** `effective_top10_pct` rising faster than `protocol_owned_pct`
  → smart-money wallets concentrating. Typical lead time: 2–8 weeks.
- **Distribution:** `effective_top10_pct` falling while `protocol_owned_pct`
  is stable → insiders / early LPs exiting to retail. Typical lead time:
  1–4 weeks.
- **New whale detection:** addresses appearing in the top-30 that weren't
  there a snapshot ago. Flagged if they hold >=1% of supply.
- **Fee momentum:** `fees_to_mcap_ratio` rising without price rising →
  yield compression = pre-rerating setup.
- **Liquidity drift:** `liquidity_to_float_usd` falling while mcap stable
  → market makers pulling depth.
- **CEX rotation:** direction of `flows.netflows[cex]` over the window.

Every input is already in the pipeline as a snapshot — `trend` just adds
the "direction of travel" layer.

Usage:
    token-research trend MORPHO --chain ethereum --address 0x58D...
    token-research trend ONDO --chain ethereum --address 0xfABA... --window 14
    token-research trend PENDLE --chain ethereum --address 0x808... --no-save
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from token_research.commands import compare, flows as flows_cmd, holders as holders_cmd, score as score_cmd
from token_research.config import AppConfig
from token_research.models import CommandResult, CoverageGap, CoverageMetric, WarningCode, WarningItem, dataclass_list
from token_research.providers.registry import build_source_refs
from token_research.resolver import resolve_with_sources


# ---------------------------------------------------------------------------
# Snapshot shape
# ---------------------------------------------------------------------------

# The trend snapshot is deliberately compact — just the fields we diff over
# time. Full compare/yields output can always be re-fetched from raw/cache.
# Keeping snapshots small means we can run trend frequently and accumulate
# long histories without the data dir exploding.

_SNAPSHOT_VERSION = 1


def _safe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _build_snapshot(
    identity,
    compare_result: dict,
    holders_result: dict,
    flows_result: dict | None,
    score_result: dict | None = None,
) -> dict:
    """Extract the key trend-relevant fields from the fatter command outputs."""
    sup = (compare_result.get("metrics") or {}).get("supply") or {}
    mkt = (compare_result.get("metrics") or {}).get("market") or {}
    fees = (compare_result.get("metrics") or {}).get("fees") or {}
    yld = (compare_result.get("metrics") or {}).get("yield") or {}

    hmetrics = holders_result.get("metrics") or {}
    top_holders = hmetrics.get("top_holders") or []
    top_addrs = [
        str(h.get("address") or "").lower()
        for h in top_holders[:30]
        if isinstance(h, dict) and h.get("address")
    ]
    # Keep per-address balance for the top 30 so new-whale detection can
    # compare "how much new exposure" rather than just "did this address
    # exist last week".
    top_balances = {}
    for h in top_holders[:30]:
        if isinstance(h, dict) and h.get("address"):
            addr = str(h["address"]).lower()
            val = _safe_float(h.get("value"))
            if val is not None:
                top_balances[addr] = val

    score_block: dict = {}
    if score_result:
        smetrics = score_result.get("metrics") or {}
        score_block["composite_score"] = smetrics.get("composite_score")
        score_block["coverage_ratio"] = smetrics.get("coverage_ratio")
        pillars = smetrics.get("pillars") or {}
        # Snapshot every pillar — even ones not weighted in the composite —
        # so future weight changes can be back-tested over the snapshot history.
        score_block["pillars"] = {k: pillars.get(k) for k in pillars}

    netflows_block = {}
    if flows_result:
        nf = (flows_result.get("metrics") or {}).get("netflows") or []
        for row in nf:
            if not isinstance(row, dict):
                continue
            cat = str(row.get("category") or "unknown")
            net = _safe_float(row.get("net_raw"))
            if net is not None:
                netflows_block[cat] = net

    return {
        "version": _SNAPSHOT_VERSION,
        "timestamp": int(datetime.now(UTC).timestamp()),
        "date_iso": datetime.now(UTC).strftime("%Y-%m-%d"),
        "symbol": identity.symbol,
        "chain": identity.chain,
        "token_address": identity.token_address,
        "supply": {
            "total_supply_tokens": sup.get("total_supply_tokens"),
            "price_usd": sup.get("price_usd"),
            "total_supply_usd": sup.get("total_supply_usd"),
            "protocol_owned_tokens": sup.get("protocol_owned_tokens"),
            "protocol_owned_pct_of_supply": sup.get("protocol_owned_pct_of_supply"),
            "effective_circulating_pct_of_supply": sup.get("effective_circulating_pct_of_supply"),
            "top10_pct_of_supply": sup.get("top10_pct_of_supply"),
            "effective_top10_pct_of_supply": sup.get("effective_top10_pct_of_supply"),
        },
        "market": {
            "mcap_usd": mkt.get("mcap_usd"),
            "tvl_usd": mkt.get("tvl_usd"),
            "dex_liquidity_usd": mkt.get("dex_liquidity_usd"),
            "mcap_to_tvl": mkt.get("mcap_to_tvl"),
            "liquidity_to_mcap": mkt.get("liquidity_to_mcap"),
            "liquidity_to_float_usd": mkt.get("liquidity_to_float_usd"),
        },
        "fees": {
            "annualized_fees_usd": fees.get("annualized_fees_usd"),
            "annualized_revenue_usd": fees.get("annualized_revenue_usd"),
            "fees_to_mcap": fees.get("fees_to_mcap"),
            "fees_to_tvl": fees.get("fees_to_tvl"),
            "revenue_share_of_fees": fees.get("revenue_share_of_fees"),
        },
        "yield": {
            "pool_count": yld.get("pool_count"),
            "weighted_avg_apy_percent": yld.get("weighted_avg_apy_percent"),
            "best_apy_percent": yld.get("best_apy_percent"),
        },
        "top_holders": top_addrs,
        "top_holder_balances": top_balances,
        "netflows_7d": netflows_block,
        "score": score_block or None,
    }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _trend_dir(config: AppConfig, chain: str, symbol: str) -> Path:
    safe_symbol = (symbol or "unknown").lower().replace("/", "_")
    safe_chain = (chain or "unknown").lower()
    d = config.data_dir / "trends" / f"{safe_chain}-{safe_symbol}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_snapshot(config: AppConfig, snapshot: dict) -> Path:
    d = _trend_dir(config, snapshot["chain"], snapshot["symbol"])
    path = d / f"{snapshot['date_iso']}.json"
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
    return path


def _load_historical_snapshots(config: AppConfig, chain: str, symbol: str) -> list[dict]:
    d = _trend_dir(config, chain, symbol)
    out: list[dict] = []
    for f in sorted(d.glob("*.json")):
        try:
            entry = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(entry, dict) and entry.get("timestamp"):
            out.append(entry)
    out.sort(key=lambda s: s.get("timestamp") or 0)
    return out


def _find_nearest_historical(snapshots: list[dict], target_ts: float) -> dict | None:
    """Return the snapshot with timestamp closest to target_ts, or None."""
    if not snapshots:
        return None
    return min(snapshots, key=lambda s: abs((s.get("timestamp") or 0) - target_ts))


# ---------------------------------------------------------------------------
# Delta math
# ---------------------------------------------------------------------------


def _delta(current: Any, previous: Any) -> dict | None:
    """Return {previous, current, delta, delta_pct} for two scalar values.

    Returns None if either side is missing / non-numeric.
    """
    cur = _safe_float(current)
    prev = _safe_float(previous)
    if cur is None or prev is None:
        return None
    d: dict = {"previous": prev, "current": cur, "delta": round(cur - prev, 6)}
    if prev != 0:
        d["delta_pct"] = round((cur - prev) / prev * 100, 3)
    return d


def _supply_deltas(curr: dict, prev: dict) -> dict:
    fields = (
        "total_supply_tokens",
        "protocol_owned_pct_of_supply",
        "effective_circulating_pct_of_supply",
        "top10_pct_of_supply",
        "effective_top10_pct_of_supply",
        "price_usd",
    )
    out: dict = {}
    for f in fields:
        d = _delta((curr or {}).get(f), (prev or {}).get(f))
        if d is not None:
            out[f] = d
    return out


def _market_deltas(curr: dict, prev: dict) -> dict:
    fields = (
        "mcap_usd",
        "tvl_usd",
        "dex_liquidity_usd",
        "mcap_to_tvl",
        "liquidity_to_mcap",
        "liquidity_to_float_usd",
    )
    return {
        f: d
        for f in fields
        if (d := _delta((curr or {}).get(f), (prev or {}).get(f))) is not None
    }


def _fees_deltas(curr: dict, prev: dict) -> dict:
    fields = (
        "annualized_fees_usd",
        "annualized_revenue_usd",
        "fees_to_mcap",
        "fees_to_tvl",
        "revenue_share_of_fees",
    )
    return {
        f: d
        for f in fields
        if (d := _delta((curr or {}).get(f), (prev or {}).get(f))) is not None
    }


def _yield_deltas(curr: dict, prev: dict) -> dict:
    fields = ("pool_count", "weighted_avg_apy_percent", "best_apy_percent")
    return {
        f: d
        for f in fields
        if (d := _delta((curr or {}).get(f), (prev or {}).get(f))) is not None
    }


def _score_deltas(curr: dict | None, prev: dict | None) -> dict:
    """Per-pillar delta on the score block. Velocity = composite delta."""
    if not curr or not prev:
        return {}
    out: dict = {}
    composite = _delta(curr.get("composite_score"), prev.get("composite_score"))
    if composite is not None:
        out["composite_score"] = composite
    cur_pillars = curr.get("pillars") or {}
    prev_pillars = prev.get("pillars") or {}
    pillar_deltas: dict = {}
    for key in cur_pillars:
        d = _delta(cur_pillars.get(key), prev_pillars.get(key))
        if d is not None:
            pillar_deltas[key] = d
    if pillar_deltas:
        out["pillars"] = pillar_deltas
    return out


# ---------------------------------------------------------------------------
# Whale detection
# ---------------------------------------------------------------------------

WHALE_THRESHOLD_PCT = 1.0  # a "whale" here is any holder with ≥1% of supply


def _detect_holder_changes(
    curr_snapshot: dict, prev_snapshot: dict
) -> dict:
    """Identify new / departed / grown / shrunk top-30 holders vs the prior snapshot.

    Returns a dict with:
        new_whales:      [address, balance, estimated_pct]  (>= WHALE_THRESHOLD_PCT)
        new_in_top30:    [address, balance]  (all new top-30 entries, any size)
        departed:        [address, last_known_balance]
        grew_significantly: [address, delta, delta_pct]   (top-30 that kept their slot but added ≥10%)
        shrunk_significantly: [address, delta, delta_pct] (top-30 that kept their slot but dropped ≥10%)
    """
    curr_addrs = set(curr_snapshot.get("top_holders") or [])
    prev_addrs = set(prev_snapshot.get("top_holders") or [])
    curr_bals = curr_snapshot.get("top_holder_balances") or {}
    prev_bals = prev_snapshot.get("top_holder_balances") or {}

    new_in_top30 = sorted(curr_addrs - prev_addrs)
    departed = sorted(prev_addrs - curr_addrs)
    still_present = curr_addrs & prev_addrs

    new_whales: list[dict] = []
    for addr in new_in_top30:
        bal = _safe_float(curr_bals.get(addr))
        new_whales.append({"address": addr, "balance_raw": bal})

    departed_list: list[dict] = []
    for addr in departed:
        bal = _safe_float(prev_bals.get(addr))
        departed_list.append({"address": addr, "last_balance_raw": bal})

    grew: list[dict] = []
    shrunk: list[dict] = []
    for addr in still_present:
        c = _safe_float(curr_bals.get(addr))
        p = _safe_float(prev_bals.get(addr))
        if c is None or p is None or p == 0:
            continue
        delta_pct = (c - p) / p * 100
        row = {
            "address": addr,
            "previous_balance_raw": p,
            "current_balance_raw": c,
            "delta_pct": round(delta_pct, 3),
        }
        if delta_pct >= 10:
            grew.append(row)
        elif delta_pct <= -10:
            shrunk.append(row)

    return {
        "new_in_top30": new_whales,
        "departed_from_top30": departed_list,
        "grew_significantly": grew,
        "shrunk_significantly": shrunk,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    window_days: int = 30,
    no_save: bool = False,
):
    identity, _, warnings, coverage_gaps = resolve_with_sources(query, chain, address, config)

    # Collect current state from the underlying commands.
    compare_result = compare.run(query, chain, address, config, include_premium).to_dict()
    holders_result = holders_cmd.run(query, chain, address, config, include_premium).to_dict()
    # Flows is optional — no Dune key / query failure shouldn't block trend.
    flows_result = None
    try:
        flows_result = flows_cmd.run(query, chain, address, config, include_premium).to_dict()
    except Exception as exc:  # pragma: no cover
        warnings.append(
            WarningItem(code=WarningCode.FLOWS_UNAVAILABLE, message=str(exc), severity="info")
        )

    # Score is optional too — partial data shouldn't break the trend snapshot.
    # In-process memoization means most upstream fetches were already paid above.
    score_result = None
    try:
        score_result = score_cmd.run(query, chain, address, config, include_premium).to_dict()
    except Exception as exc:  # pragma: no cover
        warnings.append(
            WarningItem(code=WarningCode.SCORE_UNAVAILABLE, message=str(exc), severity="info")
        )

    snapshot = _build_snapshot(identity, compare_result, holders_result, flows_result, score_result)

    saved_path = None
    if not no_save and identity.symbol and identity.chain:
        try:
            saved_path = _save_snapshot(config, snapshot)
        except OSError as exc:
            warnings.append(
                WarningItem(code=WarningCode.TREND_SAVE_FAILED, message=str(exc), severity="warning")
            )

    # Load history and pick the closest prior snapshot within the window.
    history = _load_historical_snapshots(config, identity.chain or "", identity.symbol or "")
    # Exclude the one we just saved (same timestamp) from the comparison set.
    cutoff_ts = snapshot["timestamp"] - window_days * 86400
    # "Prior" = snapshots older than today, closest to cutoff_ts. Pick the
    # latest one older than today (strictly before the current snapshot) and
    # within the window.
    prior_candidates = [
        s for s in history
        if (s.get("timestamp") or 0) < snapshot["timestamp"]
        and (s.get("timestamp") or 0) >= cutoff_ts
    ]
    nearest_prior = _find_nearest_historical(prior_candidates, cutoff_ts) if prior_candidates else None

    deltas_block: dict = {
        "window_days": window_days,
        "has_baseline": nearest_prior is not None,
        "baseline_date_iso": (nearest_prior or {}).get("date_iso"),
    }
    whale_changes: dict = {}

    if nearest_prior:
        deltas_block["supply"] = _supply_deltas(
            snapshot.get("supply") or {}, nearest_prior.get("supply") or {}
        )
        deltas_block["market"] = _market_deltas(
            snapshot.get("market") or {}, nearest_prior.get("market") or {}
        )
        deltas_block["fees"] = _fees_deltas(
            snapshot.get("fees") or {}, nearest_prior.get("fees") or {}
        )
        deltas_block["yield"] = _yield_deltas(
            snapshot.get("yield") or {}, nearest_prior.get("yield") or {}
        )
        deltas_block["score"] = _score_deltas(
            snapshot.get("score"), nearest_prior.get("score")
        )
        whale_changes = _detect_holder_changes(snapshot, nearest_prior)
    else:
        coverage_gaps.append(
            CoverageGap(
                metric=CoverageMetric.DELTAS,
                reason=(
                    f"No historical snapshot within {window_days} days. "
                    "Run `trend` on this token at least twice (separated by the "
                    "desired window) for delta signals."
                ),
                suggested_source="Re-run this command after the next observation interval.",
            )
        )

    # High-level trend signals the user actually cares about.
    signals: list[str] = []
    supply_d = (deltas_block.get("supply") or {})
    market_d = (deltas_block.get("market") or {})
    fees_d = (deltas_block.get("fees") or {})

    # Accumulation: effective top-10 rising (smart money concentrating)
    eff10 = supply_d.get("effective_top10_pct_of_supply")
    if eff10 and eff10.get("delta_pct") is not None:
        if eff10["delta_pct"] >= 5:
            signals.append(
                f"ACCUMULATION: effective top-10 share rose {eff10['delta_pct']:+.2f}% "
                "— non-protocol holders are concentrating"
            )
        elif eff10["delta_pct"] <= -5:
            signals.append(
                f"DISTRIBUTION: effective top-10 share fell {eff10['delta_pct']:+.2f}% "
                "— non-protocol holders are dispersing"
            )
    # Protocol treasury activity
    prot_own = supply_d.get("protocol_owned_pct_of_supply")
    if prot_own and prot_own.get("delta_pct") is not None:
        if prot_own["delta_pct"] <= -2:
            signals.append(
                f"TREASURY_DISTRIBUTION: protocol-owned share fell {prot_own['delta_pct']:+.2f}% "
                "— foundation tokens entering circulation"
            )
        elif prot_own["delta_pct"] >= 2:
            signals.append(
                f"TREASURY_ACCUMULATION: protocol-owned share rose {prot_own['delta_pct']:+.2f}% "
                "— unusual for a live token, investigate"
            )

    # Fee momentum
    fees_to_mcap = fees_d.get("fees_to_mcap")
    if fees_to_mcap and fees_to_mcap.get("delta_pct") is not None:
        if fees_to_mcap["delta_pct"] >= 10:
            signals.append(
                f"FEE_MOMENTUM_UP: fees/mcap rose {fees_to_mcap['delta_pct']:+.2f}% "
                "— yield compression, pre-rerating setup"
            )
        elif fees_to_mcap["delta_pct"] <= -10:
            signals.append(
                f"FEE_MOMENTUM_DOWN: fees/mcap fell {fees_to_mcap['delta_pct']:+.2f}% "
                "— fundamentals detaching from price"
            )

    # Liquidity drift
    liq_float = market_d.get("liquidity_to_float_usd")
    if liq_float and liq_float.get("delta_pct") is not None:
        if liq_float["delta_pct"] <= -20:
            signals.append(
                f"LIQUIDITY_DRAIN: liq/float ratio fell {liq_float['delta_pct']:+.2f}% "
                "— market makers pulling depth"
            )
        elif liq_float["delta_pct"] >= 20:
            signals.append(
                f"LIQUIDITY_BUILD: liq/float ratio rose {liq_float['delta_pct']:+.2f}% "
                "— deepening market interest"
            )

    # Composite-score velocity — captures multi-pillar drift in one number.
    score_d = (deltas_block.get("score") or {}).get("composite_score")
    if score_d and score_d.get("delta") is not None:
        delta_val = score_d["delta"]
        if delta_val >= 5:
            signals.append(
                f"SCORE_VELOCITY_UP: composite score rose {delta_val:+.1f} pts "
                "— quality re-rating in progress"
            )
        elif delta_val <= -5:
            signals.append(
                f"SCORE_VELOCITY_DOWN: composite score fell {delta_val:+.1f} pts "
                "— quality deterioration, investigate which pillar drove the drop"
            )

    # New whales
    new_whales = whale_changes.get("new_in_top30") or []
    if len(new_whales) >= 2:
        signals.append(
            f"NEW_WHALES: {len(new_whales)} new address(es) entered top-30 "
            "— possible accumulation starts"
        )
    departed = whale_changes.get("departed_from_top30") or []
    if len(departed) >= 2:
        signals.append(
            f"WHALES_EXITED: {len(departed)} address(es) left top-30 "
            "— possible distribution"
        )

    metrics: dict = {
        "snapshot": snapshot,
        "baseline": nearest_prior,
        "deltas": deltas_block,
        "holder_changes": whale_changes,
        "signals": signals,
        "history": {
            "snapshots_available": len(history),
            "earliest_date": (history[0] or {}).get("date_iso") if history else None,
            "latest_date": (history[-1] or {}).get("date_iso") if history else None,
        },
        "saved_to": str(saved_path) if saved_path else None,
    }

    return CommandResult(
        command="trend",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
            "window_days": window_days,
            "no_save": no_save,
        },
        resolved_identity=asdict(identity),
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
