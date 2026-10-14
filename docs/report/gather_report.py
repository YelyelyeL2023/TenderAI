"""Collect live figures and real agent answers for the PDF report. Run from the repo root with the project venv."""
import json
import logging
import sys
from dataclasses import asdict

import psycopg

from tenderai.agent.agent import Agent
from tenderai.config import get_settings

logging.disable(logging.WARNING)
out_path = sys.argv[1]
s = get_settings()
data = {}
with psycopg.connect(s.database_url) as c:
    q = lambda sql: c.execute(sql).fetchall()  # noqa: E731
    data["core_counts"] = {t: q(f"SELECT count(*) FROM core.{t}")[0][0] for t in
                           ("plan_point", "announcement", "lot", "contract", "contract_unit", "subject", "dq_issue", "cpi")}
    data["core_counts"]["lot_in_scope"] = q("SELECT count(*) FROM core.lot WHERE in_scope")[0][0]
    data["mart_counts"] = {t: q(f"SELECT count(*) FROM mart.{t}")[0][0] for t in
                           ("price_obs", "price_deviation", "price_stats", "volume_yearly")}
    data["raw_count"] = q("SELECT count(*) FROM raw.ows_object")[0][0]
    data["runs"] = [dict(zip(("run_id", "kind", "status", "started", "finished"), map(str, r))) for r in
                    q("SELECT run_id, kind, status, started_at, finished_at FROM ops.ingest_run ORDER BY run_id DESC LIMIT 4")]
    last = q("SELECT stats FROM ops.ingest_run WHERE kind = 'incremental' AND status = 'ok' ORDER BY run_id DESC LIMIT 1")
    data["last_incremental"] = last[0][0] if last else None
    data["watermark"] = str(q("SELECT value FROM ops.watermark WHERE name = 'journal'")[0][0])
    data["subject_types"] = q("""SELECT payload->>'name_ru', count(*) FROM core.plan_point p
                                 JOIN raw.ref r ON r.ref_name = 'ref_subject_type' AND r.code = p.subject_type_id::text
                                 WHERE p.is_latest GROUP BY 1 ORDER BY 2 DESC""")
    data["dq_rules"] = q("SELECT entity, rule, severity, count(*) FROM core.dq_issue GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 12")

agent = Agent()
data["examples"] = {}
for key, question in {
    "anomaly": "Найди закупки с отклонением цены > 30% от средневзвешенной по данному ЕНСТРУ, топ-5",
    "fair": "Оцени адекватность цены лота №74823661-ЗЦП1 относительно аналогичных контрактов других ведомств "
            "в идентичном городе поставки",
    "volume": "Выяви нетипичное завышение количества ТРУ по сравнению с предыдущими годами, топ-5",
    "kk": "2025 жылы жазғы дизель отынының орташа бағасы қанша?",
    "refusal": "Покажи закупки акимата города Алматы за 2025 год",
}.items():
    a = agent.ask(question)
    data["examples"][key] = {"question": question, "markdown": a.markdown, "route": a.route,
                             "verdict_source": a.verdict_source, "timings": a.timings}

for name, path in (("corpus", "eval/report_corpus.json"), ("golden", "eval/report_llm.json"),
                   ("corpus_rules", "eval/report_corpus_rules.json"), ("golden_rules", "eval/report_rules.json"),
                   ("paraphrases", "eval/report_paraphrases.json"), ("heldout", "eval/report_heldout.json"),
                   ("heldout_rules", "eval/report_heldout_rules.json")):
    try:
        r = json.load(open(path, encoding="utf-8"))
        data[f"eval_{name}"] = {k: r.get(k) for k in ("cases", "fact_accuracy", "facts_total", "facts_correct",
                                                      "intent_accuracy", "llm_verdict_share", "repeats",
                                                      "worst_run_fact_accuracy", "stable_share", "check_accuracy",
                                                      "checks_total", "checks_passed", "facts_echoed_not_scored")}
    except FileNotFoundError:
        pass
json.dump(data, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("ok", out_path)
