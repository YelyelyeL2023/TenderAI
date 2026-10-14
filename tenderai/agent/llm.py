"""Thin client for a local Ollama server (native /api/chat).

Native API rather than the OpenAI-compatible one because it gives JSON-schema constrained output
(`format`) and lets us switch Qwen3.5's thinking mode off (`think: false`) for fast, deterministic calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from tenderai.config import get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float = 120.0):
        s = get_settings()
        self.base_url = (base_url or s.llm_base_url).rstrip("/").removesuffix("/v1")
        self.model = model or s.llm_model
        self.num_gpu = s.llm_num_gpu
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def chat(self, messages: list[dict], schema: dict | None = None, temperature: float = 0.0,
             max_tokens: int = 512) -> str:
        body: dict[str, Any] = {
            "model": self.model, "messages": messages, "stream": False, "think": False,
            "options": {"temperature": temperature, "num_predict": max_tokens, "seed": 7, "num_gpu": self.num_gpu},
        }
        if schema:
            body["format"] = schema
        try:
            r = self._http.post("/api/chat", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"LLM request failed: {e}") from e
        return r.json()["message"]["content"]

    def json(self, messages: list[dict], schema: dict, max_tokens: int = 400) -> dict:
        text = self.chat(messages, schema=schema, max_tokens=max_tokens)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"LLM returned invalid JSON: {text[:200]}") from e

    def available(self) -> bool:
        try:
            tags = self._http.get("/api/tags", timeout=5).json().get("models", [])
            return any(m.get("name", "").startswith(self.model) for m in tags)
        except httpx.HTTPError:
            return False
