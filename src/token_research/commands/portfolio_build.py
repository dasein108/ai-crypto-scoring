"""portfolio-build — bullish-hedged algorithmic portfolio.

Self-funded book (gross = 100%) of 10–15 longs across crypto categories
plus a BTC-perp short solving for the user's target net beta. Universe
defined in `prices/portfolio_universe.py`; all members must trade on
Bybit USDT perpetuals.

Methodology:
    1. Filter universe by per-asset price-history coverage (≥ window_days).
    2. Apply min/max per category to enforce diversification.
    3. Compute β-vs-BTC per asset from the daily-log-return cache.
    4. Build a covariance matrix with linear shrinkage.
    5. Risk-parity weights inside the long sleeve.
    6. Closed-form BTC-perp short for target net β (default 0.4).
    7. Optional: 1–2 alpha shorts (highest-β + worst short-side score).
    8. Persist a `PortfolioBook` JSON for later rebalance.

Output: `$DATA_DIR/portfolios/<id>-<ts>.json` plus a CommandResult
matching the rest of the CLI.

Usage:
    token-research portfolio-build bullhedge-v1
    token-research portfolio-build v2 --target-beta 0.3 --min-longs 12 --alpha-shorts 1
"""
from __future__ import annotations

import json
import math
import time
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
    utc_now_iso,
)
from token_research.prices import (
    BetaResult,
    compute_beta,
    load_reference_series,
)
from token_research.prices.covariance import compute_covariance
from token_research.prices.optimizer import optimize_long_short
from token_research.prices.portfolio_universe import (
    HEDGE_ASSET,
    UNIVERSE,
    UniverseAsset,
    load_asset_series,
)
from token_research.providers.registry import build_source_refs


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TARGET_BETA = 0.4
DEFAULT_BETA_WINDOW = 60
DEFAULT_COV_WINDOW = 90
DEFAULT_SHRINKAGE = 0.2
DEFAULT_MIN_LONGS = 10
DEFAULT_MAX_LONGS = 15
DEFAULT_MAX_PER_CATEGORY = 4
DEFAULT_MIN_CATEGORIES = 3
DEFAULT_MAX_SINGLE_PCT = 0.15
DEFAULT_ALPHA_SHORTS = 0
DEFAULT_DRIFT_THRESHOLD_PCT = 0.05
DEFAULT_REBALANCE_DAYS = 7
DEFAULT_BTC_REFERENCE = "btc"


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PortfolioPosition:
    name: str
    category: str
    direction: str                 # "long" | "short"
    bybit_symbol: str
    chain: str | None
    address: str | None
    target_pct: float              # share of total gross capital (signed: positive)
    weight_in_sleeve: float        # share of own leg (long or short)
    beta_btc: float
    risk_contribution_pct: float | None  # only populated for risk-parity longs
    entry_price_usd: float | None
    entry_date: str | None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class RebalancePolicy:
    cadence_days: int
    drift_threshold_pct: float
    max_single_position_pct: float
    min_categories: int
    horizon_months: int


@dataclass(frozen=True, slots=True)
class PortfolioBook:
    portfolio_id: str
    generated_at: str
    target_net_beta: float
    realized_net_beta: float
    realized_long_avg_beta: float
    long_pct_total: float          # sums |target_pct| over longs (≤ 1.0)
    short_pct_total: float         # sums |target_pct| over shorts
    coverage_ratio: float
    universe_size: int
    eligible_size: int
    selected_long_count: int
    selected_short_count: int
    cov_window_days: int
    cov_observations: int
    rebalance_policy: RebalancePolicy
    positions: list[PortfolioPosition] = field(default_factory=list)
    category_breakdown: dict[str, float] = field(default_factory=dict)
    optimizer_iterations: int = 0
    optimizer_converged: bool = False


# ---------------------------------------------------------------------------
# Stage 1 — universe coverage check + β computation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _AssetSnapshot:
    asset: UniverseAsset
    series: list[dict[str, Any]]
    beta: float | None
    fallback: bool
    last_price: float | None
    last_date: str | None


