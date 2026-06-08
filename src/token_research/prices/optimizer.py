"""Portfolio optimizer — risk-parity inside the long sleeve plus a
closed-form BTC-perp short that brings net β to a configurable target.

The book is a self-funded 100%-gross structure (long_pct + short_pct = 1.0,
no leverage), which means once the optimizer fixes one of {long_pct,
short_pct, target_β} the other two are determined. We hold the user's
target β as the constraint and solve for the long/short split.

Pure stdlib; no scipy, no numpy. The risk-parity solver is a fixed-point
iteration on the marginal-risk-contribution identity that converges in
20-50 iterations for the matrix sizes we use (≤ 20 assets).
"""
from __future__ import annotations

import math
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Risk parity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RiskParityResult:
    weights: list[float]            # sums to 1.0 inside the leg
    risk_contributions: list[float] # MRC × weight, sums to total portfolio variance
    iterations: int
    converged: bool
    final_dispersion: float


def _matvec(sigma: list[list[float]], w: list[float]) -> list[float]:
    n = len(sigma)
    return [sum(sigma[i][j] * w[j] for j in range(n)) for i in range(n)]


def solve_risk_parity(
    sigma: list[list[float]],
    *,
    max_iter: int = 500,
    tol: float = 1e-7,
) -> RiskParityResult:
    """Equal-risk-contribution weights for a long-only sleeve.

    Iteration scheme: at each step every weight is rescaled so its
    marginal contribution moves toward the cross-sectional average.
    Converges to the unique long-only ERC point as long as `sigma` is
    PSD with strictly positive diagonal.

    `weights` sums to 1.0 — caller scales to its dollar gross.
    """
    n = len(sigma)
    if n == 0:
        return RiskParityResult([], [], 0, True, 0.0)
    if n == 1:
        return RiskParityResult([1.0], [sigma[0][0]], 0, True, 0.0)

    # Seed: inverse-vol weights — closer to ERC than equal-weight.
    diag = [max(sigma[i][i], 1e-12) for i in range(n)]
    inv_vol = [1.0 / math.sqrt(v) for v in diag]
    s = sum(inv_vol)
    w = [iv / s for iv in inv_vol]

    target = 1.0 / n  # equal share of total risk per asset
    last_disp = float("inf")
    for it in range(max_iter):
        Sw = _matvec(sigma, w)
        port_var = sum(w[i] * Sw[i] for i in range(n))
        if port_var <= 0:
            break
        # Per-asset risk contribution share.
        rc_share = [(w[i] * Sw[i]) / port_var for i in range(n)]
        disp = max(abs(rc - target) for rc in rc_share)
        if disp < tol:
            return RiskParityResult(
                weights=w,
                risk_contributions=[w[i] * Sw[i] for i in range(n)],
                iterations=it,
                converged=True,
                final_dispersion=disp,
            )
        # Multiplicative update — each weight scaled by sqrt(target / rc).
        # Damped to prevent oscillation.
        damping = 0.5
        new_w = [
            w[i] * (1.0 + damping * (math.sqrt(target / max(rc_share[i], 1e-18)) - 1.0))
            for i in range(n)
        ]
        # Re-normalize to sum 1.
        ssum = sum(new_w)
        if ssum <= 0:
            break
        w = [x / ssum for x in new_w]
        last_disp = disp

    Sw = _matvec(sigma, w)
    return RiskParityResult(
        weights=w,
        risk_contributions=[w[i] * Sw[i] for i in range(n)],
        iterations=max_iter,
        converged=False,
        final_dispersion=last_disp,
    )


# ---------------------------------------------------------------------------
# Macro hedge sizing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HedgeSize:
    long_pct: float    # share of total gross going long
    short_pct: float   # share of total gross going short (positive number)
    realized_long_pct_of_capital: float
    realized_short_pct_of_capital: float
    realized_net_beta: float
    realized_long_avg_beta: float

    def assert_invariants(self) -> None:
        """Self-funded book — long_pct + short_pct = 1.0 always."""
        assert abs((self.long_pct + self.short_pct) - 1.0) < 1e-9, (
            f"long+short ≠ 1: {self.long_pct} + {self.short_pct}"
        )


