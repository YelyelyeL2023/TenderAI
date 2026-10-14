"""Build the hand-written evaluation corpus: ~50 analyst-style questions with reference answers.

Unlike build_golden.py (templated, random sample), every question here was chosen after looking at the data:
real customers referred to by name, everyday items (fuel, paper, pens, vegetables), lots with a clear fair /
overpriced / underpriced verdict, and edge cases (no data, out-of-scope year or customer, missing lot number,
item names the matcher gets wrong). Reference answers are computed here with plain SQL over the warehouse,
independently of the agent's tool code; volume anomalies re-implement the documented rule from scratch. Both read the
same marts, so an ETL error would affect both: eval/corpus.md lists the facts that were also checked against the live
OWS API.

One ENSTRU code is often one variant of a product, told apart only by the plan point description (petrol
192021.530.000001 = АИ-92, .550.000000 = АИ-95; diesel 192026.510.000000 = summer, .000001 = winter; office paper
172314.500.000002 = A4, .000001 = A3). A question that names the variant is answered for that code; a question about the
product in general is answered across all its codes.

Output (one case per line, compatible with eval/run_eval.py):
  id, category, lang, question, intent, facts {strings, numbers}  -- scored automatically
  expect {verdict | found}                                         -- scored automatically: Fair Price verdict, refusal
  reference_answer                                                 -- what a correct answer says
  expected_behavior                                                -- what else to check by hand (pitfalls)

Usage: python eval/build_corpus.py            # writes eval/corpus.jsonl and eval/corpus.md
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from tenderai.config import get_settings

OUT = Path(__file__).parent
SERVICE_UNITS = ("service", "work")

# customers referred to by name in the questions
NAMES = {
    "231040023028": "Национальный госпиталь МЦ УДП",
    "990740002243": "Больница МЦ УДП",
    "140340016539": "Президентский центр",
    "970940001378": "Автохозяйство УДП",
    "981140001551": "УМТО при Генеральной прокуратуре",
    "150540000186": "ТОО «Бурабай даму»",
    "940740000911": "ГНПП «Бурабай»",
    "990340005977": "АО «ЦКБ»",
    "020240002363": "АО «Отель «Алатау»",
    "960440000220": "Медицинский центр УДП",
    "980440001034": "Автохозяйство ДХО",
    "020440003656": "Дирекция административных зданий (ДАЗ)",
}

# variants per the plan point descriptions (core.plan_point.desc_ru)
DIESEL, DIESEL_WINTER = "192026.510.000000", "192026.510.000001"          # летнее, зимнее (.000002 — арктическое)
PETROL, PETROL95 = "192021.530.000001", "192021.550.000000"               # АИ-92, АИ-95
PAPER, PEN = "172314.500.000002", "329912.130.000000"                     # бумага А4 (.000001 — А3), ручка шариковая
TOMATO, LETTUCE, SOAP, CARTRIDGE = "011334.100.000000", "011314.900.000000", "204131.950.000000", "262040.000.000282"
PHONE = "611011.200.000000"                                               # SOAP — хозяйственное (.900.* — туалетное)
# every grade / season: all codes with the product's ENSTRU reference name (diesel also has 192026.520.000000 — межсезонное)
PETROL_ALL = "enstru_code IN (SELECT code FROM core.ref_enstru WHERE lower(name_ru) = 'бензин для двигателей с искровым зажиганием')"
DIESEL_ALL = "enstru_code IN (SELECT code FROM core.ref_enstru WHERE lower(name_ru) = 'топливо дизельное')"


def fmt(x, digits: int = 2) -> str:
    """Same number style as the agent's answers: '1 234 567,89'."""
    s = f"{float(x):,.{digits}f}".replace(",", " ").replace(".", ",")
    return s.removesuffix(",00") if digits else s


def r2(x) -> float:
    return round(float(x), 2)


def n_(n, one: str, few: str, many: str) -> str:
    """'1 позиция', '3 позиции', '2 284 позиций'."""
    n = int(n)
    form = one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many
    return f"{fmt(n, 0)} {form}"


def pos(n) -> str:
    return n_(n, "позиция", "позиции", "позиций")


def cust(n) -> str:
    return n_(n, "заказчика", "заказчиков", "заказчиков")


UNIT_RU = {"kg": "кг", "L": "л", "pcs": "шт.", "u5111": "пачку"}
UNIT_KK = {"kg": "кг", "L": "литр", "pcs": "дана", "u5111": "бір бума"}


def of_pos(n) -> str:
    """Genitive after «из»: 'из 21 позиции', 'из 32 позиций'."""
    n = int(n)
    return f"{fmt(n, 0)} {'позиции' if n % 10 == 1 and n % 100 != 11 else 'позиций'}"


def rows_(n) -> str:
    return n_(n, "ряд", "ряда", "рядов")


def of_rows(n) -> str:
    n = int(n)
    return f"{fmt(n, 0)} {'ряда' if n % 10 == 1 and n % 100 != 11 else 'рядов'}"


