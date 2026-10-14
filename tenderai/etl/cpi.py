"""Official CPI loader (stat.gov.kz monthly bulletin T-15-02-M «Индекс потребительских цен и производные показатели»).

From each monthly workbook it takes:
  sheet "1" — national CPI by category (Товары и услуги / Продовольственные / Непродовольственные / Платные услуги)
  sheet "2" — CPI by region, the same four category blocks
and two columns located by their header text (layouts differ between years):
  «<prev month>» (month-on-month) and «декабрю 2020г.» (fixed base, December 2020 = 100).

The fixed-base column is stored as the level, so no chaining error accumulates across months.
A CSV with `month` and `index` | `mom_pct` columns is still accepted for other sources.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import date
from pathlib import Path

import psycopg

from tenderai.config import get_settings

log = logging.getLogger(__name__)

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6, "июл": 7, "август": 8,
          "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
CATEGORIES = {"товары и услуги": "total", "продовольственные товары": "food",
              "непродовольственные товары": "nonfood", "платные услуги": "services"}
# bulletin region name (normalised) -> 2-digit KATO
REGIONS = {
    "республика казахстан": "KZ", "абай": "10", "абайская": "10", "жетысуская": "33", "улытауская": "62", "акмолинская": "11", "актюбинская": "15", "алматинская": "19",
    "атырауская": "23", "западно-казахстанская": "27", "жамбылская": "31", "жетісу": "33", "жетысу": "33",
    "карагандинская": "35", "костанайская": "39", "кызылординская": "43", "мангистауская": "47",
    "павлодарская": "55", "северо-казахстанская": "59", "туркестанская": "61", "ұлытау": "62", "улытау": "62",
    "восточно-казахстанская": "63", "астана": "71", "алматы": "75", "шымкент": "79",
}


def _norm(s) -> str:
    s = str(s or "").replace("T", "Т").strip().lower()          # sheet 2 uses a Latin "T" in "Tовары"
    s = re.sub(r"^(город|г\.)\s*", "", s)
    s = re.sub(r"\s+(область|обл\.)$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _month_from_name(path: Path) -> date:
    m = re.search(r"\((\d{2})\s+(\d{4})\)", path.name)
    if not m:
        raise ValueError(f"cannot read month from file name: {path.name}")
    return date(int(m.group(2)), int(m.group(1)), 1)


def _columns(rows: list[tuple]) -> tuple[int, int]:
    """Indices of the month-on-month column and the 'December 2020' fixed-base column."""
    head = rows[3]  # 4th row holds "<prev month> | декабрю ... | ... | декабрю 2020г."
    base = mom = None
    for i, v in enumerate(head):
        t = str(v or "").lower()
        if "2020" in t and "декабр" in t:
            base = i
    mom = 1  # first data column is always "к <previous month>"
    if base is None:
        raise ValueError("fixed-base (December 2020) column not found")
    return mom, base


def parse_workbook(path: Path) -> list[tuple[date, str, str, float, float]]:
    import openpyxl

    month = _month_from_name(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: list[tuple[date, str, str, float, float]] = []
    # sheet 1: national, category per row
    rows = list(wb["1"].iter_rows(values_only=True))
    mom_i, base_i = _columns(rows)
    for r in rows[4:]:
        cat = CATEGORIES.get(_norm(r[0]))
        if cat and r[base_i] is not None:
            out.append((month, "KZ", cat, float(r[base_i]), float(r[mom_i])))
    # sheet 2: category header rows followed by region rows
    rows = list(wb["2"].iter_rows(values_only=True))
    mom_i, base_i = _columns(rows)
    cat = None
    for r in rows[4:]:
        name = _norm(r[0])
        if name in CATEGORIES:
            cat = CATEGORIES[name]
            continue
        region = REGIONS.get(name)
        if cat and region and region != "KZ" and r[base_i] is not None:
            out.append((month, region, cat, float(r[base_i]), float(r[mom_i])))
    wb.close()
    return out


def load_cpi(path: str) -> dict:
    p = Path(path)
    rows: list[tuple[date, str, str, float, float | None]] = []
    if p.is_dir():
        files = sorted(p.glob("*.xlsx"))
        for f in files:
            got = parse_workbook(f)
            if not any(r[1] == "KZ" and r[2] == "total" for r in got):
                raise ValueError(f"{f.name}: national total CPI not found")
            rows += got
        source = "stat.gov.kz T-15-02-M"
        # one month back from the first bulletin, derived from its month-on-month change
        first = min(r[0] for r in rows)
        prev = date(first.year - (first.month == 1), (first.month - 2) % 12 + 1, 1)
        rows += [(prev, reg, cat, lvl / (mom / 100), None) for m, reg, cat, lvl, mom in list(rows)
                 if m == first and mom]
    else:
        rows, source = _from_csv(p), "csv"
    with psycopg.connect(get_settings().database_url) as conn, conn.transaction():
        conn.execute("DELETE FROM core.cpi WHERE source = %s", (source,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO core.cpi (month, region_code, category, index, mom, source) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (month, region_code, category) DO UPDATE SET index = EXCLUDED.index, mom = EXCLUDED.mom, "
                "source = EXCLUDED.source", [(*r, source) for r in rows])
    months = sorted({r[0] for r in rows})
    return {"rows": len(rows), "months": len(months), "from": str(months[0]), "to": str(months[-1]),
            "regions": len({r[1] for r in rows}), "categories": sorted({r[2] for r in rows})}


def _from_csv(p: Path) -> list[tuple[date, str, str, float, float | None]]:
    with open(p, encoding="utf-8-sig") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: r["month"])
    out, level = [], 100.0
    for i, r in enumerate(rows):
        y, m = (int(x) for x in r["month"][:7].split("-"))
        mom = None
        if r.get("index"):
            level = float(r["index"].replace(",", "."))
        else:
            mom = float(r["mom_pct"].replace(",", "."))
            factor = mom / 100 if mom > 50 else 1 + mom / 100
            level = 100.0 if i == 0 else level * factor
        out.append((date(y, m, 1), "KZ", "total", level, mom))
    return out
