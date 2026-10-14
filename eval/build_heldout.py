"""Build the held-out set: fresh questions written after the agent was finished, on customers, items and wordings
that the corpus, the paraphrase set and the router's few-shot examples do not use.

The corpus doubled as a development set (rules were added when its questions failed), so its score is optimistic.
This set is the honest measure: do NOT change the agent to make a held-out question pass. If a held-out failure
reveals a general bug, fix it, then write new held-out questions and keep the old ones in the corpus.

Reference answers use the same independent SQL as eval/build_corpus.py. Fair-price lots are picked by a fixed rule
(first lots in md5 order whose contract price is clearly at, far above or far below the median price of other
customers), not by looking at the agent's output. The comparison set follows the documented Fair Price hierarchy
(docs/analytics.md, step 1: same delivery city, else same oblast, else national; N >= 5), re-implemented here on raw
prices; the margins (±5%, +100…+400%, ≤ −60%) leave room for the agent's inflation and regional adjustments.
Only homogeneous comparison sets qualify (IQR / median <= 0.8, N >= 8).

Usage: python eval/build_heldout.py        # writes eval/heldout.jsonl and eval/heldout.md
       python eval/run_eval.py eval/heldout.jsonl --repeats 3 --out eval/report_heldout.json
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).parent))
from build_corpus import SERVICE_UNITS, UNIT_RU, Corpus, cust, fmt, n_, pos, rows_, write_md

from tenderai.config import get_settings

OUT = Path(__file__).parent
POTATO, POTATO_FROZEN, ONION = "011351.100.000000", "103111.100.000000", "011343.100.000000"   # свежий; замороженный
# a question about a product in general covers every variant code with its ENSTRU name (as in build_corpus.py)
POTATO_ALL = "enstru_code IN (SELECT code FROM core.ref_enstru WHERE lower(name_ru) = 'картофель')"
STAPLER = "282323.900.000002"   # степлер канцелярский механический (282323.900.* also holds hole punches, laminators)
BATTERY_AA = "272011.900.000004"                                # батарейка, тип АА (по описанию пункта плана)


def used_lots() -> set[str]:
    """Lot numbers that appear in any other evaluation set."""
    out = set()
    for f in ("corpus.jsonl", "golden.jsonl", "paraphrases.jsonl"):
        p = OUT / f
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                out.update(s for s in json.loads(line)["facts"]["strings"] if "-" in s and not s.startswith("contract"))
    return out


def comparison(cp: Corpus, lot_number: str) -> dict:
    """The lot's price against the median of other customers' prices, at the first level of the hierarchy with
    N >= 5: same city -> same oblast -> national (same ENSTRU and unit, ±18 months)."""
    L = cp.lot(lot_number)
    where = cp.one("""SELECT kato_code, region_code FROM core.lot WHERE NOT is_deleted AND lot_number = %s
                      ORDER BY in_scope DESC, id DESC LIMIT 1""", (lot_number,))
    cu = cp.one("""SELECT c.sign_date FROM core.contract_unit u JOIN core.contract c ON c.id = u.contract_id AND c.is_latest
                   JOIN core.lot l ON l.id = u.lot_id
                   WHERE l.lot_number = %s AND NOT u.deleted AND u.unit_price_base > 0 ORDER BY u.id LIMIT 1""",
                (lot_number,))
    when = (cu["sign_date"] if cu else None)
    peers = cp.q("""SELECT price, month, city_kato, region_code FROM mart.price_obs
                    WHERE enstru_code = %s AND base_unit = %s AND customer_bin <> %s""",
                 (L["enstru_code"], L["base_unit"], L["customer_bin"]))
    if when:
        peers = [r for r in peers if abs((r["month"].year - when.year) * 12 + r["month"].month - when.month) <= 18]
    levels = [("тот же город", [r for r in peers if where["kato_code"] and r["city_kato"] == where["kato_code"]]),
              ("та же область", [r for r in peers if where["region_code"] and r["region_code"] == where["region_code"]]),
              ("вся страна", peers)]
    level, sel = next(((n, x) for n, x in levels if len(x) >= 5), levels[-1])
    prices = sorted(float(r["price"]) for r in sel)
    med = statistics.median(prices) if prices else None
    q = statistics.quantiles(prices, n=4) if len(prices) >= 2 else [med, med, med]
    return {**L, "level": level, "n_level": len(sel), "median_level": med,
            "dispersion": (q[2] - q[0]) / med if med else None,     # IQR / median of the comparison set
            "deviation": L["price"] / med - 1 if med else None}


def pick_lots(cp: Corpus) -> dict[str, dict]:
    """One lot per verdict, by a fixed rule over 2025 contract lines of goods in plain units (pieces, kg, litres) with
    >= 20 peers. Overpriced is capped at +400%: beyond ~21x the price is usually a unit error, not an overpayment."""
    taken = used_lots()
    cands = cp.q("""SELECT DISTINCT ON (l.lot_number) l.lot_number, md5(l.lot_number) h
                    FROM mart.price_obs o JOIN core.lot l ON l.id = o.lot_id
                    WHERE o.source = 'contract' AND o.year = 2025 AND o.base_unit IN ('pcs', 'kg', 'L')
                      -- one item per lot: a multi-item lot ("Продукты питания", 24 lines) has no single price
                      AND (SELECT count(*) FROM core.contract_unit u WHERE u.lot_id = l.id AND NOT u.deleted) = 1
                      AND (SELECT count(*) FROM mart.price_obs p WHERE p.enstru_code = o.enstru_code
                           AND p.base_unit = o.base_unit AND p.customer_bin <> o.customer_bin) >= 20
                    ORDER BY l.lot_number""")
    rules = {"fair": lambda d: abs(d) <= 0.05, "overpriced_anomaly": lambda d: 1.0 <= d <= 4.0,
             "underpriced_anomaly": lambda d: d <= -0.6}
    found: dict[str, dict] = {}
    for c in sorted(cands, key=lambda r: r["h"]):
        if c["lot_number"] in taken:
            continue
        L = comparison(cp, c["lot_number"])
        # homogeneous comparison sets only: in a mixed code ("spare parts", "books") a price gap is not overpricing
        if (not L.get("contract") or L["deviation"] is None or L["n_peers"] < 20 or L["n_level"] < 8
                or L["dispersion"] > 0.8):
            continue
        dev = L["deviation"]
        for verdict, ok in rules.items():
            if verdict not in found and ok(dev):
                found[verdict] = {**L, "deviation": dev}
        if len(found) == len(rules):
            break
    return found


def build(cp: Corpus) -> None:
    vol = cp.volume_series()

    # ------------------------------------------------------------------ search
    a = cp.obs_agg("customer_bin = '100140011059' AND year = 2025", {})
    cp.add("h_search_01", "search", "ru", "Что закупал детский сад «Қарлығаш» в 2025 году?", "search",
           f"Детский сад «Қарлығаш» МЦ УДП (100140011059) в 2025 году: {pos(a['n'])} на {fmt(a['total'], 0)} ₸.",
           "Заказчик назван по имени с казахской буквой «Қ».", ["100140011059"], [a["n"], a["total"]])

    a = cp.obs_agg("customer_bin = '000740001307' AND year = 2026", {})
    cp.add("h_search_02", "search", "kk", "2026 жылы «Бүркіт» авиакомпаниясы не сатып алды?", "search",
           f"«Бүркіт» мемлекеттік авиакомпаниясы (000740001307) 2026 жылы {a['n']} позиция бойынша "
           f"{fmt(a['total'], 0)} ₸ сатып алды.",
           "Казахское название (по-русски — «Беркут»).", ["000740001307"], [a["n"], a["total"]])

    top = cp.one("""SELECT obj_id, name_clean, amount, url FROM mart.price_obs
                    WHERE customer_bin = '210240033968' AND year = 2024 ORDER BY amount DESC LIMIT 1""")
    cp.add("h_search_03", "search", "ru", "Самая дорогая покупка Инженерно-технического центра УДП в 2024 году?",
           "search",
           f"Крупнейшая позиция ИТЦ УДП (210240033968) в 2024 году — {top['obj_id']} «{top['name_clean']}» на "
           f"{fmt(top['amount'], 0)} ₸ ({top['url']}).",
           "Первой в Top-K должна стоять эта позиция.", ["210240033968", top["obj_id"]], [top["amount"]])

    # ------------------------------------------------------------------ analytics
    def stat_case(cid, lang, question, enstru, unit, year, label, behavior=""):
        s = cp.stats(enstru, unit, year)
        per = str(year) if year else "2024–2026"
        cp.add(cid, "analytics", lang, question, "analytics",
               f"{label} (ЕНС ТРУ {enstru}, цена за {UNIT_RU.get(unit, unit)}) за {per}: средневзвешенная цена "
               f"{fmt(s['wavg'])} ₸, медиана {fmt(s['med'])} ₸ (Q1–Q3: {fmt(s['q1'])}–{fmt(s['q3'])} ₸); "
               f"{n_(s['n'], 'закупка', 'закупки', 'закупок')} у {cust(s['customers'])}.",
               ("Цена без НДС за базовую единицу. " + behavior).strip(), [enstru], [s["wavg"], s["med"], s["n"]])

    fresh, frozen = cp.stats(POTATO, "kg", 2025), cp.stats(POTATO_FROZEN, "kg", 2025)
    cp.add("h_analytics_01", "analytics", "ru", "Средняя цена картофеля за килограмм в 2025 году", "analytics",
           f"Картофель, 2025, цена за кг — два варианта: свежий ({POTATO}) — средневзвешенная {fmt(fresh['wavg'])} ₸, "
           f"медиана {fmt(fresh['med'])} ₸, {fresh['n']} закупок; замороженный ({POTATO_FROZEN}) — средневзвешенная "
           f"{fmt(frozen['wavg'])} ₸, медиана {fmt(frozen['med'])} ₸, {frozen['n']} закупок.",
           "Цена без НДС за базовую единицу. Варианты — разные товары, их цены показываются отдельно.",
           [POTATO, POTATO_FROZEN], [fresh["wavg"], fresh["med"], fresh["n"], frozen["wavg"], frozen["med"], frozen["n"]])
    stat_case("h_analytics_02", "kk", "2024 жылы пияздың бір келісі орташа қанша тұрды?", ONION, "kg", 2024,
              "Лук репчатый", "«Пияз» — лук репчатый; вопрос на казахском.")
    stat_case("h_analytics_03", "ru", "Какая медианная цена канцелярского степлера?", STAPLER, "pcs", None,
              "Степлер канцелярский")
    stat_case("h_analytics_04", "ru", "Сколько стоит одна батарейка типа АА?", BATTERY_AA, "pcs", None,
              "Батарейка АА", "Тип батарейки различается только описанием пункта плана (ААА, АА, крона, D…); "
                              "ответ по другому типу — ошибка.")
    a = cp.obs_agg("customer_bin = '020240002363' AND year = 2025", {})
    cp.add("h_analytics_05", "analytics", "ru", "Сколько потратил Отель «Алатау» в 2025 году?", "analytics",
           f"АО «Отель «Алатау» (020240002363) в 2025 году: {pos(a['n'])} на {fmt(a['total'], 0)} ₸.",
           "", ["020240002363"], [a["n"], a["total"]])

    # ------------------------------------------------------------------ compare
    rows = {r["customer_bin"]: r for r in cp.by_customer(
        "customer_bin = ANY(%(b)s) AND year = 2025", {"b": ["210240019348", "050740004819"]})}
    i, k = rows["210240019348"], rows["050740004819"]
    cp.add("h_compare_01", "compare", "ru", "Сравни закупки Института парламентаризма и ТОО «Казстройсистема» за 2025 год",
           "compare",
           f"2025 год: Институт парламентаризма (210240019348) — {pos(i['n'])} на {fmt(i['total'], 0)} ₸; "
           f"ТОО «Казстройсистема» (050740004819) — {pos(k['n'])} на {fmt(k['total'], 0)} ₸.",
           "Оба заказчика должны попасть в сравнение.", ["210240019348", "050740004819"], [i["total"], k["total"]])

    rows = cp.by_customer("enstru_code = %(e)s", {"e": STAPLER})
    cp.add("h_compare_02", "compare", "ru", "Кто из заказчиков потратил больше всего на канцелярские степлеры?",
           "compare",
           f"Больше всех на канцелярские степлеры ({STAPLER}) за 2024–2026 потратил {rows[0]['customer_bin']} — "
           f"{fmt(rows[0]['total'], 0)} ₸ ({pos(rows[0]['n'])}); всего заказчиков — {len(rows)}.",
           "", [rows[0]["customer_bin"]], [rows[0]["total"]])

    yrs = {r["year"]: r for r in cp.q("""SELECT year, count(*) n, sum(amount) total FROM mart.price_obs
                                         WHERE customer_bin = '140340016539' AND year IN (2024, 2025) GROUP BY 1""")}
    ch = (float(yrs[2025]["total"]) / float(yrs[2024]["total"]) - 1) * 100
    cp.add("h_compare_03", "compare", "ru", "Как изменились расходы Президентского центра в 2025 году по сравнению с 2024?",
           "compare",
           f"Президентский центр (140340016539): 2024 — {pos(yrs[2024]['n'])} на {fmt(yrs[2024]['total'], 0)} ₸; "
           f"2025 — {pos(yrs[2025]['n'])} на {fmt(yrs[2025]['total'], 0)} ₸ ({fmt(ch, 1)}%).",
           "Сравнение двух лет одного заказчика.", ["140340016539"], [yrs[2024]["total"], yrs[2025]["total"]])

    # ------------------------------------------------------------------ price anomalies
    a = cp.anomalies("customer_bin = '971040001050'", {}, 0.40)
    cp.add("h_price_anomaly_01", "price_anomaly", "ru",
           "Есть ли у Дирекции государственных резиденций закупки дороже средневзвешенной более чем на 40%?",
           "price_anomaly",
           f"Да: у Дирекции государственных резиденций (971040001050) из {fmt(a['n'], 0)} сопоставимых позиций "
           f"{fmt(a['flagged'], 0)} дороже средневзвешенной аналогов более чем на 40%; подтверждено робастно "
           f"{a['confirmed']}, оценка переплаты {fmt(a['overpay'], 0)} ₸.",
           "Порог 40% из вопроса.", ["971040001050"], [a["n"], a["flagged"], a["confirmed"]])

    a = cp.anomalies(POTATO_ALL, {}, 0.30)
    cp.add("h_price_anomaly_02", "price_anomaly", "kk",
           "Картоп сатып алуларында бағасы орташа өлшенгеннен 30%-дан астам жоғары болғандары бар ма?", "price_anomaly",
           f"Картоп (барлық нұсқалары: жаңа және мұздатылған): {fmt(a['n'], 0)} позицияның {fmt(a['flagged'], 0)}-інде баға 30%-дан астам жоғары; "
           f"робастты расталғаны — {a['confirmed']}.",
           "«Картоп» — картофель.", [], [a["n"], a["flagged"]])

    a = cp.anomalies("enstru_code = %(e)s", {"e": STAPLER}, 0.40, direction="under")
    cp.add("h_price_anomaly_03", "price_anomaly", "ru",
           "Найди закупки канцелярских степлеров, которые дешевле аналогов более чем на 40%", "price_anomaly",
           f"Степлер канцелярский ({STAPLER}): из {fmt(a['n'], 0)} позиций дешевле средневзвешенной аналогов более чем "
           f"на 40% — {fmt(a['flagged'], 0)}, робастно подтверждено {a['confirmed']}.",
           "Направление «дешевле» (заниженные цены).", [], [a["n"], a["flagged"]])

    # ------------------------------------------------------------------ volume anomalies
    def vol_case(cid, lang, question, sel_, label, strings):
        sel = [v for v in sel_ if v["base_unit"] not in SERVICE_UNITS]
        fl = [v for v in sel if v["flagged"]]
        co = [v for v in fl if v["confirmed"]]
        cp.add(cid, "volume_anomaly", lang, question, "volume_anomaly",
               f"{label}: {rows_(len(sel))}, рост более чем в 1,5 раза — {len(fl)}, подтверждено {len(co)}, "
               f"доп. плановые расходы {fmt(sum(v['extra'] for v in co), 0)} ₸.",
               "", strings, [len(sel), len(fl), len(co)])

    vol_case("h_volume_anomaly_01", "ru", "Резко ли вырос плановый объём закупок у Отеля «Алатау»?",
             [v for v in vol if v["customer_bin"] == "020240002363"], "Отель «Алатау» (020240002363)", ["020240002363"])
    vol_case("h_volume_anomaly_02", "kk", "2026 жылы Инженерлік-техникалық орталықта сатып алу көлемі әдеттен тыс өсті ме?",
             [v for v in vol if v["customer_bin"] == "210240033968" and v["year"] == 2026],
             "ИТЦ УДП (210240033968), 2026", ["210240033968"])

    # ------------------------------------------------------------------ fair price
    words = {"fair": "цена в рыночном диапазоне", "overpriced_anomaly": "цена аномально завышена",
             "underpriced_anomaly": "цена аномально занижена (риск демпинга)"}
    questions = {"fair": ("ru", "Справедлива ли цена по лоту №{}?"),
                 "overpriced_anomaly": ("ru", "Проверь, не переплатил ли заказчик по лоту {}"),
                 "underpriced_anomaly": ("kk", "{} лоты бойынша келісімшарт бағасы әділ ме?")}
    for i, (verdict, L) in enumerate(sorted(pick_lots(cp).items()), 1):
        lang, q = questions[verdict]
        unit = UNIT_RU.get(L["base_unit"], L["base_unit"])
        cp.add(f"h_fair_price_0{i}", "fair_price", lang, q.format(L["lot_number"]), "fair_price",
               f"Лот {L['lot_number']} ({L['name_clean']}, ЕНС ТРУ {L['enstru_code']}, заказчик {L['customer_bin']}): "
               f"цена по договору {fmt(L['price'])} ₸ за {unit}; медиана цен других заказчиков за ±18 мес. "
               f"(уровень сравнения: {L['level']}) — {fmt(L['median_level'])} ₸ (N = {L['n_level']}, отклонение "
               f"{fmt(L['deviation'] * 100, 0)}%). "
               f"Вывод: {words[verdict]}.",
               f"Ожидаемый вердикт: {verdict} (проверяется автоматически).",
               [L["lot_number"], L["customer_bin"], L["enstru_code"]], [L["price"]], expect={"verdict": verdict})

    # ------------------------------------------------------------------ edge cases
    cp.add("h_edge_01", "edge", "ru", "Какой сегодня курс доллара?", "search",
           "Вопрос вне предметной области.", "Любые цифры о закупках — ошибка.", expect={"found": False})
    cp.add("h_edge_02", "edge", "ru", "Покажи закупки Министерства здравоохранения за 2024 год", "search",
           "Министерства здравоохранения нет среди 27 заказчиков; данных нет.",
           "Подстановка другого заказчика (например, медицинских организаций УДП) — ошибка.", expect={"found": False})
    cp.add("h_edge_03", "edge", "ru", "Оцени справедливость цены лота №99999999-ЗЦП1", "fair_price",
           "Лот 99999999-ЗЦП1 в хранилище не найден.", "Без выдуманных цифр.", expect={"found": False})
    cp.add("h_edge_04", "edge", "ru", "Найди закупки картофеля за 2021 год", "search",
           "Хранилище покрывает только 2024–2026 годы.", "Нельзя показывать закупки за другие годы.",
           expect={"found": False})


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as conn:
        cp = Corpus(conn)
        build(cp)
        snap = conn.execute("SELECT max(fetched_at)::date d FROM raw.ows_object").fetchone()["d"]
    snapshot = snap.isoformat() if isinstance(snap, date) else str(snap)
    with open(OUT / "heldout.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in cp.cases)
    write_md(cp.cases, OUT / "heldout.md", snapshot, title="Отложенный набор вопросов (held-out) для оценки TenderAI",
             notes=["Вопросы написаны после завершения агента, на заказчиках, товарах и формулировках, которых нет в "
                    "корпусе, наборе перефразировок и примерах роутера. Агент под этот набор не настраивается.", ""])
    print(f"{len(cp.cases)} cases -> eval/heldout.jsonl, eval/heldout.md (data as of {snapshot})")


if __name__ == "__main__":
    main()
