"""ETL: raw -> core (-> marts). One transaction, so readers never see a half-built core."""
from __future__ import annotations

import logging
import time
from importlib import resources

import psycopg

from tenderai.config import TARGET_BINS, get_settings
from tenderai.etl.lot_names import clean_name
from tenderai.etl.units import normalize_unit

log = logging.getLogger(__name__)


def _sql(name: str) -> str:
    return resources.files("tenderai.etl").joinpath(name).read_text(encoding="utf-8")


def load_units(conn: psycopg.Connection) -> int:
    rows = conn.execute(
        "SELECT payload->>'code', payload->>'name_ru', payload->>'alpha_code' FROM raw.ref WHERE ref_name = 'ref_units'"
    ).fetchall()
    conn.execute("TRUNCATE core.ref_unit")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO core.ref_unit (code, name_ru, alpha_code, base_unit, factor) VALUES (%s,%s,%s,%s,%s)",
            [(code, name, alpha, *normalize_unit(code)) for code, name, alpha in rows if code],
        )
    return len(rows)


def clean_names(conn: psycopg.Connection, table: str) -> int:
    rows = conn.execute(f"SELECT id, name_ru FROM core.{table}").fetchall()
    ids = [r[0] for r in rows]
    names = [clean_name(r[1]) for r in rows]
    conn.execute(
        f"UPDATE core.{table} t SET name_clean = v.n FROM unnest(%s::bigint[], %s::text[]) v(id, n) WHERE t.id = v.id",
        (ids, names),
    )
    return len(rows)


def run_etl(database_url: str | None = None, with_marts: bool = True) -> dict:
    url = database_url or get_settings().database_url
    stats: dict = {}
    t0 = time.time()
    with psycopg.connect(url) as conn:
        with conn.transaction():
            stats["units"] = load_units(conn)
            conn.execute("TRUNCATE core.target_customer")
            conn.execute("INSERT INTO core.target_customer SELECT unnest(%s::text[])", (list(TARGET_BINS),))
            conn.execute(_sql("transform.sql"))
            stats["names_plan_point"] = clean_names(conn, "plan_point")
            stats["names_lot"] = clean_names(conn, "lot")
            conn.execute(
                "UPDATE core.contract_unit u SET name_clean = p.name_clean FROM core.plan_point p WHERE p.id = u.plan_point_id"
            )
            conn.execute(_sql("dq.sql"))
            if with_marts:
                conn.execute(_sql("marts.sql"))
        for table in ("plan_point", "announcement", "lot", "contract", "contract_unit", "subject", "dq_issue"):
            stats[table] = conn.execute(f"SELECT count(*) FROM core.{table}").fetchone()[0]
        conn.execute("ANALYZE")
    stats["seconds"] = round(time.time() - t0, 1)
    log.info("ETL done: %s", stats)
    return stats
