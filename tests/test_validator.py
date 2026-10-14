from tenderai.agent.validator import validate

FACTS = {"summary": {"target_price": 130.0, "fair_price": 100.0, "deviation_pct": 30.0, "n": 7,
                     "total_amount_kzt": 123687944.0, "customer": "000740001307"},
         "params": {"year": 2025}}


def test_supported_numbers_pass():
    v = validate("Цена 130 ₸ выше справедливой (100 ₸) на 30% по выборке из 7 договоров за 2025 год.", FACTS)
    assert v.ok, v.unsupported


def test_scaled_and_formatted_numbers_pass():
    assert validate("Общая сумма 123,7 млн тенге, заказчик 000740001307.", FACTS).ok
    assert validate("Сумма 123 687 944 тенге.", FACTS).ok


def test_invented_number_fails():
    v = validate("Отклонение составило 45% при 12 аналогах.", FACTS)
    assert not v.ok and "45%" in v.unsupported


def test_unknown_bin_fails():
    assert not validate("Заказчик 999999999999 переплатил.", FACTS).ok


# --- claim checks: numbers that exist in the facts but are used in the wrong role (seen in eval/corpus runs)
from tenderai.agent.tools import ToolResult
from tenderai.agent.validator import check_claims, claims_from, fix_id_terms

VOL = ToolResult("volume_anomalies", {}, "m", 347, {"series_compared": 347, "flagged": 128, "share_pct": 36.9,
                                                   "confirmed": 22, "confirmed_share_pct": 6.3,
                                                   "confirmed_extra_amount_kzt": 77147105.0})


def test_total_presented_as_the_flagged_count_is_caught():
    bad = "ҚР Президенті Іс басқармасының жоспарлы сатып алу көлемі 347 қатары бойынша 1,5 еседен астам өсті."
    assert check_claims(bad, claims_from(VOL))
    good_kk = "347 қатарының 128-інде көлем 1,5 еседен астам өсті."
    good_ru = "Из 347 рядов рост более чем в 1,5 раза выявлен в 128."
    assert not check_claims(good_kk, claims_from(VOL)) and not check_claims(good_ru, claims_from(VOL))


def test_price_outside_band_claimed_inside_is_caught():
    fair = ToolResult("fair_price_for_lot", {}, "m", 26, {"target_price": 263.39, "fair_band": [249.44, 262.42]})
    bad = "Лоттың бағасы нарықтық диапазонда (249,44–262,42 ₸) орналасқан және әділ деп танылды."
    ok = "Цена 263,39 ₸ немного выше диапазона Q1–Q3 (249,44–262,42 ₸), но отклонение всего 2,1%."
    assert check_claims(bad, claims_from(fair)) and not check_claims(ok, claims_from(fair))


def test_organisation_number_labelled_inn_becomes_bin():
    bins = {"960440000220"}
    assert fix_id_terms("потратил 36 883 592 000 ₸ (ИНН 960440000220)", bins).endswith("(БИН 960440000220)")
    assert fix_id_terms("ЖСН 960440000220", bins) == "БСН 960440000220"
    assert fix_id_terms("ИИН 870101300123", bins) == "ИИН 870101300123"  # a person: left alone


def test_q1_q3_band_is_not_a_confidence_interval():
    fair = ToolResult("fair_price_for_lot", {}, "m", 33, {"target_price": 866.08, "fair_band": [752.07, 1029.2]})
    bad = "Отклонение находится в пределах доверительного интервала Q1–Q3 (от 752,07 ₸ до 1 029,20 ₸)."
    ok = "Цена 866,08 ₸ лежит внутри диапазона цен аналогов Q1–Q3 (752,07–1 029,20 ₸)."
    assert check_claims(bad, claims_from(fair)) and not check_claims(ok, claims_from(fair))
