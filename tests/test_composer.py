from datetime import date

import pytest

from tenderai.agent.composer import render, template_verdict, verdict_brief
from tenderai.agent.tools import ToolResult
from tenderai.agent.validator import validate

FAIR = ToolResult(
    tool="fair_price_for_lot", params={"lot_ref": "74987392-ЗЦП2"}, method="Fair Price", n_analyzed=403,
    summary={"lot": {"id": 1, "lot_number": "74987392-ЗЦП2", "number_anno": "x", "customer_bin": "970940001378",
                     "name_ru": "Картридж", "enstru_code": "262040.000.000282", "base_unit": "pcs", "count": 4,
                     "amount": 320000, "kato_code": "711210000", "url": "https://goszakup.gov.kz/ru/announce/index/1"},
             "price_kind": "цена по договору", "target_price": 58214.0, "target_url": "u", "fair_price": 69156.5,
             "fair_band": [41016.68, 119577.87], "deviation_pct": -15.8, "robust_z": -0.22, "verdict": "fair",
             "confidence": 0.69, "confidence_label": "medium"},
    rows=[{"obj_id": "contract_unit:1", "customer_bin": "020440003656", "price": 79660.71, "quantity": 1,
           "month": date(2024, 12, 1), "city_kato": "711210000", "url": "https://goszakup.gov.kz/x"}],
    comparison={"level": "same_city", "n": 403, "weighted_mean": 79986.4,
                "adjustments": {"price_index": "internal", "regional_coefficient_target": "n/a", "seasonal": {1: 1.0}}},
    dq={"lot_issue_rate": 0.192}, limitations=["высокий разброс цен аналогов"])


@pytest.mark.parametrize("lang", ["ru", "kk"])
def test_render_has_all_required_sections(lang):
    md = render(FAIR, lang, template_verdict(FAIR, lang))
    assert md.count("### ") == 6  # verdict, params, analytics, metric, confidence, top-k
    assert "74987392-ЗЦП2" in md and "69 156,50" in md and "https://goszakup.gov.kz" in md


def test_template_verdict_passes_its_own_validation():
    v = template_verdict(FAIR, "ru")
    assert validate(v, {"brief": verdict_brief(FAIR, "ru")}).ok


def test_not_found_says_no_data():
    r = ToolResult("search_procurements", {}, "—", 0, {}, found=False)
    assert "нет" in template_verdict(r, "ru").lower()


def test_kazakh_answer_localises_method_and_limitations():
    md = render(FAIR, "kk", template_verdict(FAIR, "kk"))
    assert "Fair Price: аналогтар бағасының медианасы" in md
    assert "аналогтар бағасының шашыраңқылығы жоғары" in md
    assert "высокий разброс" not in md


def test_unknown_limitation_falls_back_to_russian():
    from tenderai.agent.i18n import limitation
    assert limitation("что-то новое", "kk") == "что-то новое"
    assert limitation("место поставки лота определено косвенно (источник: announcement)", "kk").endswith(
        "(дереккөз: announcement)")


def test_no_data_answer_says_why():
    r = ToolResult("none", {}, "—", 0, {}, found=False,
                   limitations=["заказчик «акимат» не найден среди 27 заказчиков хранилища"])
    assert template_verdict(r, "ru").endswith("Заказчик «акимат» не найден среди 27 заказчиков хранилища.")
    assert "тапсырыс берушісі" in template_verdict(r, "kk")


def test_below_market_anomalies_are_worded_as_below():
    r = ToolResult("price_anomalies", {"threshold": 0.5, "direction": "under"}, "m", 100,
                   {"flagged": 9, "share_pct": 9.0, "confirmed": 2, "confirmed_share_pct": 2.0, "likely_unit_errors": 0,
                    "estimated_overpayment_kzt": None, "confirmed_overpayment_kzt": None, "enstru_groups_compared": 5})
    v = template_verdict(r, "ru")
    assert "ниже" in v and "выше" not in v and "переплат" not in v