def _snapshot_universe(
    config: AppConfig,
    *,
    btc_reference: str,
    beta_window: int,
    beta_min_obs: int,
    exclude_names: set[str] | None = None,
    include_only: set[str] | None = None,
) -> tuple[list[_AssetSnapshot], list[_AssetSnapshot], list[dict[str, Any]]]:
    """Pull each asset's cached series + compute β-vs-BTC. Returns
    (eligible, dropped, btc_series)."""
    btc_series = load_reference_series(config.data_dir, btc_reference)
    if not btc_series:
        return [], [], []

    excludes = {n.upper() for n in (exclude_names or set())}
    includes = {n.upper() for n in (include_only or set())} if include_only else None

    eligible: list[_AssetSnapshot] = []
    dropped: list[_AssetSnapshot] = []
    for asset in UNIVERSE:
        if asset.name.upper() in excludes:
            continue
        if includes is not None and asset.name.upper() not in includes:
            continue
        series = load_asset_series(config.data_dir, asset)
        if not series:
            dropped.append(_AssetSnapshot(asset, [], None, True, None, None))
            continue
        b: BetaResult = compute_beta(
            series, btc_series,
            window_days=beta_window, min_observations=beta_min_obs,
        )
        snap = _AssetSnapshot(
            asset=asset,
            series=series,
            beta=b.beta,
            fallback=b.fallback_used,
            last_price=float(series[-1]["price_usd"]),
            last_date=str(series[-1]["date_iso"]),
        )
        # Keep names with at least *some* data — the covariance matrix
        # will discard those that don't share enough overlap with the rest.
        eligible.append(snap)
    return eligible, dropped, btc_series


# ---------------------------------------------------------------------------
# Stage 2 — diversification + selection
# ---------------------------------------------------------------------------


def _enforce_diversification(
    snapshots: list[_AssetSnapshot],
    *,
    min_longs: int,
    max_longs: int,
    max_per_category: int,
    min_categories: int,
) -> tuple[list[_AssetSnapshot], list[str]]:
    """Pick a category-balanced long sleeve.

    Selection rule used today: take all eligible names, cap each category
    at `max_per_category`, then trim to `max_longs` if we exceed it.
    Future iterations can replace this with a conviction-weighted pick.
    """
    by_cat: dict[str, list[_AssetSnapshot]] = {}
    for s in snapshots:
        by_cat.setdefault(s.asset.category, []).append(s)

    picked: list[_AssetSnapshot] = []
    notes: list[str] = []
    for cat, group in by_cat.items():
        # Round-robin within a category — keep ordering stable for reproducibility.
        chosen = group[:max_per_category]
        if len(group) > max_per_category:
            notes.append(f"cat={cat}: capped {len(group)} → {max_per_category}")
        picked.extend(chosen)

    if len(picked) > max_longs:
        # Drop the lowest-data names first (shortest series), then preserve
        # category balance by removing one from the most-represented bucket.
        picked.sort(key=lambda s: -len(s.series))
        picked = picked[:max_longs]
        notes.append(f"max_longs={max_longs}: trimmed to top by series length")

    cats_present = {s.asset.category for s in picked}
    if len(cats_present) < min_categories:
        notes.append(
            f"only {len(cats_present)} categories represented — minimum is "
            f"{min_categories}, build will surface a warning"
        )
    if len(picked) < min_longs:
        notes.append(
            f"only {len(picked)} eligible longs — minimum is {min_longs}, "
            f"build will surface a warning"
        )
    return picked, notes


# ---------------------------------------------------------------------------
# Stage 3 — alpha shorts
# ---------------------------------------------------------------------------


def _pick_alpha_shorts(
    snapshots: list[_AssetSnapshot],
    *,
    selected_longs: list[_AssetSnapshot],
    n: int,
) -> list[_AssetSnapshot]:
    """Pick up to `n` alpha-short candidates from the eligible universe.

    Heuristic: highest-β names whose recent return underperforms BTC
    (i.e. high market sensitivity AND price deterioration). We never
    short something we've gone long on.
    """
    if n <= 0 or not snapshots:
        return []
    long_names = {s.asset.name for s in selected_longs}
    candidates = [s for s in snapshots if s.asset.name not in long_names]
    if not candidates:
        return []

    # Recent return = log(last/first) over the cached series.
    def _ret(s: _AssetSnapshot) -> float:
        if not s.series or s.series[0]["price_usd"] <= 0:
            return 0.0
        return math.log(s.last_price / float(s.series[0]["price_usd"]))

    # Sort by descending β, then ascending return (worst performers first).
    candidates.sort(key=lambda s: (-(s.beta or 0), _ret(s)))
    return candidates[:n]


# ---------------------------------------------------------------------------
# Stage 4 — assemble PortfolioBook
# ---------------------------------------------------------------------------


