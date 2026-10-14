"""Regenerate docs/examples.md from live agent answers (real data, real LLM verdicts).

Usage: python eval/make_examples.py [--out docs/examples.md]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

import psycopg

from tenderai.agent.agent import Agent
from tenderai.config import get_settings


def pick_lot() -> str:
    """A contracted goods lot with a same-city comparison set, so the fairness example is representative."""
    with psycopg.connect(get_settings().database_url) as c:
        row = c.execute("""
            SELECT l.lot_number FROM core.lot l JOIN core.contract_unit u ON u.lot_id = l.id AND NOT u.deleted
            WHERE l.in_scope AND l.base_unit IN ('L', 'kg', 'pcs') AND l.kato_source IN ('lot', 'plan')
              AND (SELECT count(*) FROM mart.price_obs o WHERE o.enstru_code = l.enstru_code AND o.base_unit = l.base_unit
                   AND o.customer_bin <> l.customer_bin AND o.city_kato = l.kato_code) >= 15
            ORDER BY md5(l.id::text) LIMIT 1""").fetchone()
    return row[0] if row else "74987392-ЗЦП2"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/examples.md")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    lot = pick_lot()
    questions = [
        ("Аномалии цен (обязательный тип)", "Найди закупки с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ"),
        ("Справедливость цены (обязательный тип)",
         f"Оцени адекватность цены лота №{lot} относительно аналогичных контрактов других ведомств в идентичном городе поставки"),
        ("Объёмы (обязательный тип)", "Выяви нетипичное завышение количества ТРУ по сравнению с предыдущими годами"),
        ("Аналитика на казахском", "2025 жылы жазғы дизель отынының орташа бағасы қанша?"),
        ("Сравнение", "Сравни закупки картриджей между заказчиками в 2025 году"),
        ("Поиск", "Найди закупки бумаги для офисной техники заказчика 990740002243 в 2025 году"),
        ("Нет данных (запрет на домысливание)", "Оцени адекватность цены лота №1-НЕСУЩЕСТВУЕТ"),
    ]
    agent = Agent()
    out = ["# Примеры ответов AI-агента\n",
           f"Сгенерировано `eval/make_examples.py` {datetime.now():%Y-%m-%d %H:%M} на реальных данных хранилища "
           f"(27 заказчиков, 2024–2026); LLM: {agent.llm.model if agent.llm else 'нет (шаблоны)'}.\n",
           "Каждый ответ содержит шесть обязательных разделов; вердикт написан LLM и прошёл проверку фактов "
           "(или заменён шаблоном — см. строку «вердикт»).\n"]
    for title, q in questions:
        a = agent.ask(q)
        print(f"{title}: intent={a.route['intent']} verdict={a.verdict_source} {a.timings}")
        out += [f"\n---\n\n## {title}\n", f"> **Вопрос:** {q}\n",
                f"_intent: `{a.route['intent']}` · router: {a.route['router']} · вердикт: {a.verdict_source} · "
                f"время: {round(sum(a.timings.values()), 1)} с_\n", a.markdown.replace("### ", "#### "), ""]
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("written", args.out)


if __name__ == "__main__":
    main()
