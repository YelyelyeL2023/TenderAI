"""Run an evaluation set (golden, corpus, paraphrases or held-out) through the agent and score fact accuracy
(target >= 85%).

A fact counts as correct when it appears in the rendered answer: identifiers verbatim, numbers parsed from
Russian/Kazakh formatting with 0.5% tolerance. Small numbers (< 1000: counts, unit prices) must appear outside the
Top-K list, where they would otherwise match by chance. Facts already present in the question (a BIN, lot number or
code the user typed) are not scored: echoing them proves nothing.
Cases may also carry `expect`, scored separately as checks: {"verdict": "fair"} is the Fair Price verdict;
{"found": false} means the agent must answer "no data" / refuse rather than produce figures.
Also reports intent accuracy and LLM-verdict validation.
With --repeats N every question is asked N times; the report adds the worst run's accuracy and the questions whose
understanding (intent, item, customer) or missed facts differed between runs.

Usage: python eval/run_eval.py eval/golden.jsonl [--no-llm] [--repeats N] [--out eval/report.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time

from tenderai.agent.agent import Agent

NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[.,](\d+))?")

DETAILS = re.compile(r"\n### (?:Детализация|Толығырақ) \(Top-")
SMALL = 1000   # numbers below this (counts, unit prices) must be found outside the Top-K list


def numbers_in(text: str) -> list[float]:
    out = []
    for m in NUM.finditer(text):
        whole = re.sub(r"[   ]", "", m.group(1))
        out.append(float(f"{whole}.{m.group(2)}" if m.group(2) else whole))
    return out


def has_number(x: float, found: list[float]) -> bool:
    return any(abs(v - x) <= max(0.005 * abs(x), 0.01) for v in found)


def scored_facts(case: dict) -> tuple[list[str], list[float], int]:
    """The case's facts minus those already in the question; returns (strings, numbers, n_echoed)."""
    q = case["question"]
    q_nums = numbers_in(q)
    strings = [s for s in case["facts"]["strings"] if s not in q]
    numbers = [float(n) for n in case["facts"]["numbers"] if float(n) not in q_nums]
    echoed = len(case["facts"]["strings"]) + len(case["facts"]["numbers"]) - len(strings) - len(numbers)
    return strings, numbers, echoed


def check_expect(expect: dict, tool: dict) -> list[str]:
    """Failed checks, e.g. ["verdict: expected fair, got elevated"]."""
    failed = []
    if "verdict" in expect:
        got = (tool.get("summary") or {}).get("verdict")
        if got != expect["verdict"]:
            failed.append(f"verdict: expected {expect['verdict']}, got {got}")
    if "found" in expect and bool(tool.get("found")) != expect["found"]:
        failed.append(f"found: expected {expect['found']}, got {tool.get('found')}")
    return failed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("golden")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--out", default="eval/report.json")
    ap.add_argument("--repeats", type=int, default=1,
                    help="ask every question N times: the LLM is not fully deterministic, so a single pass can hide "
                         "answers that only go wrong some of the time")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    with open(args.golden, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f if line.strip()]
    agent = Agent(use_llm=not args.no_llm)
    runs = max(1, args.repeats)
    total = correct = intents_ok = llm_verdicts = echoed_total = checks_total = checks_ok = 0
    per_run = [[0, 0] for _ in range(runs)]           # correct, total facts in each pass
    results = []
    for c in cases:
        strings, numbers, echoed = scored_facts(c)
        n_facts = len(strings) + len(numbers)
        expect = c.get("expect") or {}
        echoed_total += echoed
        attempts = []
        for k in range(runs):
            t = time.time()
            ans = agent.ask(c["question"])
            md = ans.markdown
            nums = numbers_in(md)
            nums_main = numbers_in(DETAILS.split(md)[0])   # without the Top-K list
            misses = [s for s in strings if s not in md]
            misses += [n for n in numbers if not has_number(n, nums if abs(n) >= SMALL else nums_main)]
            failed = check_expect(expect, ans.tool)
            total += n_facts
            correct += n_facts - len(misses)
            checks_total += len(expect)
            checks_ok += len(expect) - len(failed)
            per_run[k][0] += n_facts - len(misses)
            per_run[k][1] += n_facts
            intents_ok += ans.route["intent"] == c["intent"]
            llm_verdicts += ans.verdict_source == "llm"
            attempts.append({"intent": ans.route["intent"], "item": ans.route.get("item_text"),
                             "customer": ans.route.get("customer_text"), "missed": misses, "failed_checks": failed,
                             "verdict_source": ans.verdict_source, "validation": ans.validation,
                             "seconds": round(time.time() - t, 1)})
            tag = f"#{k + 1}" if runs > 1 else ""
            print(f"{c['id']:<10}{tag:<3} {c['lang']} intent={ans.route['intent']:<15} facts {n_facts - len(misses)}/{n_facts} "
                  f"checks {len(expect) - len(failed)}/{len(expect)} verdict={ans.verdict_source:<8} "
                  f"{attempts[-1]['seconds']}s {'MISS ' + str(misses) if misses else ''} {'FAIL ' + str(failed) if failed else ''}")
        # stable: every pass understood the question the same way and got the same facts right
        stable = len({(a["intent"], a["item"], a["customer"], tuple(map(str, a["missed"])), tuple(a["failed_checks"]))
                      for a in attempts}) == 1
        first = attempts[0]
        results.append({"id": c["id"], "lang": c["lang"], "group": c.get("group"), "intent_expected": c["intent"],
                        "intent": first["intent"], "facts": n_facts, "facts_echoed": echoed, "missed": first["missed"],
                        "checks": len(expect), "failed_checks": first["failed_checks"],
                        "verdict_source": first["verdict_source"], "validation": first["validation"],
                        "seconds": first["seconds"], "stable": stable, "attempts": attempts if runs > 1 else None})
    asked = len(cases) * runs
    report = {"cases": len(cases), "repeats": runs, "fact_accuracy": round(correct / total, 4) if total else None,
              "facts_total": total, "facts_correct": correct, "facts_echoed_not_scored": echoed_total,
              "check_accuracy": round(checks_ok / checks_total, 4) if checks_total else None,
              "checks_total": checks_total, "checks_passed": checks_ok,
              "intent_accuracy": round(intents_ok / asked, 4),
              "llm_verdict_share": round(llm_verdicts / asked, 4), "llm": not args.no_llm,
              "worst_run_fact_accuracy": round(min(c / t for c, t in per_run if t), 4) if total else None,
              "stable_share": round(sum(r["stable"] for r in results) / len(results), 4) if results else None,
              "unstable": [r["id"] for r in results if not r["stable"]], "results": results}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    chk = f"{report['check_accuracy']:.1%} ({checks_ok}/{checks_total})" if checks_total else "-"
    print(f"\nFACT ACCURACY {report['fact_accuracy']:.1%} ({correct}/{total}; {echoed_total} echoed from the question, "
          f"not scored) | verdict/refusal checks {chk} | intent accuracy {report['intent_accuracy']:.1%} | "
          f"LLM verdicts {report['llm_verdict_share']:.0%}")
    if runs > 1:
        print(f"STABILITY over {runs} runs: worst run {report['worst_run_fact_accuracy']:.1%} | "
              f"identical in every run {report['stable_share']:.1%} | unstable: {report['unstable'] or 'none'}")


if __name__ == "__main__":
    main()
