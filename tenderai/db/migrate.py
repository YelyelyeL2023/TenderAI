"""Minimal ordered SQL migrations (plain files are easier for reviewers to read than ORM migrations)."""
import os
from importlib import resources

import psycopg
import psycopg.sql

from tenderai.config import get_settings


def migrate(database_url: str | None = None) -> list[str]:
    url = database_url or get_settings().database_url
    files = sorted(p for p in resources.files("tenderai.db.sql").iterdir() if p.name.endswith(".sql"))
    applied: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public.schema_migration (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        done = {r[0] for r in conn.execute("SELECT name FROM public.schema_migration")}
        for f in files:
            if f.name in done:
                continue
            with conn.transaction():
                conn.execute(f.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO public.schema_migration(name) VALUES (%s)", (f.name,))
            applied.append(f.name)
        # production: the read-only agent role gets its password from a secret, not the dev default
        if pw := os.environ.get("AGENT_DB_PASSWORD"):
            conn.execute(psycopg.sql.SQL("ALTER ROLE tenderai_agent PASSWORD {}").format(psycopg.sql.Literal(pw)))
    return applied
