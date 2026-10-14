"""Command-line entry point: `python -m tenderai.cli <command>`."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from tenderai.config import TARGET_BINS


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tenderai")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply SQL migrations")
    b = sub.add_parser("backfill", help="full load of target BINs for 2024-2026")
    b.add_argument("--bins", nargs="*", default=list(TARGET_BINS))
    b.add_argument("--entities", nargs="*", default=["plan_point", "trd_buy", "lot", "contract"])
    b.add_argument("--no-refs", action="store_true")
    b.add_argument("--no-subjects", action="store_true")
    e = sub.add_parser("etl", help="rebuild core and marts from raw")
    e.add_argument("--no-marts", action="store_true")
    i = sub.add_parser("incremental", help="fetch changes since the last run, apply deletions, rebuild marts")
    i.add_argument("--no-etl", action="store_true")
    sub.add_parser("worker", help="run the incremental sync every 3 hours")
    lc = sub.add_parser("load-cpi", help="load official CPI: folder of stat.gov.kz T-15-02-M .xlsx bulletins, or a CSV")
    lc.add_argument("path")
    a = sub.add_parser("ask", help="ask the agent a question from the terminal")
    a.add_argument("question")
    a.add_argument("--no-llm", action="store_true", help="rule-based routing and template verdicts only")
    a.add_argument("--json", action="store_true", help="print the full structured answer")
    sv = sub.add_parser("serve", help="run the HTTP API and web chat")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8010)
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.platform == "win32":  # psycopg async requires the selector loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    if args.cmd == "migrate":
        from tenderai.db.migrate import migrate
        print("applied:", migrate() or "nothing new")
    elif args.cmd == "backfill":
        from tenderai.ingest.backfill import backfill
        stats = asyncio.run(backfill(args.bins, args.entities, not args.no_refs, not args.no_subjects))
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif args.cmd == "etl":
        from tenderai.etl.run import run_etl
        print(json.dumps(run_etl(with_marts=not args.no_marts), ensure_ascii=False, indent=2))
    elif args.cmd == "incremental":
        from tenderai.ingest.incremental import incremental
        print(json.dumps(asyncio.run(incremental(not args.no_etl)), ensure_ascii=False, indent=2, default=str))
    elif args.cmd == "load-cpi":
        from tenderai.etl.cpi import load_cpi
        print(json.dumps(load_cpi(args.path), ensure_ascii=False))
    elif args.cmd == "worker":
        from tenderai.worker.scheduler import main as worker_main
        worker_main()
    elif args.cmd == "ask":
        from dataclasses import asdict

        from tenderai.agent.agent import Agent
        sys.stdout.reconfigure(encoding="utf-8")
        ans = Agent(use_llm=not args.no_llm).ask(args.question)
        print(json.dumps(asdict(ans), ensure_ascii=False, indent=2, default=str) if args.json else ans.markdown)
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("tenderai.api.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
