"""Build the golden evaluation set from the warehouse with independent SQL.

Each case: question (RU or KZ), expected intent, and expected facts that must appear in the answer —
identifiers (lot numbers, BINs, ENSTRU codes) and numbers (prices, counts, sums). Ground truth is computed
here with plain SQL over core/mart tables, not through the agent's tool code.

Usage: python eval/build_golden.py [--seed 7] > eval/golden.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys

import psycopg
from psycopg.rows import dict_row

from tenderai.config import get_settings


def q(conn, sql, params=None):
    return conn.execute(sql, params).fetchall()


def fair_price_cases(conn, rnd, n=10):
    rows = q(conn, """
        SELECT l.lot_number, l.customer_bin, l.enstru_code, round(l.unit_price_base::numeric, 2) AS price
        FROM core.lot l
        WHERE l.in_scope AND NOT l.is_deleted AND l.unit_price_base > 0 AND l.enstru_code IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM core.contract_unit u WHERE u.lot_id = l.id AND NOT u.deleted)
          AND (SELECT count(*) FROM mart.price_obs o WHERE o.enstru_code = l.enstru_code AND o.base_unit = l.base_unit
               AND o.customer_bin <> l.customer_bin) >= 8
        ORDER BY md5(l.id::text) LIMIT %s""", (n,))
    out = []
    for i, r in enumerate(rows):
        kk = i % 3 == 2
        text = (f"{r['lot_number']} лотының бағасы басқа ведомстволардың ұқсас келісімшарттарымен салыстырғанда қаншалықты әділ?"
                if kk else f"Оцени адекватность цены лота №{r['lot_number']} относительно аналогичных контрактов других ведомств в идентичном городе поставки")
        out.append({"id": f"fair_{i}", "lang": "kk" if kk else "ru", "question": text, "intent": "fair_price",
                    "facts": {"strings": [r["lot_number"], r["customer_bin"], r["enstru_code"]], "numbers": [float(r["price"])]}})
    return out


def stats_cases(conn, rnd, n=10):
    rows = q(conn, """
        SELECT s.enstru_code, s.base_unit, s.period, s.n, round(s.weighted_avg::numeric, 2) wavg, round(s.median::numeric, 2) med,
               (SELECT min(name_clean) FROM mart.price_obs o WHERE o.enstru_code = s.enstru_code) AS name
        FROM mart.price_stats s
        WHERE s.period = 'all' AND s.n BETWEEN 15 AND 400 AND s.base_unit NOT LIKE 'u%%'
        ORDER BY md5(s.enstru_code) LIMIT %s""", (n,))
    out = []
    for i, r in enumerate(rows):
        kk = i % 3 == 2
        text = (f"ЕНС ТРУ {r['enstru_code']} бойынша орташа өлшенген баға қанша?" if kk
                else f"Какова средневзвешенная и медианная цена по ЕНС ТРУ {r['enstru_code']} ({r['name']})?")
        out.append({"id": f"stats_{i}", "lang": "kk" if kk else "ru", "question": text, "intent": "analytics",
                    "facts": {"strings": [r["enstru_code"]], "numbers": [float(r["wavg"]), float(r["med"]), r["n"]]}})
    return out


def search_cases(conn, rnd, n=10):
    rows = q(conn, """
        SELECT customer_bin, year, count(*) n, round(sum(amount)::numeric, 0) total, count(DISTINCT customer_bin) c
        FROM mart.price_obs GROUP BY customer_bin, year HAVING count(*) BETWEEN 20 AND 5000
        ORDER BY md5(customer_bin || year) LIMIT %s""", (n,))
    out = []
    for i, r in enumerate(rows):
        kk = i % 3 == 2
        text = (f"{r['year']} жылы {r['customer_bin']} тапсырыс берушінің барлық сатып алуларын тап" if kk
                else f"Найди все закупки заказчика {r['customer_bin']} за {r['year']} год")
        out.append({"id": f"search_{i}", "lang": "kk" if kk else "ru", "question": text, "intent": "search",
                    "facts": {"strings": [r["customer_bin"]], "numbers": [r["n"], float(r["total"])]}})
    return out


def anomaly_cases(conn, rnd, n=5):
    rows = q(conn, """
        SELECT customer_bin, count(*) FILTER (WHERE deviation > 0.3) flagged, count(*) total
        FROM mart.price_deviation WHERE NOT is_service GROUP BY customer_bin HAVING count(*) FILTER (WHERE deviation > 0.3) BETWEEN 1 AND 500
        ORDER BY md5(customer_bin) LIMIT %s""", (n,))
    return [{"id": f"anom_{i}", "lang": "ru", "intent": "price_anomaly",
             "question": f"Найди закупки заказчика {r['customer_bin']} с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ",
             "facts": {"strings": [r["customer_bin"]], "numbers": [r["flagged"], r["total"]]}} for i, r in enumerate(rows)]


def volume_cases(conn, rnd, n=5):
    bins = q(conn, """SELECT customer_bin FROM mart.volume_yearly GROUP BY customer_bin HAVING count(DISTINCT year) >= 2
                      ORDER BY md5(customer_bin) LIMIT %s""", (n,))
    return [{"id": f"vol_{i}", "lang": "ru", "intent": "volume_anomaly",
             "question": f"Выяви нетипичное завышение количества ТРУ у заказчика {r['customer_bin']} по сравнению с предыдущими годами",
             "facts": {"strings": [r["customer_bin"]], "numbers": []}} for i, r in enumerate(bins)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rnd = random.Random(args.seed)
    sys.stdout.reconfigure(encoding="utf-8")
    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as conn:
        cases = (fair_price_cases(conn, rnd) + stats_cases(conn, rnd) + search_cases(conn, rnd)
                 + anomaly_cases(conn, rnd) + volume_cases(conn, rnd))
    for c in cases:
        print(json.dumps(c, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
