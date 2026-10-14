from tenderai.agent.router import rule_route


def test_required_question_types_rules():
    assert rule_route("Найди закупки с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ").intent == "price_anomaly"
    r = rule_route("Оцени адекватность цены лота №80800385-ОИ6 относительно аналогичных контрактов других ведомств")
    assert r.intent == "fair_price" and r.lot_ref == "80800385-ОИ6"
    assert rule_route("Выяви нетипичное завышение количества ТРУ по сравнению с предыдущими годами").intent == "volume_anomaly"


def test_hard_facts():
    r = rule_route("Закупки заказчика 990740002243 по ЕНС ТРУ 192026.510.000000 в 2025 году, топ-5, порог 25%")
    assert r.bins == ["990740002243"] and r.enstru_code == "192026.510.000000"
    assert r.year == 2025 and r.top_k == 5 and abs(r.threshold - 0.25) < 1e-9


def test_kazakh_detected():
    assert rule_route("2025 жылы дизель отынының орташа бағасы қанша?").lang == "kk"
    assert rule_route("Сколько стоит бумага?").lang == "ru"


def test_kazakh_lot_number_before_word():
    r = rule_route("78864628-ЗЦП1 лотының бағасы басқа ведомстволардың ұқсас келісімшарттарымен салыстырғанда қаншалықты әділ?")
    assert r.intent == "fair_price" and r.lot_ref == "78864628-ЗЦП1" and r.lang == "kk"


def test_weighted_average_question_is_analytics_not_anomaly():
    assert rule_route("Какова средневзвешенная и медианная цена по ЕНС ТРУ 192026.510.000000?").intent == "analytics"


def test_year_compared_with_base_year_takes_the_first():
    r = rule_route("У Больницы МЦ УДП в 2025 году были закупки с резко выросшим количеством по сравнению с 2024?")
    assert r.year == 2025 and r.years == [2025, 2024]


def test_year_range_keeps_all_years():
    r = rule_route("Как менялась цена бензина с 2024 по 2026 год?")
    assert r.year is None and r.years == [2024, 2026]


def test_year_outside_warehouse_is_flagged():
    r = rule_route("Найди закупки Отеля «Алатау» за 2022 год")
    assert r.year is None and r.out_of_scope_years == [2022]


def test_below_market_sets_direction():
    r = rule_route("Найди закупки, где цена ниже рынка более чем на 50% (возможный демпинг)")
    assert r.intent == "price_anomaly" and r.direction == "under" and abs(r.threshold - 0.5) < 1e-9


def test_which_customer_spends_most_is_a_comparison():
    assert rule_route("Какой заказчик потратил больше всего на картриджи?").intent == "compare"
    assert rule_route("Кто из заказчиков больше всех тратит на бензин?").intent == "compare"


def test_off_topic_question():
    assert rule_route("Какая погода будет завтра в Астане?").off_topic
    assert not rule_route("Найди закупки ноутбуков").off_topic


def test_unit_named_in_question():
    assert rule_route("Найди закупки офисной бумаги А4 в пачках").unit == "u5111"
    assert rule_route("2024 жылы кеңсе қағазының бір бумасының орташа бағасы қанша болды?").unit == "u5111"
    assert rule_route("Сколько в среднем стоил литр бензина в 2026 году?").unit == "L"
    assert rule_route("Найди закупки бумаги").unit is None


def test_grown_quantity_compared_with_a_year_is_a_volume_question():
    r = rule_route("У Больницы МЦ УДП в 2025 году были закупки с резко выросшим количеством по сравнению с 2024?")
    assert r.intent == "volume_anomaly" and r.year == 2025


def test_comparing_two_years_is_a_comparison():
    r = rule_route("Сравни 2024 и 2025 годы по общему объёму закупок")
    assert r.intent == "compare" and r.years == [2024, 2025]


def test_phrasings_the_rules_used_to_miss():
    assert rule_route("Обоснована ли цена картриджа по лоту 79649992-ЗЦП1 по сравнению с другими заказчиками?").intent \
        == "fair_price"
    assert rule_route("Топ-5 самых переплаченных закупок картриджей").intent == "price_anomaly"
    assert rule_route("2026 жылы бағасы орташа өлшенгеннен 25%-дан астам ауытқыған сатып алуларды тап").intent \
        == "price_anomaly"


def test_classifier_name_is_not_an_item():
    from tenderai.agent.router import _clean_item
    assert _clean_item("ЕНСТРУ") is None and _clean_item("данному ЕНС ТРУ") is None
    assert _clean_item("бумага по данному ЕНСТРУ") == "бумага"
    assert _clean_item("кодовый замок") == "кодовый замок"


def test_kazakh_volume_question_is_not_off_topic():
    r = rule_route("Алдыңғы жылдармен салыстырғанда ТЖҚ саны күрт өскен жағдайларды анықта")
    assert not r.off_topic
    assert rule_route("Какая погода будет завтра в Астане?").off_topic
