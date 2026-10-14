import json

import httpx
import pytest

from evalkit.core.jsonpath import extract, fill
from evalkit.core.models import TestCase
from evalkit.core.runner import run_eval
from evalkit.core.storage import RunNotFoundError
from evalkit.core.testset import dump_testset, parse_testset
from evalkit.scorers import Services, build_scorers
from evalkit.targets import HttpTarget, PythonTarget, build_target


# ---------------------------------------------------------------------------------------------- jsonpath
def test_extract_paths():
    data = {"a": {"b": [{"t": "x"}, {"t": "y"}]}, "n": 3}
    assert extract(data, "a.b[0].t") == "x"
    assert extract(data, "a.b[].t") == ["x", "y"]
    assert extract(data, "a.c") is None and extract(data, "a.b[5].t") is None
    assert extract(data, None) is data


def test_fill_replaces_whole_and_embedded_values():
    body = {"q": "{{input}}", "msg": "ask: {{input}}!", "n": 5, "list": ["{{input}}"]}
    assert fill(body, input="hi") == {"q": "hi", "msg": "ask: hi!", "n": 5, "list": ["hi"]}


# ---------------------------------------------------------------------------------------------- test sets
def test_jsonl_roundtrip_and_aliases():
    text = '{"question": "Q1?", "expected": "A", "relevant": ["d1"], "tags": ["x"]}\n# comment\n{"id": "b", "input": "Q2?"}\n'
    cases = parse_testset(text)
    assert [c.id for c in cases] == ["case-1", "b"] and cases[0].input == "Q1?"
    again = parse_testset(dump_testset(cases))
    assert [c.to_dict() for c in again] == [c.to_dict() for c in cases]


def test_csv_with_lists_and_flags():
    csv_text = "id,input,expected,relevant,should_abstain\na,Q?,A,d1|d2,\nb,Z?,,,true\n"
    a, b = parse_testset(csv_text, "csv")
    assert a.relevant == ["d1", "d2"] and a.should_abstain is False
    assert b.should_abstain is True and b.expected is None


def test_bad_test_sets_explain_themselves():
    with pytest.raises(ValueError, match="line 1"):
        parse_testset("not json")
    with pytest.raises(ValueError, match="duplicate"):
        parse_testset('{"id":"a","input":"x"}\n{"id":"a","input":"y"}')
    with pytest.raises(ValueError, match="empty"):
        parse_testset("\n")
    with pytest.raises(ValueError, match="no input"):
        TestCase.from_dict({"id": "z"})


# ---------------------------------------------------------------------------------------------- storage
def test_runs_and_results_are_saved(store):
    from evalkit.core.models import CaseResult, Output, Score

    rid = store.create_run("demo", "v1", {"a": 1})
    store.add_result(rid, CaseResult("q", 0, "hi", Output(answer="yo", latency_ms=5), {"s": Score(1.0, True)}, True))
    store.finish_run(rid, {"pass_rate": 1.0})
    run = store.get_run(rid)
    assert run["status"] == "done" and run["summary"]["pass_rate"] == 1.0 and run["config"] == {"a": 1}
    [res] = store.get_results(rid)
    assert res.output.answer == "yo" and res.scores["s"].passed is True
    assert store.latest_run("demo")["id"] == rid and store.latest_run("demo", before=rid) is None
    store.delete_run(rid)
    with pytest.raises(RunNotFoundError):
        store.get_run(rid)


def test_testsets_are_stored(store):
    store.save_testset("t", [TestCase("a", "q?")])
    assert store.list_testsets()[0]["count"] == 1 and store.get_testset("t")[0].input == "q?"
    store.delete_testset("t")
    assert store.list_testsets() == []


# ---------------------------------------------------------------------------------------------- targets
def http_target(handler, **kw):
    return HttpTarget("http://app/ask", transport=httpx.MockTransport(handler), **kw)


async def test_http_target_reads_answer_contexts_tokens_and_cost():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"data": {"answer": "Paris"}, "docs": [{"id": "d1", "text": "Paris is...", "score": 0.9}],
                                         "usage": {"in": 100, "out": 20}})  # fmt: skip

    t = http_target(handler, answer_path="data.answer", contexts_path="docs", tokens_in_path="usage.in",
                    tokens_out_path="usage.out", input_price_per_m=1.0, output_price_per_m=2.0)  # fmt: skip
    out = await t.ask("where?")
    assert seen["body"] == {"question": "where?"}
    assert out.answer == "Paris" and out.contexts[0].id == "d1" and out.contexts[0].score == 0.9
    assert (out.tokens_in, out.tokens_out) == (100, 20) and out.cost == pytest.approx(0.00014)
    assert out.latency_ms > 0 and out.error is None