class Corpus:
    def __init__(self, conn):
        self.c = conn
        self.cases: list[dict] = []

    def q(self, sql, params=None):
        return self.c.execute(sql, params).fetchall()

    def one(self, sql, params=None):
        return self.c.execute(sql, params).fetchone()

    def add(self, cid, category, lang, question, intent, answer, behavior, strings=(), numbers=(), expect=None):
        self.cases.append({"id": cid, "category": category, "lang": lang, "question": question, "intent": intent,
                           "facts": {"strings": list(strings), "numbers": [r2(n) for n in numbers]},
                           "expect": expect or {}, "reference_answer": answer, "expected_behavior": behavior})

    # ------------------------------------------------------------------ shared ground-truth queries
    def obs_agg(self, where: str, params: dict) -> dict:
        return self.one(f"""SELECT count(*) n, coalesce(sum(amount), 0) total, count(DISTINCT customer_bin) customers,
                                   count(*) FILTER (WHERE source = 'contract') n_contract
                            FROM mart.price_obs WHERE {where}""", params)

    def stats(self, enstru: str, unit: str, year: int | None) -> dict:
        """`enstru` is a code or a LIKE pattern ('192021.%%')."""
        return self.one("""SELECT count(*) n, count(DISTINCT customer_bin) customers,
                                  sum(amount) / nullif(sum(quantity), 0) wavg,
                                  percentile_cont(0.5) WITHIN GROUP (ORDER BY price) med,
                                  percentile_cont(0.25) WITHIN GROUP (ORDER BY price) q1,
                                  percentile_cont(0.75) WITHIN GROUP (ORDER BY price) q3,
                                  min(price) lo, max(price) hi
                           FROM mart.price_obs WHERE enstru_code LIKE %(e)s AND base_unit = %(u)s
                             AND (%(y)s::int IS NULL OR year = %(y)s)""",
                        {"e": enstru.replace("%%", "%"), "u": unit, "y": year})

    def by_customer(self, where: str, params: dict) -> list[dict]:
        return self.q(f"""SELECT customer_bin, count(*) n, sum(amount) total,
                                 sum(amount) / nullif(sum(quantity), 0) wavg,
                                 percentile_cont(0.5) WITHIN GROUP (ORDER BY price) med
                          FROM mart.price_obs WHERE {where} GROUP BY 1 ORDER BY total DESC""", params)

    def anomalies(self, scope: str, params: dict, th: float, direction: str = "over") -> dict:
        rule = "deviation > %(th)s" if direction == "over" else "deviation < -%(th)s"
        rob = "robust_z_log > 3" if direction == "over" else "robust_z_log < -3"
        return self.one(f"""SELECT count(*) n,
                                   count(*) FILTER (WHERE {rule}) flagged,
                                   count(*) FILTER (WHERE {rule} AND {rob} AND NOT likely_unit_error) confirmed,
                                   sum(amount - peer_weighted_avg * quantity)
                                       FILTER (WHERE {rule} AND {rob} AND NOT likely_unit_error) overpay
                            FROM mart.price_deviation WHERE NOT is_service AND {scope}""", {**params, "th": th})

    def lot(self, lot_number: str) -> dict:
        lot = self.one("""SELECT id, lot_number, customer_bin, enstru_code, base_unit, name_clean, unit_price_base,
                                 publish_date, url
                          FROM core.lot WHERE NOT is_deleted AND lot_number = %s
                          ORDER BY in_scope DESC, id DESC LIMIT 1""", (lot_number,))
        cu = self.one("""SELECT u.id, u.unit_price_base, c.sign_date FROM core.contract_unit u
                         JOIN core.contract c ON c.id = u.contract_id AND c.is_latest
                         WHERE u.lot_id = %s AND NOT u.deleted AND u.unit_price_base > 0 ORDER BY u.id LIMIT 1""",
                      (lot["id"],))
        if not lot["enstru_code"]:  # some lots carry no code; the contract line (price observation) does
            o = self.one("SELECT enstru_code, base_unit, name_clean FROM mart.price_obs WHERE lot_id = %s LIMIT 1",
                         (lot["id"],))
            lot = {**lot, **o, "code_from_contract": True}
        price = float(cu["unit_price_base"] if cu else lot["unit_price_base"])
        when = (cu["sign_date"] if cu else lot["publish_date"]).date().replace(day=1)
        peers = [float(r["price"]) for r in self.q(
            """SELECT price, month FROM mart.price_obs WHERE enstru_code = %s AND base_unit = %s AND customer_bin <> %s""",
            (lot["enstru_code"], lot["base_unit"], lot["customer_bin"]))
            if abs((r["month"].year - when.year) * 12 + r["month"].month - when.month) <= 18]
        return {**lot, "price": price, "contract": bool(cu), "n_peers": len(peers),
                "peer_median": statistics.median(peers) if peers else None}

    # ------------------------------------------------------------------ volume rule, re-implemented from the spec
    def volume_series(self) -> list[dict]:
        rows = self.q("""SELECT customer_bin, enstru_code, base_unit, year, quantity, amount, name_clean
                         FROM mart.volume_yearly""")
        series = defaultdict(dict)
        for r in rows:
            series[(r["customer_bin"], r["enstru_code"], r["base_unit"])][r["year"]] = r
        out = []
        for (b, e, u), by_year in series.items():
            for y, r in by_year.items():
                prev = {py: float(v["quantity"]) for py, v in by_year.items() if py < y and float(v["quantity"]) > 0}
                qty = float(r["quantity"])
                if not prev or qty <= 0:
                    continue
                base = statistics.median(prev.values())
                ratio = qty / base if base > 0 else math.inf
                z = None
                if len(prev) >= 2:
                    logs = [math.log(v) for v in prev.values()]
                    med = statistics.median(logs)
                    mad = statistics.median(abs(x - med) for x in logs)
                    z = (math.inf if math.log(qty) > med else 0.0) if mad == 0 else (math.log(qty) - med) / (1.4826 * mad)
                flagged = ratio > 1.5 and (z is None or z > 3)
                extra = float(r["amount"] or 0) - float(r["amount"] or 0) / ratio if ratio > 0 else 0.0
                extreme = (z is not None and z > 3) or (z is None and ratio >= 3)
                out.append({"customer_bin": b, "enstru_code": e, "base_unit": u, "year": y, "name": r["name_clean"],
                            "qty": qty, "prev": prev, "ratio": ratio, "z": z, "flagged": flagged, "extra": extra,
                            "confirmed": flagged and extreme and extra >= 1_000_000})
        return out


