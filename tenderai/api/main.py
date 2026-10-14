"""HTTP API + minimal web chat.  Run: uvicorn tenderai.api.main:app --port 8010"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import resources

import psycopg
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from tenderai.agent.agent import Agent
from tenderai.config import get_settings

log = logging.getLogger(__name__)
app = FastAPI(title="TenderAI", version="0.1.0",
              description="Explainable AI agent for Kazakhstan public procurement (OWS v3)")
_agent: Agent | None = None
_lock = asyncio.Lock()  # one local LLM: serialise requests rather than thrash GPU memory


def agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@app.get("/", response_class=HTMLResponse)
def chat_page() -> str:
    return resources.files("tenderai.api").joinpath("static/chat.html").read_text(encoding="utf-8")


@app.post("/ask")
async def ask(body: AskIn) -> dict:
    async with _lock:
        try:
            ans = await asyncio.to_thread(agent().ask, body.question)
        except psycopg.Error as e:
            log.exception("database error")
            raise HTTPException(503, f"database error: {type(e).__name__}") from e
    return asdict(ans)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    out: dict = {"time": datetime.now(UTC).isoformat()}
    try:
        with psycopg.connect(s.database_url, connect_timeout=3) as c:
            last_fetch = c.execute("SELECT max(fetched_at) FROM raw.ows_object").fetchone()[0]
            wm = c.execute("SELECT value FROM ops.watermark WHERE name = 'journal'").fetchone()
            last_run = c.execute("SELECT kind, status, finished_at FROM ops.ingest_run ORDER BY run_id DESC LIMIT 1").fetchone()
        out["db"] = "ok"
        fresh = wm[0] if wm else last_fetch
        out["data_as_of"] = fresh.isoformat() if fresh else None
        out["lag_hours"] = round((datetime.now(UTC) - fresh).total_seconds() / 3600, 1) if fresh else None
        out["freshness_sla_ok"] = out["lag_hours"] is not None and out["lag_hours"] <= 24
        out["last_ingest"] = {"kind": last_run[0], "status": last_run[1],
                              "finished_at": last_run[2].isoformat() if last_run[2] else None} if last_run else None
    except psycopg.Error as e:
        out["db"] = f"error: {type(e).__name__}"
    out["llm"] = {"model": s.llm_model, "available": agent().llm is not None}
    return out
