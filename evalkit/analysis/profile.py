"""Cost and speed profile: line up several setups and find the cheapest one that is still good enough."""

from __future__ import annotations


def profile_runs(runs: list[dict], min_pass_rate: float = 0.8) -> dict:
    """`runs` are rows from Storage.list_runs (each has a name/version and a summary).

    A setup is "good enough" when its pass rate reaches `min_pass_rate`. Among those, the winner is the cheapest
    (ties go to the faster one). The Pareto set are the setups no other setup beats on quality, cost AND speed.
    """
    rows = []
    for r in runs:
        s = r["summary"]
        if not s or not s.get("n"):
            continue
        rows.append({
            "run_id": r["id"], "name": r["name"], "version": r["version"],
            "label": f"{r['name']} {r['version']}".strip() or f"run {r['id']}",
            "pass_rate": s["pass_rate"], "cost_per_case": s.get("cost_per_case", 0.0),
            "p95_ms": (s.get("latency") or {}).get("p95_ms") or 0.0, "n": s["n"],
        })  # fmt: skip
    if not rows:
        raise ValueError("no finished runs to profile")

    def dominated(a: dict) -> bool:
        return any(
            b is not a and b["pass_rate"] >= a["pass_rate"] and b["cost_per_case"] <= a["cost_per_case"]
            and b["p95_ms"] <= a["p95_ms"]
            and (b["pass_rate"] > a["pass_rate"] or b["cost_per_case"] < a["cost_per_case"] or b["p95_ms"] < a["p95_ms"])
            for b in rows
        )  # fmt: skip

    for a in rows:
        a["pareto"] = not dominated(a)
        a["good_enough"] = a["pass_rate"] >= min_pass_rate
    good = sorted((r for r in rows if r["good_enough"]), key=lambda r: (r["cost_per_case"], r["p95_ms"]))
    best = good[0] if good else None
    for a in rows:
        a["recommended"] = a is best
    rows.sort(key=lambda r: (not r["good_enough"], r["cost_per_case"], r["p95_ms"]))
    if best:
        dearest = max(good, key=lambda r: r["cost_per_case"])
        saving = 1 - best["cost_per_case"] / dearest["cost_per_case"] if dearest["cost_per_case"] else 0.0
        msg = f"Cheapest setup with a pass rate of at least {min_pass_rate:.0%}: {best['label']}" + (
            f" ({saving:.0%} cheaper than the most expensive good setup)." if saving > 0 else "."
        )
    else:  # fmt: skip
        msg = f"No setup reaches a pass rate of {min_pass_rate:.0%}. Best is {max(rows, key=lambda r: r['pass_rate'])['label']}."
    return {"min_pass_rate": min_pass_rate, "rows": rows, "recommendation": msg}