def build(cp: Corpus) -> None:
    vol = cp.volume_series()

    # =================================================================== SEARCH
    a = cp.obs_agg("customer_bin = %(b)s AND year = 2025", {"b": "231040023028"})
    cp.add("search_01", "search", "ru", "Покажи все закупки Национального госпиталя (БИН 231040023028) за 2025 год",
           "search",
           f"За 2025 год у Национального госпиталя МЦ УДП (231040023028) {pos(a['n'])} на {fmt(a['total'], 0)} ₸ "
           f"(из них {fmt(a['n_contract'], 0)} — строки договоров, остальное — лоты без договора).",
           "Фильтр по БИН и 2025 году; Top-K — крупнейшие позиции со ссылками.",
           ["231040023028"], [a["n"], a["total"]])

    a = cp.obs_agg("customer_bin = %(b)s AND year = 2026", {"b": "140340016539"})
    cp.add("search_02", "search", "ru", "Какие закупки проводил Президентский центр в 2026 году?", "search",
           f"Президентский центр РК (БИН 140340016539) в 2026 году: {pos(a['n'])} на {fmt(a['total'], 0)} ₸.",
           "Заказчик назван только по имени — агент должен сам найти БИН 140340016539.",
           ["140340016539"], [a["n"], a["total"]])

    top = cp.one("""SELECT obj_id, name_clean, amount, url FROM mart.price_obs
                    WHERE customer_bin = '970940001378' AND year = 2025 ORDER BY amount DESC LIMIT 1""")
    cp.add("search_03", "search", "ru", "Какая самая крупная закупка Автохозяйства УДП в 2025 году?", "search",
           f"Крупнейшая позиция Автохозяйства УДП (970940001378) в 2025 году — {top['obj_id']} «{top['name_clean']}» "
           f"на {fmt(top['amount'], 0)} ₸ ({top['url']}).",
           "Первой в Top-K должна стоять именно эта позиция. Сумма сверена с живым API OWS (договор 23797345).",
           ["970940001378", top["obj_id"]], [top["amount"]])

    a = cp.obs_agg("customer_bin = %(b)s AND year = 2024", {"b": "150540000186"})
    cp.add("search_04", "search", "kk", "2024 жылы «Бурабай даму» ЖШС қандай сатып алулар жүргізді?", "search",
           f"«Бурабай даму» ЖШС (БСН 150540000186) 2024 жылы {a['n']} позиция бойынша {fmt(a['total'], 0)} ₸ сатып алды.",
           "Казахский вопрос, заказчик по имени; ответ на казахском.",
           ["150540000186"], [a["n"], a["total"]])

    a = cp.obs_agg("customer_bin = %(b)s AND year = 2024", {"b": "981140001551"})
    cp.add("search_05", "search", "ru",
           "Сколько позиций закупило УМТО при Генеральной прокуратуре в 2024 году и на какую сумму?", "search",
           f"УМТО при Генпрокуратуре (981140001551) в 2024 году: {pos(a['n'])} на {fmt(a['total'], 0)} ₸.",
           "Длинное официальное название; нужен поиск по имени.",
           ["981140001551"], [a["n"], a["total"]])

    a = cp.obs_agg("name_clean = 'ноутбук'", {})
    codes = cp.q("""SELECT enstru_code, count(*) n FROM mart.price_obs WHERE name_clean = 'ноутбук'
                    GROUP BY 1 ORDER BY 2 DESC, 1""")
    cp.add("search_06", "search", "ru", "Найди все закупки ноутбуков", "search",
           f"Найдено {pos(a['n'])} с ноутбуками на {fmt(a['total'], 0)} ₸ у {cust(a['customers'])}; позиции разнесены "
           f"по {len(codes)} кодам ЕНС ТРУ ({', '.join(c['enstru_code'] for c in codes[:4])}…).",
           "Правильный ответ охватывает все коды с наименованием «ноутбук». Ловушка: если агент сузит поиск до одного "
           f"кода ({codes[0]['enstru_code']}, {pos(codes[0]['n'])}), ответ неполный.",
           [], [a["n"]])

    a = cp.obs_agg("enstru_code = %(e)s AND base_unit = 'u5111'", {"e": PAPER})
    cp.add("search_07", "search", "ru", "Найди закупки офисной бумаги А4 в пачках", "search",
           f"Бумага для офисного оборудования формата А4 (ЕНС ТРУ {PAPER}, единица — пачка): {pos(a['n'])} на "
           f"{fmt(a['total'], 0)} ₸ у {cust(a['customers'])}.",
           "Коды 172314.500.* различаются форматом (по описанию пунктов плана: .000001 — А3, .000002 — А4, .000005 — А5); "
           "верен только А4. Ловушка распознавания: «бумага а4» по триграммам сопоставляется с общим «бумага» "
           "(171273.500.000000), а не с офисной бумагой.",
           [], [a["n"], a["total"]])

    a = cp.obs_agg("customer_bin = '990340005977' AND year = 2026", {})
    assert a["n"] == 0
    last = cp.one("SELECT max(year) y FROM mart.price_obs WHERE customer_bin = '990340005977'")["y"]
    nl = cp.obs_agg("customer_bin = '990340005977' AND year = %(y)s", {"y": last})["n"]
    cp.add("search_08", "search", "ru", "Покажи закупки АО «ЦКБ» за 2026 год", "search",
           f"У АО «ЦКБ» (990340005977) закупок за 2026 год в хранилище нет; последние данные — за {last} год "
           f"({pos(nl)}).",
           "Ожидается честный ответ «данных нет», без цифр из других лет. Проверено и по живому API OWS: договоров "
           "ЦКБ с датой подписания в 2026 году и пунктов плана на 2026 год нет.",
           ["990340005977"], [], expect={"found": False})

    cp.add("search_09", "search", "ru", "Найди закупки Отеля «Алатау» за 2022 год", "search",
           "Хранилище покрывает только 2024–2026 годы, данных за 2022 год нет.",
           "Правильно — сообщить, что 2022 год вне периода, а не показывать закупки за другие годы.",
           [], [], expect={"found": False})

    # =================================================================== ANALYTICS (price statistics)
    def stat_case(cid, lang, question, enstru, unit, year, label, extra_behavior=""):
        s = cp.stats(enstru, unit, year)
        per = str(year) if year else "2024–2026"
        ans = (f"{label} (ЕНС ТРУ {enstru}, цена за {UNIT_RU.get(unit, unit)}) за {per}: средневзвешенная цена "
               f"{fmt(s['wavg'])} ₸, медиана {fmt(s['med'])} ₸ (Q1–Q3: {fmt(s['q1'])}–{fmt(s['q3'])} ₸); "
               f"{n_(s['n'], 'закупка', 'закупки', 'закупок')} у {cust(s['customers'])}." if lang == "ru" else
               f"{label} (ЕНС ТРУ {enstru}, {UNIT_KK.get(unit, unit)} үшін), {per}: орташа өлшенген баға {fmt(s['wavg'])} ₸, медиана "
               f"{fmt(s['med'])} ₸; {s['customers']} тапсырыс берушінің {s['n']} сатып алуы.")
        cp.add(cid, "analytics", lang, question, "analytics", ans,
               ("Цена без НДС за базовую единицу. " + extra_behavior).strip(),
               [enstru], [s["wavg"], s["med"], s["n"]])

    stat_case("analytics_01", "ru", "Какая средняя цена летнего дизельного топлива в 2025 году?", DIESEL, "L", 2025,
              "Дизельное топливо летнее", "Сезон различается только описанием пункта плана: 192026.510.000000 — "
              "летнее, .000001 — зимнее, .000002 — арктическое.")
    stat_case("analytics_02", "ru", "Сколько в среднем стоил литр бензина АИ-92 в 2026 году?", PETROL, "L", 2026,
              "Бензин АИ-92", "Марка различается только описанием пункта плана: 192021.530.000001 — АИ-92, "
                              "192021.550.000000 — АИ-95. Ловушка распознавания: «бензин» по триграммам ближе к "
                              "«бензидамин» (лекарство) и «бензол».")
    stat_case("analytics_03", "kk", "2024 жылы А4 форматты кеңсе қағазының бір бумасының орташа бағасы қанша болды?",
              PAPER, "u5111", 2024, "А4 кеңсе қағазы", "Единица — пачка (u5111); формат А4 — код 172314.500.000002.")
    s = cp.stats(PEN, "pcs", None)
    stat_case("analytics_04", "ru", "Какая медианная цена канцелярской ручки за весь период?", PEN, "pcs", None,
              "Ручка канцелярская", f"Разброс большой (от {fmt(s['lo'])} до {fmt(s['hi'])} ₸; максимум — вероятно, "
                                    f"ошибка единицы или набор), медиана устойчивее средней.")
    stat_case("analytics_05", "ru", "Статистика цен по ЕНС ТРУ 011334.100.000000 за 2025 год", TOMATO, "kg", 2025,
              "Помидоры", "Код указан явно — распознавание не нужно.")
    stat_case("analytics_06", "ru", "Сколько стоит килограмм салата?", LETTUCE, "kg", None, "Салат")
    stat_case("analytics_07", "ru", "Средняя цена твёрдого хозяйственного мыла в 2026 году", SOAP, "pcs", 2026,
              "Хозяйственное мыло твёрдое", "204131.950.000000 — хозяйственное твёрдое; хозяйственное жидкое — "
                                            "204131.500.000000, туалетное — 204131.900.*; их в ответ включать нельзя.")
    stat_case("analytics_11", "ru", "Сколько стоил литр зимнего дизельного топлива в 2025 году?", DIESEL_WINTER, "L",
              2025, "Дизельное топливо зимнее", "Проверка варианта: зимнее — 192026.510.000001. Ответ по летнему "
                                                "(192026.510.000000, самый частый код) — ошибка.")
    stat_case("analytics_12", "ru", "Какая средняя цена бензина АИ-95 в 2025 году?", PETROL95, "L", 2025,
              "Бензин АИ-95", "Проверка варианта: АИ-95 — 192021.550.000000. Ответ по АИ-92 "
                              "(192021.530.000001, самый частый код) — ошибка.")

    y92 = {yr: cp.stats(PETROL, "L", yr) for yr in (2024, 2025, 2026)}
    y95 = {yr: cp.stats(PETROL95, "L", yr) for yr in (2024, 2025, 2026)}

    def trend(y):
        growth = (float(y[2026]["wavg"]) / float(y[2024]["wavg"]) - 1) * 100
        return (f"2024 — {fmt(y[2024]['wavg'])} ₸, 2025 — {fmt(y[2025]['wavg'])} ₸, 2026 — {fmt(y[2026]['wavg'])} ₸ "
                f"(рост {fmt(growth, 1)}%)")
    cp.add("analytics_08", "analytics", "ru", "Как менялась цена бензина с 2024 по 2026 год?", "analytics",
           f"Средневзвешенная цена литра бензина по маркам: АИ-92 ({PETROL}) — {trend(y92)}; АИ-95 ({PETROL95}) — "
           f"{trend(y95)}.",
           "Бензин — две марки (два кода); их нельзя сводить в одну среднюю, потому что доля АИ-95 меняется по годам. "
           "Ответ только по одной марке неполный.",
           [PETROL, PETROL95], [*(y92[k]["wavg"] for k in y92), *(y95[k]["wavg"] for k in y95)])

    a = cp.obs_agg("year = 2025", {})
    cp.add("analytics_09", "analytics", "ru", "Сколько всего потратили 27 заказчиков на закупки в 2025 году?",
           "analytics",
           f"В 2025 году 27 заказчиков: {pos(a['n'])} на {fmt(a['total'], 0)} ₸ (договоры плюс лоты без договора).",
           "Без предмета закупки — общий итог за год.", [], [a["n"], a["total"]])

    a = cp.obs_agg("customer_bin = '960440000220' AND year = 2024", {})
    cp.add("analytics_10", "analytics", "ru", "Сколько Медицинский центр УДП потратил на закупки в 2024 году?",
           "analytics",
           f"Медицинский центр УДП (960440000220) в 2024 году: {pos(a['n'])} на {fmt(a['total'], 0)} ₸ — "
           f"крупные договоры услуг, поэтому позиций мало, а сумма большая.",
           "Не перепутать с Больницей МЦ УДП (990740002243) и Национальным госпиталем. Крупнейший договор (21349959, 23 184 192 000 ₸) сверен с живым API OWS.",
           ["960440000220"], [a["total"]])

    # =================================================================== COMPARE
    rows = cp.by_customer("enstru_code = %(e)s AND year = 2025", {"e": PHONE})
    cp.add("compare_01", "compare", "ru", "Сравни расходы на услуги телефонной связи между заказчиками в 2025 году",
           "compare",
           f"Услуги телефонной связи (611011.200.000000) в 2025 году закупали {len(rows)} заказчиков; больше всех — "
           f"{rows[0]['customer_bin']} ({fmt(rows[0]['total'], 0)} ₸, {pos(rows[0]['n'])}), меньше всех — "
           f"{rows[-1]['customer_bin']} ({fmt(rows[-1]['total'], 0)} ₸).",
           "Таблица по заказчикам, отсортированная по сумме.",
           [rows[0]["customer_bin"]], [rows[0]["total"]])

    rows = {r["customer_bin"]: r for r in cp.by_customer(
        "customer_bin = ANY(%(b)s) AND year = 2025", {"b": ["990740002243", "231040023028"]})}
    h, g = rows["990740002243"], rows["231040023028"]
    cp.add("compare_02", "compare", "ru",
           "Сравни объём закупок Больницы МЦ УДП и Национального госпиталя за 2025 год", "compare",
           f"2025 год: Больница МЦ УДП (990740002243) — {pos(h['n'])} на {fmt(h['total'], 0)} ₸; Национальный госпиталь "
           f"(231040023028) — {pos(g['n'])} на {fmt(g['total'], 0)} ₸. Больница закупила в "
           f"{fmt(float(h['total']) / float(g['total']), 1)} раза больше.",
           "Два заказчика по имени; оба должны попасть в сравнение.",
           ["990740002243", "231040023028"], [h["total"], g["total"]])

    rows = cp.by_customer(f"{PETROL_ALL} AND base_unit = 'L'", {})
    cp.add("compare_03", "compare", "ru", "Кто из заказчиков больше всех тратит на бензин?", "compare",
           f"Больше всех на бензин (все марки: АИ-92 и АИ-95) за 2024–2026 потратил {rows[0]['customer_bin']} — "
           f"{fmt(rows[0]['total'], 0)} ₸ ({pos(rows[0]['n'])}); на втором месте "
           f"{rows[1]['customer_bin']} — {fmt(rows[1]['total'], 0)} ₸, на третьем {rows[2]['customer_bin']} — "
           f"{fmt(rows[2]['total'], 0)} ₸.",
           "Расходы складываются по всем маркам бензина; только АИ-92 занижает суммы и меняет третье место. "
           "Ловушка распознавания «бензин».", [rows[0]["customer_bin"]], [rows[0]["total"]])

    rows = {r["customer_bin"]: r for r in cp.by_customer(
        "customer_bin = ANY(%(b)s) AND year = 2026", {"b": ["940740000911", "150540000186"]})}
    p, d = rows["940740000911"], rows["150540000186"]
    cp.add("compare_04", "compare", "kk", "2026 жылы «Бурабай» ұлттық паркі мен «Бурабай даму» шығындарын салыстыр",
           "compare",
           f"2026 жыл: «Бурабай» ұлттық паркі (940740000911) — {p['n']} позиция, {fmt(p['total'], 0)} ₸; «Бурабай даму» "
           f"(150540000186) — {d['n']} позиция, {fmt(d['total'], 0)} ₸.",
           "Два похожих названия «Бурабай» — нужны оба БИН, не один.",
           ["940740000911", "150540000186"], [p["total"], d["total"]])

    rows = cp.by_customer("enstru_code = %(e)s AND base_unit = 'L' AND year = 2024", {"e": DIESEL})
    lo = min(rows, key=lambda r: r["wavg"]); hi = max(rows, key=lambda r: r["wavg"])
    cp.add("compare_05", "compare", "ru", "Сравни цены на летнее дизельное топливо у разных заказчиков в 2024 году",
           "compare",
           f"Летнее дизельное топливо (192026.510.000000) в 2024 году покупали {len(rows)} заказчиков; самая высокая "
           f"средневзвешенная цена — у {hi['customer_bin']} ({fmt(hi['wavg'])} ₸/л), самая низкая — у {lo['customer_bin']} "
           f"({fmt(lo['wavg'])} ₸/л).",
           "Сравнение цен корректно, потому что один код и одна единица (литр).",
           [hi["customer_bin"], lo["customer_bin"]], [hi["wavg"], lo["wavg"]])

    rows = cp.by_customer("enstru_code = %(e)s AND base_unit = 'pcs'", {"e": CARTRIDGE})
    cp.add("compare_06", "compare", "ru", "Какой заказчик потратил больше всего на картриджи по ЕНС ТРУ 262040.000.000282?",
           "compare",
           f"По коду 262040.000.000282 (штуки) больше всех потратил {rows[0]['customer_bin']} — {fmt(rows[0]['total'], 0)} ₸ "
           f"({pos(rows[0]['n'])}); всего заказчиков — {len(rows)}.",
           "Код задан явно.", [CARTRIDGE, rows[0]["customer_bin"]], [rows[0]["total"]])

    yrs = {r["year"]: r for r in cp.q("""SELECT year, count(*) n, sum(amount) total FROM mart.price_obs
                                         WHERE year IN (2024, 2025) GROUP BY 1""")}
    ch = (float(yrs[2025]["total"]) / float(yrs[2024]["total"]) - 1) * 100
    cp.add("compare_07", "compare", "ru", "Сравни 2024 и 2025 годы по общему объёму закупок", "compare",
           f"2024 год: {pos(yrs[2024]['n'])} на {fmt(yrs[2024]['total'], 0)} ₸; 2025 год: {pos(yrs[2025]['n'])} на "
           f"{fmt(yrs[2025]['total'], 0)} ₸ ({fmt(ch, 1).replace('-', '−')}% к 2024 году).",
           "Сравниваются годы, а не заказчики: в ответе должны быть итоги обоих лет.",
           [], [yrs[2024]["total"], yrs[2025]["total"]])

    # =================================================================== PRICE ANOMALIES
    a = cp.anomalies("true", {}, 0.30)
    cp.add("price_anomaly_01", "price_anomaly", "ru",
           "Найди закупки с отклонением цены более 30% от средневзвешенной по ЕНС ТРУ", "price_anomaly",
           f"Из {fmt(a['n'], 0)} сопоставимых позиций (товары, ≥3 аналога) у {fmt(a['flagged'], 0)} цена выше "
           f"средневзвешенной более чем на 30%; робастная проверка (z > 3) подтверждает {fmt(a['confirmed'], 0)} "
           f"с оценкой переплаты "
           f"{fmt(a['overpay'], 0)} ₸.",
           "Услуги и работы исключены; подтверждённые случаи идут первыми.",
           [], [a["n"], a["flagged"], a["confirmed"]])

    a = cp.anomalies("customer_bin = %(b)s AND year = 2025", {"b": "990740002243"}, 0.50)
    cp.add("price_anomaly_02", "price_anomaly", "ru",
           "У Больницы МЦ УДП (990740002243) в 2025 году есть закупки с ценой выше средней более чем на 50%?",
           "price_anomaly",
           f"Да: в 2025 году из {fmt(a['n'], 0)} сопоставимых позиций Больницы МЦ УДП {fmt(a['flagged'], 0)} дороже средневзвешенной "
           f"аналогов более чем на 50%; подтверждено робастно {a['confirmed']}, оценка переплаты {fmt(a['overpay'], 0)} ₸.",
           "Порог 50% из текста вопроса.", ["990740002243"], [a["n"], a["flagged"], a["confirmed"]])

    a = cp.anomalies(PETROL_ALL, {}, 0.30)
    cp.add("price_anomaly_03", "price_anomaly", "ru", "Есть ли завышенные цены на бензин?", "price_anomaly",
           (f"Нет. По бензину (все марки: АИ-92 и АИ-95) сравнено {pos(a['n'])}, и ни одна не дороже средневзвешенной "
            f"аналогов своей марки более чем на 30%: цены на бензин у заказчиков очень однородные." if a["flagged"] == 0 else
            f"По бензину (все марки) из {of_pos(a['n'])} {a['flagged']} дороже средневзвешенной более чем на 30%, "
            f"робастно подтверждено {a['confirmed']}."),
           "Аналоги — внутри своей марки (АИ-92 и АИ-95 — разные коды). Ловушка распознавания «бензин». "
           "Правильный ответ — «завышенных цен нет», а не список других товаров.",
           [], [a["n"]])

    a = cp.anomalies("customer_bin = %(b)s", {"b": "980440001034"}, 0.30)
    cp.add("price_anomaly_04", "price_anomaly", "ru", "Найди аномалии цен у Автохозяйства ДХО", "price_anomaly",
           f"У Автохозяйства ДХО (980440001034) из {of_pos(a['n'])} {fmt(a['flagged'], 0)} дороже средневзвешенной более чем на 30%; "
           f"подтверждено {a['confirmed']}, оценка переплаты {fmt(a['overpay'], 0)} ₸.",
           "Не путать с Автохозяйством УДП (970940001378).", ["980440001034"], [a["n"], a["flagged"], a["confirmed"]])

    a = cp.anomalies("year = 2026", {}, 0.25)
    cp.add("price_anomaly_05", "price_anomaly", "kk",
           "2026 жылы бағасы орташа өлшенгеннен 25%-дан астам ауытқыған сатып алуларды тап", "price_anomaly",
           f"2026 жылы {fmt(a['n'], 0)} салыстырмалы позицияның {fmt(a['flagged'], 0)}-інде баға 25%-дан астам жоғары; оның "
           f"{a['confirmed']}-і робастты тексеріспен расталды, ықтимал артық төлем — {fmt(a['overpay'], 0)} ₸.",
           "Порог 25% и год 2026 из казахского вопроса.", [], [a["n"], a["flagged"], a["confirmed"]])

    top5 = cp.q("""SELECT obj_id, round(price::numeric, 2) price, round(peer_weighted_avg::numeric, 2) peer,
                          round((deviation * 100)::numeric, 1) dev
                   FROM mart.price_deviation
                   WHERE NOT is_service AND enstru_code = %s AND deviation > 0.3
                   ORDER BY likely_unit_error, coalesce(robust_z_log > 3, false) DESC,
                            (amount - peer_weighted_avg * quantity) DESC NULLS LAST LIMIT 5""", (CARTRIDGE,))
    cp.add("price_anomaly_06", "price_anomaly", "ru", "Топ-5 самых переплаченных закупок картриджей", "price_anomaly",
           "Топ-5 по картриджам (262040.000.000282): " + "; ".join(
               f"{r['obj_id']} — {fmt(r['price'])} ₸ против {fmt(r['peer'])} ₸ (+{fmt(r['dev'], 1)}%)" for r in top5) + ".",
           "Ровно 5 строк; первая — подтверждённая с наибольшей переплатой. Код «картридж» с наибольшим числом закупок — "
           "262040.000.000282.", [r["obj_id"] for r in top5[:3]], [])

    a = cp.anomalies("true", {}, 0.50, direction="under")
    cp.add("price_anomaly_07", "price_anomaly", "ru",
           "Найди закупки, где цена ниже рынка более чем на 50% (возможный демпинг)", "price_anomaly",
           f"Дешевле средневзвешенной аналогов более чем на 50%: {fmt(a['flagged'], 0)} из {fmt(a['n'], 0)} позиций; робастно "
           f"подтверждено (z < −3) {a['confirmed']}.",
           "Ловушка: направление «ниже» — нужны заниженные цены, а не завышенные.", [], [a["flagged"]])

    a = cp.anomalies(f"{DIESEL_ALL} AND year = 2024", {}, 0.30)
    hits = cp.q(f"""SELECT obj_id, customer_bin, enstru_code, round(price::numeric, 2) price,
                           round(peer_weighted_avg::numeric, 2) peer, round((deviation * 100)::numeric, 1) dev
                    FROM mart.price_deviation WHERE NOT is_service AND {DIESEL_ALL} AND year = 2024
                      AND deviation > 0.3 ORDER BY deviation DESC""")
    cp.add("price_anomaly_08", "price_anomaly", "ru", "Есть ли аномально дорогие закупки дизельного топлива в 2024 году?",
           "price_anomaly",
           f"Дизельное топливо (все сезоны: летнее, зимнее, межсезонное, арктическое) в 2024 году: из {of_pos(a['n'])} дороже средневзвешенной "
           f"аналогов своего кода более чем на 30% — {a['flagged']}, робастно подтверждено {a['confirmed']}"
           + (": " + "; ".join(f"{h['obj_id']} ({h['enstru_code']}) у {h['customer_bin']} — {fmt(h['price'])} ₸/л против "
                               f"{fmt(h['peer'])} ₸/л (+{fmt(h['dev'], 1)}%)" for h in hits) if hits else "") + ".",
           "Аналоги — внутри своего кода (летнее, зимнее, межсезонное и арктическое топливо — разные коды; межсезонное — 192026.520.000000, вне префикса 192026.510). Ответ должен назвать "
           "именно эти закупки.", [h["obj_id"] for h in hits], [a["n"]])

    # =================================================================== FAIR PRICE
    def fair_case(cid, lang, lot_number, verdict, question=None):
        L = cp.lot(lot_number)
        kind = "по договору" if L["contract"] else "лота (договор не заключён)"
        dev = (L["price"] / L["peer_median"] - 1) * 100
        dev_s = "0" if abs(dev) < 0.5 else ("+" if dev > 0 else "−") + fmt(abs(dev), 0)
        unit = UNIT_RU.get(L["base_unit"], L["base_unit"])
        words = {"fair": "цена в рыночном диапазоне", "overpriced_anomaly": "цена аномально завышена",
                 "underpriced_anomaly": "цена аномально занижена (риск демпинга)"}
        words_kk = {"fair": "баға нарықтық диапазонда", "overpriced_anomaly": "баға аномальды жоғары",
                    "underpriced_anomaly": "баға аномальды төмен"}
        q = question or (f"Оцени адекватность цены лота №{lot_number} относительно аналогичных закупок других ведомств"
                         if lang == "ru" else
                         f"{lot_number} лотының бағасы басқа ведомстволардың ұқсас сатып алуларымен салыстырғанда әділ ме?")
        ans = (f"Лот {lot_number} ({L['name_clean']}, ЕНС ТРУ {L['enstru_code']}, заказчик {L['customer_bin']}): цена {kind} "
               f"{fmt(L['price'])} ₸ за {unit}; медиана цен других заказчиков за ±18 мес. — "
               f"{fmt(L['peer_median'])} ₸ (N = {L['n_peers']}, отклонение {dev_s}%). Вывод: {words[verdict]}."
               if lang == "ru" else
               f"{lot_number} лоты ({L['name_clean']}, ЕНС ТРУ {L['enstru_code']}, тапсырыс беруші {L['customer_bin']}): "
               f"баға {fmt(L['price'])} ₸; басқа тапсырыс берушілердің медианасы {fmt(L['peer_median'])} ₸ "
               f"(N = {L['n_peers']}). Қорытынды: {words_kk[verdict]}.")
        note = (" У самого лота нет кода ЕНС ТРУ — он есть только в строке договора; агент должен взять код оттуда, "
                "а не отвечать «сравнение невозможно»." if L.get("code_from_contract") else "")
        cp.add(cid, "fair_price", lang, q, "fair_price", ans,
               f"Ожидаемый вердикт: {verdict} (проверяется автоматически). Fair Price агента включает поправки на "
               f"инфляцию и регион, поэтому может отличаться от сырой медианы на несколько процентов; вердикт должен "
               f"совпасть." + note,
               [lot_number, L["customer_bin"], L["enstru_code"]], [L["price"]], expect={"verdict": verdict})

    fair_case("fair_price_01", "ru", "73177002-ЗЦП1", "fair")
    fair_case("fair_price_02", "ru", "73016695-ЗЦП1", "fair",
              "Насколько справедлива цена бумаги в лоте 73016695-ЗЦП1?")
    fair_case("fair_price_03", "kk", "69672998-ОИ2", "fair")
    fair_case("fair_price_04", "ru", "70562383-ЗЦП1", "overpriced_anomaly",
              "Не завышена ли цена ручек в лоте №70562383-ЗЦП1?")
    fair_case("fair_price_05", "ru", "87544706-ЗЦП4", "overpriced_anomaly")
    fair_case("fair_price_06", "ru", "79649992-ЗЦП1", "underpriced_anomaly",
              "Обоснована ли цена картриджа по лоту 79649992-ЗЦП1 по сравнению с другими заказчиками?")
    fair_case("fair_price_07", "ru", "74823661-ЗЦП1", "fair")

    cp.add("fair_price_08", "fair_price", "ru", "Оцени справедливость цены лота №12345678-ЗЦП9", "fair_price",
           "Лот 12345678-ЗЦП9 в хранилище не найден (2024–2026, 27 заказчиков); оценка невозможна.",
           "Ожидается сообщение «лот не найден», без выдуманных цифр (по живому API OWS такого лота тоже нет).",
           ["12345678-ЗЦП9"], [], expect={"found": False})

    cp.add("fair_price_09", "fair_price", "ru", "Справедливая ли цена на бензин у Автохозяйства УДП?", "fair_price",
           "Для оценки справедливости нужен конкретный лот: укажите номер лота (например, «лот №63262929-ОИ2»).",
           "Номер лота не указан — агент должен попросить его, а не считать Fair Price по случайной позиции.", [], [],
           expect={"found": False})

    # =================================================================== VOLUME ANOMALIES
    def vol_summary(sel):
        sel = [v for v in sel if v["base_unit"] not in SERVICE_UNITS]
        fl = [v for v in sel if v["flagged"]]
        co = [v for v in fl if v["confirmed"]]
        return sel, fl, co, sum(v["extra"] for v in co)

    def top_line(co):
        t = max(co, key=lambda v: v["extra"])
        prev = ", ".join(f"{y}: {fmt(q, 0)}" for y, q in sorted(t["prev"].items()))
        return t, (f"крупнейший случай — «{t['name']}» ({t['enstru_code']}, {t['base_unit']}) у {t['customer_bin']}: "
                   f"{t['year']} — {fmt(t['qty'], 0)} против {prev} (×{fmt(t['ratio'])}), доп. расходы {fmt(t['extra'], 0)} ₸")

    sel, fl, co, extra = vol_summary(vol)
    t, line = top_line(co)
    cp.add("volume_anomaly_01", "volume_anomaly", "ru",
           "Выяви нетипичное завышение количества ТРУ по сравнению с предыдущими годами", "volume_anomaly",
           f"Сравнено {rows_(len(sel))} «заказчик × ЕНС ТРУ × год» (плановые объёмы, без услуг и работ); рост более чем в "
           f"1,5 раза — {fmt(len(fl), 0)}; подтверждено как нетипичный и существенный — {len(co)} с доп. плановыми расходами "
           f"{fmt(extra, 0)} ₸; {line}.",
           "Рост считается к медиане предыдущих лет; подтверждённые ряды идут первыми.",
           [t["enstru_code"]], [len(sel), len(fl), len(co)])

    sel, fl, co, extra = vol_summary([v for v in vol if v["customer_bin"] == "990740002243" and v["year"] == 2025])
    t, line = top_line(co)
    cp.add("volume_anomaly_02", "volume_anomaly", "ru",
           "У Больницы МЦ УДП в 2025 году были закупки с резко выросшим количеством по сравнению с 2024?",
           "volume_anomaly",
           f"Да: из {of_rows(len(sel))} Больницы МЦ УДП за 2025 год рост более чем в 1,5 раза — {len(fl)}, подтверждено "
           f"{len(co)} (рост ≥ 3× и ≥ 1 млн ₸ доп. расходов), всего {fmt(extra, 0)} ₸; {line}.",
           "Для 2025 года база — один предыдущий год, z-оценки нет.",
           ["990740002243"], [len(sel), len(fl), len(co)])

    petrol = {r["enstru_code"] for r in cp.q(f"SELECT DISTINCT enstru_code FROM mart.volume_yearly WHERE {PETROL_ALL}")}
    sel, fl, co, extra = vol_summary([v for v in vol if v["enstru_code"] in petrol])
    flagged = sorted(fl, key=lambda v: v["ratio"], reverse=True)
    cp.add("volume_anomaly_03", "volume_anomaly", "ru", "Был ли у кого-то из заказчиков нетипичный рост объёмов закупки бензина?",
           "volume_anomaly",
           f"По бензину сравнено {rows_(len(sel))}; рост более чем в 1,5 раза — {len(fl)}, подтверждено {len(co)}"
           + (f"; наибольший рост — у {flagged[0]['customer_bin']} в {flagged[0]['year']} году (×{fmt(flagged[0]['ratio'])})"
              if flagged else "") + ".",
           "Все марки бензина (АИ-92 и АИ-95); рост считается внутри каждой марки.", [], [len(sel), len(fl)])

    sel, fl, co, extra = vol_summary([v for v in vol if v["customer_bin"] == "970940001378"])
    cp.add("volume_anomaly_04", "volume_anomaly", "kk",
           "ҚР Президенті Іс басқармасының Автошаруашылығында сатып алу көлемінің күрт өсуі болды ма?", "volume_anomaly",
           f"Автошаруашылық (970940001378): {len(sel)} қатар салыстырылды, 1,5 еседен астам өсу — {len(fl)}, "
           f"расталғаны — {len(co)}, қосымша жоспарлы шығыстар — {fmt(extra, 0)} ₸.",
           "Казахский вопрос; заказчик по имени (970940001378).", ["970940001378"], [len(sel), len(fl), len(co)])

    sel, fl, co, extra = vol_summary([v for v in vol if v["year"] == 2026])
    cp.add("volume_anomaly_05", "volume_anomaly", "ru", "Какие закупки в 2026 году нетипично выросли по объёму?",
           "volume_anomaly",
           f"2026 год: сравнено {rows_(len(sel))}, рост более чем в 1,5 раза (с z > 3 при двух предыдущих годах) — {len(fl)}, "
           f"подтверждено {len(co)} с доп. плановыми расходами {fmt(extra, 0)} ₸.",
           "Для 2026 года база — 2024 и 2025 годы; нужна z-оценка.", [], [len(sel), len(fl), len(co)])

    sel, fl, co, extra = vol_summary([v for v in vol if v["customer_bin"] == "020440003656"])
    cp.add("volume_anomaly_06", "volume_anomaly", "ru",
           "Есть ли нетипичный рост количества закупаемых товаров у Дирекции административных зданий Администрации "
           "Президента и Правительства?", "volume_anomaly",
           f"Дирекция административных зданий Администрации Президента и Правительства (020440003656): {rows_(len(sel))}, "
           f"рост более чем в 1,5 раза — {len(fl)}, подтверждено {len(co)}, доп. плановые расходы {fmt(extra, 0)} ₸.",
           "Ловушка: похожие названия — Алматинский филиал этой же дирекции (210941010761) и РГП «ДАЗ ДХО» "
           "(030440003698); ни один из них в ответ попасть не должен.",
           ["020440003656"], [len(sel), len(fl), len(co)])

    # an intuitive case: a bulk consumable (hundreds of kg / litres / pieces) at a customer not used above
    t = max((v for v in vol if v["confirmed"] and v["year"] == 2025 and v["base_unit"] in ("kg", "L", "pcs")
             and min(v["prev"].values()) >= 100 and v["customer_bin"] != "990740002243"), key=lambda v: v["extra"])
    unit = UNIT_RU.get(t["base_unit"], t["base_unit"])
    cp.add("volume_anomaly_07", "volume_anomaly", "ru",
           f"Почему у заказчика {t['customer_bin']} в {t['year']} году так вырос плановый объём по позиции «{t['name']}»? "
           f"Это аномалия?", "volume_anomaly",
           f"Да, это подтверждённая аномалия: в {t['year']} году запланировано {fmt(t['qty'], 0)} {unit} против "
           f"{fmt(next(iter(t['prev'].values())), 0)} {unit} в 2024 году (×{fmt(t['ratio'])}), доп. плановые расходы "
           f"{fmt(t['extra'], 0)} ₸. Причину (новый объект, смена фасовки, ошибка единиц) по данным установить нельзя.",
           "Агент не должен выдумывать причину роста.", [t["customer_bin"]], [t["qty"]])

    # =================================================================== EDGE CASES
    cp.add("edge_01", "edge", "ru", "Какая погода будет завтра в Астане?", "search",
           "Вопрос вне предметной области: агент отвечает только по данным госзакупок 27 заказчиков за 2024–2026.",
           "Ожидается отказ или пояснение области; любые цифры о закупках — ошибка. Поле intent условное.", [], [],
           expect={"found": False})

    cp.add("edge_02", "edge", "ru", "Покажи закупки акимата города Алматы за 2025 год", "search",
           "Акимата Алматы нет среди 27 заказчиков в хранилище; данных нет.",
           "Ловушка: поиск по имени может зацепиться за «Алматы» в названиях 171041003124 (филиал «Ок-Жетпес» Алматы) "
           "или 210941010761 (Алматинский филиал ДАЗ). Подстановка другого заказчика — ошибка.", [], [],
           expect={"found": False})


