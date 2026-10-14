import pytest

from tenderai.etl.lot_names import clean_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Лот №3 Бумага офисная А4", "бумага офисная а4"),
        ("1. Бумага «Снегурочка» формата А4", "бумага снегурочка формата а4"),
        ("Услуги по уборке помещений на 2025 год", "услуги по уборке помещений"),
        ("Услуги связи на 2024-2026 гг.", "услуги связи"),
        ("Картридж (согласно технической спецификации)", "картридж"),
        ("Картридж в соответствии с технической спецификацией", "картридж"),
        ("Бензин АИ-92,,  для нужд ГУ Аппарат акима", "бензин аи-92"),
        ("Флеш-накопитель USB 3.0", "флеш-накопитель usb 3.0"),
        ("Топливо дизельное (летнее)", "топливо дизельное (летнее)"),
        ("Ёлка новогодняя", "елка новогодняя"),
        ("", ""),
        (None, ""),
    ],
)
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_homoglyphs_fixed_only_in_cyrillic_words():
    # Latin 'o' and 'a' typed inside a Cyrillic word; the Latin acronym stays untouched
    assert clean_name("Пoстaвкa кабеля HDMI") == "поставка кабеля hdmi"


def test_procedural_lead_verbs_removed():
    assert clean_name("Приобретение ручек канцелярских") == "ручек канцелярских"
    assert clean_name("Закуп продуктов питания") == "продуктов питания"
    assert clean_name("Приобретение") == "приобретение"  # nothing after it: keep the word
