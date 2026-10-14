from tenderai.agent.tools import _name_tokens, _same_word, _split_names


def test_split_several_customer_names():
    assert _split_names("Больница МЦ УДП и Национальный госпиталь") == ["Больница МЦ УДП", "Национальный госпиталь"]
    assert _split_names("«Бурабай» ұлттық паркі мен «Бурабай даму»") == ["Бурабай» ұлттық паркі", "Бурабай даму"]
    assert _split_names("Отель Алатау; Президентский центр") == ["Отель Алатау", "Президентский центр"]


def test_official_forms_collapse_to_abbreviations():
    assert _name_tokens('ГУ "Медицинский центр Управления Делами Президента Республики Казахстан"') == \
        ["медицинский", "центр", "удп"]
    assert "умто" in _name_tokens("Управление материально-технического обеспечения при Генеральной прокуратуре")
    assert _name_tokens("ҚР Президенті Іс басқармасының Автошаруашылығы")[0] == "удп"


def test_same_word_ignores_endings_only():
    assert _same_word("центр", "центра") and _same_word("автошаруашылығында", "автошаруашылығы")
    assert _same_word("офисная", "офисного") and _same_word("картриджи", "картридж")
    assert not _same_word("бензин", "бензидамин") and not _same_word("даму", "дамыту")


def test_other_organisation_types_are_recognised():
    from tenderai.agent.tools import _OTHER_ORG
    assert _OTHER_ORG.match("акимата") and _OTHER_ORG.match("министерства")
    assert not _OTHER_ORG.match("автохозяйства") and not _OTHER_ORG.match("заказчиков")
