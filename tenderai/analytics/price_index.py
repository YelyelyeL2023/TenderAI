"""Price indices for the time (inflation) adjustment.

Preference order:
  1. Official CPI (core.cpi, stat.gov.kz bulletins): fixed-base level by month × region (2-digit KATO) × category
     (food | nonfood | services | total). The category comes from the item's ENSTRU code (see `cpi_category`).
     Missing region -> national; month outside coverage -> nearest covered month (stated in the answer).
  2. Internal procurement price index built from mart.price_obs when no CPI is loaded:
       for every ENSTRU x unit group g:  r_{g,m} = median(log p in month m) - median(log p in g)
       index(m) = exp( median_g r_{g,m} )   (only groups observed in >= 3 months contribute)
     Normalising within each group removes the effect of *which* items were bought in a month.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import psycopg

MIN_MONTHS_PER_GROUP = 3
FOOD_DIVISIONS = {"01", "02", "03", "10", "11"}       # agriculture, forestry, fishing, food, beverages
CONSTRUCTION_DIVISIONS = {"41", "42", "43"}


def cpi_category(enstru_code: str | None, base_unit: str | None) -> str:
    """Map an item to a CPI category by the 2-digit KPVED division its ENSTRU code starts with
    (same leading digits in the old and new ENSTRU formats)."""
    div = (enstru_code or "")[:2]
    if base_unit == "work" or div in CONSTRUCTION_DIVISIONS:
        return "total"
    if div in FOOD_DIVISIONS:
        return "food"
    if base_unit == "service" or (div.isdigit() and int(div) >= 35):
        return "services"
    return "nonfood"


@dataclass
class MonthlyIndex:
    """Internal index: one national level per month (region and category are ignored)."""
    name: str
    levels: dict[date, float]

    def __call__(self, month: date, region: str | None = None, category: str = "total") -> float:
        m = month.replace(day=1)
        if m in self.levels:
            return self.levels[m]
        nearest = min(self.levels, key=lambda k: abs((k - m).days))
        return self.levels[nearest]


@dataclass
class OfficialCPI:
    name: str
    levels: dict[tuple[date, str, str], float]
    months: list[date] = field(default_factory=list)
    clamped: set[date] = field(default_factory=set)     # months outside coverage that were requested

    def __post_init__(self) -> None:
        self.months = sorted({k[0] for k in self.levels})

    def __call__(self, month: date, region: str | None = None, category: str = "total") -> float:
        m = month.replace(day=1)
        if m < self.months[0] or m > self.months[-1]:
            self.clamped.add(m)
            m = self.months[0] if m < self.months[0] else self.months[-1]
        for key in ((m, region or "KZ", category), (m, "KZ", category), (m, "KZ", "total")):
            if key in self.levels:
                return self.levels[key]
        raise KeyError(f"CPI missing for {m}")

    @property
    def levels_by_month(self) -> dict[date, float]:
        return {k[0]: v for k, v in self.levels.items() if k[1] == "KZ" and k[2] == "total"}


def build_internal_index(rows: list[tuple[str, str, date, float]]) -> MonthlyIndex:
    """rows: (enstru_code, base_unit, month, price)."""
    by_group: dict[tuple[str, str], dict[date, list[float]]] = defaultdict(lambda: defaultdict(list))
    for enstru, unit, month, price in rows:
        if price and price > 0:
            by_group[(enstru, unit)][month].append(np.log(price))
    rel: dict[date, list[float]] = defaultdict(list)
    for months in by_group.values():
        if len(months) < MIN_MONTHS_PER_GROUP:
            continue
        g_med = float(np.median([x for xs in months.values() for x in xs]))
        for m, xs in months.items():
            rel[m].append(float(np.median(xs)) - g_med)
    if not rel:
        return MonthlyIndex("none", {})
    raw = {m: float(np.exp(np.median(v))) for m, v in rel.items()}
    base = raw[min(raw)]
    return MonthlyIndex("internal_procurement_index", {m: v / base * 100 for m, v in sorted(raw.items())})


def load_index(conn: psycopg.Connection) -> OfficialCPI | MonthlyIndex:
    cpi = conn.execute("SELECT month, region_code, category, index FROM core.cpi").fetchall()
    if cpi:
        return OfficialCPI("official_cpi_stat_gov_kz", {(m, r, c): float(v) for m, r, c, v in cpi})
    rows = conn.execute("SELECT enstru_code, base_unit, month, price FROM mart.price_obs").fetchall()
    return build_internal_index([(e, u, m, float(p)) for e, u, m, p in rows])