def test_compare_years_renders_a_table_with_change():
    r = ToolResult("compare_years", {"years": [2024, 2025]}, "m", 30, {"years": 2},
                   rows=[{"year": 2024, "n": 10, "total_amount": 1000, "customers": 2, "weighted_avg_price": 1},
                         {"year": 2025, "n": 20, "total_amount": 1500, "customers": 3, "weighted_avg_price": 1,
                          "change_pct": 50.0}])
    md = render(r, "ru", template_verdict(r, "ru"))
    assert "| 2025 | 20 | 1 500 | 3 | 50,0% |" in md and "50,0%" in template_verdict(r, "ru")


def test_every_table_answer_lists_top_k_purchases_with_links():
    r = ToolResult("price_stats", {"enstru_code": "011334.100.000000"}, "m", 35, {"groups": 1},
                   rows=[{"enstru_code": "011334.100.000000", "base_unit": "kg", "period": "2025", "n": 35, "n_customers": 8,
                          "weighted_avg": 781.04, "median": 866.07, "q1": 772, "q3": 1050, "min_price": 1, "max_price": 2}],
                   examples=[{"obj_id": "contract_unit:1", "name_clean": "помидор", "amount": 3848000, "quantity": 5200,
                              "price": 740, "customer_bin": "990740002243", "month": date(2025, 9, 1),
                              "url": "https://goszakup.gov.kz/ru/egzcontract/cpublic/show/1"}])
    md = render(r, "ru", template_verdict(r, "ru"))
    assert "### Детализация (Top-1)" in md and "[contract_unit:1](https://goszakup.gov.kz" in md


def test_fair_price_answer_judges_the_quantity_too():
    from tenderai.agent.composer import quantity_line
    q = {"year": 2025, "quantity": 9764, "previous": {2024: 8987}, "ratio": 1.09, "flagged": False}
    assert quantity_line(q, "kg", "ru").endswith("(×1,09) — в пределах обычного")
    assert "нет плановой истории" in quantity_line({"year": 2024, "quantity": 5, "previous": {}}, "kg", "ru")


def test_aggregate_confidence():
    from tenderai.agent.tools import aggregate_confidence
    assert aggregate_confidence(500, 0.05)[1] == "high"
    assert aggregate_confidence(3, 0.0)[1] == "low"


def test_empty_search_explains_filters_not_method_notes():
    r = ToolResult("price_anomalies", {"threshold": 0.3, "direction": "over", "text": "xyz", "include_services": False},
                   "m", 0, {}, found=False, limitations=["средневзвешенная считается в сопоставимых ценах (ИПЦ)"])
    v = template_verdict(r, "ru")
    assert "text = xyz" in v and "ИПЦ" not in v


def test_price_stats_of_several_variants_reports_each_separately():
    row = {"base_unit": "L", "period": "2026", "n_customers": 5, "q1": 1, "q3": 2, "min_price": 1, "max_price": 2}
    r = ToolResult("price_stats", {"enstru_code": ["192021.530.000001", "192021.550.000000"], "year": 2026}, "m", 85,
                   {"groups": 2}, rows=[{**row, "enstru_code": "192021.530.000001", "variant": "марка АИ-92", "n": 56,
                                         "weighted_avg": 217.19, "median": 215.52},
                                        {**row, "enstru_code": "192021.550.000000", "variant": "марка АИ-95", "n": 29,
                                         "weighted_avg": 284.05, "median": 286.21}])
    v = template_verdict(r, "ru")
    assert "марка АИ-92" in v and "217,19" in v and "марка АИ-95" in v and "284,05" in v and "без общей средней" in v
    md = render(r, "ru", v)
    assert "| Вариант (ЕНС ТРУ) | Период |" in md and "| марка АИ-95 (`192021.550.000000`, L) | 2026 | 29 |" in md
