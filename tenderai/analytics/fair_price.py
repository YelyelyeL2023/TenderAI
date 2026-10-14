"""Fair Price metric.

Formal definition (also in docs/analytics.md). For a target item with ENSTRU code e, normalised unit u,
delivery location (city KATO c, oblast r) and date t, given comparable observations i with unit price p_i:

  1. Comparable set S: same e and u, other customers, ±18 months around t, DQ-clean.
     Hierarchy (first level with N >= MIN_N wins): same city -> same oblast -> national -> ENSTRU prefix.
  2. Time adjustment:     p_i'  = p_i * I(t) / I(t_i)            I = CPI (official) or internal price index
                          p_i'  = p_i' * S(m_t) / S(m_i)          S = monthly seasonal index (only if N >= 30)
  3. Region adjustment:   p_i'' = p_i' * K(r) / K(r_i)            K(r) = shrunk ratio of regional to national median
  4. FairPrice = median(p_i''), fair band = [Q1, Q3] of p_i''
  5. deviation = (p - FairPrice) / FairPrice,  robust z = (p - median) / (1.4826 * MAD)
  6. verdict by thresholds; confidence from N, dispersion, match level, DQ.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np

from tenderai.analytics.price_index import cpi_category
from tenderai.analytics.stats import robust_z, weighted_mean

MIN_N = 5
SEASONAL_MIN_N = 30
SEASONAL_MIN_MONTHS = 6
REGION_SHRINK_K = 10.0
TIME_WINDOW_MONTHS = 18

FAIR_TOLERANCE = 0.10      # within ±10% of fair price (or inside IQR band) -> fair
ANOMALY_THRESHOLD = 0.30   # the assignment's "> 30%" rule
ROBUST_Z_THRESHOLD = 3.0


@dataclass(frozen=True)
class Observation:
    obj_id: str                 # e.g. "contract_unit:54520936"
    price: float                # unit price, KZT, without VAT
    quantity: float
    enstru: str
    unit: str                   # normalised unit code
    month: date                 # first day of month of the price (sign date / publish date)
    customer_bin: str
    city_kato: str | None = None   # KATO of the delivery settlement (9 digits)
    region: str | None = None      # oblast = first 2 digits of KATO
    source: str = "contract"       # contract | lot | plan


@dataclass
class FairPriceResult:
    target_price: float
    fair_price: float
    band_low: float
    band_high: float
    weighted_mean: float
    n: int
    deviation: float
    robust_z: float
    verdict: str
    confidence: float
    confidence_label: str
    comparison_level: str
    adjustments: dict = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    sample_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def month_diff(a: date, b: date) -> int:
    return (a.year - b.year) * 12 + (a.month - b.month)


PriceIndex = Callable[[date, str | None, str], float]   # level for (month, region, CPI category), any base


def enstru_prefixes(code: str) -> list[str]:
    """Progressively coarser ENSTRU prefixes for fallback matching.

    New format '106121.300.000001' -> ['106121.300', '106121']
    Old format '10.39.11.100.010.00.0166.000000000000' -> ['10.39.11.100.010', '10.39.11.100', '10.39.11']
    """
    parts = code.split(".")
    if len(parts) == 3 and len(parts[0]) == 6:          # new KTRU format
        return [f"{parts[0]}.{parts[1]}", parts[0]]
    out = []
    for k in (5, 4, 3):
        if len(parts) > k:
            out.append(".".join(parts[:k]))
    return out


def select_comparables(
    target: Observation, candidates: Sequence[Observation], min_n: int = MIN_N
) -> tuple[list[Observation], str]:
    """Apply the comparison hierarchy. Candidates must already share the normalised unit."""
    pool = [
        c for c in candidates
        if c.customer_bin != target.customer_bin
        and c.obj_id != target.obj_id
        and c.unit == target.unit
        and abs(month_diff(c.month, target.month)) <= TIME_WINDOW_MONTHS
        and c.price > 0
    ]
    exact = [c for c in pool if c.enstru == target.enstru]
    levels: list[tuple[str, list[Observation]]] = []
    if target.city_kato:
        levels.append(("same_city", [c for c in exact if c.city_kato == target.city_kato]))
    if target.region:
        levels.append(("same_region", [c for c in exact if c.region == target.region]))
    levels.append(("national", exact))
    for prefix in enstru_prefixes(target.enstru):
        levels.append((f"enstru_prefix:{prefix}", [c for c in pool if c.enstru.startswith(prefix + ".") or c.enstru == prefix]))
    for name, subset in levels:
        if len(subset) >= min_n:
            return subset, name
    best = max(levels, key=lambda lv: len(lv[1]))
    return best[1], best[0] + ":insufficient"


def seasonal_index(obs: Sequence[Observation], prices: Sequence[float]) -> dict[int, float] | None:
    """Monthly multiplicative index S(m) = shrunk median(month m) / median(all); None if data is too thin."""
    if len(obs) < SEASONAL_MIN_N:
        return None
    by_month: dict[int, list[float]] = defaultdict(list)
    for o, p in zip(obs, prices):
        by_month[o.month.month].append(p)
    if len(by_month) < SEASONAL_MIN_MONTHS:
        return None
    overall = float(np.median(prices))
    idx = {}
    for m, ps in by_month.items():
        w = len(ps) / (len(ps) + REGION_SHRINK_K)
        idx[m] = w * (float(np.median(ps)) / overall) + (1 - w)
    return idx


def regional_coefficients(obs: Sequence[Observation], prices: Sequence[float]) -> dict[str, float]:
    overall = float(np.median(prices))
    by_region: dict[str, list[float]] = defaultdict(list)
    for o, p in zip(obs, prices):
        if o.region:
            by_region[o.region].append(p)
    out = {}
    for r, ps in by_region.items():
        w = len(ps) / (len(ps) + REGION_SHRINK_K)
        out[r] = w * (float(np.median(ps)) / overall) + (1 - w)
    return out


def verdict_for(deviation: float, rz: float, in_band: bool) -> str:
    if deviation > ANOMALY_THRESHOLD or (math.isfinite(rz) and rz > ROBUST_Z_THRESHOLD):
        return "overpriced_anomaly"
    if deviation < -ANOMALY_THRESHOLD or (math.isfinite(rz) and rz < -ROBUST_Z_THRESHOLD):
        return "underpriced_anomaly"
    if in_band or abs(deviation) <= FAIR_TOLERANCE:
        return "fair"
    return "elevated" if deviation > 0 else "below_market"


def confidence_score(
    n: int, dispersion: float, level: str, dq_issue_rate: float, region_known: bool
) -> tuple[float, str, list[str]]:
    notes = []
    n_score = min(1.0, math.log(max(n, 1)) / math.log(30))
    disp_score = 1.0 / (1.0 + max(dispersion, 0.0))
    level_score = {"same_city": 1.0, "same_region": 0.9, "national": 0.75}.get(level.split(":")[0], 0.4)
    if level.startswith("enstru_prefix"):
        notes.append("сравнение по укрупнённому коду ЕНС ТРУ (точных аналогов недостаточно)")
    if level.endswith(":insufficient"):
        notes.append(f"выборка меньше минимума ({MIN_N}), вывод предварительный")
    if dispersion > 1.0:
        notes.append("высокий разброс цен аналогов (IQR/медиана > 100%)")
    if not region_known:
        notes.append("место поставки целевого объекта не указано — региональная поправка не применялась")
    dq_score = 1.0 - min(max(dq_issue_rate, 0.0), 1.0)
    score = 0.35 * n_score + 0.25 * disp_score + 0.2 * level_score + 0.1 * dq_score + 0.1 * (1.0 if region_known else 0.5)
    if n < MIN_N:
        score = min(score, 0.44)
    if dispersion > 1.0:  # comparables disagree too much for a "high" verdict whatever N is
        score = min(score, 0.69)
    label = "high" if score >= 0.7 else "medium" if score >= 0.45 else "low"
    return round(score, 3), label, notes


def evaluate_fair_price(
    target: Observation,
    candidates: Sequence[Observation],
    price_index: PriceIndex | None = None,
    price_index_name: str = "none",
    dq_issue_rate: float = 0.0,
) -> FairPriceResult | None:
    comps, level = select_comparables(target, candidates)
    if not comps:
        return None
    prices = np.array([c.price for c in comps], dtype=float)
    adjustments: dict = {"price_index": price_index_name}

    # 2. time: each comparable is moved to the target month with the CPI of ITS OWN region and the item's
    #    category (the regional coefficient below then handles price-level differences between regions)
    if price_index is not None:
        category = cpi_category(target.enstru, target.unit)
        factors = np.array([price_index(target.month, c.region, category) / price_index(c.month, c.region, category)
                            for c in comps])
        prices = prices * factors
        adjustments["cpi_category"] = category
        adjustments["time_factor_range"] = [round(float(factors.min()), 4), round(float(factors.max()), 4)]
    # 2b. seasonality
    s_idx = seasonal_index(comps, prices)
    if s_idx:
        st = s_idx.get(target.month.month, 1.0)
        prices = prices * np.array([st / s_idx.get(c.month.month, 1.0) for c in comps])
        adjustments["seasonal"] = {m: round(v, 4) for m, v in sorted(s_idx.items())}
    else:
        adjustments["seasonal"] = "not applied (insufficient data)"
    # 3. region (only meaningful when the comparison set spans several regions)
    k = regional_coefficients(comps, prices)
    if target.region and len(k) > 1:
        kt = k.get(target.region, 1.0)
        prices = prices * np.array([kt / k.get(c.region, 1.0) if c.region else 1.0 for c in comps])
        adjustments["regional_coefficient_target"] = round(kt, 4)
        adjustments["regions_in_sample"] = len(k)
    else:
        adjustments["regional_coefficient_target"] = "not applied (single region)"

    fair = float(np.median(prices))
    q1, q3 = (float(x) for x in np.quantile(prices, [0.25, 0.75]))
    dev = (target.price - fair) / fair if fair > 0 else float("nan")
    rz = robust_z(target.price, prices)
    verdict = verdict_for(dev, rz, q1 <= target.price <= q3)
    dispersion = (q3 - q1) / fair if fair > 0 else float("inf")
    conf, label, notes = confidence_score(len(comps), dispersion, level, dq_issue_rate, bool(target.region))
    if price_index is None:
        notes.append("инфляционная поправка не применялась")
    order = np.argsort(np.abs(prices - fair))
    return FairPriceResult(
        target_price=target.price,
        fair_price=round(fair, 2),
        band_low=round(q1, 2),
        band_high=round(q3, 2),
        weighted_mean=round(weighted_mean(prices, [c.quantity for c in comps]), 2),
        n=len(comps),
        deviation=round(dev, 4),
        robust_z=round(rz, 2) if math.isfinite(rz) else rz,
        verdict=verdict,
        confidence=conf,
        confidence_label=label,
        comparison_level=level,
        adjustments=adjustments,
        limitations=notes,
        sample_ids=[comps[i].obj_id for i in order],
    )
