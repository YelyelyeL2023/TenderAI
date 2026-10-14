"""Item resolution with product variants: codes that share a name and differ only in the plan description."""
from tenderai.agent.tools import rank_items

PETROL = "бензин для двигателей с искровым зажиганием"
VARIANTS = {
    "p92": [("марка АИ-92", 302)], "p95": [("марка АИ-95", 157)],
    "d_summer": [("летнее", 193)], "d_winter": [("зимнее", 63)],
    "conv": [("для дизельного двигателя, каталитический", 2)],
    "a4": [("формат А4", 296)], "a3": [("формат А3", 219)],
    "aa": [("тип АА", 172)], "aaa": [("тип ААА", 155)], "pack": [("свинцово-кислотная, аккумуляторная", 74)],
    "med": [("бензидамин", 2)],
}


def rows(*spec):
    return [{"enstru_code": c, "base_unit": u, "name": name, "n": n, "sim": 0.3} for c, u, name, n in spec]


def resolve(text, *spec):
    return rank_items(text, rows(*spec), VARIANTS)[0]


def test_generic_product_keeps_every_variant():
    top = resolve("бензин", ("p92", "L", PETROL, 180), ("p95", "L", PETROL, 107), ("med", "u872", "бензидамин", 2))
    assert top["enstru_code"] == "p92" and set(top["codes"]) == {"p92", "p95"}
    assert [v["variant"] for v in top["variants"]] == ["марка АИ-92", "марка АИ-95"]


def test_named_variant_selects_its_code_only():
    spec = (("p92", "L", PETROL, 180), ("p95", "L", PETROL, 107))
    assert resolve("бензин АИ-95", *spec)["codes"] == ["p95"]
    assert resolve("бензин аи92", *spec)["codes"] == ["p92"]   # hyphen optional
    diesel = (("d_summer", "L", "топливо дизельное", 100), ("d_winter", "L", "топливо дизельное", 35))
    assert resolve("зимнего дизельного топлива", *diesel)["codes"] == ["d_winter"]


def test_short_variant_words_and_qualifiers():
    paper = (("a3", "u5111", "бумага для офисного оборудования", 96), ("a4", "u5111", "бумага для офисного оборудования", 136))
    assert resolve("офисная бумага А4 в пачках", *paper)["codes"] == ["a4"]
    batteries = (("aaa", "pcs", "батарейка", 79), ("aa", "pcs", "батарейка", 83))
    assert resolve("батарейка типа ААА", *batteries)["codes"] == ["aaa"]


def test_name_words_outrank_description_words():
    top = resolve("дизельное топливо", ("conv", "pcs", "преобразователь топлива", 2),
                  ("d_summer", "L", "топливо дизельное", 100), ("d_winter", "L", "топливо дизельное", 35))
    assert top["name"] == "топливо дизельное" and set(top["codes"]) == {"d_summer", "d_winter"}


def test_product_split_into_variants_wins_on_its_total():
    # each battery type has fewer purchases than the single "батарея" code, together they have more
    top = resolve("батарейки", ("pack", "pcs", "батарея", 100), ("aa", "pcs", "батарейка", 83), ("aaa", "pcs", "батарейка", 79))
    assert top["name"] == "батарейка" and set(top["codes"]) == {"aa", "aaa"}


def test_unexplained_word_drops_to_similarity_tier():
    top = resolve("бензин АИ-98", ("p92", "L", PETROL, 180), ("p95", "L", PETROL, 107))
    assert top["tier"] == 3
