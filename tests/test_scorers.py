import json

import pytest

from evalkit.analysis import stats
from evalkit.core.models import TestCase
from evalkit.llm.base import FakeLLM, HashEmbedder, cosine
from evalkit.scorers import Services, build_scorers
from evalkit.scorers.base import normalize
from evalkit.scorers.judge import Judge, lexical_support, parse_json, sentences

from .conftest import make_output


async def score(cfg, case, output, services=None):
    [s] = build_scorers([cfg])
    return await s.score(case, output, services or Services())


def test_normalize():
    assert normalize("The Eiffel Tower!") == "eiffel tower"


async def test_exact_and_contains(case):
    assert (await score({"name": "exact"}, case, make_output("paris."))).passed is True
    assert (await score({"name": "exact"}, case, make_output("It is Paris"))).passed is False
    assert (await score({"name": "contains"}, case, make_output("It is in Paris, France"))).passed is True
    assert (await score({"name": "contains", "any_of": ["Lyon", "Nice"]}, case, make_output("Nice"))).passed is True
    assert (await score({"name": "exact"}, TestCase("z", "q"), make_output("x"))).value is None


async def test_regex(case):
    assert (await score({"name": "regex", "pattern": r"\bparis\b"}, case, make_output("Paris"))).passed is True
    no_secret = {"name": "regex", "pattern": r"sk-\w+", "must_match": False}
    assert (await score(no_secret, case, make_output("key sk-abc"))).passed is False
    assert (await score(no_secret, case, make_output("clean"))).passed is True


async def test_similarity_with_hash_embedder(case):
    close = await score({"name": "similarity", "threshold": 0.5}, case, make_output("Paris is the answer"))
    far = await score({"name": "similarity", "threshold": 0.5}, case, make_output("bananas and apples are fruit"))
    assert close.value > far.value and far.passed is False


async def test_retrieval_metrics():
    case = TestCase("q", "x", relevant=["d2", "d9"])
    out = make_output("a", contexts=[("d1", "."), ("d2", "."), ("d3", ".")])
    assert (await score({"name": "hit_rate", "k": 1}, case, out)).passed is False
    assert (await score({"name": "hit_rate", "k": 2}, case, out)).passed is True
    assert (await score({"name": "mrr"}, case, out)).value == 0.5
    rec = await score({"name": "recall", "k": 3, "threshold": 1.0}, case, out)
    assert rec.value == 0.5 and rec.passed is False
    assert (await score({"name": "mrr"}, TestCase("q", "x"), out)).value is None


async def test_classification_label(case):
    c = TestCase("q", "x", expected="Spam")
    assert (await score({"name": "classification"}, c, make_output("spam"))).passed is True
    assert (await score({"name": "classification"}, c, make_output("ham"))).passed is False


async def test_latency_and_cost(case):
    assert (await score({"name": "latency", "max_ms": 100}, case, make_output("x", latency_ms=50))).passed is True
    assert (await score({"name": "latency", "max_ms": 100}, case, make_output("x", latency_ms=500))).passed is False
    assert (await score({"name": "cost", "max_usd": 0.01}, case, make_output("x", cost=0.5))).passed is False


async def test_abstain_both_directions():
    unanswerable = TestCase("q", "x", should_abstain=True)
    answerable = TestCase("q", "x", expected="y")
    assert (await score({"name": "abstain"}, unanswerable, make_output("I don't know."))).passed is True
    assert (await score({"name": "abstain"}, unanswerable, make_output("It is blue."))).passed is False
    assert (
        await score({"name": "abstain"}, answerable, make_output("This is not mentioned in the documents"))
    ).passed is False
    assert (await score({"name": "abstain"}, answerable, make_output("Blue"))).passed is True
    assert (
        await score({"name": "abstain", "check_over_refusal": False}, answerable, make_output("Blue"))
    ).value is None


# ---------------------------------------------------------------------------------------------- LLM scorers
def judge_reply(**grades):
    return json.dumps({**grades, "reason": "because"})


async def test_judge_maps_1_to_5_onto_0_to_1(case):
    llm = FakeLLM(judge_reply(correctness=5))
    s = await score({"name": "judge"}, case, make_output("Paris"), Services(llm=llm))
    assert s.value == 1.0 and s.passed is True and s.detail == "because"
    assert "Reference answer:\nParis" in llm.prompts[0]
    low = await score({"name": "judge"}, case, make_output("Rome"), Services(llm=FakeLLM(judge_reply(correctness=1))))
    assert low.value == 0.0 and low.passed is False


async def test_judge_multiple_criteria_and_fenced_json(case):
    reply = "```json\n" + judge_reply(correctness=5, completeness=3) + "\n```"
    s = await score({"name": "judge", "criteria": ["correctness", "completeness"]}, case, make_output("Paris"),
                    Services(llm=FakeLLM(reply)))  # fmt: skip
    assert s.value == pytest.approx(0.75)