CORPUS_NOTES = [
             "Коды ЕНС ТРУ часто различают варианты товара только описанием пункта плана: бензин 192021.530.000001 — "
             "АИ-92, 192021.550.000000 — АИ-95; дизельное топливо 192026.510.000000 — летнее, .000001 — зимнее; "
             "бумага 172314.500.000002 — А4, .000001 — А3. Вопрос о варианте отвечается по его коду, вопрос о товаре "
             "в целом — по всем кодам.", "",
             "Сверено с живым API OWS v3: суммы в search_03 и analytics_10, все лоты fair_price (заказчик, предмет, "
             "количество), отсутствие лота 12345678-ЗЦП9 и данных ЦКБ за 2026 год.", ""]


def write_md(cases: list[dict], path: Path, snapshot: str, title: str = "Корпус вопросов для оценки качества ответов TenderAI",
             notes: list[str] = CORPUS_NOTES) -> None:
    titles = {"search": "Поиск", "analytics": "Аналитика цен и сумм", "compare": "Сравнение",
              "price_anomaly": "Ценовые аномалии", "fair_price": "Справедливость цены (Fair Price)",
              "volume_anomaly": "Аномалии объёма", "edge": "Пограничные случаи"}
    script = "build_corpus.py" if path.stem == "corpus" else f"build_{path.stem}.py"
    lines = [f"# {title}", "",
             f"Снимок данных: {snapshot}. Сгенерировано `eval/{script}`; ответы посчитаны SQL-запросами к "
             "хранилищу независимо от кода агента.", "",
             f"Всего вопросов: {len(cases)}. Автоматически проверяются факты (БИН, коды, числа; факты, которые уже "
             "есть в тексте вопроса, не засчитываются), вердикты Fair Price и отказы "
             f"(`python eval/run_eval.py eval/{path.stem}.jsonl`); остальное — по полю «Что проверить».", "", *notes]
    cat = None
    for c in cases:
        if c["category"] != cat:
            cat = c["category"]
            lines += ["", f"## {titles[cat]}", ""]
        facts = ", ".join([f"`{s}`" for s in c["facts"]["strings"]] + [fmt(n) for n in c["facts"]["numbers"]]) or "—"
        lines += [f"### {c['id']} ({c['lang']}, intent `{c['intent']}`)", "", f"**Вопрос:** {c['question']}", "",
                  f"**Эталонный ответ:** {c['reference_answer']}", "", f"**Проверяемые факты:** {facts}", ""]
        if c.get("expect"):
            exp = "; ".join(f"вердикт `{v}`" if k == "verdict" else ("отказ / «данных нет»" if v is False else f"{k} = {v}")
                            for k, v in c["expect"].items())
            lines += [f"**Проверяется автоматически:** {exp}", ""]
        if c["expected_behavior"]:
            lines += [f"**Что проверить:** {c['expected_behavior']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as conn:
        cp = Corpus(conn)
        build(cp)
        snap = conn.execute("SELECT max(fetched_at)::date d FROM raw.ows_object").fetchone()["d"]
    snapshot = snap.isoformat() if isinstance(snap, date) else str(snap)
    with open(OUT / "corpus.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in cp.cases)
    write_md(cp.cases, OUT / "corpus.md", snapshot)
    print(f"{len(cp.cases)} cases -> eval/corpus.jsonl, eval/corpus.md (data as of {snapshot})")


if __name__ == "__main__":
    main()
