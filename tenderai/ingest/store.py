"""Raw-layer persistence: idempotent page upserts with change detection."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb


def payload_hash(obj: dict) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0

    def add(self, other: UpsertStats) -> None:
        self.inserted += other.inserted
        self.updated += other.updated
        self.unchanged += other.unchanged

    def as_dict(self) -> dict:
        return {"inserted": self.inserted, "updated": self.updated, "unchanged": self.unchanged}


UPSERT_SQL = """
INSERT INTO raw.ows_object (entity, id, payload, payload_hash, source, is_deleted)
SELECT %(entity)s, t.id, t.payload, t.h, %(source)s, false
FROM unnest(%(ids)s::bigint[], %(payloads)s::jsonb[], %(hashes)s::text[]) AS t(id, payload, h)
ON CONFLICT (entity, id) DO UPDATE
   SET payload = EXCLUDED.payload, payload_hash = EXCLUDED.payload_hash, source = EXCLUDED.source,
       fetched_at = now(), is_deleted = false
 WHERE raw.ows_object.payload_hash IS DISTINCT FROM EXCLUDED.payload_hash
    OR raw.ows_object.is_deleted
RETURNING (xmax = 0) AS inserted
"""


async def upsert_objects(
    conn: psycopg.AsyncConnection, entity: str, items: list[dict], id_field: str, source: str
) -> UpsertStats:
    # de-duplicate within the page (last wins) — OWS occasionally repeats a row across page boundaries
    by_id: dict[int, dict] = {}
    for it in items:
        if it.get(id_field) is not None:
            by_id[int(it[id_field])] = it
    if not by_id:
        return UpsertStats()
    ids = list(by_id)
    payloads = [Jsonb(by_id[i]) for i in ids]
    hashes = [payload_hash(by_id[i]) for i in ids]
    cur = await conn.execute(
        UPSERT_SQL, {"entity": entity, "source": source, "ids": ids, "payloads": payloads, "hashes": hashes}
    )
    rows = await cur.fetchall()
    ins = sum(1 for (r,) in rows if r)
    upd = len(rows) - ins
    return UpsertStats(inserted=ins, updated=upd, unchanged=len(ids) - len(rows))


async def mark_deleted(conn: psycopg.AsyncConnection, entity: str, ids: list[int], live_since: str | None = None) -> int:
    """Soft-delete objects. With `live_since` (API local time), objects whose stored version was re-indexed at or
    after that moment are kept: the current run fetched them live, so the deletion predates a re-publication."""
    if not ids:
        return 0
    cur = await conn.execute(
        "UPDATE raw.ows_object SET is_deleted = true, fetched_at = now() "
        "WHERE entity = %s AND id = ANY(%s) AND NOT is_deleted "
        "  AND (%s::timestamp IS NULL OR coalesce((payload->>'indexDate')::timestamp, '-infinity') < %s::timestamp)",
        (entity, ids, live_since, live_since),
    )
    return cur.rowcount


async def upsert_refs(conn: psycopg.AsyncConnection, ref_name: str, items: list[dict]) -> int:
    rows = []
    for it in items:
        key = it.get("id", it.get("code"))
        if key is None:
            continue
        rows.append((ref_name, str(key), Jsonb(it)))
    if rows:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO raw.ref (ref_name, code, payload) VALUES (%s, %s, %s) "
                "ON CONFLICT (ref_name, code) DO UPDATE SET payload = EXCLUDED.payload, fetched_at = now()",
                rows,
            )
    return len(rows)


@dataclass
class RunLog:
    conn: psycopg.AsyncConnection
    kind: str
    run_id: int | None = None
    stats: dict = field(default_factory=dict)

    async def start(self) -> RunLog:
        cur = await self.conn.execute("INSERT INTO ops.ingest_run (kind) VALUES (%s) RETURNING run_id", (self.kind,))
        self.run_id = (await cur.fetchone())[0]
        return self

    async def count(self, entity: str, scope: str, api_total: int | None, fetched: int) -> None:
        await self.conn.execute(
            "INSERT INTO ops.ingest_count (run_id, entity, scope, api_total, fetched) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (run_id, entity, scope) DO UPDATE SET api_total = EXCLUDED.api_total, fetched = EXCLUDED.fetched",
            (self.run_id, entity, scope, api_total, fetched),
        )

    async def finish(self, status: str = "ok", error: str | None = None) -> None:
        await self.conn.execute(
            "UPDATE ops.ingest_run SET finished_at = now(), status = %s, stats = %s, error = %s WHERE run_id = %s",
            (status, Jsonb(self.stats), error, self.run_id),
        )
