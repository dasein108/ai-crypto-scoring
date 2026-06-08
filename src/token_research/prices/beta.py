"""Beta-vs-BTC computation from cached daily price series.

Pure stdlib (no numpy). Given two `[{date_iso, price_usd}]` series — typically
the token and a BTC reference — aligns them on date_iso, computes daily log
returns over the trailing window, and runs OLS:

    β = cov(r_token, r_btc) / var(r_btc)

Returns `None` rather than a degenerate value when:
- fewer than `min_observations` overlapping days are available
- BTC return variance is zero (markets closed / synthetic flat path)

Callers fall back to a documented default (typically 1.0) and record a
coverage gap.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


# Reference cache key for BTC. The canonical price is Binance `BTC/USDT`,
# fetched directly into `prices/_reference/btc.json` rather than going
# through a wrapped-token (WBTC) proxy.
DEFAULT_BTC_REFERENCE = "btc"

DEFAULT_WINDOW_DAYS = 60
DEFAULT_MIN_OBSERVATIONS = 20


@dataclass(frozen=True, slots=True)
class BetaResult:
    beta: float | None
    observations: int
    window_days: int
    correlation: float | None
    fallback_used: bool
    reason: str | None


def _log_return(p_prev: float, p_curr: float) -> float | None:
    if p_prev <= 0 or p_curr <= 0:
        return None
    return math.log(p_curr / p_prev)


def _aligned_log_returns(
    a: list[dict[str, Any]],
    b: list[dict[str, Any]],
    *,
    window_days: int,
) -> tuple[list[float], list[float]]:
    """Return paired daily log-returns for the trailing `window_days` days
    of dates that exist in *both* series. Ordering preserved."""
    if not a or not b:
        return [], []

    a_map = {str(r["date_iso"]): float(r["price_usd"]) for r in a if r.get("price_usd") is not None}
    b_map = {str(r["date_iso"]): float(r["price_usd"]) for r in b if r.get("price_usd") is not None}
    common_dates = sorted(set(a_map) & set(b_map))
    if window_days > 0:
        common_dates = common_dates[-(window_days + 1):]
    if len(common_dates) < 2:
        return [], []

    ra: list[float] = []
    rb: list[float] = []
    for prev_date, curr_date in zip(common_dates, common_dates[1:]):
        rp_a = _log_return(a_map[prev_date], a_map[curr_date])
        rp_b = _log_return(b_map[prev_date], b_map[curr_date])
        if rp_a is None or rp_b is None:
            continue
        ra.append(rp_a)
        rb.append(rp_b)
    return ra, rb


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _covariance(xs: list[float], ys: list[float]) -> float:
    """Sample covariance (Bessel-corrected). Returns 0 when fewer than 2 points."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mx, my = _mean(xs), _mean(ys)
    return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)


def _variance(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def compute_beta(
    token_series: list[dict[str, Any]],
    btc_series: list[dict[str, Any]],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> BetaResult:
    """β regression over the trailing daily log-returns.

    `token_series` / `btc_series` are price-cache rows. Date overlap is
    enforced inside `_aligned_log_returns` so a misaligned BTC reference
    (e.g. cached for a different range) still produces a sensible answer.
    """
    if not token_series or not btc_series:
        return BetaResult(
            beta=None, observations=0, window_days=window_days,
            correlation=None, fallback_used=True, reason="empty_series",
        )

    ra, rb = _aligned_log_returns(token_series, btc_series, window_days=window_days)
    n = len(ra)
    if n < min_observations:
        return BetaResult(
            beta=None, observations=n, window_days=window_days,
            correlation=None, fallback_used=True,
            reason=f"insufficient_overlap ({n} < {min_observations})",
        )

    var_btc = _variance(rb)
    if var_btc <= 0:
        return BetaResult(
            beta=None, observations=n, window_days=window_days,
            correlation=None, fallback_used=True, reason="btc_variance_zero",
        )

    cov_tb = _covariance(ra, rb)
    var_token = _variance(ra)
    beta = cov_tb / var_btc

    if var_token > 0:
        denom = math.sqrt(var_token * var_btc)
        corr = cov_tb / denom if denom > 0 else None
    else:
        corr = None

    return BetaResult(
        beta=round(beta, 4),
        observations=n,
        window_days=window_days,
        correlation=round(corr, 4) if corr is not None else None,
        fallback_used=False,
        reason=None,
    )
