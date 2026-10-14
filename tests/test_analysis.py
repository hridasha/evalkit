import json
import random

import pytest

from evalkit.analysis.calibrate import calibrate_judge, parse_labels
from evalkit.analysis.compare import compare_runs
from evalkit.analysis.consistency import consistency_report
from evalkit.analysis.profile import profile_runs
from evalkit.core import report as html_report
from evalkit.core.gate import check_gate
from evalkit.core.models import CaseResult, Output, Score
from evalkit.core.summary import summarize
from evalkit.llm.base import FakeLLM
from evalkit.scorers import Services, build_scorers


def result(cid, passed, answer="a", value=None, latency=10.0, cost=0.0, repeat=0, label="s"):
    value = (1.0 if passed else 0.0) if value is None else value
    return CaseResult(cid, repeat, f"q {cid}", Output(answer=answer, latency_ms=latency, cost=cost),
                      {label: Score(value, passed)}, passed)  # fmt: skip


def run_of(passes, **kw):
    return [result(f"c{i:02d}", p, **kw) for i, p in enumerate(passes)]


# ---------------------------------------------------------------------------------------------- compare
def test_compare_finds_a_clear_improvement():
    a = run_of([False] * 25 + [True] * 25)
    b = run_of([True] * 22 + [False] * 3 + [True] * 25)
    c = compare_runs(a, b)
    assert c["pass_rate"]["diff"] == pytest.approx(0.44) and c["verdict"] == "B is better"
    assert len(c["improved"]) == 22 and c["regressed"] == []
    assert c["scorers"]["s"]["verdict"] == "B is better" and "B is better" in c["headline"]


def test_compare_refuses_to_call_noise_a_win():
    rng = random.Random(1)
    base = [rng.random() < 0.6 for _ in range(30)]
    other = list(base)
    other[0], other[1] = (not other[0]), (not other[1])  # two flips
    c = compare_runs(run_of(base), run_of(other))
    assert c["verdict"] == "no clear difference"


def test_compare_detects_a_regression_and_needs_common_cases():
    a = run_of([True] * 30)
    b = run_of([False] * 12 + [True] * 18)
    c = compare_runs(a, b)
    assert c["verdict"] == "A is better" and len(c["regressed"]) == 12
    with pytest.raises(ValueError, match="in common"):
        compare_runs(run_of([True]), [result("zzz", True)])


def test_compare_averages_repeats_and_reports_speed_and_cost():
    a = [result("c1", True, repeat=0, latency=100, cost=0.01), result("c1", False, repeat=1, latency=300, cost=0.01),
         result("c2", True), result("c3", True)]  # fmt: skip
    b = [result(cid, True, cost=0.02) for cid in ("c1", "c2", "c3")]
    c = compare_runs(a, b)
    assert c["latency_cost"]["a"]["cost_per_case"] == pytest.approx(0.005)
    assert c["latency_cost"]["b"]["cost_per_case"] == pytest.approx(0.02)
    assert c["pass_rate"]["a"] == pytest.approx((0.5 + 1 + 1) / 3)


# ---------------------------------------------------------------------------------------------- consistency
async def test_consistency_flags_unstable_questions():
    stable = [result("a", True, "Thirty days", repeat=i) for i in range(3)]
    flaky = [result("b", i != 1, ans, repeat=i) for i, ans in enumerate(["Paris", "Maybe Lyon or Nice", "Paris"])]
    para = [result("c", True, "ten"), result("c~p1", True, "ten")]
    rep = await consistency_report(stable + flaky + para)
    assert rep["questions"] == 3 and rep["flaky"] == 1 and rep["unstable"] == ["b"]
    row = next(r for r in rep["rows"] if r["case_id"] == "a")
    assert row["agreement"] == 1.0 and row["distinct"] == 1 and not row["unstable"]


async def test_consistency_ignores_single_answers_and_errors():
    err = CaseResult("e", 0, "q", Output(error="boom"), {}, False)
    rep = await consistency_report([result("x", True), err])
    assert rep["questions"] == 0


# ---------------------------------------------------------------------------------------------- profile
def fake_run(rid, name, version, pass_rate, cost, p95):
    return {"id": rid, "name": name, "version": version,
            "summary": {"n": 50, "pass_rate": pass_rate, "cost_per_case": cost, "latency": {"p95_ms": p95}}}  # fmt: skip


def test_profile_recommends_cheapest_good_enough_setup():
    runs = [fake_run(1, "rag", "big-model", 0.92, 0.0100, 900), fake_run(2, "rag", "small-model", 0.85, 0.0010, 400),
            fake_run(3, "rag", "tiny-model", 0.60, 0.0001, 200), fake_run(4, "rag", "bad", 0.80, 0.0200, 2000)]  # fmt: skip
    p = profile_runs(runs, min_pass_rate=0.8)
    rec = [r for r in p["rows"] if r["recommended"]]
    assert [r["version"] for r in rec] == ["small-model"] and "95% cheaper" in p["recommendation"]
    by = {r["version"]: r for r in p["rows"]}
    assert (
        by["bad"]["pareto"] is False and by["tiny-model"]["pareto"] is True and by["tiny-model"]["good_enough"] is False
    )


