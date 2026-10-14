"""Abnormal year-over-year increase of planned quantities (customer x ENSTRU x unit).

Primary rule (explainable):  ratio = qty_Y / median(qty in previous years)  > RATIO_THRESHOLD
                             and the year's quantity is a robust outlier vs that customer's history
                             (robust z on log-quantities > Z_THRESHOLD, when >= 2 previous years exist).
Confirmed tier: flagged AND (robust z > 3 with >= 2 previous years, or ratio >= 3 with one previous year)
                AND the extra planned spend (amount - amount / ratio) >= 1,000,000 KZT.
Secondary signal: Isolation Forest over [log ratio, log amount ratio, log unit-price ratio, n plan points]
fitted across all customers — reported as an extra score, never used alone to flag.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

RATIO_THRESHOLD = 1.5
Z_THRESHOLD = 3.0
MIN_PREV_QTY = 0.0
# confirmation tier: statistically extreme growth AND material in money terms
CONFIRM_RATIO_SINGLE_YEAR = 3.0     # with one previous year there is no z-score: require >= 3x growth
MIN_EXTRA_AMOUNT_KZT = 1_000_000


@dataclass
class VolumeRow:
    customer_bin: str
    enstru_code: str
    base_unit: str
    year: int
    quantity: float
    amount: float
    n_points: int
    name_clean: str | None = None
    plan_point_ids: list[int] = field(default_factory=list)


@dataclass
class VolumeAnomaly:
    customer_bin: str
    enstru_code: str
    base_unit: str
    name_clean: str | None
    year: int
    quantity: float
    baseline_quantity: float
    previous: dict[int, float]
    ratio: float
    robust_z: float | None
    amount: float
    if_score: float | None
    flagged: bool
    plan_point_ids: list[int]
    confirmed: bool = False

    @property
    def extra_amount(self) -> float:
        return self.amount - self.amount / self.ratio if self.ratio > 0 else 0.0


def _robust_z_log(x: float, hist: list[float]) -> float | None:
    if len(hist) < 2:
        return None
    logs = np.log(np.asarray(hist))
    med = float(np.median(logs))
    mad = float(np.median(np.abs(logs - med)))
    scale = 1.4826 * mad
    if scale == 0:
        # identical history: any growth beyond the threshold is a clear break from a flat pattern
        return math.inf if math.log(x) > med else 0.0
    return (math.log(x) - med) / scale


def detect_volume_anomalies(rows: list[VolumeRow], ratio_threshold: float = RATIO_THRESHOLD) -> list[VolumeAnomaly]:
    series: dict[tuple[str, str, str], dict[int, VolumeRow]] = defaultdict(dict)
    for r in rows:
        series[(r.customer_bin, r.enstru_code, r.base_unit)][r.year] = r
    out: list[VolumeAnomaly] = []
    for (bin_, enstru, unit), by_year in series.items():
        for year, r in by_year.items():
            prev = {y: v.quantity for y, v in by_year.items() if y < year and v.quantity > MIN_PREV_QTY}
            if not prev or r.quantity <= 0:
                continue
            baseline = float(np.median(list(prev.values())))
            ratio = r.quantity / baseline if baseline > 0 else math.inf
            rz = _robust_z_log(r.quantity, list(prev.values()))
            flagged = ratio > ratio_threshold and (rz is None or rz > Z_THRESHOLD)
            a = VolumeAnomaly(bin_, enstru, unit, r.name_clean, year, r.quantity, baseline, prev, ratio,
                              rz, r.amount, None, flagged, r.plan_point_ids)
            extreme = (rz is not None and rz > Z_THRESHOLD) or (rz is None and ratio >= CONFIRM_RATIO_SINGLE_YEAR)
            a.confirmed = flagged and extreme and a.extra_amount >= MIN_EXTRA_AMOUNT_KZT
            out.append(a)
    _isolation_forest(out, series)
    return out


def _isolation_forest(items: list[VolumeAnomaly], series) -> None:
    if len(items) < 50:
        return
    from sklearn.ensemble import IsolationForest

    feats = []
    for a in items:
        prev_rows = [series[(a.customer_bin, a.enstru_code, a.base_unit)][y] for y in a.previous]
        prev_amt = float(np.median([p.amount for p in prev_rows])) or 1.0
        prev_up = float(np.median([p.amount / p.quantity for p in prev_rows if p.quantity > 0])) or 1.0
        cur_up = a.amount / a.quantity if a.quantity > 0 else prev_up
        n_points = series[(a.customer_bin, a.enstru_code, a.base_unit)][a.year].n_points
        feats.append([math.log(max(a.ratio, 1e-6)), math.log(max(a.amount, 1.0) / max(prev_amt, 1.0)),
                      math.log(max(cur_up, 1e-6) / max(prev_up, 1e-6)), n_points])
    X = np.nan_to_num(np.asarray(feats, dtype=float), posinf=20.0, neginf=-20.0)
    model = IsolationForest(n_estimators=200, contamination="auto", random_state=42).fit(X)
    scores = -model.score_samples(X)  # higher = more anomalous
    for a, s in zip(items, scores):
        a.if_score = round(float(s), 4)