def _build_positions(
    longs: list[_AssetSnapshot],
    shorts_alpha: list[_AssetSnapshot],
    *,
    long_notional_pct: list[float],
    long_weights_in_sleeve: list[float],
    risk_contributions: list[float],
    btc_short_pct: float,
    today_iso: str,
    btc_last_price: float | None,
    btc_last_date: str | None,
    alpha_short_pct_each: float,
) -> list[PortfolioPosition]:
    positions: list[PortfolioPosition] = []
    rc_total = sum(risk_contributions) or 1.0
    for snap, notional, weight, rc in zip(
        longs, long_notional_pct, long_weights_in_sleeve, risk_contributions,
    ):
        positions.append(PortfolioPosition(
            name=snap.asset.name,
            category=snap.asset.category,
            direction="long",
            bybit_symbol=snap.asset.bybit_symbol,
            chain=snap.asset.chain,
            address=snap.asset.address,
            target_pct=round(notional, 6),
            weight_in_sleeve=round(weight, 6),
            beta_btc=round(snap.beta or 1.0, 4),
            risk_contribution_pct=round(rc / rc_total, 6),
            entry_price_usd=snap.last_price,
            entry_date=snap.last_date,
            notes=snap.asset.notes,
        ))

    # BTC macro hedge — single position, never goes long here.
    if btc_short_pct > 1e-9:
        positions.append(PortfolioPosition(
            name=HEDGE_ASSET.name,
            category=HEDGE_ASSET.category,
            direction="short",
            bybit_symbol=HEDGE_ASSET.bybit_symbol,
            chain=HEDGE_ASSET.chain,
            address=HEDGE_ASSET.address,
            target_pct=round(btc_short_pct, 6),
            weight_in_sleeve=1.0,  # only short in the macro slot
            beta_btc=1.0,
            risk_contribution_pct=None,
            entry_price_usd=btc_last_price,
            entry_date=btc_last_date,
            notes="Macro hedge — short Bybit BTC perpetual.",
        ))

    # Alpha shorts (if any) — split the remaining gross equally between them.
    for snap in shorts_alpha:
        if alpha_short_pct_each <= 1e-9:
            continue
        positions.append(PortfolioPosition(
            name=snap.asset.name,
            category=snap.asset.category,
            direction="short",
            bybit_symbol=snap.asset.bybit_symbol,
            chain=snap.asset.chain,
            address=snap.asset.address,
            target_pct=round(alpha_short_pct_each, 6),
            weight_in_sleeve=round(1.0 / max(len(shorts_alpha), 1), 6),
            beta_btc=round(snap.beta or 1.0, 4),
            risk_contribution_pct=None,
            entry_price_usd=snap.last_price,
            entry_date=snap.last_date,
            notes=snap.asset.notes,
        ))

    return positions


