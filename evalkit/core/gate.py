"""The quality gate: pass or fail a build from a run's numbers.

[gate]
min_pass_rate = 0.80            # overall
max_errors = 0                  # app errors (crashes, timeouts)
max_p95_ms = 4000
max_cost_per_case = 0.01
max_regression = 0.03           # versus the baseline run: allowed drop in pass rate
require_significant = true      # only fail on a regression that is statistically clear
[gate.scorers]                  # minimum mean value per scorer
faithfulness = 0.85
hit_rate = 0.9
"""

from __future__ import annotations

from dataclasses import dataclass, field

KNOWN = {"min_pass_rate", "max_errors", "max_p95_ms", "max_cost_per_case", "max_regression", "require_significant",
         "scorers", "alpha"}  # fmt: skip


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "failures": self.failures, "checks": self.checks}


def check_gate(summary: dict, gate: dict, comparison: dict | None = None) -> GateResult:
    unknown = sorted(set(gate) - KNOWN)
    if unknown:
        raise ValueError(f"unknown gate setting(s): {', '.join(unknown)}")
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    if "min_pass_rate" in gate:
        v = summary["pass_rate"]
        check("pass rate", v >= gate["min_pass_rate"], f"{v:.1%} (need at least {gate['min_pass_rate']:.1%})")
    if "max_errors" in gate:
        v = summary["errors"]
        check("errors", v <= gate["max_errors"], f"{v} (allowed {gate['max_errors']})")
    if "max_p95_ms" in gate:
        v = (summary.get("latency") or {}).get("p95_ms")
        check(
            "p95 latency",
            v is None or v <= gate["max_p95_ms"],
            f"{v if v is None else round(v)} ms (limit {gate['max_p95_ms']})",
        )
    if "max_cost_per_case" in gate:
        v = summary.get("cost_per_case", 0.0)
        check("cost per question", v <= gate["max_cost_per_case"], f"${v:.5f} (limit ${gate['max_cost_per_case']})")
    for label, minimum in (gate.get("scorers") or {}).items():
        s = (summary.get("scorers") or {}).get(label)
        v = None if s is None else s["mean"]
        if v is None:
            check(f"scorer {label}", False, "no scores were produced for it")
        else:
            check(f"scorer {label}", v >= minimum, f"{v:.3f} (need at least {minimum})")
    if "max_regression" in gate:
        if comparison is None:
            check("regression", True, "no baseline run to compare with (skipped)")
        else:
            pr = comparison["pass_rate"]
            dropped = -pr["diff"]
            real = pr["p_value"] < gate.get("alpha", 0.05)
            bad = dropped > gate["max_regression"] and (real or not gate.get("require_significant", True))
            note = "" if real else " (not statistically clear)"
            check("regression vs baseline", not bad,
                  f"pass rate {pr['a']:.1%} -> {pr['b']:.1%}, change {pr['diff']:+.1%}{note} (allowed drop {gate['max_regression']:.1%})")  # fmt: skip
    failures = [f"{c['name']}: {c['detail']}" for c in checks if not c["ok"]]
    return GateResult(not failures, failures, checks)
