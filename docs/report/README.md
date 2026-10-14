# Report generator

Builds `TenderAI_Report.pdf` (how the application works and how it meets the assignment) from live data.

```bash
python docs/report/gather_report.py inputs.json              # project venv: counts, freshness, eval results, real answers
python docs/report/make_report.py inputs.json TenderAI_Report.pdf   # needs reportlab; reuses helpers from make_pdf.py
```

The architecture document `TenderAI_Architecture.pdf` comes from the same folder and needs no database:

```bash
python docs/report/make_pdf.py TenderAI_Architecture.pdf
```

Its figures (volumes, evaluation scores, sync timings) are written into `make_pdf.py`; update them after a new
sync or evaluation run.

`gather_report.py` asks the agent five questions, so Postgres and Ollama must be running. Run the evaluations first
(`eval/run_eval.py`) so the report quotes current scores. Fonts are taken from `C:/Windows/Fonts` (Arial, Consolas,
Segoe UI Symbol).