async def test_http_target_failures_become_errors_not_crashes():
    assert "HTTP 500" in (await http_target(lambda r: httpx.Response(500, text="boom")).ask("q")).error
    assert (
        "no 'answer'"
        in (await http_target(lambda r: httpx.Response(200, json={"x": 1}), answer_path="answer").ask("q")).error
    )
    assert (
        "could not find an answer" in (await http_target(lambda r: httpx.Response(200, json={"x": 1})).ask("q")).error
    )

    def refuse(req):
        raise httpx.ConnectError("down")

    assert "ConnectError" in (await http_target(refuse).ask("q")).error
    plain = await http_target(lambda r: httpx.Response(200, text="plain text")).ask("q")
    assert plain.answer == "plain text"


def test_http_target_headers_use_environment(monkeypatch):
    monkeypatch.setenv("APP_KEY", "s3cret")
    t = http_target(lambda r: httpx.Response(200, json={}), headers={"Authorization": "Bearer ${APP_KEY}"})
    assert t.headers["Authorization"] == "Bearer s3cret"


def fn_ok(question):
    return {"answer": f"echo {question}", "contexts": ["a", {"id": "d9", "text": "b", "score": 0.5}], "tokens_in": 3}


def fn_str(question):
    return "just text"


def fn_bad(question):
    raise RuntimeError("model crashed")


async def test_python_target_variants():
    out = await PythonTarget("tests.test_core:fn_ok").ask("hi")
    assert out.answer == "echo hi" and [c.id for c in out.contexts] == ["0", "d9"] and out.tokens_in == 3
    assert (await PythonTarget("tests.test_core:fn_str").ask("x")).answer == "just text"
    assert "model crashed" in (await PythonTarget("tests.test_core:fn_bad").ask("x")).error
    with pytest.raises(ValueError):
        PythonTarget("nocolon")
    with pytest.raises(ValueError, match="unknown target"):
        build_target({"type": "ftp"})


# ---------------------------------------------------------------------------------------------- runner
async def test_run_scores_saves_and_summarizes(store):
    cases = [TestCase("a", "capital of france?", expected="Paris", tags=["geo"]),
             TestCase("b", "capital of peru?", expected="Lima", tags=["geo"]),
             TestCase("c", "boom", expected="x")]  # fmt: skip

    def handler(req):
        q = json.loads(req.content)["question"]
        if q == "boom":
            return httpx.Response(500, text="x")
        return httpx.Response(200, json={"answer": "Paris" if "france" in q else "Cusco"})

    report = await run_eval(cases, http_target(handler), build_scorers([{"name": "exact"}]), Services(), store, name="demo",
                            version="v1", concurrency=2)  # fmt: skip
    s = report.summary
    assert (s["n"], s["passed"], s["errors"]) == (3, 1, 1)
    assert s["scorers"]["exact"]["pass_rate"] == 0.5 and s["by_tag"]["geo"]["pass_rate"] == 0.5
    assert [r.case_id for r in report.results] == ["a", "b", "c"]
    assert store.get_run(report.run_id)["status"] == "done" and len(store.get_results(report.run_id)) == 3


async def test_repeats_and_paraphrases_become_extra_results():
    cases = [TestCase("a", "q?", expected="x", paraphrases=["q again?", "q third?"])]
    report = await run_eval(cases, http_target(lambda r: httpx.Response(200, json={"answer": "x"})),
                            build_scorers([{"name": "exact"}]), Services(), repeats=2, paraphrases=True)  # fmt: skip
    assert sorted((r.case_id, r.repeat) for r in report.results) == [
        (c, n) for c in ("a", "a~p1", "a~p2") for n in (0, 1)
    ]


async def test_a_broken_scorer_is_reported_not_fatal():
    report = await run_eval([TestCase("a", "q", expected="x")], http_target(lambda r: httpx.Response(200, json={"answer": "x"})),
                            build_scorers([{"name": "judge"}, {"name": "exact"}]), Services())  # fmt: skip
    r = report.results[0]
    assert "scorer error" in r.scores["judge"].detail and r.scores["judge"].passed is None
    assert r.passed is True  # the judge could not run, but exact passed


def test_unknown_or_duplicate_scorers_are_explained():
    with pytest.raises(ValueError, match="unknown scorer"):
        build_scorers([{"name": "magic"}])
    with pytest.raises(ValueError, match="share the label"):
        build_scorers([{"name": "exact"}, {"name": "exact"}])
    assert (
        len(build_scorers([{"name": "latency", "max_ms": 10}, {"name": "latency", "max_ms": 99, "label": "slow"}])) == 2
    )
    with pytest.raises(ValueError, match="bad settings"):
        build_scorers([{"name": "latency", "oops": 1}])
