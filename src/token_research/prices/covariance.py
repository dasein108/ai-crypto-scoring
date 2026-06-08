"""Daily-log-return covariance matrix with Ledoit–Wolf-style shrinkage.

Pure stdlib. Designed for the portfolio optimizer where we typically have
12–18 names × 60–120 daily observations — right at the edge where a raw
sample covariance is too noisy to be invertible. We shrink toward
`(avg_var) · I` with a fixed intensity (default 0.2), which gives
practically identical risk-parity weights to the full L–W estimator at
1/100th the implementation cost.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CovarianceMatrix:
    """Square covariance + the metadata callers need to reason about it.

    `assets` ordering is the only ground truth — sigma rows/cols match it
    1:1, and downstream code never indexes by name.
    """
    assets: tuple[str, ...]
    observations: int
    window_days: int
    shrinkage: float
    sigma: list[list[float]]   # ann-free; daily log-return scale

    def __getitem__(self, ij: tuple[int, int]) -> float:
        i, j = ij
        return self.sigma[i][j]

    def diagonal(self) -> list[float]:
        return [self.sigma[i][i] for i in range(len(self.assets))]


def _log_returns(rows: list[dict[str, Any]]) -> dict[str, float]:
    """date_iso → log-return for that day. Skips zero/negative prices."""
    out: dict[str, float] = {}
    if len(rows) < 2:
        return out
    for prev, curr in zip(rows, rows[1:]):
        p, c = float(prev["price_usd"]), float(curr["price_usd"])
        if p <= 0 or c <= 0:
            continue
        out[str(curr["date_iso"])] = math.log(c / p)
    return out


def _aligned_returns(
    series_by_asset: list[tuple[str, list[dict[str, Any]]]],
    *,
    window_days: int,
) -> tuple[list[str], list[list[float]]]:
    """Return (asset_names, return_matrix) using the dates common to all assets.

    Layout: rows = assets, cols = trailing `window_days` daily returns
    (most recent last). Assets with no overlapping data are *dropped* —
    caller surfaces that as a coverage gap.
    """
    return_maps: list[tuple[str, dict[str, float]]] = []
    for name, rows in series_by_asset:
        rmap = _log_returns(rows)
        if rmap:
            return_maps.append((name, rmap))

    if not return_maps:
        return [], []

    # Date intersection across all assets that have any data.
    common: set[str] | None = None
    for _, rmap in return_maps:
        s = set(rmap)
        common = s if common is None else (common & s)
    if not common:
        return [], []

    dates = sorted(common)
    if window_days > 0:
        dates = dates[-window_days:]
    if len(dates) < 2:
        return [], []

    names = [n for n, _ in return_maps]
    matrix = [[rmap[d] for d in dates] for _, rmap in return_maps]
    return names, matrix


def _row_mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def sample_covariance(matrix: list[list[float]]) -> list[list[float]]:
    """Sample covariance (Bessel-corrected). `matrix[i]` is asset i's returns."""
    n_assets = len(matrix)
    if n_assets == 0:
        return []
    n_obs = len(matrix[0])
    if n_obs < 2:
        return [[0.0] * n_assets for _ in range(n_assets)]
    means = [_row_mean(row) for row in matrix]
    sigma = [[0.0] * n_assets for _ in range(n_assets)]
    for i in range(n_assets):
        for j in range(i, n_assets):
            s = 0.0
            mi, mj = means[i], means[j]
            for k in range(n_obs):
                s += (matrix[i][k] - mi) * (matrix[j][k] - mj)
            cov = s / (n_obs - 1)
            sigma[i][j] = cov
            sigma[j][i] = cov
    return sigma


def shrink_to_identity(
    sample: list[list[float]],
    *,
    intensity: float,
) -> list[list[float]]:
    """Linear shrinkage toward `mean_var · I`.

    `intensity ∈ [0, 1]`: 0 returns the raw sample, 1 returns the diagonal
    `mean_var · I`. Default 0.2 in the optimizer is empirically robust at
    14×120 samples without throwing away too much off-diagonal signal.
    """
    n = len(sample)
    if n == 0 or intensity <= 0:
        return [row[:] for row in sample]
    if intensity >= 1:
        intensity = 1.0
    mean_var = sum(sample[i][i] for i in range(n)) / n
    out = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            target = mean_var if i == j else 0.0
            out[i][j] = (1 - intensity) * sample[i][j] + intensity * target
    return out


def compute_covariance(
    series_by_asset: list[tuple[str, list[dict[str, Any]]]],
    *,
    window_days: int = 90,
    shrinkage: float = 0.2,
) -> CovarianceMatrix | None:
    """End-to-end: align returns → sample cov → shrink. Returns None when
    no usable overlap exists.
    """
    names, matrix = _aligned_returns(series_by_asset, window_days=window_days)
    if not names or not matrix:
        return None
    sample = sample_covariance(matrix)
    sigma = shrink_to_identity(sample, intensity=shrinkage)
    return CovarianceMatrix(
        assets=tuple(names),
        observations=len(matrix[0]),
        window_days=window_days,
        shrinkage=shrinkage,
        sigma=sigma,
    )
