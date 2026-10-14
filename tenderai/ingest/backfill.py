"""Full backfill of the target BINs for 2024–2026.

Scope rules (see docs/data_scope.md):
- plan points:   subjectBiin = BIN, plnPointYear in TARGET_YEARS
- announcements: orgBin = BIN, publishDate within the period; plus any announcement referenced by an
                 in-scope lot but organised by someone else (single organiser etc.)
- lots:          customerBin = BIN (the API has no date filter on lots, all are stored raw and scoped in core)
- contracts:     customerBin = BIN, signDate within the period (line items come nested)
- subjects:      the target customers + every supplier that appears on an in-scope contract
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import psycopg

from tenderai.config import TARGET_BINS, TARGET_YEARS, get_settings
from tenderai.ingest.queries import ENTITIES, REFS
from tenderai.ingest.store import RunLog, UpsertStats, upsert_objects, upsert_refs
from tenderai.ows.client import OwsClient

log = logging.getLogger(__name__)

PERIOD = [f"{min(TARGET_YEARS)}-01-01", f"{max(TARGET_YEARS)}-12-31 23:59:59"]


def bin_filters(entity: str, bin_: str) -> dict:
    return {
        "plan_point": {"subjectBiin": bin_, "plnPointYear": list(TARGET_YEARS)},
        "trd_buy": {"orgBin": bin_, "publishDate": PERIOD},
        "lot": {"customerBin": bin_},
        "contract": {"customerBin": bin_, "signDate": PERIOD},
    }[entity]


async def load_query(
    ows: OwsClient, conn: psycopg.AsyncConnection, entity: str, flt: dict, source_tag: str
) -> tuple[UpsertStats, int | None, int]:
    root, query, id_field = ENTITIES[entity]
    stats, total, fetched = UpsertStats(), None, 0
    async for items, page in ows.graphql_paginate(root, query, {"filter": flt}):
        total = page.get("totalCount", total)
        fetched += len(items)
        stats.add(await upsert_objects(conn, entity, items, id_field, f"graphql:{root}:{source_tag}"))
        await conn.commit()
    return stats, total, fetched


async def load_refs(ows: OwsClient, conn: psycopg.AsyncConnection) -> dict:
    out = {}
    for ref in REFS:
        n = 0
        async for items in ows.rest_paginate(f"/v3/refs/{ref}"):
            n += await upsert_refs(conn, ref, items)
        await conn.commit()
        out[ref] = n
        log.info("ref %s: %s rows", ref, n)
    return out


async def load_by_ids(
    ows: OwsClient, conn: psycopg.AsyncConnection, entity: str, ids: Iterable[int], id_filter: str = "id"
) -> UpsertStats:
    ids = sorted(set(ids))
    stats = UpsertStats()
    for i in range(0, len(ids), 200):
        chunk = ids[i : i + 200]
        s, _, _ = await load_query(ows, conn, entity, {id_filter: chunk}, "ids")
        stats.add(s)
        # remember ids the API did not return so later runs skip them for a week
        await conn.execute(
            """INSERT INTO ops.object_miss (entity, id)
               SELECT %(e)s, x FROM unnest(%(ids)s::bigint[]) x
               WHERE NOT EXISTS (SELECT 1 FROM raw.ows_object o WHERE o.entity = %(e)s AND o.id = x)
               ON CONFLICT (entity, id) DO UPDATE SET checked_at = now()""", {"e": entity, "ids": chunk})
        await conn.commit()
    return stats


async def missing_announcement_ids(conn: psycopg.AsyncConnection) -> list[int]:
    """Announcements referenced by lots whose plan point is in scope, but not yet stored."""
    cur = await conn.execute(
        """
        WITH in_scope_points AS (SELECT id FROM raw.ows_object WHERE entity = 'plan_point'),
        scoped_lots AS (
            SELECT DISTINCT (l.payload->>'trdBuyId')::bigint AS trd_buy_id
            FROM raw.ows_object l
            CROSS JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(l.payload->'pointList') = 'array' THEN l.payload->'pointList' ELSE '[]'::jsonb END) p(pid)
            WHERE l.entity = 'lot' AND p.pid ~ '^[0-9]+$' AND p.pid::bigint IN (SELECT id FROM in_scope_points)
        )
        SELECT s.trd_buy_id FROM scoped_lots s
        WHERE s.trd_buy_id > 0
          AND NOT EXISTS (SELECT 1 FROM raw.ows_object t WHERE t.entity = 'trd_buy' AND t.id = s.trd_buy_id)
          AND NOT EXISTS (SELECT 1 FROM ops.object_miss m WHERE m.entity = 'trd_buy' AND m.id = s.trd_buy_id
                          AND m.checked_at > now() - interval '7 days')
        """
    )
    return [r[0] for r in await cur.fetchall()]


async def missing_plan_point_ids(conn: psycopg.AsyncConnection) -> list[int]:
    """Plan points referenced by stored lots / contract lines but not loaded (other years or filed under
    another BIN). They carry the ENSTRU code, unit and delivery place those lots and contracts need."""
    cur = await conn.execute(
        """
        WITH refs AS (
            SELECT p.pid::bigint AS id FROM raw.ows_object l
            CROSS JOIN LATERAL jsonb_array_elements_text(CASE WHEN jsonb_typeof(l.payload->'pointList') = 'array' THEN l.payload->'pointList' ELSE '[]'::jsonb END) p(pid)
            WHERE l.entity = 'lot' AND p.pid ~ '^[0-9]+$'
            UNION
            SELECT (u->>'plnPointId')::bigint FROM raw.ows_object c
            CROSS JOIN LATERAL jsonb_array_elements(
                CASE WHEN jsonb_typeof(c.payload->'ContractUnits') = 'array' THEN c.payload->'ContractUnits' ELSE '[]' END) u
            WHERE c.entity = 'contract'
        )
        SELECT id FROM refs
        WHERE id > 0 AND NOT EXISTS (SELECT 1 FROM raw.ows_object o WHERE o.entity = 'plan_point' AND o.id = refs.id)
          AND NOT EXISTS (SELECT 1 FROM ops.object_miss m WHERE m.entity = 'plan_point' AND m.id = refs.id
                          AND m.checked_at > now() - interval '7 days')
        """
    )
    return [r[0] for r in await cur.fetchall()]


async def supplier_bins(conn: psycopg.AsyncConnection) -> list[str]:
    """Suppliers on stored contracts that are not yet in the subject store (set difference, hashable),
    skipping those the API had no record of within the last week."""
    cur = await conn.execute(
        """
        SELECT payload->>'supplierBiin' FROM raw.ows_object
        WHERE entity = 'contract' AND coalesce(payload->>'supplierBiin', '') <> ''
        EXCEPT
        (SELECT payload->>'bin' FROM raw.ows_object WHERE entity = 'subject' AND payload->>'bin' IS NOT NULL
         UNION ALL
         SELECT payload->>'iin' FROM raw.ows_object WHERE entity = 'subject' AND payload->>'iin' IS NOT NULL
         UNION ALL
         SELECT biin FROM ops.subject_miss WHERE checked_at > now() - interval '7 days')
        """
    )
    return [r[0] for r in await cur.fetchall()]


async def load_subjects(ows: OwsClient, conn: psycopg.AsyncConnection, biins: Iterable[str]) -> UpsertStats:
    stats = UpsertStats()
    biins = list(biins)

    async def one(b: str) -> list[dict]:
        body = await ows.graphql(ENTITIES["subject"][1], {"limit": 5, "filter": {"bin": b}})
        items = body["data"].get("Subjects") or []
        if not items:  # individual entrepreneurs are registered by IIN
            body = await ows.graphql(ENTITIES["subject"][1], {"limit": 5, "filter": {"iin": b}})
            items = body["data"].get("Subjects") or []
        return items

    for i in range(0, len(biins), 50):
        batch = biins[i : i + 50]
        results = await asyncio.gather(*(one(b) for b in batch))
        misses = [b for b, items in zip(batch, results) if not items]
        for items in results:
            stats.add(await upsert_objects(conn, "subject", items, "pid", "graphql:Subjects:bin"))
        if misses:
            await conn.execute(
                "INSERT INTO ops.subject_miss (biin) SELECT unnest(%s::text[]) "
                "ON CONFLICT (biin) DO UPDATE SET checked_at = now()", (misses,))
        await conn.commit()
        log.info("subjects %s/%s", min(i + 50, len(biins)), len(biins))
    return stats


async def backfill(
    bins: Iterable[str] = TARGET_BINS,
    entities: Iterable[str] = ("plan_point", "trd_buy", "lot", "contract"),
    with_refs: bool = True,
    with_subjects: bool = True,
) -> dict:
    settings = get_settings()
    async with OwsClient(settings) as ows, await psycopg.AsyncConnection.connect(settings.database_url) as conn:
        run = await RunLog(conn, "backfill").start()
        await conn.commit()
        try:
            if with_refs:
                run.stats["refs"] = await load_refs(ows, conn)
            for entity in entities:
                agg = UpsertStats()
                for b in bins:
                    s, total, fetched = await load_query(ows, conn, entity, bin_filters(entity, b), b)
                    agg.add(s)
                    await run.count(entity, b, total, fetched)
                    await conn.commit()
                    log.info("%-10s %s: api_total=%s fetched=%s %s", entity, b, total, fetched, s.as_dict())
                run.stats[entity] = agg.as_dict()
            pp_missing = await missing_plan_point_ids(conn)
            log.info("referenced plan points outside the BIN/year scope: %s", len(pp_missing))
            run.stats["plan_point_extra"] = (await load_by_ids(ows, conn, "plan_point", pp_missing)).as_dict()
            missing = await missing_announcement_ids(conn)
            log.info("announcements organised by third parties: %s", len(missing))
            run.stats["trd_buy_extra"] = (await load_by_ids(ows, conn, "trd_buy", missing)).as_dict()
            if with_subjects:
                targets = list(bins) + await supplier_bins(conn)
                run.stats["subject"] = (await load_subjects(ows, conn, sorted(set(targets)))).as_dict()
            await run.finish("ok")
        except Exception as e:
            await conn.rollback()
            await run.finish("failed", repr(e))
            await conn.commit()
            raise
        await conn.commit()
        return run.stats
