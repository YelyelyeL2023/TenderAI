from datetime import date

import pytest

from tenderai.analytics.price_index import build_internal_index
from tenderai.analytics.volumes import VolumeRow, detect_volume_anomalies


def vr(year, qty, bin_="000740001307", enstru="192026.510.000000", amount=None):
    return VolumeRow(bin_, enstru, "L", year, qty, amount if amount is not None else qty * 250.0, 1)


def test_tripled_quantity_is_flagged():
    res = detect_volume_anomalies([vr(2024, 100), vr(2025, 110), vr(2026, 330)])
    a = next(x for x in res if x.year == 2026)
    assert a.baseline_quantity == pytest.approx(105)
    assert a.ratio == pytest.approx(330 / 105)
    assert a.flagged


def test_moderate_growth_not_flagged():
    res = detect_volume_anomalies([vr(2024, 100), vr(2025, 110), vr(2026, 140)])
    assert not next(x for x in res if x.year == 2026).flagged


def test_first_year_has_no_baseline():
    res = detect_volume_anomalies([vr(2024, 100)])
    assert res == []


def test_series_are_per_customer_and_item():
    rows = [vr(2024, 100), vr(2025, 400, bin_="111111111111")]
    assert detect_volume_anomalies(rows) == []  # different customers do not form a history


def test_internal_index_tracks_uniform_inflation():
    rows = []
    for g in range(5):  # five items, each 10% more expensive in March than in January
        for m, mult in ((1, 1.0), (2, 1.05), (3, 1.10)):
            for k in range(3):
                rows.append((f"e{g}", "pcs", date(2025, m, 1), 100.0 * (g + 1) * mult))
    idx = build_internal_index(rows)
    assert idx(date(2025, 1, 1)) == pytest.approx(100.0)
    assert idx(date(2025, 3, 1)) == pytest.approx(110.0, rel=1e-6)


def test_internal_index_ignores_purchase_mix():
    # an expensive item bought only in March must not raise the March index
    rows = [(f"e{g}", "pcs", date(2025, m, 1), 100.0) for g in range(3) for m in (1, 2, 3)]
    rows += [("lux", "pcs", date(2025, 3, 1), 100000.0)]
    idx = build_internal_index(rows)
    assert idx(date(2025, 3, 1)) == pytest.approx(100.0)


def test_confirmed_tier_needs_extreme_and_material_growth():
    # 1 -> 3 disk arrays: flagged by the ratio rule but immaterial (extra spend 2 * 1 000 = 2 000 KZT)
    small = detect_volume_anomalies([vr(2024, 1, amount=1000), vr(2025, 3, amount=3000)])[0]
    assert small.flagged and not small.confirmed
    # diesel 17 500 -> 303 803 L at ~250 KZT/L: 17x growth and ~71 M KZT extra spend
    big = detect_volume_anomalies([vr(2024, 17500), vr(2025, 303803)])[0]
    assert big.flagged and big.confirmed
    # +80% with one previous year: rule yes, confirmation no (needs >= 3x)
    mid = detect_volume_anomalies([vr(2024, 100000), vr(2025, 180000)])[0]
    assert mid.flagged and not mid.confirmed
