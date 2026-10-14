"""Fact validator: every number, BIN and date in LLM-written prose must be traceable to the tool output.

The LLM sees the tool facts and writes a 1–3 sentence verdict. Before it is shown, each numeric token
is parsed (Russian/Kazakh formatting: "1 234 567,8", "12,5 млн", "30%") and matched against all numbers
present in the tool result, with their common derived forms (rounded, ×100 for shares, /1e3, /1e6, /1e9).
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime

from tenderai.config import TARGET_BINS

NUM = re.compile(
    r"(?<![\w.])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?\s*(%|млрд|млн|тыс|миллиард|миллион|мың|млрд\.|млн\.|тыс\.)?",
    re.IGNORECASE,
)
BIN = re.compile(r"\b\d{12}\b")
DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
FREE_SMALL_INTS = {0, 1, 2, 3}       # "1–3 предложения", "в 2 раза" etc. carry no factual risk
REL_TOL = 0.006


@dataclass
class Validation:
    ok: bool
    checked: int
    unsupported: list[str]
    issues: list[str] = field(default_factory=list)   # numbers that exist in the facts but are used wrongly


def _walk(obj, out: list[float], strings: list[str]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        if math.isfinite(obj):
            out.append(float(obj))
    elif isinstance(obj, str):
        strings.append(obj)
        for m in re.finditer(r"-?\d+(?:\.\d+)?", obj):  # machine formats: JSON, ISO dates
            out.append(float(m.group(0)))
        for m in NUM.finditer(obj):  # human formats: "58 214", "69 156,50"
            out.append(_parse(m))
    elif isinstance(obj, (date, datetime)):
        strings.append(obj.isoformat())
        out.extend([obj.year, obj.month, obj.day])
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, (int, float)):  # e.g. {2024: qty} year keys
                _walk(k, out, strings)
            _walk(v, out, strings)
    elif isinstance(obj, Iterable):
        for v in obj:
            _walk(v, out, strings)
    else:
        try:
            out.append(float(obj))  # Decimal etc.
        except (TypeError, ValueError):
            pass


def allowed_numbers(facts: dict) -> tuple[list[float], str]:
    nums: list[float] = []
    strings: list[str] = []
    _walk(facts, nums, strings)
    derived = set()
    for x in nums:
        for v in (x, abs(x), x * 100, abs(x) * 100, x / 1e3, x / 1e6, x / 1e9):
            derived.add(v)
    return sorted(derived), " ".join(strings)


def _parse(m: re.Match) -> float:
    whole = re.sub(r"[   ]", "", m.group(1))
    # a scale word ("12,5 млн") means the prose already uses the /1e6 form, which is in the derived set
    return float(f"{whole}.{m.group(2)}" if m.group(2) else whole)


def _matches(v: float, allowed: list[float]) -> bool:
    for a in allowed:
        if a == v:
            return True
        if a != 0 and abs(v - a) / abs(a) <= REL_TOL:
            return True
        # the prose may round: 12.47 -> "12,5", 123687944 -> "123,7 млн"
        if abs(v - round(a, 1)) < 1e-9 or abs(v - round(a)) < 1e-9 or abs(v - round(a, 2)) < 1e-9:
            return True
    return False


# ---------------------------------------------------------------------- claim checks
# A number can exist in the facts and still be used wrongly: "347 series grew" when 347 were compared and 128 grew,
# or "the price lies within 249–262" when it is 263. These checks cover the claim types seen in evaluation.

# words that present a number as the size of the compared population ("из 9 982", "9982 қатарының")
_POPULATION = re.compile(r"\bиз\b|сравн|сопостав|проанализ|анализ|всего|выборк|N\s*=|среди|проверен|салыстыр|талда|"
                         r"барлығы|ішінен|ішінде|арасында|іріктеме|қатарының|қатардың|позицияның|позициялардың|аналог",
                         re.IGNORECASE)
_INSIDE = re.compile(r"в пределах|внутри|в диапазоне|находится в|входит в|диапазон(?:ын)?да|аралығында|шегінде",
                     re.IGNORECASE)
_OUTSIDE = re.compile(r"\bвне\b|за пределами|выше|ниже|превыша|\bтыс\b|жоғары|төмен|асып", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CONF_INTERVAL = re.compile(r"доверительн\w*\s+интервал|сенім\w*\s+аралығ", re.IGNORECASE)


def claims_from(result) -> dict:
    """The structured facts that claim checks need, taken from a ToolResult."""
    s = result.summary or {}
    out: dict = {}
    total = {"price_anomalies": result.n_analyzed, "volume_anomalies": s.get("series_compared")}.get(result.tool)
    if total:
        others = {float(v) for k, v in s.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
                  and k not in ("series_compared",)}
        if float(total) not in others:  # ambiguous when the total equals another headline figure
            out["total"] = float(total)
    if result.tool == "fair_price_for_lot" and s.get("fair_band") and s.get("target_price") is not None:
        out["price"], out["band"] = float(s["target_price"]), [float(x) for x in s["fair_band"]]
    return out


def check_claims(text: str, claims: dict) -> list[str]:
    issues: list[str] = []
    total = claims.get("total")
    if total:
        for m in NUM.finditer(BIN.sub(" ", text)):
            if m.group(3) or abs(_parse(m) - total) > 0.5:
                continue
            around = text[max(0, m.start() - 45): m.end() + 35]
            if not _POPULATION.search(around):
                issues.append(f"Число {m.group(0).strip()} — это общее количество сравниваемых объектов, а не число "
                              f"отобранных или выросших; подай его как «из {m.group(0).strip()} …».")
    if "band" in claims and _CONF_INTERVAL.search(text):
        issues.append("Диапазон Q1–Q3 — это разброс цен аналогов, а не доверительный интервал; не называй его так.")
    if "band" in claims:
        lo, hi = claims["band"]
        price = claims["price"]
        outside = price < lo - 0.005 or price > hi + 0.005  # band edges are exact 2-decimal figures
        for sent in _SENTENCE.split(text):
            nums = [_parse(m) for m in NUM.finditer(sent)]
            mentions_band = any(_matches(v, [lo]) for v in nums) and any(_matches(v, [hi]) for v in nums)
            if outside and mentions_band and _INSIDE.search(sent) and not _OUTSIDE.search(sent):
                issues.append(f"Цена {price:g} лежит вне диапазона Q1–Q3 ({lo:g}–{hi:g}); не утверждай, что она внутри.")
    return issues


def organisation_bins(result=None) -> set[str]:
    """BINs known to be organisations (the customers), so an 'ИНН/ИИН' label on them is wrong."""
    return set(TARGET_BINS)


_ID_LABEL = re.compile(r"\b(ИНН|ИИН|ЖСН)(\s*[:№]?\s*)(\d{12})\b")


def fix_id_terms(text: str, org_bins: set[str]) -> str:
    """An organisation's 12-digit number is a BIN (БИН / БСН), never an individual's ИИН / Russian ИНН."""
    def sub(m: re.Match) -> str:
        if m.group(3) not in org_bins:
            return m.group(0)
        return ("БСН" if m.group(1) == "ЖСН" else "БИН") + m.group(2) + m.group(3)
    return _ID_LABEL.sub(sub, text)


def validate(text: str, facts: dict, claims: dict | None = None) -> Validation:
    allowed, haystack = allowed_numbers(facts)
    unsupported: list[str] = []
    checked = 0
    for b in BIN.findall(text):
        checked += 1
        if b not in haystack:
            unsupported.append(b)
    masked = BIN.sub(" ", text)
    for m in DATE.finditer(masked):
        checked += 1
        y = int(m.group(3) or m.group(4))
        if not _matches(float(y), allowed):
            unsupported.append(m.group(0))
    masked = DATE.sub(" ", masked)
    for m in NUM.finditer(masked):
        v = _parse(m)
        if v in FREE_SMALL_INTS and not m.group(3):
            continue
        checked += 1
        if not _matches(v, allowed):
            unsupported.append(m.group(0).strip())
    issues = check_claims(text, claims) if claims else []
    return Validation(ok=not unsupported and not issues, checked=checked, unsupported=unsupported, issues=issues)
