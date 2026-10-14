"""A/B test: did version B really beat version A, or is that just noise?"""

from __future__ import annotations

from collections import defaultdict

from evalkit.analysis.stats import bootstrap_diff, mcnemar_exact, mean, percentile
from evalkit.core.models import CaseResult


def _per_case(results: list[CaseResult]) -> dict[str, dict]:
    """Average repeats so each question counts once."""
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        grouped[r.case_id].append(r)
    out = {}
    for cid, rs in grouped.items():
        scores: dict[str, float] = {}
        for label in {k for r in rs for k in r.scores}:
            vals = [r.scores[label].value for r in rs if label in r.scores and r.scores[label].value is not None]
            if vals:
                scores[label] = sum(vals) / len(vals)
        out[cid] = {
            "pass": sum(1 for r in rs if r.passed) / len(rs),
            "scores": scores,
            "latency": mean([r.output.latency_ms for r in rs if not r.output.error]),
            "cost": sum(r.output.cost for r in rs) / len(rs),
        }
    return out


def _verdict(diff: float, p: float, alpha: float) -> str:
    if p >= alpha:
        return "no clear difference"
    return "B is better" if diff > 0 else "A is better"


def compare_runs(a: list[CaseResult], b: list[CaseResult], alpha: float = 0.05) -> dict:
    pa, pb = _per_case(a), _per_case(b)
    ids = sorted(set(pa) & set(pb))
    if not ids:
        raise ValueError("the two runs have no test cases in common")

    def paired(getter) -> tuple[list[float], list[float]]:
        xs, ys = [], []
        for i in ids:
            x, y = getter(pa[i]), getter(pb[i])
            if x is not None and y is not None:
                xs.append(x)
                ys.append(y)
        return xs, ys

    out: dict = {
        "n_paired": len(ids),
        "only_in_a": len(set(pa) - set(pb)),
        "only_in_b": len(set(pb) - set(pa)),
        "alpha": alpha,
    }

    xs, ys = paired(lambda c: c["pass"])
    boot = bootstrap_diff(xs, ys)
    mc = mcnemar_exact([x >= 0.5 for x in xs], [y >= 0.5 for y in ys])
    out["pass_rate"] = {
        "a": mean(xs), "b": mean(ys), "diff": boot["diff"], "ci": [boot["ci_low"], boot["ci_high"]],
        "p_value": boot["p_value"], "mcnemar_p": mc["p_value"], "verdict": _verdict(boot["diff"], boot["p_value"], alpha),
    }  # fmt: skip

    scorers = {}
    for label in sorted({k for c in [*pa.values(), *pb.values()] for k in c["scores"]}):
        xs, ys = paired(lambda c, label=label: c["scores"].get(label))
        if len(xs) < 2:
            continue
        r = bootstrap_diff(xs, ys)
        scorers[label] = {"n": len(xs), "a": mean(xs), "b": mean(ys), "diff": r["diff"], "ci": [r["ci_low"], r["ci_high"]],
                          "p_value": r["p_value"], "verdict": _verdict(r["diff"], r["p_value"], alpha)}  # fmt: skip
    out["scorers"] = scorers

    def side(res: list[CaseResult]) -> dict:
        lat = [r.output.latency_ms for r in res if not r.output.error]
        return {"p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "cost_total": sum(r.output.cost for r in res),
                "cost_per_case": sum(r.output.cost for r in res) / len(res) if res else 0.0}  # fmt: skip

    out["latency_cost"] = {"a": side(a), "b": side(b)}
    out["improved"] = [i for i in ids if pb[i]["pass"] > pa[i]["pass"]]
    out["regressed"] = [i for i in ids if pb[i]["pass"] < pa[i]["pass"]]
    pr = out["pass_rate"]
    out["verdict"] = pr["verdict"]
    out["headline"] = (
        f"Pass rate {pr['a']:.0%} -> {pr['b']:.0%} ({pr['diff']:+.1%}, 95% range {pr['ci'][0]:+.1%} to {pr['ci'][1]:+.1%}, "
        f"p={pr['p_value']:.3f}): {pr['verdict']}. {len(out['improved'])} improved, {len(out['regressed'])} regressed."
    )
    return out
