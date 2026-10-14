"""Build the paraphrase set: the assignment's mandatory question types (and one analytics question), each asked
several ways in Russian and Kazakh. Every wording in a group must produce the same facts, so together with
`run_eval.py --repeats N` this measures stability, not just accuracy: whether a rewording or another run of the
LLM changes how the question is understood.

Expected facts come from the same independent SQL as eval/build_corpus.py.

Usage: python eval/build_paraphrases.py        # writes eval/paraphrases.jsonl
       python eval/run_eval.py eval/paraphrases.jsonl --repeats 3 --out eval/report_paraphrases.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).parent))
from build_corpus import DIESEL, SERVICE_UNITS, Corpus, r2

from tenderai.config import get_settings

FAIR_LOT = "73177002-ЗЦП1"   # fair-priced lettuce lot with a same-city comparison set (see eval/corpus.md, fair_price_01)

GROUPS = {
    "anomaly_30": ("price_anomaly", [
        ("ru", "Найди закупки с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ"),   # the assignment's wording
        ("ru", "Где цена за единицу превышает средневзвешенную по тому же ЕНС ТРУ больше чем на 30 процентов?"),
        ("ru", "Какие закупки дороже средневзвешенной цены по своему коду ЕНС ТРУ больше чем на 30%?"),
        ("ru", "Покажи позиции, где цена превышает средневзвешенную по коду ЕНСТРУ более чем на 30%"),
        ("ru", "Выяви ценовые аномалии: отклонение от средневзвешенной цены по ЕНС ТРУ свыше 30%"),
        ("kk", "ЕНС ТРУ бойынша орташа өлшенген бағадан 30%-дан астам ауытқыған сатып алуларды тап"),
        ("kk", "Бағасы ЕНС ТРУ бойынша орташа өлшенген бағадан 30%-дан артық ауытқитын сатып алуларды көрсет"),
    ]),
    "fair_lot": ("fair_price", [
        ("ru", f"Оцени адекватность цены лота №{FAIR_LOT} относительно аналогичных контрактов других ведомств "
               f"в идентичном городе поставки"),                                                      # the assignment's wording
        ("ru", f"Адекватна ли цена лота {FAIR_LOT}?"),
        ("ru", f"Справедливая ли цена в лоте №{FAIR_LOT} по сравнению с другими заказчиками?"),
        ("ru", f"Лот {FAIR_LOT}: цена обоснована?"),
        ("kk", f"{FAIR_LOT} лотының бағасы әділ ме?"),
        ("kk", f"{FAIR_LOT} лотының бағасын басқа ведомстволардың ұқсас келісімшарттарымен салыстырып бағала"),
    ]),
    "volume_growth": ("volume_anomaly", [
        ("ru", "Выяви нетипичное завышение количества ТРУ по сравнению с предыдущими годами"),     # the assignment's wording
        ("ru", "Где количество закупаемых товаров нетипично выросло по сравнению с прошлыми годами?"),
        ("ru", "Найди аномальный рост объёмов закупок относительно предыдущих лет"),
        ("ru", "Выяви случаи, когда заказчики резко увеличили количество закупаемых ТРУ по сравнению с прошлыми годами"),
        ("kk", "Көлемі алдыңғы жылдармен салыстырғанда әдеттен тыс өскен сатып алуларды тап"),
        ("kk", "Алдыңғы жылдармен салыстырғанда ТЖҚ саны күрт өскен жағдайларды анықта"),
    ]),
    # summer diesel: 192026.510.000000 is the summer grade only (winter and arctic have their own codes)
    "diesel_2025": ("analytics", [
        ("ru", "Сколько в среднем стоило летнее дизельное топливо в 2025 году?"),
        ("ru", "Сколько стоил литр летнего дизельного топлива в 2025 году?"),
        ("ru", "Средневзвешенная цена летнего дизтоплива за 2025 год"),
        ("ru", "Летнее дизельное топливо в 2025 году: какая средняя цена за литр?"),
        ("kk", "2025 жылы жазғы дизель отынының бір литрі орташа қанша тұрды?"),
    ]),
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as conn:
        cp = Corpus(conn)
        a = cp.anomalies("true", {}, 0.30)
        lot = cp.lot(FAIR_LOT)
        vol = [v for v in cp.volume_series() if v["base_unit"] not in SERVICE_UNITS]
        flagged = [v for v in vol if v["flagged"]]
        st = cp.stats(DIESEL, "L", 2025)
    facts = {
        "anomaly_30": {"strings": [], "numbers": [a["n"], a["flagged"], a["confirmed"]]},
        "fair_lot": {"strings": [FAIR_LOT, lot["customer_bin"], lot["enstru_code"]], "numbers": [lot["price"]]},
        "volume_growth": {"strings": [], "numbers": [len(vol), len(flagged), sum(v["confirmed"] for v in flagged)]},
        "diesel_2025": {"strings": [DIESEL], "numbers": [st["wavg"], st["med"], st["n"]]},
    }
    out = Path(__file__).parent / "paraphrases.jsonl"
    rows = []
    for group, (intent, variants) in GROUPS.items():
        fx = {"strings": facts[group]["strings"], "numbers": [r2(x) for x in facts[group]["numbers"]]}
        expect = {"verdict": "fair"} if group == "fair_lot" else {}   # fair_price_01 in eval/corpus.md
        rows += [{"id": f"{group}_{i}", "group": group, "lang": lang, "question": q, "intent": intent, "facts": fx,
                  "expect": expect} for i, (lang, q) in enumerate(variants, 1)]
    with open(out, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    print(f"{sum(len(v) for _, v in GROUPS.values())} paraphrases in {len(GROUPS)} groups -> {out}")


if __name__ == "__main__":
    main()