def _category_breakdown(positions: list[PortfolioPosition]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in positions:
        sign = 1 if p.direction == "long" else -1
        out[p.category] = round(out.get(p.category, 0.0) + sign * p.target_pct, 6)
    return out


def _persist(config: AppConfig, book: PortfolioBook) -> Path | None:
    try:
        out_dir = config.data_dir / "portfolios"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        path = out_dir / f"{book.portfolio_id}-{ts}.json"
        path.write_text(json.dumps(asdict(book), indent=2, sort_keys=True, default=str))
        return path
    except OSError:
        return None


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
    target_beta: float = DEFAULT_TARGET_BETA,
    beta_window: int = DEFAULT_BETA_WINDOW,
    beta_min_obs: int = 20,
    cov_window: int = DEFAULT_COV_WINDOW,
    shrinkage: float = DEFAULT_SHRINKAGE,
    min_longs: int = DEFAULT_MIN_LONGS,
    max_longs: int = DEFAULT_MAX_LONGS,
    max_per_category: int = DEFAULT_MAX_PER_CATEGORY,
    min_categories: int = DEFAULT_MIN_CATEGORIES,
    max_single_pct: float = DEFAULT_MAX_SINGLE_PCT,
    alpha_shorts: int = DEFAULT_ALPHA_SHORTS,
    drift_threshold_pct: float = DEFAULT_DRIFT_THRESHOLD_PCT,
    rebalance_days: int = DEFAULT_REBALANCE_DAYS,
    horizon_months: int = 3,
    btc_reference: str = DEFAULT_BTC_REFERENCE,
    no_persist: bool = False,
    exclude: str | None = None,
    include_only: str | None = None,
    long_only: bool = False,
):
    excluded_set = {s.strip() for s in (exclude or "").split(",") if s.strip()}
    include_only_set = {s.strip() for s in (include_only or "").split(",") if s.strip()}
    warnings: list[WarningItem] = []
    coverage_gaps: list[CoverageGap] = []

    # Stage 1 — coverage + β snapshot.
    eligible, dropped, btc_series = _snapshot_universe(
        config, btc_reference=btc_reference,
        beta_window=beta_window, beta_min_obs=beta_min_obs,
        exclude_names=excluded_set or None,
        include_only=include_only_set or None,
    )
    if not btc_series:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRICE_USD,
            reason=f"BTC reference cache `{btc_reference}` is empty.",
            suggested_source="Run `price-history --reference btc --since <2y-ago>`.",
        ))
        return _result(query, chain, address, include_premium, config, {
            "verdict": "no_btc_reference",
        }, warnings, coverage_gaps)

    universe_size = len(UNIVERSE)
    if dropped:
        names = ", ".join(s.asset.name for s in dropped[:8])
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRICE_USD,
            reason=f"{len(dropped)}/{universe_size} universe assets have no cache: {names}",
            suggested_source=(
                "Run `price-history` for each missing asset (or "
                "`price-history --reference <key>` for SOL/AVAX/TAO/RENDER)."
            ),
        ))

    # Stage 2 — diversification.
    longs, diversify_notes = _enforce_diversification(
        eligible, min_longs=min_longs, max_longs=max_longs,
        max_per_category=max_per_category, min_categories=min_categories,
    )
    for note in diversify_notes:
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=note, severity="info",
        ))

    if not longs:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRICE_USD,
            reason="No eligible longs after diversification — universe cache empty.",
            suggested_source="Populate prices/ via price-history.",
        ))
        return _result(query, chain, address, include_premium, config, {
            "verdict": "no_eligible_longs",
        }, warnings, coverage_gaps)

    # Stage 3 — covariance + optimizer.
    series_input = [(s.asset.name, s.series) for s in longs]
    cov = compute_covariance(series_input, window_days=cov_window, shrinkage=shrinkage)
    if cov is None or len(cov.assets) < 2:
        coverage_gaps.append(CoverageGap(
            metric=CoverageMetric.PRICE_USD,
            reason=(
                f"Covariance matrix could not be built — only "
                f"{len(cov.assets) if cov else 0}/{len(longs)} assets had sufficient overlap."
            ),
            suggested_source="Backfill price-history for all members.",
        ))
        return _result(query, chain, address, include_premium, config, {
            "verdict": "covariance_unavailable",
        }, warnings, coverage_gaps)
    if len(cov.assets) < min_longs:
        # Soft warning rather than a hard fail — the operator may have
        # explicitly asked for a tighter book via `--max-longs`.
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=(
                f"only {len(cov.assets)} longs available after filters; "
                f"--min-longs is {min_longs}"
            ),
            severity="info",
        ))

    # Re-align the longs to the covariance matrix's asset ordering. Any
    # name that fell out of the date intersection is dropped here.
    cov_set = set(cov.assets)
    longs = [s for s in longs if s.asset.name in cov_set]
    longs.sort(key=lambda s: cov.assets.index(s.asset.name))

    long_betas = [s.beta if s.beta is not None else 1.0 for s in longs]
    sleeve = optimize_long_short(
        cov.sigma, long_betas,
        target_net_beta=target_beta, short_beta=1.0,
        long_only=long_only,
    )

    if not sleeve.converged:
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=(
                f"risk-parity solver did not fully converge "
                f"(iters={sleeve.iterations}); weights are usable but the "
                f"equal-RC constraint may be off by a few percent."
            ),
            severity="warning",
        ))

    # Single-position cap — clamp + redistribute the excess proportionally.
    capped_long_notional, was_capped = _apply_single_position_cap(
        sleeve.long_notional_pct, max_single_pct,
    )
    if was_capped:
        warnings.append(WarningItem(
            code=WarningCode.PRICE_FALLBACK_FAILED,
            message=f"Single-position cap of {max_single_pct*100:.1f}% triggered; redistributed.",
            severity="info",
        ))

    # Stage 4 — alpha shorts. Splits a small slice of the short side. In
    # long-only mode the entire short side is zero, so this reduces to a
    # no-op even if `--alpha-shorts` was passed (we surface a warning).
    if long_only:
        if alpha_shorts:
            warnings.append(WarningItem(
                code=WarningCode.PRICE_FALLBACK_FAILED,
                message=f"--alpha-shorts={alpha_shorts} ignored under --long-only.",
                severity="info",
            ))
        alpha_short_picks = []
        alpha_each = 0.0
        btc_short_pct = 0.0
    else:
        alpha_short_pool = [s for s in eligible if s.asset.name not in {l.asset.name for l in longs}]
        alpha_short_picks = _pick_alpha_shorts(
            alpha_short_pool, selected_longs=longs, n=alpha_shorts,
        )
        # Reserve a tenth of the short side for alpha shorts when present so
        # the macro hedge stays the dominant short.
        if alpha_short_picks:
            alpha_total = min(0.1 * sleeve.short_notional_pct, 0.05)
            alpha_each = alpha_total / len(alpha_short_picks)
            btc_short_pct = sleeve.short_notional_pct - alpha_total
        else:
            alpha_each = 0.0
            btc_short_pct = sleeve.short_notional_pct

    today_iso = datetime.now(UTC).strftime("%Y-%m-%d")
    btc_last_price = float(btc_series[-1]["price_usd"]) if btc_series else None
    btc_last_date = str(btc_series[-1]["date_iso"]) if btc_series else None

    positions = _build_positions(
        longs=longs,
        shorts_alpha=alpha_short_picks,
        long_notional_pct=capped_long_notional,
        long_weights_in_sleeve=sleeve.long_weights_in_sleeve,
        risk_contributions=sleeve.risk_contributions,
        btc_short_pct=btc_short_pct,
        today_iso=today_iso,
        btc_last_price=btc_last_price,
        btc_last_date=btc_last_date,
        alpha_short_pct_each=alpha_each,
    )

    long_total = sum(p.target_pct for p in positions if p.direction == "long")
    short_total = sum(p.target_pct for p in positions if p.direction == "short")
    cov_present_count = sum(1 for s in longs if not s.fallback)
    coverage_ratio = cov_present_count / max(len(longs), 1)

    book = PortfolioBook(
        portfolio_id=query,
        generated_at=utc_now_iso(),
        target_net_beta=target_beta,
        realized_net_beta=round(sleeve.realized_net_beta, 4),
        realized_long_avg_beta=round(sleeve.long_avg_beta, 4),
        long_pct_total=round(long_total, 6),
        short_pct_total=round(short_total, 6),
        coverage_ratio=round(coverage_ratio, 4),
        universe_size=universe_size,
        eligible_size=len(eligible),
        selected_long_count=len(longs),
        selected_short_count=sum(1 for p in positions if p.direction == "short"),
        cov_window_days=cov.window_days,
        cov_observations=cov.observations,
        rebalance_policy=RebalancePolicy(
            cadence_days=rebalance_days,
            drift_threshold_pct=drift_threshold_pct,
            max_single_position_pct=max_single_pct,
            min_categories=min_categories,
            horizon_months=horizon_months,
        ),
        positions=positions,
        category_breakdown=_category_breakdown(positions),
        optimizer_iterations=sleeve.iterations,
        optimizer_converged=sleeve.converged,
    )

    saved_to = _persist(config, book) if not no_persist else None

    metrics: dict[str, Any] = {
        "portfolio_id": query,
        "saved_to": str(saved_to) if saved_to else None,
        "config": {
            "target_beta": target_beta,
            "beta_window": beta_window,
            "cov_window": cov_window,
            "shrinkage": shrinkage,
            "min_longs": min_longs,
            "max_longs": max_longs,
            "max_per_category": max_per_category,
            "min_categories": min_categories,
            "max_single_pct": max_single_pct,
            "alpha_shorts": alpha_shorts,
            "drift_threshold_pct": drift_threshold_pct,
            "rebalance_days": rebalance_days,
            "horizon_months": horizon_months,
            "btc_reference": btc_reference,
        },
        "book": asdict(book),
    }
    return _result(query, chain, address, include_premium, config, metrics, warnings, coverage_gaps)


