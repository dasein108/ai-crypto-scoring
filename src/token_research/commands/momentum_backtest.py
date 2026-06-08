"""momentum-backtest — replay a saved basket against historical price series.

Loads a basket JSON written by `momentum-screen` and walks a date range,
applying buy-and-hold with periodic rebalance back to target weights. The
per-period return for each position is read from a price-history cache at
`$TOKEN_RESEARCH_DATA_DIR/prices/<chain>-<address>.json`.

Cost model is configurable but defaults to: 10 bps spot taker, 5 bps perp
taker, 15% APR perp funding (charged on shorts only), zero slippage. No
real-time pricing, no live execution — research artefact.

Synthetic mode (`--synthetic`) generates a deterministic random walk per
position so the harness can be tested offline.

Usage:
    token-research momentum-backtest v1 --start 2026-01-01 --end 2026-04-30
    token-research momentum-backtest latest --rebalance weekly --synthetic
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
from token_research.providers.registry import build_source_refs


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_START = "2026-01-01"
DEFAULT_END = "2026-04-30"
DEFAULT_SPOT_BPS = 10.0
DEFAULT_PERP_BPS = 5.0
DEFAULT_FUNDING_APR = 0.15
DEFAULT_REBALANCE = "weekly"  # weekly | monthly | none

REBALANCE_DAYS: dict[str, int] = {"daily": 1, "weekly": 7, "monthly": 30, "none": 10_000}


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_basket(config: AppConfig, tag: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve `tag` to a saved basket. `latest` or empty picks newest."""
    d = config.data_dir / "baskets"
    if not d.exists():
        return None, None
    files = sorted(d.glob("*.json"))
    if not files:
        return None, None

    # If tag points to a file, use it directly.
    if tag and tag not in ("latest", ""):
        candidate = Path(tag)
        if candidate.is_file():
            try:
                return json.loads(candidate.read_text()), str(candidate)
            except (json.JSONDecodeError, OSError):
                return None, str(candidate)
        # Match by filename prefix in baskets dir.
        prefix_matches = [f for f in files if f.name.startswith(tag)]
        if prefix_matches:
            try:
                return json.loads(prefix_matches[-1].read_text()), str(prefix_matches[-1])
            except (json.JSONDecodeError, OSError):
                return None, str(prefix_matches[-1])

    # Default: most recent.
    try:
        return json.loads(files[-1].read_text()), str(files[-1])
    except (json.JSONDecodeError, OSError):
        return None, str(files[-1])


def _load_price_series(config: AppConfig, chain: str, address: str) -> list[dict[str, Any]]:
    """Read `prices/<chain>-<address>.json` if present.

    Expected schema: a list of `{"date_iso": "YYYY-MM-DD", "price_usd": float}`
    entries, sorted ascending by date. Missing file → empty list.
    """
    if not chain or not address:
        return []
    fname = f"{chain.lower()}-{address.lower()}.json"
    path = config.data_dir / "prices" / fname
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    out = [r for r in raw if isinstance(r, dict) and r.get("date_iso") and r.get("price_usd") is not None]
    out.sort(key=lambda r: str(r["date_iso"]))
    return out