def _weighted_beta(weights: list[float], betas: list[float]) -> float:
    if not weights:
        return 0.0
    return sum(w * b for w, b in zip(weights, betas))


def solve_self_funded_hedge(
    long_weights: list[float],
    long_betas: list[float],
    *,
    target_net_beta: float,
    short_beta: float = 1.0,
) -> HedgeSize:
    """Solve long%/short% so that

        long_pct + short_pct = 1                             (self-funded, gross 100%)
        long_pct·β_long_avg − short_pct·β_short = target_β

    With β_short ≈ 1 (BTC-perp), the closed form is

        long_pct  = (target_β + β_short) / (β_long_avg + β_short)
        short_pct = 1 − long_pct

    Caveats:
        - When β_long_avg ≤ target_β the long leg already exceeds the
          target and a short of zero suffices; we clamp short_pct ≥ 0.
        - When the target is unreachable (target_β > β_long_avg) we
          still return the boundary solution `short_pct = 0`,
          `long_pct = 1` and let the build command surface a warning.
    """
    if not long_weights:
        return HedgeSize(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    long_avg_beta = _weighted_beta(long_weights, long_betas)
    denom = long_avg_beta + short_beta
    if denom <= 0:
        # Degenerate (impossible for sane β values); skip the hedge.
        return HedgeSize(1.0, 0.0, 1.0, 0.0, long_avg_beta, long_avg_beta)

    long_pct = (target_net_beta + short_beta) / denom
    long_pct = max(0.0, min(1.0, long_pct))
    short_pct = 1.0 - long_pct

    realized_net = long_pct * long_avg_beta - short_pct * short_beta
    return HedgeSize(
        long_pct=long_pct,
        short_pct=short_pct,
        realized_long_pct_of_capital=long_pct,
        realized_short_pct_of_capital=short_pct,
        realized_net_beta=realized_net,
        realized_long_avg_beta=long_avg_beta,
    )


# ---------------------------------------------------------------------------
# End-to-end helper
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OptimizedSleeve:
    long_weights_in_sleeve: list[float]   # sum = 1
    long_notional_pct: list[float]        # sum = long_pct
    short_notional_pct: float             # the BTC-perp slot
    risk_contributions: list[float]
    iterations: int
    converged: bool
    long_avg_beta: float
    realized_net_beta: float


def optimize_long_short(
    sigma: list[list[float]],
    long_betas: list[float],
    *,
    target_net_beta: float,
    short_beta: float = 1.0,
    risk_parity_kwargs: dict | None = None,
    long_only: bool = False,
) -> OptimizedSleeve:
    """End-to-end optimizer used by `portfolio-build`.

    Steps:
        1. Risk-parity weights inside the long sleeve.
        2. Closed-form hedge sizing for net β = target (skipped if `long_only`).
        3. Scale the long weights so they sum to long_pct (not 1.0).

    `long_only=True` collapses to: 100% long, no hedge, net β = realized
    long-leg β. Use this when the operator wants pure market exposure
    sized by risk parity, no short side at all.
    """
    rp = solve_risk_parity(sigma, **(risk_parity_kwargs or {}))
    if long_only:
        long_avg = _weighted_beta(rp.weights, long_betas)
        hedge = HedgeSize(
            long_pct=1.0,
            short_pct=0.0,
            realized_long_pct_of_capital=1.0,
            realized_short_pct_of_capital=0.0,
            realized_net_beta=long_avg,
            realized_long_avg_beta=long_avg,
        )
    else:
        hedge = solve_self_funded_hedge(
            rp.weights, long_betas,
            target_net_beta=target_net_beta, short_beta=short_beta,
        )
    long_notional = [w * hedge.long_pct for w in rp.weights]
    return OptimizedSleeve(
        long_weights_in_sleeve=rp.weights,
        long_notional_pct=long_notional,
        short_notional_pct=hedge.short_pct,
        risk_contributions=rp.risk_contributions,
        iterations=rp.iterations,
        converged=rp.converged,
        long_avg_beta=hedge.realized_long_avg_beta,
        realized_net_beta=hedge.realized_net_beta,
    )
