"""portfolio-rebalance — drift detection + trade list for a saved book.

Loads a `PortfolioBook` written by `portfolio-build`, marks each position
to current price (read from the local cache), and emits the list of
trades needed to bring the book back to its target weights. Pure
research artefact — never executes anything.

Usage:
    token-research portfolio-rebalance bullhedge-v1
    token-research portfolio-rebalance latest --drift-threshold 0.03
    token-research portfolio-rebalance .token-research/portfolios/v1-...json
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from token_research.config import AppConfig
from token_research.models import (
    CommandResult,
    CoverageGap,
    CoverageMetric,
    WarningCode,
    WarningItem,
    dataclass_list,
)
from token_research.prices import load_reference_series, load_series
from token_research.providers.registry import build_source_refs


@dataclass(frozen=True, slots=True)
class DriftRow:
    name: str
    direction: str
    bybit_symbol: str
    target_pct: float
    current_pct: float
    drift_pct: float                # current − target, signed
    abs_drift_pct: float
    needs_trade: bool
    side_to_trade: str              # "buy" | "sell" | "none"
    notional_change_pct: float      # of total capital, signed
    entry_price_usd: float | None
    current_price_usd: float | None
    pnl_pct: float | None           # since entry, signed
    last_cache_date: str | None


@dataclass(frozen=True, slots=True)
class RebalanceReport:
    portfolio_id: str
    book_path: str | None
    rebalanced_at: str
    days_since_build: int | None
    drift_threshold_pct: float
    realized_long_pct: float
    realized_short_pct: float
    realized_net_pct: float
    cumulative_pnl_pct: float | None
    rows: list[DriftRow] = field(default_factory=list)
    trades: list[DriftRow] = field(default_factory=list)
    cache_misses: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_book(config: AppConfig, ref: str) -> tuple[dict[str, Any] | None, str | None]:
    """ref: "latest" | tag-prefix | absolute path."""
    candidate = Path(ref) if ref else None
    if candidate and candidate.is_file():
        try:
            return json.loads(candidate.read_text()), str(candidate)
        except (json.JSONDecodeError, OSError):
            return None, str(candidate)

    d = config.data_dir / "portfolios"
    if not d.exists():
        return None, None
    files = sorted(d.glob("*.json"))
    if not files:
        return None, None

    if not ref or ref == "latest":
        target = files[-1]
    else:
        prefix = [f for f in files if f.name.startswith(ref)]
        if not prefix:
            return None, None
        target = prefix[-1]
    try:
        return json.loads(target.read_text()), str(target)
    except (json.JSONDecodeError, OSError):
        return None, str(target)


def _latest_price_for(config: AppConfig, position: dict[str, Any]) -> tuple[float | None, str | None]:
    """Resolve the price-history cache for one position's chain/address or
    cache_key. Returns (last_price_usd, last_date_iso). None when missing."""
    chain = position.get("chain")
    address = position.get("address")
    if chain and address:
        rows = load_series(config.data_dir, str(chain), str(address))
        if rows:
            return float(rows[-1]["price_usd"]), str(rows[-1]["date_iso"])

    # Fall back to reference cache by short name (BTC hedge, SOL, AVAX, …).
    name = str(position.get("name", "")).lower()
    if name:
        rows = load_reference_series(config.data_dir, name)
        if rows:
            return float(rows[-1]["price_usd"]), str(rows[-1]["date_iso"])
    return None, None


# ---------------------------------------------------------------------------
# Drift math
# ---------------------------------------------------------------------------


def _compute_drift(
    positions: list[dict[str, Any]],
    *,
    config: AppConfig,
) -> tuple[list[DriftRow], list[str]]:
    """Mark each position to current price and compute its drifted weight.

    The current capital base is the sum of marked-to-market position
    values (long contributes + value, short contributes − value relative
    to entry). Each position's `current_pct` is its own value divided by
    the total absolute exposure — i.e. gross-normalized, matching how
    `target_pct` was emitted at build time.
    """
    cache_misses: list[str] = []
    marked: list[tuple[dict[str, Any], float, float | None, str | None]] = []
    total_abs_value = 0.0

    for pos in positions:
        last_price, last_date = _latest_price_for(config, pos)
        entry = pos.get("entry_price_usd")
        target = float(pos.get("target_pct") or 0.0)
        if last_price is None or entry in (None, 0):
            cache_misses.append(str(pos.get("name") or "?"))
            current_value = target  # assume on-target so we don't penalize lack of data
        else:
            ratio = last_price / float(entry)
            if pos.get("direction") == "long":
                value = target * ratio
            else:
                # Short: value gain when price falls. Flip the ratio around 1.
                value = target * (2.0 - ratio)
            current_value = max(value, 0.0)
        marked.append((pos, current_value, last_price, last_date))
        total_abs_value += current_value

    if total_abs_value <= 0:
        total_abs_value = 1.0  # avoid div-by-zero on empty books

    rows: list[DriftRow] = []
    for pos, value, last_price, last_date in marked:
        target = float(pos.get("target_pct") or 0.0)
        current_pct = value / total_abs_value
        drift = current_pct - target
        notional_change = -drift  # to bring back to target we trade against the drift
        side: str
        if pos.get("direction") == "long":
            side = "buy" if notional_change > 0 else ("sell" if notional_change < 0 else "none")
        else:
            # On the short leg, "increase short" means selling more (the symbol),
            # "decrease short" means buying back. Express in user-facing terms.
            side = "sell" if notional_change > 0 else ("buy" if notional_change < 0 else "none")
        rows.append(DriftRow(
            name=str(pos.get("name") or "?"),
            direction=str(pos.get("direction") or "?"),
            bybit_symbol=str(pos.get("bybit_symbol") or ""),
            target_pct=round(target, 6),
            current_pct=round(current_pct, 6),
            drift_pct=round(drift, 6),
            abs_drift_pct=round(abs(drift), 6),
            needs_trade=False,           # filled in by caller
            side_to_trade=side,
            notional_change_pct=round(notional_change, 6),
            entry_price_usd=pos.get("entry_price_usd"),
            current_price_usd=last_price,
            pnl_pct=(round(_position_pnl(pos, last_price), 6)
                    if last_price is not None else None),
            last_cache_date=last_date,
        ))
    return rows, cache_misses


def _position_pnl(pos: dict[str, Any], last_price: float | None) -> float:
    if last_price is None or not pos.get("entry_price_usd"):
        return 0.0
    ratio = last_price / float(pos["entry_price_usd"])
    return (ratio - 1.0) if pos.get("direction") == "long" else (1.0 - ratio)


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
    drift_threshold_pct: float | None = None,
):
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    book, path = _load_book(config, query or "latest")
    if book is None:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRIMITIVE_INFERENCE,
            reason=f"No saved portfolio for {query!r}.",
            suggested_source="Run `portfolio-build <id>` first.",
        ))
        return _result(query, chain, address, include_premium, config, {
            "verdict": "no_book",
        }, warnings, coverage_gaps)

    positions = list(book.get("positions") or [])
    if not positions:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRIMITIVE_INFERENCE,
            reason="Saved portfolio has no positions.",
            suggested_source="Re-run `portfolio-build`.",
        ))
        return _result(query, chain, address, include_premium, config, {
            "verdict": "empty_book", "book_path": path,
        }, warnings, coverage_gaps)

    threshold = (
        drift_threshold_pct
        if drift_threshold_pct is not None
        else float((book.get("rebalance_policy") or {}).get("drift_threshold_pct", 0.05))
    )
    rows, cache_misses = _compute_drift(positions, config=config)
    rows = [
        DriftRow(**{**asdict(r), "needs_trade": r.abs_drift_pct >= threshold})
        for r in rows
    ]
    trades = [r for r in rows if r.needs_trade]

    # Realized aggregates after marking to market.
    realized_long = sum(r.current_pct for r in rows if r.direction == "long")
    realized_short = sum(r.current_pct for r in rows if r.direction == "short")
    realized_net = realized_long - realized_short

    # Days since the book was generated (book.generated_at is ISO8601).
    days_since: int | None = None
    try:
        gen = datetime.fromisoformat(str(book.get("generated_at")).replace("Z", "+00:00"))
        days_since = (datetime.now(UTC) - gen).days
    except (TypeError, ValueError):
        pass

    # Cumulative PnL — average per-position pnl_pct weighted by target_pct.
    pnl_rows = [r for r in rows if r.pnl_pct is not None]
    if pnl_rows:
        weighted = sum(r.pnl_pct * r.target_pct for r in pnl_rows)  # type: ignore[operator]
        cum_pnl: float | None = round(weighted, 6)
    else:
        cum_pnl = None

    if cache_misses:
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=(
                f"{len(cache_misses)} position(s) missing current price; "
                f"drift assumed zero for: {', '.join(cache_misses[:6])}"
            ),
            severity="info",
        ))

    report = RebalanceReport(
        portfolio_id=str(book.get("portfolio_id") or ""),
        book_path=path,
        rebalanced_at=datetime.now(UTC).isoformat(),
        days_since_build=days_since,
        drift_threshold_pct=threshold,
        realized_long_pct=round(realized_long, 6),
        realized_short_pct=round(realized_short, 6),
        realized_net_pct=round(realized_net, 6),
        cumulative_pnl_pct=cum_pnl,
        rows=rows,
        trades=trades,
        cache_misses=cache_misses,
    )

    metrics: dict[str, Any] = {
        "report": asdict(report),
    }
    return _result(query, chain, address, include_premium, config, metrics, warnings, coverage_gaps)


def _result(
    query: str,
    chain: str | None,
    address: str | None,
    include_premium: bool,
    config: AppConfig,
    metrics: dict[str, Any],
    warnings: list[WarningItem],
    coverage_gaps: list[CoverageGap],
) -> CommandResult:
    return CommandResult(
        command="portfolio-rebalance",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity={"query": query, "mode": "portfolio-rebalance"},
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