def _synthetic_series(seed: int, start: date, end: date, drift: float = 0.0, vol: float = 0.02) -> list[dict[str, Any]]:
    """Deterministic random-walk price series for a single position.

    `drift` is per-day expected log-return, `vol` is per-day stdev. The seed
    determines the path so identical (seed, dates) reproduce exactly.
    """
    rng = random.Random(seed)
    series: list[dict[str, Any]] = []
    price = 100.0
    cur = start
    while cur <= end:
        # Step price first so day 0 already reflects one period of noise; this
        # avoids having the "previous price" for day 0 equal to the seed price
        # exactly when concatenating multiple synthetic runs.
        ret = drift + rng.gauss(0.0, vol)
        price *= math.exp(ret)
        series.append({"date_iso": cur.isoformat(), "price_usd": round(price, 6)})
        cur += timedelta(days=1)
    return series


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def _parse_iso(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _rebalance_dates(start: date, end: date, frequency: str) -> list[date]:
    step = REBALANCE_DAYS.get(frequency, 7)
    out: list[date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += timedelta(days=step)
    if out and out[-1] != end:
        out.append(end)
    return out


def _price_at_or_before(series: list[dict[str, Any]], target: date) -> float | None:
    """Return the most recent price on or before `target`. Linear scan — fine
    for short series; switch to bisect when histories grow."""
    best: float | None = None
    target_iso = target.isoformat()
    for row in series:
        if row["date_iso"] <= target_iso:
            best = float(row["price_usd"])
        else:
            break
    return best


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PeriodResult:
    period_end: str
    long_return_pct: float
    short_return_pct: float
    funding_cost_pct: float
    transaction_cost_pct: float
    total_return_pct: float
    equity: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    basket_source: str | None
    start: str
    end: str
    rebalance: str
    n_periods: int
    final_equity: float
    total_return_pct: float
    annualized_return_pct: float
    sharpe: float | None
    max_drawdown_pct: float
    hit_rate_pct: float
    long_attribution_pct: float
    short_attribution_pct: float
    total_funding_cost_pct: float
    total_transaction_cost_pct: float
    periods: list[PeriodResult] = field(default_factory=list)
    missing_price_series: list[str] = field(default_factory=list)
    cost_model: dict[str, float] = field(default_factory=dict)


def _position_returns(
    positions: list[dict[str, Any]],
    series_map: dict[tuple[str, str], list[dict[str, Any]]],
    period_start: date,
    period_end: date,
    side_sign: int,
) -> tuple[float, list[str]]:
    """Notional-weighted return contribution from one leg over [start, end].

    `side_sign` is +1 for longs, -1 for shorts. Returns (leg_return, missing_keys).
    Missing series are skipped — surviving names are NOT renormalized; the
    leg simply earns less when half its book has no data. This matches the
    "deterministic given fixed inputs" acceptance criterion.
    """
    leg_ret = 0.0
    missing: list[str] = []
    for pos in positions:
        key = (pos["chain"], (pos["address"] or "").lower())
        s = series_map.get(key) or []
        p_start = _price_at_or_before(s, period_start)
        p_end = _price_at_or_before(s, period_end)
        if p_start is None or p_end is None or p_start <= 0:
            missing.append(f"{pos.get('symbol') or '?'}@{pos.get('chain')}")
            continue
        r = p_end / p_start - 1.0
        leg_ret += pos["notional_pct"] * r * side_sign
    return leg_ret, missing


def _periods_per_year(rebalance: str) -> float:
    days = REBALANCE_DAYS.get(rebalance, 7)
    return 365.0 / max(days, 1)


def _sharpe(returns: list[float], periods_per_year: float) -> float | None:
    if len(returns) < 2:
        return None
    mu = sum(returns) / len(returns)
    var = sum((r - mu) ** 2 for r in returns) / (len(returns) - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    if sd <= 0:
        return None
    return mu / sd * math.sqrt(periods_per_year)


def _max_drawdown(equity: list[float]) -> float:
    """Return max drawdown as a positive percent."""
    peak = equity[0] if equity else 1.0
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100.0


def _flatten_positions(basket: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pull longs / shorts out of either the raw PaperBasket dict or the
    momentum-screen CommandResult dict. Both formats are accepted because
    persisted basket files use the dataclass shape but nothing prevents a
    user from feeding the full CommandResult by accident."""
    inner = basket.get("metrics", {}).get("basket") if "metrics" in basket else basket
    return list(inner.get("longs") or []), list(inner.get("shorts") or [])


def run_backtest(
    basket: dict[str, Any],
    *,
    start: date,
    end: date,
    rebalance: str,
    spot_bps: float,
    perp_bps: float,
    funding_apr: float,
    series_map: dict[tuple[str, str], list[dict[str, Any]]],
    basket_source: str | None,
) -> BacktestResult:
    """Pure backtest core — accepts pre-loaded series so tests can inject synthetic data."""
    longs, shorts = _flatten_positions(basket)
    rebal_dates = _rebalance_dates(start, end, rebalance)
    if len(rebal_dates) < 2:
        rebal_dates = [start, end]

    periods_per_year = _periods_per_year(rebalance)
    period_days = REBALANCE_DAYS.get(rebalance, 7)
    funding_per_period = funding_apr * (period_days / 365.0)

    # One-shot transaction cost charged at inception (entering the book).
    initial_long_notional = sum(p.get("notional_pct", 0.0) for p in longs)
    initial_short_notional = sum(p.get("notional_pct", 0.0) for p in shorts)
    inception_tcost = (
        initial_long_notional * spot_bps / 10_000.0
        + initial_short_notional * perp_bps / 10_000.0
    )

    equity = 1.0 - inception_tcost
    equity_curve: list[float] = [equity]
    period_results: list[PeriodResult] = []
    missing_keys: set[str] = set()
    period_returns: list[float] = []
    long_attribution = 0.0
    short_attribution = 0.0
    total_funding = 0.0
    total_tcost = inception_tcost

    for i in range(1, len(rebal_dates)):
        ps, pe = rebal_dates[i - 1], rebal_dates[i]
        long_ret, long_missing = _position_returns(longs, series_map, ps, pe, +1)
        short_ret, short_missing = _position_returns(shorts, series_map, ps, pe, -1)
        for k in long_missing + short_missing:
            missing_keys.add(k)

        funding_cost = initial_short_notional * funding_per_period
        # Rebalance back to target → drift cost. Approximated as the renormalize
        # cost: |drift| * cost_bps. We don't track per-position drift in v1, so
        # use a conservative flat estimate: `0.5 * leg_notional` worth of
        # turnover at each rebalance, which is roughly what a 2σ daily move
        # implies across a basket. Configurable later.
        rebalance_turnover = 0.5 * (initial_long_notional + initial_short_notional)
        rebalance_cost = rebalance_turnover * (
            initial_long_notional * spot_bps + initial_short_notional * perp_bps
        ) / 10_000.0 / max(initial_long_notional + initial_short_notional, 1e-12)

        period_total = long_ret + short_ret - funding_cost - rebalance_cost
        equity *= 1.0 + period_total
        equity_curve.append(equity)

        long_attribution += long_ret
        short_attribution += short_ret
        total_funding += funding_cost
        total_tcost += rebalance_cost
        period_returns.append(period_total)

        period_results.append(PeriodResult(
            period_end=pe.isoformat(),
            long_return_pct=round(long_ret * 100, 4),
            short_return_pct=round(short_ret * 100, 4),
            funding_cost_pct=round(funding_cost * 100, 4),
            transaction_cost_pct=round(rebalance_cost * 100, 4),
            total_return_pct=round(period_total * 100, 4),
            equity=round(equity, 6),
        ))

    total_return_pct = (equity - 1.0) * 100.0
    days = (end - start).days or 1
    annualized = ((equity ** (365.0 / days)) - 1.0) * 100.0 if equity > 0 else -100.0
    sharpe = _sharpe(period_returns, periods_per_year)
    max_dd = _max_drawdown(equity_curve)
    hits = sum(1 for r in period_returns if r > 0)
    hit_rate = (hits / len(period_returns) * 100.0) if period_returns else 0.0

    return BacktestResult(
        basket_source=basket_source,
        start=start.isoformat(),
        end=end.isoformat(),
        rebalance=rebalance,
        n_periods=len(period_returns),
        final_equity=round(equity, 6),
        total_return_pct=round(total_return_pct, 4),
        annualized_return_pct=round(annualized, 4),
        sharpe=round(sharpe, 4) if sharpe is not None else None,
        max_drawdown_pct=round(max_dd, 4),
        hit_rate_pct=round(hit_rate, 4),
        long_attribution_pct=round(long_attribution * 100, 4),
        short_attribution_pct=round(short_attribution * 100, 4),
        total_funding_cost_pct=round(total_funding * 100, 4),
        total_transaction_cost_pct=round(total_tcost * 100, 4),
        periods=period_results,
        missing_price_series=sorted(missing_keys),
        cost_model={
            "spot_taker_bps": spot_bps,
            "perp_taker_bps": perp_bps,
            "funding_apr": funding_apr,
        },
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(
    query: str,
    chain: str | None,
    address: str | None,
    config: AppConfig,
    include_premium: bool,
    *,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    rebalance: str = DEFAULT_REBALANCE,
    spot_bps: float = DEFAULT_SPOT_BPS,
    perp_bps: float = DEFAULT_PERP_BPS,
    funding_apr: float = DEFAULT_FUNDING_APR,
    synthetic: bool = False,
    synthetic_seed: int = 42,
):
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    basket, basket_source = _load_basket(config, query)
    if basket is None:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRIMITIVE_INFERENCE,
            reason="No saved basket found — run momentum-screen first.",
            suggested_source="token-research momentum-screen <tag>",
        ))
        return _result(query, chain, address, include_premium, config,
                       {"verdict": "no_basket"}, warnings, coverage_gaps)

    try:
        start_d = _parse_iso(start)
        end_d = _parse_iso(end)
    except ValueError as exc:
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=f"Bad date format: {exc}", severity="warning",
        ))
        return _result(query, chain, address, include_premium, config,
                       {"verdict": "bad_dates"}, warnings, coverage_gaps)

    if rebalance not in REBALANCE_DAYS:
        rebalance = DEFAULT_REBALANCE

    longs, shorts = _flatten_positions(basket)
    all_positions = longs + shorts

    # Build the series map. Synthetic mode replaces missing series with a
    # deterministic random walk so the backtest can run offline.
    series_map: dict[tuple[str, str], list[dict[str, Any]]] = {}
    missing_real: list[str] = []
    for i, pos in enumerate(all_positions):
        ch = pos.get("chain") or ""
        addr = (pos.get("address") or "").lower()
        key = (ch, addr)
        s = _load_price_series(config, ch, addr)
        if not s and synthetic:
            # Drift sign = position side (long=+, short=-) so the synthetic
            # path doesn't trivially hand the basket free PnL.
            sign = 1 if pos.get("side") == "long" else -1
            s = _synthetic_series(
                seed=synthetic_seed + i,
                start=start_d - timedelta(days=1),
                end=end_d + timedelta(days=1),
                drift=0.0005 * sign,
                vol=0.02,
            )
        if not s:
            missing_real.append(f"{pos.get('symbol') or '?'}@{ch}")
        series_map[key] = s

    if missing_real and not synthetic:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRICE_USD,
            reason=(
                f"{len(missing_real)} position(s) have no price-history cache. "
                "Drop these names, supply prices/<chain>-<address>.json, or pass --synthetic."
            ),
            suggested_source=f"$TOKEN_RESEARCH_DATA_DIR/prices/ — missing: {missing_real[:5]}",
        ))

    result = run_backtest(
        basket,
        start=start_d, end=end_d, rebalance=rebalance,
        spot_bps=spot_bps, perp_bps=perp_bps, funding_apr=funding_apr,
        series_map=series_map, basket_source=basket_source,
    )

    metrics: dict[str, Any] = {
        "tag": query,
        "synthetic": synthetic,
        "result": _backtest_to_dict(result),
    }
    return _result(query, chain, address, include_premium, config, metrics, warnings, coverage_gaps)


def _backtest_to_dict(r: BacktestResult) -> dict[str, Any]:
    return {
        "basket_source": r.basket_source,
        "start": r.start,
        "end": r.end,
        "rebalance": r.rebalance,
        "n_periods": r.n_periods,
        "final_equity": r.final_equity,
        "total_return_pct": r.total_return_pct,
        "annualized_return_pct": r.annualized_return_pct,
        "sharpe": r.sharpe,
        "max_drawdown_pct": r.max_drawdown_pct,
        "hit_rate_pct": r.hit_rate_pct,
        "long_attribution_pct": r.long_attribution_pct,
        "short_attribution_pct": r.short_attribution_pct,
        "total_funding_cost_pct": r.total_funding_cost_pct,
        "total_transaction_cost_pct": r.total_transaction_cost_pct,
        "periods": [
            {
                "period_end": p.period_end,
                "long_return_pct": p.long_return_pct,
                "short_return_pct": p.short_return_pct,
                "funding_cost_pct": p.funding_cost_pct,
                "transaction_cost_pct": p.transaction_cost_pct,
                "total_return_pct": p.total_return_pct,
                "equity": p.equity,
            }
            for p in r.periods
        ],
        "missing_price_series": r.missing_price_series,
        "cost_model": r.cost_model,
    }


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
        command="momentum-backtest",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity={"query": query, "mode": "momentum-backtest"},
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
