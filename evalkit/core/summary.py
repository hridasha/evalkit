"""Turn a list of case results into the numbers on a report."""

from __future__ import annotations

from collections import defaultdict

from evalkit.analysis.stats import macro_f1, mean, percentile, wilson
from evalkit.core.models import CaseResult, TestCase


def summarize(results: list[CaseResult], cases: dict[str, TestCase] | None = None) -> dict:
    cases = cases or {}
    n = len(results)
    passes = sum(1 for r in results if r.passed)
    errors = sum(1 for r in results if r.output.error)
    lat = [r.output.latency_ms for r in results if not r.output.error]

    scorers: dict[str, dict] = {}
    labels = sorted({k for r in results for k in r.scores})
    for label in labels:
        scored = [r.scores[label] for r in results if label in r.scores and r.scores[label].value is not None]
        verdicts = [s.passed for s in scored if s.passed is not None]
        scorers[label] = {
            "n": len(scored),
            "mean": mean([s.value for s in scored]),
            "pass_rate": (sum(1 for v in verdicts if v) / len(verdicts)) if verdicts else None,
            "skipped": n - len(scored),
        }

    by_tag: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        case = cases.get(r.case_id.split("~")[0])
        for tag in case.tags if case else []:
            by_tag[tag].append(r.passed)

    problems: dict[tuple[str, str], int] = defaultdict(int)
    for r in results:
        for label, s in r.scores.items():
            if s.detail.startswith("scorer error: "):
                problems[(label, s.detail.removeprefix("scorer error: ")[:300])] += 1
    warnings = [
        {"scorer": k[0], "count": c, "message": k[1]} for k, c in sorted(problems.items(), key=lambda kv: -kv[1])
    ]

    low, high = wilson(passes, n)
    cost = sum(r.output.cost for r in results)
    summary = {
        "n": n,
        "passed": passes,
        "pass_rate": passes / n if n else 0.0,
        "pass_ci": [low, high],
        "errors": errors,
        "warnings": warnings,
        "scorers": scorers,
        "latency": {
            "mean_ms": mean(lat),
            "p50_ms": percentile(lat, 50),
            "p95_ms": percentile(lat, 95),
            "max_ms": max(lat, default=None),
        },  # fmt: skip
        "tokens_in": sum(r.output.tokens_in for r in results),
        "tokens_out": sum(r.output.tokens_out for r in results),
        "cost_total": cost,
        "cost_per_case": cost / n if n else 0.0,
        "by_tag": {t: {"n": len(v), "pass_rate": sum(v) / len(v)} for t, v in sorted(by_tag.items())},
    }
    if "classification" in scorers:
        pairs = [
            (cases[r.case_id.split("~")[0]].expected, r.output.answer.strip().lower())
            for r in results
            if r.case_id.split("~")[0] in cases and cases[r.case_id.split("~")[0]].expected is not None
        ]
        if pairs:
            summary["classification"] = macro_f1([t.strip().lower() for t, _ in pairs], [p for _, p in pairs])
    return summary