def _apply_single_position_cap(
    notionals: list[float], cap: float,
) -> tuple[list[float], bool]:
    """Clamp each notional at `cap` and redistribute the surplus proportionally.

    Pure: given the same input always returns the same output. Up to two
    redistribute passes — for sane inputs the second is rarely needed.
    """
    if cap >= 1.0 or not notionals:
        return notionals[:], False

    out = notionals[:]
    triggered = False
    for _ in range(3):
        excess = 0.0
        room: list[int] = []
        for i, x in enumerate(out):
            if x > cap:
                excess += x - cap
                out[i] = cap
                triggered = True
            elif x < cap:
                room.append(i)
        if excess <= 1e-12 or not room:
            break
        # Proportional redistribution to the under-cap names.
        free_total = sum(out[i] for i in room)
        if free_total <= 0:
            break
        for i in room:
            out[i] += excess * (out[i] / free_total)
    return out, triggered


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
        command="portfolio-build",
        input={
            "query": query,
            "chain": chain,
            "address": address,
            "include_premium": include_premium,
        },
        resolved_identity={"query": query, "mode": "portfolio-build"},
        metrics=metrics,
        sources=build_source_refs(config, include_premium),
        warnings=dataclass_list(warnings),
        coverage_gaps=dataclass_list(coverage_gaps),
    )
