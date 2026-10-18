"""Incremental refresh (target: data lag < 24 h; scheduled every 3 h).

1. Changed/new objects: for every target BIN and entity, re-query GraphQL with `indexDate >= watermark - overlap`.
   This touches only our customers (~108 small requests) instead of re-fetching the whole national journal.
2. Deletions: page through /v3/journal for the same window and soft-delete stored objects with action 'D'
   (deletions cannot be discovered by a filtered query).
3. Fetch announcements/suppliers newly referenced and advance the 'incremental' watermark.
4. Rebuild core + marts; the ETL advances the 'journal' watermark that /health reports, so a failed rebuild shows
   as growing lag (and an 'etl_failed' run) instead of fresh data.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone

import psycopg

from tenderai.config import TARGET_BINS, get_settings
from tenderai.etl.run import run_etl
from tenderai.ingest.backfill import (
    bin_filters,
    load_by_ids,
    load_query,
    load_subjects,
    missing_announcement_ids,
    missing_plan_point_ids,
    supplier_bins,
)
from tenderai.ingest.store import RunLog, UpsertStats, mark_deleted
from tenderai.ows.client import OwsClient

log = logging.getLogger(__name__)

OVERLAP = timedelta(hours=2)
JOURNAL_SERVICE_TO_ENTITY = {"plans": "plan_point", "lots": "lot", "trd_buy": "trd_buy", "contract": "contract"}
ENTITIES = ("plan_point", "trd_buy", "lot", "contract")


async def _get_watermark(conn: psycopg.AsyncConnection) -> datetime:
    row = await (await conn.execute("SELECT value FROM ops.watermark WHERE name = 'incremental'")).fetchone()
    if row:
        return row[0]
    row = await (await conn.execute(
        "SELECT min(started_at) FROM ops.ingest_run WHERE kind = 'backfill' AND status = 'ok'")).fetchone()
    if not row or not row[0]:
        raise RuntimeError("no successful backfill yet — run `tenderai backfill` first")
    return row[0]


async def journal_deletions(ows: OwsClient, since: datetime, until: datetime) -> dict[str, set[int]]:
    """Object ids deleted in [since, until] grouped by entity, read from /v3/journal.

    The journal is national and its volume swings: about 105 000 changes on a normal day, 4.6 million on
    2026-10-12 (a portal-wide re-index). Asking the server for deletions only (`action=D`) cuts that by an order of
    magnitude. Re-publication after a deletion is not visible this way; the caller protects every object that
    this run has just fetched live (see `mark_deleted`).
    """
    # date_to is exclusive: the window runs to the end of `until`'s day
    path = (f"/v3/journal?date_from={since:%Y-%m-%d}&date_to={until + timedelta(days=1):%Y-%m-%d}"
            f"&action=D&limit=500")
    first = await ows.rest_get(path)
    pages = int(first.get("max_page") or 1)
    log.info("journal: %s deletion pages for %s .. %s", pages, f"{since:%Y-%m-%d}", f"{until:%Y-%m-%d}")
    out: dict[str, set[int]] = {e: set() for e in JOURNAL_SERVICE_TO_ENTITY.values()}

    def collect(items: list[dict]) -> None:
        for it in items:
            ent = JOURNAL_SERVICE_TO_ENTITY.get(it.get("service_name"))
            oid = str(it.get("object_id", ""))
            if ent and oid.isdigit() and it.get("action") == "D":
                out[ent].add(int(oid))

    collect(first.get("items") or [])
    for start in range(2, pages + 1, 8):
        batch = await asyncio.gather(*(ows.rest_get(f"{path}&page={p}") for p in range(start, min(start + 8, pages + 1))))
        for body in batch:
            collect(body.get("items") or [])
        if (start - 2) % 200 < 8:
            log.info("journal: page %s/%s", min(start + 7, pages), pages)
    return out


async def incremental(with_etl: bool = True) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    async with OwsClient(settings) as ows, await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        run = await RunLog(conn, "incremental").start()
        await conn.commit()
        try:
            since = (await _get_watermark(conn)) - OVERLAP
            run.stats["window"] = [since.isoformat(), now.isoformat()]
            since_s = since.astimezone(timezone(timedelta(hours=5))).strftime("%Y-%m-%dT%H:%M:%S")  # API is UTC+5
            for entity in ENTITIES:
                agg = UpsertStats()
                for b in TARGET_BINS:
                    flt = {**bin_filters(entity, b), "indexDate": [since_s, ""]}
                    s, total, fetched = await load_query(ows, conn, entity, flt, f"incr:{b}")
                    agg.add(s)
                    if fetched:
                        await run.count(entity, b, total, fetched)
                run.stats[entity] = agg.as_dict()
                log.info("incremental %s: %s", entity, agg.as_dict())
            deleted = await journal_deletions(ows, since, now)
            # objects re-indexed inside this window were just fetched live: a deletion older than that is stale
            run.stats["deleted"] = {e: await mark_deleted(conn, e, sorted(ids), live_since=since_s)
                                    for e, ids in deleted.items()}
            await conn.commit()
            run.stats["plan_point_extra"] = (await load_by_ids(ows, conn, "plan_point", await missing_plan_point_ids(conn))).as_dict()
            run.stats["trd_buy_extra"] = (await load_by_ids(ows, conn, "trd_buy", await missing_announcement_ids(conn))).as_dict()
            run.stats["subject"] = (await load_subjects(ows, conn, await supplier_bins(conn))).as_dict()
            await conn.execute(
                "INSERT INTO ops.watermark (name, value) VALUES ('incremental', %s) "
                "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (now,))
            await conn.commit()
        except Exception as e:
            await conn.rollback()
            await run.finish("failed", repr(e))
            await conn.commit()
            raise
        # raw is committed; the 'journal' freshness watermark moves only when run_etl rebuilds the marts
        if with_etl:
            try:
                run.stats["etl"] = await asyncio.to_thread(run_etl)
            except Exception as e:
                await run.finish("etl_failed", repr(e))
                await conn.commit()
                raise
        await run.finish("ok")
        await conn.commit()
    return run.stats
