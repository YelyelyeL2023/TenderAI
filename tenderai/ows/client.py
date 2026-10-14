"""Async client for goszakup OWS v3 (GraphQL + REST dictionaries).

- Bearer token is taken from settings and never logged (httpx request logging is silenced).
- Retries with exponential backoff on network errors, 429 and 5xx.
- The API sometimes reports errors inside an HTTP 200 body, so bodies are checked too.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from typing import Any

import httpx

from tenderai.config import Settings, get_settings

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx INFO logs would include full URLs/headers context

RETRY_STATUSES = {429, 500, 502, 503, 504}


class OwsError(RuntimeError):
    pass


class OwsClient:
    def __init__(self, settings: Settings | None = None, max_retries: int = 6):
        self.settings = settings or get_settings()
        self.max_retries = max_retries
        self._sem = asyncio.Semaphore(self.settings.ows_concurrency)
        self._http = httpx.AsyncClient(
            base_url=self.settings.ows_base_url,
            headers={
                "Authorization": f"Bearer {self.settings.ows_token.get_secret_value()}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(120.0, connect=20.0),
        )

    async def __aenter__(self) -> OwsClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _request(self, method: str, url: str, **kw) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                async with self._sem:
                    resp = await self._http.request(method, url, **kw)
                if resp.status_code in RETRY_STATUSES:
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                if resp.status_code in (401, 403):
                    raise OwsError(f"OWS access denied ({resp.status_code}) for {url} — check OWS_TOKEN")
                resp.raise_for_status()
                body = resp.json()
                if isinstance(body, dict) and body.get("error"):
                    raise OwsError(f"OWS error for {url}: {body.get('error')}")
                return body
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if attempt == self.max_retries:
                    raise OwsError(f"OWS request failed after {attempt} attempts: {url}: {e}") from e
                log.warning("OWS retry %s/%s for %s (%s)", attempt, self.max_retries, url, type(e).__name__)
                await asyncio.sleep(delay + random.random())
                delay = min(delay * 2, 30)
        raise AssertionError("unreachable")

    # ---------------- GraphQL ----------------
    async def graphql(self, query: str, variables: dict | None = None) -> dict[str, Any]:
        body = await self._request("POST", "/v3/graphql", json={"query": query, "variables": variables or {}})
        if body.get("errors"):
            raise OwsError(f"GraphQL errors: {body['errors'][:2]}")
        return body

    async def graphql_paginate(
        self, root: str, query: str, variables: dict | None = None
    ) -> AsyncIterator[tuple[list[dict], dict]]:
        """Yield (items, pageInfo) pages for a query taking `$limit` and `$after` variables.

        OWS returns rows ordered by id desc; `extensions.pageInfo.lastId` is the cursor for `after`.
        """
        vars_ = dict(variables or {})
        vars_.setdefault("limit", self.settings.ows_page_size)
        after: int | None = None
        while True:
            vars_["after"] = after
            body = await self.graphql(query, vars_)
            items = (body.get("data") or {}).get(root) or []
            page = (body.get("extensions") or {}).get("pageInfo") or {}
            yield items, page
            if not items or not page.get("hasNextPage"):
                return
            new_after = page.get("lastId")
            if new_after is None or new_after == after:
                raise OwsError(f"{root}: pagination cursor did not advance (lastId={new_after})")
            after = new_after

    # ---------------- REST ----------------
    async def rest_paginate(self, path: str, limit: int = 500) -> AsyncIterator[list[dict]]:
        sep = "&" if "?" in path else "?"
        url: str | None = f"{path}{sep}limit={limit}"
        while url:
            body = await self._request("GET", url)
            items = body.get("items") if isinstance(body, dict) else body
            yield items or []
            nxt = body.get("next_page") if isinstance(body, dict) else None
            url = nxt or None

    async def rest_get(self, path: str) -> dict[str, Any]:
        return await self._request("GET", path)