def test_profile_when_nothing_is_good_enough_or_empty():
    p = profile_runs([fake_run(1, "r", "a", 0.5, 0.01, 100)], 0.9)
    assert "No setup reaches" in p["recommendation"] and not any(r["recommended"] for r in p["rows"])
    with pytest.raises(ValueError):
        profile_runs([{"id": 1, "name": "x", "version": "", "summary": {}}])


# ---------------------------------------------------------------------------------------------- calibrate
def test_parse_labels_validates():
    rows = parse_labels("input,answer,expected,human_pass\nq,a,e,yes\nq2,a2,e2,0\n")
    assert [r["human"] for r in rows] == [True, False]
    with pytest.raises(ValueError, match="human_pass"):
        parse_labels("input,answer,human_pass\nq,a,maybe\n")
    with pytest.raises(ValueError, match="needs both"):
        parse_labels("input,answer,human_pass\n,a,yes\n")
    with pytest.raises(ValueError, match="no rows"):
        parse_labels("input,answer,human_pass\n")


async def test_calibration_measures_agreement_with_a_human():
    rows = parse_labels("input,answer,expected,human_pass\n" + "\n".join(
        f"q{i},{'good' if i % 2 == 0 else 'bad'},ref,{'yes' if i % 2 == 0 else 'no'}" for i in range(10)))  # fmt: skip

    def judge_says(prompt: str) -> str:
        return json.dumps({"correctness": 5 if "Answer to grade:\ngood" in prompt else 1, "reason": "r"})

    [judge] = build_scorers([{"name": "judge"}])
    r = await calibrate_judge(rows, judge, Services(llm=FakeLLM(judge_says)))
    assert r["agreement"] == 1.0 and r["kappa"] == 1.0 and r["verdict"] == "trustworthy"

    r2 = await calibrate_judge(rows, judge, Services(llm=FakeLLM(json.dumps({"correctness": 5}))))
    assert r2["judge_too_generous"] == 5 and r2["verdict"] == "not reliable"


# ---------------------------------------------------------------------------------------------- gate
def summary(**kw):
    base = {"pass_rate": 0.9, "errors": 0, "latency": {"p95_ms": 500}, "cost_per_case": 0.001,
            "scorers": {"faithfulness": {"mean": 0.95}}}  # fmt: skip
    return {**base, **kw}


def test_gate_passes_and_fails_on_each_limit():
    gate = {
        "min_pass_rate": 0.8,
        "max_errors": 0,
        "max_p95_ms": 1000,
        "max_cost_per_case": 0.01,
        "scorers": {"faithfulness": 0.9},
    }
    assert check_gate(summary(), gate).passed
    assert not check_gate(summary(pass_rate=0.7), gate).passed
    assert not check_gate(summary(errors=2), gate).passed
    assert not check_gate(summary(latency={"p95_ms": 5000}), gate).passed
    assert not check_gate(summary(cost_per_case=0.5), gate).passed
    low = check_gate(summary(scorers={"faithfulness": {"mean": 0.5}}), gate)
    assert not low.passed and "faithfulness" in low.failures[0]
    assert not check_gate(summary(scorers={}), gate).passed  # a scorer that produced nothing cannot pass silently


def test_gate_regression_needs_a_real_drop():
    gate = {"max_regression": 0.05}
    real = compare_runs(run_of([True] * 40), run_of([False] * 15 + [True] * 25))
    assert not check_gate(summary(), gate, real).passed
    noisy = compare_runs(run_of([True] * 20 + [False] * 20), run_of([True] * 19 + [False] * 21))
    assert check_gate(summary(), gate, noisy).passed
    assert not check_gate(
        summary(),
        {**gate, "require_significant": False},
        compare_runs(run_of([True] * 20 + [False] * 20), run_of([True] * 16 + [False] * 24)),
    ).passed
    assert check_gate(summary(), gate, None).passed  # no baseline: skipped, not failed
    with pytest.raises(ValueError, match="unknown gate"):
        check_gate(summary(), {"min_pas_rate": 1})


# ---------------------------------------------------------------------------------------------- summary + reports
def test_summary_has_tags_latency_and_classification():
    from evalkit.core.models import TestCase

    cases = {
        "a": TestCase("a", "q", expected="spam", tags=["x"]),
        "b": TestCase("b", "q", expected="ham", tags=["x", "y"]),
    }
    rs = [
        result("a", True, answer="spam", label="classification"),
        result("b", False, answer="spam", label="classification"),
    ]
    s = summarize(rs, cases)
    assert s["n"] == 2 and s["by_tag"]["x"]["pass_rate"] == 0.5 and s["by_tag"]["y"]["n"] == 1
    assert s["classification"]["accuracy"] == 0.5 and "spam" in s["classification"]["per_label"]
    assert s["scorers"]["classification"]["pass_rate"] == 0.5


def test_html_reports_render_and_escape():
    rs = [result("a", True, answer="<script>alert(1)</script>"), result("b", False)]
    run = {"id": 1, "name": "demo", "version": "v<1>", "created_at": 0, "status": "done", "summary": summarize(rs)}
    html = html_report.render_run(run, rs, check_gate(run["summary"], {"min_pass_rate": 0.9}).to_dict())
    assert "<script>alert" not in html and "&lt;script&gt;" in html and "Quality gate: FAILED" in html
    cmp = compare_runs(run_of([False] * 20), run_of([True] * 20))
    page = html_report.render_compare(cmp, {**run, "id": 1}, {**run, "id": 2})
    assert "B is better" in page and "A/B comparison" in page