async def test_judge_rejects_nonsense(case):
    with pytest.raises(ValueError, match="outside 1-5"):
        await score({"name": "judge"}, case, make_output("x"), Services(llm=FakeLLM(judge_reply(correctness=9))))
    with pytest.raises(ValueError, match="did not return JSON"):
        await score({"name": "judge"}, case, make_output("x"), Services(llm=FakeLLM("sorry, no")))
    with pytest.raises(ValueError, match="needs a judge model"):
        await score({"name": "judge"}, case, make_output("x"))
    with pytest.raises(ValueError, match="unknown judge criteria"):
        Judge(criteria=["vibes"])
    assert (await score({"name": "judge"}, TestCase("q", "x"), make_output("x"), Services(llm=FakeLLM()))).value is None


async def test_faithfulness_llm_mode_counts_claims():
    reply = json.dumps(
        {"claims": [{"claim": "A", "verdict": "supported"}, {"claim": "B made up", "verdict": "unsupported"}]}
    )
    out = make_output("A. B.", contexts=[("d1", "A is true")])
    s = await score({"name": "faithfulness", "threshold": 0.8}, TestCase("q", "x"), out, Services(llm=FakeLLM(reply)))
    assert s.value == 0.5 and s.passed is False and "B made up" in s.detail


async def test_faithfulness_lexical_mode_works_without_a_model():
    ctx = [("d1", "The refund window is thirty days from delivery.")]
    ok = await score({"name": "faithfulness", "mode": "lexical"}, TestCase("q", "x"),
                     make_output("The refund window is thirty days.", contexts=ctx))  # fmt: skip
    bad = await score({"name": "faithfulness", "mode": "lexical"}, TestCase("q", "x"),
                      make_output("Shipping to Mars costs fifty dollars extra.", contexts=ctx))  # fmt: skip
    assert ok.passed is True and bad.passed is False
    assert (
        await score({"name": "faithfulness", "mode": "lexical"}, TestCase("q", "x"), make_output("hi there friend"))
    ).value is None


def test_helpers():
    assert parse_json('noise {"a": 1} noise') == {"a": 1}
    assert sentences("One two three. Ok. Four five six seven!") == ["One two three.", "Four five six seven!"]
    assert lexical_support("alpha beta gamma", "alpha beta gamma delta") is True


async def test_hash_embedder_similarity():
    a, b, c = await HashEmbedder().embed(["red car wins race", "the red car wins the race", "quantum field theory"])
    assert cosine(a, b) > 0.5 > cosine(a, c) + 0.3


# ---------------------------------------------------------------------------------------------- statistics
def test_percentile_and_wilson():
    assert stats.percentile([1, 2, 3, 4], 50) == 2 and stats.percentile([], 50) is None
    low, high = stats.wilson(8, 10)
    assert 0.4 < low < 0.8 < high < 1.0
    assert stats.wilson(0, 0) == (0.0, 1.0)


def test_bootstrap_detects_a_real_improvement_and_ignores_noise():
    a = [0.0] * 20 + [1.0] * 20
    better = [1.0] * 14 + [0.0] * 6 + [1.0] * 20
    r = stats.bootstrap_diff(a, better)
    assert r["diff"] == pytest.approx(0.35) and r["ci_low"] > 0 and r["p_value"] < 0.05
    same = stats.bootstrap_diff(a, list(a))
    assert same["diff"] == 0 and same["p_value"] == 1.0
    flip = [1 - x if i % 2 else x for i, x in enumerate(a)]
    assert stats.bootstrap_diff(a, flip)["p_value"] > 0.05 or abs(stats.bootstrap_diff(a, flip)["diff"]) < 0.2
    with pytest.raises(ValueError):
        stats.bootstrap_diff([1], [])


def test_mcnemar_exact():
    assert stats.mcnemar_exact([True] * 5, [True] * 5)["p_value"] == 1.0
    r = stats.mcnemar_exact([False] * 10 + [True] * 5, [True] * 10 + [True] * 5)
    assert (r["only_a"], r["only_b"]) == (0, 10) and r["p_value"] < 0.01
    assert stats.mcnemar_exact([True, False], [False, True])["p_value"] == 1.0


def test_kappa_and_f1():
    assert stats.cohen_kappa([True, True, False, False], [True, True, False, False]) == 1.0
    assert stats.cohen_kappa([True, False, True, False], [False, True, False, True]) == -1.0
    assert stats.cohen_kappa([], []) is None
    f = stats.macro_f1(["a", "a", "b", "b"], ["a", "b", "b", "b"])
    assert f["accuracy"] == 0.75 and f["per_label"]["b"]["recall"] == 1.0 and 0.7 < f["macro_f1"] < 0.8
