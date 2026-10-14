"""Robust weighted statistics used across the analytics layer (numpy only, deterministic)."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

MAD_TO_SIGMA = 1.4826  # scales MAD to be a consistent estimator of sigma for normal data


def _arr(values: Sequence[float], weights: Sequence[float] | None) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(values, dtype=float)
    w = np.ones_like(v) if weights is None else np.asarray(weights, dtype=float)
    if v.shape != w.shape:
        raise ValueError("values and weights must have the same length")
    mask = np.isfinite(v) & np.isfinite(w) & (w > 0)
    return v[mask], w[mask]


def weighted_quantile(values: Sequence[float], q: float, weights: Sequence[float] | None = None) -> float:
    """Weighted quantile via the midpoint-CDF interpolation (equals np.quantile for equal weights)."""
    v, w = _arr(values, weights)
    if v.size == 0:
        return float("nan")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cdf = (np.cumsum(w) - 0.5 * w) / w.sum()
    return float(np.interp(q, cdf, v))


def weighted_median(values: Sequence[float], weights: Sequence[float] | None = None) -> float:
    return weighted_quantile(values, 0.5, weights)


def weighted_mean(values: Sequence[float], weights: Sequence[float] | None = None) -> float:
    v, w = _arr(values, weights)
    return float(np.average(v, weights=w)) if v.size else float("nan")


def mad(values: Sequence[float]) -> float:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan")
    return float(np.median(np.abs(v - np.median(v))))


def robust_z(x: float, values: Sequence[float]) -> float:
    """(x - median) / (1.4826 * MAD); falls back to IQR/1.349 when MAD is 0 (many identical prices)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size < 3:
        return float("nan")
    med = float(np.median(v))
    scale = MAD_TO_SIGMA * mad(v)
    if scale == 0:
        q1, q3 = np.quantile(v, [0.25, 0.75])
        scale = float(q3 - q1) / 1.349
    if scale == 0:
        return 0.0 if x == med else float("inf") * np.sign(x - med)
    return (x - med) / scale


def iqr_bounds(values: Sequence[float], k: float = 1.5) -> tuple[float, float]:
    q1, q3 = np.quantile(np.asarray(values, dtype=float), [0.25, 0.75])
    return float(q1 - k * (q3 - q1)), float(q3 + k * (q3 - q1))
