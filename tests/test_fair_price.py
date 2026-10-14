from datetime import date

import pytest

from tenderai.analytics.fair_price import (
    Observation,
    enstru_prefixes,
    evaluate_fair_price,
    select_comparables,
    verdict_for,
)
from tenderai.analytics.stats import robust_z, weighted_median, weighted_quantile


def obs(i, price, bin_="111111111111", city="711210000", month=date(2025, 3, 1), enstru="192026.510.000000",
        unit="112", qty=100.0):
    return Observation(f"contract_unit:{i}", price, qty, enstru, unit, month, bin_, city, city[:2] if city else None)


TARGET = obs(0, 130.0, bin_="000740001307")


def test_weighted_median_matches_numpy_for_equal_weights():
    assert weighted_median([1, 2, 3, 4, 100]) == 3
    assert weighted_quantile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)


def test_weighted_median_respects_weights():
    assert weighted_median([10, 20], [1, 9]) > 15


def test_robust_z_ignores_outlier_in_scale():
    base = [100, 101, 99, 100, 102, 98, 100]
    assert abs(robust_z(100, base)) < 0.5
    assert robust_z(200, base) > 10


def test_enstru_prefixes_both_formats():
    assert enstru_prefixes("192026.510.000000") == ["192026.510", "192026"]
    assert enstru_prefixes("10.39.11.100.010.00.0166.000000000000")[0] == "10.39.11.100.010"


def test_hierarchy_prefers_same_city_and_excludes_own_customer():
    cands = [obs(i, 100, city="711210000") for i in range(1, 6)] + [obs(i, 90, city="751110000") for i in range(6, 20)]
    cands.append(obs(99, 1.0, bin_="000740001307"))  # same customer — must be excluded
    comps, level = select_comparables(TARGET, cands)
    assert level == "same_city"
    assert len(comps) == 5 and all(c.customer_bin != TARGET.customer_bin for c in comps)


def test_hierarchy_falls_back_to_national_then_prefix():
    cands = [obs(i, 100, city="751110000") for i in range(1, 7)]
    _, level = select_comparables(TARGET, cands)
    assert level == "national"
    cands = [obs(i, 100, city="751110000", enstru="192026.510.000099") for i in range(1, 7)]
    _, level = select_comparables(TARGET, cands)
    assert level == "enstru_prefix:192026.510"


def test_unit_mismatch_and_time_window_are_excluded():
    cands = [obs(i, 100, unit="166") for i in range(1, 10)] + [obs(i, 100, month=date(2022, 1, 1)) for i in range(10, 20)]
    comps, level = select_comparables(TARGET, cands)
    assert comps == [] and level.endswith(":insufficient")


def test_overpriced_lot_is_flagged_with_known_numbers():
    cands = [obs(i, p) for i, p in enumerate([95, 98, 100, 100, 102, 105, 100], start=1)]
    r = evaluate_fair_price(TARGET, cands)
    assert r.fair_price == 100.0
    assert r.deviation == pytest.approx(0.30)
    assert r.verdict == "overpriced_anomaly"  # robust z is far above 3 even though deviation is exactly 30%
    assert r.n == 7 and r.comparison_level == "same_city"
    assert r.sample_ids[0].startswith("contract_unit:")


def test_fair_price_inside_band():
    cands = [obs(i, p) for i, p in enumerate([120, 125, 130, 135, 140, 128, 132], start=1)]
    r = evaluate_fair_price(TARGET, cands)
    assert r.verdict == "fair"


def test_inflation_adjustment_raises_old_prices():
    old = [obs(i, 100, month=date(2024, 3, 1)) for i in range(1, 8)]
    index = {date(2024, 3, 1): 100.0, date(2025, 3, 1): 110.0}
    r = evaluate_fair_price(TARGET, old, price_index=lambda m, region, cat: index[m], price_index_name="test")
    assert r.fair_price == pytest.approx(110.0)
    assert r.deviation == pytest.approx(130 / 110 - 1, abs=1e-4)


def test_regional_coefficient_moves_fair_price_toward_target_region():
    # target in Astana (71), national sample: Almaty (75) prices higher than Astana
    cands = [obs(i, 100, city="711210000") for i in range(1, 4)] + [obs(i, 150, city="751110000") for i in range(4, 12)]
    t = obs(0, 100.0, bin_="000740001307", city="711299999")
    r = evaluate_fair_price(t, cands)
    assert r.comparison_level == "same_region" or r.comparison_level == "national"
    if r.comparison_level == "national":
        assert r.adjustments["regions_in_sample"] == 2
        assert r.fair_price < 150


def test_confidence_low_for_small_sample():
    r = evaluate_fair_price(TARGET, [obs(1, 100), obs(2, 101)])
    assert r.confidence_label == "low"
    assert any("предварительный" in n for n in r.limitations)


@pytest.mark.parametrize(
    ("dev", "rz", "in_band", "expected"),
    [(0.5, 1.0, False, "overpriced_anomaly"), (0.05, 0.5, False, "fair"), (0.2, 1.0, False, "elevated"),
     (-0.4, -1.0, False, "underpriced_anomaly"), (-0.2, -1.0, False, "below_market"), (0.2, 1.0, True, "fair")],
)
def test_verdict_thresholds(dev, rz, in_band, expected):
    assert verdict_for(dev, rz, in_band) == expected


def test_cpi_uses_comparables_own_region_and_item_category():
    from tenderai.analytics.price_index import OfficialCPI
    levels = {}
    for region, growth in (("71", 1.10), ("75", 1.20)):        # Astana +10%, Almaty +20% over the year
        for cat in ("nonfood", "food", "total"):
            g = growth if cat == "nonfood" else 1.0
            levels[(date(2024, 3, 1), region, cat)] = 100.0
            levels[(date(2025, 3, 1), region, cat)] = 100.0 * g
            levels[(date(2024, 3, 1), "KZ", cat)] = 100.0
            levels[(date(2025, 3, 1), "KZ", cat)] = 100.0
    cpi = OfficialCPI("test", levels)
    astana = [obs(i, 100, month=date(2024, 3, 1), city="711210000") for i in range(1, 8)]
    r = evaluate_fair_price(TARGET, astana, price_index=cpi, price_index_name="test")
    assert r.adjustments["cpi_category"] == "nonfood"          # 19xxxx = refined petroleum -> non-food
    assert r.fair_price == pytest.approx(110.0)                 # Astana non-food inflation, not Almaty's


def test_cpi_category_mapping():
    from tenderai.analytics.price_index import cpi_category
    assert cpi_category("101111.400.000008", "kg") == "food"       # beef
    assert cpi_category("192026.510.000000", "L") == "nonfood"     # diesel
    assert cpi_category("811010.000.000000", "service") == "services"
    assert cpi_category("410040.300.000009", "work") == "total"
    assert cpi_category("10.39.11.100.010.00.0166.000000000000", "kg") == "food"   # old format
