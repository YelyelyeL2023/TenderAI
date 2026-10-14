# TenderAI — AI agent for Kazakhstan public procurement analytics

An explainable analytical agent over the goszakup.gov.kz **OWS v3** API, covering 27 customer organisations
from 2024 to 2026. It answers questions in Russian or Kazakh about search, comparison, analytics, price
anomalies, price fairness and abnormal volume growth. Every answer cites the procurement and contract IDs
with links, the sample size N, the method, its confidence and its limitations.

```
OWS v3 GraphQL/REST ─► ingest (async, retries, idempotent upserts) ─► raw (JSONB)
        └─ journal (deletions) ─► incremental sync every 3 h (lag < 24 h)
raw ─► ETL (SQL + Python: units, names, KATO, ENSTRU, DQ rules) ─► core ─► marts
marts ─► deterministic tools (Fair Price, anomalies, volumes, stats) ─► local LLM (Qwen3.5 9B, Ollama)
      ─► fact validator ─► templated answer (6 sections) ─► FastAPI /ask + web chat
```

**The LLM never calculates.** All figures come from SQL and Python tools. The LLM classifies the question
and writes a 1–3 sentence verdict, and every number, BIN and date in that verdict is checked against the
tool output before it is shown.

## Quick start (local)

```bash
cp .env.example .env            # put the OWS token into OWS_TOKEN
docker compose -f deploy/docker-compose.yml up -d postgres ollama
docker compose -f deploy/docker-compose.yml exec ollama ollama pull qwen3.5:9b
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Linux/macOS: .venv/bin/pip
python -m tenderai.cli migrate
python -m tenderai.cli backfill      # ~25 min: plans, announcements, lots, contracts, suppliers
python -m tenderai.cli load-cpi cpi_dataset   # official CPI (stat.gov.kz T-15-02-M bulletins, .xlsx)
python -m tenderai.cli etl           # raw -> core -> marts (~1 min)
python -m tenderai.cli serve         # http://127.0.0.1:8010
```

To run everything in containers: `docker compose --env-file .env -f deploy/docker-compose.yml up -d --build`.
This starts postgres, ollama, migrate, api and the worker (incremental sync every 3 h).

Default host ports are Postgres **5434**, Ollama **11434** and API **8010**. They can be changed with `PG_HOST_PORT`,
`OLLAMA_HOST_PORT` and `API_HOST_PORT`.

## CLI

| Command | Purpose |
|---|---|
| `migrate` | apply SQL migrations (`tenderai/db/sql`) |
| `backfill` | full load of the 27 BINs for 2024–2026 |
| `incremental` | changes since the last run plus journal deletions, then rebuild marts |
| `etl` | rebuild core and marts from raw |
| `load-cpi <dir>` | load official CPI from a folder of stat.gov.kz monthly bulletins (or a CSV) |
| `worker` | incremental sync every 3 hours |
| `ask "…"` | ask from the terminal (`--no-llm`, `--json`) |
| `serve` | HTTP API and web chat |

## Tests and evaluation

```bash
pytest                                              # unit tests: names, units, Fair Price, anomalies, router, validator
python eval/build_golden.py > eval/golden.jsonl     # golden set: 40 templated questions, SQL-derived expected facts
python eval/build_corpus.py                         # corpus: 54 hand-picked questions incl. traps (eval/corpus.md)
python eval/run_eval.py eval/corpus.jsonl           # fact accuracy (target >= 85%); --no-llm for the rules fallback
python eval/build_paraphrases.py                    # mandatory question types, each worded 5-7 ways (RU and KZ)
python eval/run_eval.py eval/paraphrases.jsonl --repeats 3   # stability: same facts for every wording and every run
python eval/build_heldout.py                        # held-out set: 23 fresh questions the agent was not tuned on
python eval/run_eval.py eval/heldout.jsonl --repeats 3 --out eval/report_heldout.json
python eval/check_leakage.py                        # no eval question in the LLM examples, no eval fact in the code
```

`--repeats N` asks every question N times and reports the worst run and the questions whose understanding or
facts changed between runs: the LLM is not fully deterministic, so a single pass can hide intermittent errors.

Scoring: a fact (identifier or number) counts when it appears in the answer. Facts that the question itself
contains (a BIN, lot number or code the user typed) are not scored, and small numbers must appear outside the
Top-K list. Fair Price verdicts and refusals ("no data", out-of-scope) are scored separately as checks.

The corpus doubled as a development set (rules were added when its questions failed), so its score is optimistic;
the held-out set is the honest measure and the agent is not changed to make its questions pass.

All sets are rebuilt from the current data after a sync, so their reference answers match the warehouse.
Latest results (data as of 2026-10-13, evaluated 2026-10-14; held-out, corpus and paraphrases asked 3 times each):

| Set | Fact accuracy | Verdict / refusal checks | Without the LLM |
|---|---|---|---|
| Held-out (23) | 86.4% (171/198) | 21/21 | 54.5% |
| Corpus (54) | 97.3% (438/450) | 39/39 | 61.3% |
| Paraphrases (24) | 94.8% (219/231) | 18/18 | — |
| Golden (40) | 100% (89/89) | — | 100% |

Product variants (one product split into ENSTRU codes that differ only in the plan description: petrol АИ-92 / АИ-95,
summer / winter diesel, A4 / A3 paper, battery types) are told apart through `mart.enstru_variant`; before that the
held-out set scored 71.0% and the corpus 80.7%. Remaining misses: Kazakh item names the LLM transliterates instead of
translating («пияз»), a customer name the LLM also copies into the item field (varies between sessions), and
abbreviations («дизтопливо»). Without the LLM, item names in free text are not extracted at all.

## Report

`TenderAI_Report.pdf` explains how the application works and maps each requirement of the assignment to its
implementation. Rebuild it from live data with the scripts in [docs/report](docs/report/README.md).

## Documentation

- [docs/architecture.md](docs/architecture.md): architecture, components, data flow, deployment
- [docs/schema.md](docs/schema.md): storage schema (raw / core / mart) and ER diagram
- [docs/analytics.md](docs/analytics.md): Fair Price definition, anomaly and volume methods, confidence
- [docs/data_scope.md](docs/data_scope.md): OWS v3 entities used, why, and API findings
- [docs/examples.md](docs/examples.md): example agent answers
- [eval/corpus.md](eval/corpus.md): evaluation questions with reference answers
- [eval/heldout.md](eval/heldout.md): held-out questions with reference answers
- [docs/risks.md](docs/risks.md): risks and limitations
