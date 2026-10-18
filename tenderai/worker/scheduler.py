"""Long-running worker: incremental sync every 3 hours (SLA: data lag < 24 h).
In Kubernetes the same job runs as the tenderai-incremental CronJob instead (deploy/k8s/30-app.yaml)."""
from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from tenderai.ingest.incremental import incremental

log = logging.getLogger(__name__)


def job() -> None:
    try:
        stats = asyncio.run(incremental())
        log.info("incremental sync ok: %s", {k: v for k, v in stats.items() if k != "etl"})
    except Exception:
        log.exception("incremental sync failed; will retry at the next tick")


def main(interval_hours: int = 3) -> None:
    sched = BlockingScheduler(timezone="Asia/Almaty")
    sched.add_job(job, "interval", hours=interval_hours, max_instances=1, coalesce=True)
    log.info("worker started: incremental sync every %sh", interval_hours)
    job()  # run once at start-up so a fresh container catches up immediately
    sched.start()
