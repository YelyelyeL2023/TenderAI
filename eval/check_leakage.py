"""Check that the evaluation sets cannot be answered by anything other than the agent's general logic.

1. Few-shot leakage: the router's LLM examples must not be (or closely paraphrase) an evaluation question;
   similarity is the Jaccard index of character 3-grams, flagged at >= THRESHOLD.
2. Hard-coding: no identifier or reference figure from an evaluation set may appear in the agent's source
   (tenderai/): lot numbers, object IDs, ENSTRU codes, BINs (except the 27 target customers listed in config.py
   by the assignment) and reference numbers >= 10 000 written as digits (round thousands excluded: they occur
   naturally, e.g. unit factors).

Usage: python eval/check_leakage.py        # exit code 1 if anything is found
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_SETS = ("corpus.jsonl", "paraphrases.jsonl", "golden.jsonl", "heldout.jsonl")
THRESHOLD = 0.5


def eval_cases() -> list[dict]:
    out = []
    for name in EVAL_SETS:
        p = ROOT / "eval" / name
        if p.exists():
            out += [{**json.loads(line), "set": name} for line in p.read_text(encoding="utf-8").splitlines() if line]
    return out


def few_shot_examples() -> list[str]:
    from tenderai.agent.router import SYSTEM
    return re.findall(r"«(.+?)» ->", SYSTEM)


def trigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", s.lower())).strip()
    return {s[i:i + 3] for i in range(len(s) - 2)}


def similarity(a: str, b: str) -> float:
    x, y = trigrams(a), trigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def few_shot_leaks(cases: list[dict], examples: list[str]) -> list[tuple[float, str, str, str]]:
    hits = []
    for ex in examples:
        best = max(cases, key=lambda c: similarity(ex, c["question"]))
        score = similarity(ex, best["question"])
        if score >= THRESHOLD:
            hits.append((round(score, 2), ex, best["id"], best["set"]))
    return hits


def hard_coded(cases: list[dict]) -> list[tuple[str, str, str]]:
    from tenderai.config import TARGET_BINS
    tokens: dict[str, str] = {}
    for c in cases:
        for s in c["facts"]["strings"]:
            if s not in TARGET_BINS:
                tokens[s] = c["id"]
        for n in c["facts"]["numbers"]:
            if abs(n) >= 10_000 and n % 1000:
                tokens[str(int(n))] = c["id"]
    sources = {p: p.read_text(encoding="utf-8", errors="ignore") for p in (ROOT / "tenderai").rglob("*")
               if p.suffix in (".py", ".sql", ".html", ".js", ".json", ".yaml", ".yml") and "__pycache__" not in p.parts}
    return [(t, cid, str(p.relative_to(ROOT))) for t, cid in tokens.items() for p, s in sources.items()
            if re.search(r"(?<![\w.])" + re.escape(t) + r"(?![\w])", s)]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    cases = eval_cases()
    leaks, coded = few_shot_leaks(cases, few_shot_examples()), hard_coded(cases)
    for score, ex, cid, name in leaks:
        print(f"FEW-SHOT LEAK {score:.2f}: «{ex}» ~ {cid} ({name})")
    for token, cid, path in coded:
        print(f"HARD-CODED: {token} (from {cid}) found in {path}")
    print(f"{len(cases)} questions, {len(few_shot_examples())} few-shot examples: "
          f"{len(leaks)} few-shot leaks, {len(coded)} hard-coded identifiers/figures")
    return 1 if leaks or coded else 0


if __name__ == "__main__":
    raise SystemExit(main())
