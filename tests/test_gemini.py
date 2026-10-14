import httpx
import pytest

from evalkit.core.models import CaseResult, Output, Score
from evalkit.core.summary import summarize
from evalkit.llm import CachedLLM, GeminiEmbedder, GeminiLLM, QuotaExhaustedError
from evalkit.llm.base import FakeLLM, LLMError


def ok(text="hello"):
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
        },
    )


def daily_quota():
    return httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "message": "quota",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                     "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "9541s"},
                ],
            }
        },
    )  # fmt: skip


def llm(handler, **kw):
    return GeminiLLM(api_key="k", transport=httpx.MockTransport(handler), **kw)


async def test_complete_sends_key_prompt_and_counts_tokens():
    seen = {}

    def handler(req: httpx.Request):
        seen["key"] = req.headers["x-goog-api-key"]
        seen["url"] = str(req.url)
        seen["body"] = req.content.decode()
        return ok("fine")

    g = llm(handler, model="m1")
    assert await g.complete("Q?", system="be brief", json_mode=True) == "fine"
    assert seen["key"] == "k" and "models/m1:generateContent" in seen["url"]
    assert "application/json" in seen["body"] and "be brief" in seen["body"]
    assert (g.usage.calls, g.usage.tokens_in, g.usage.tokens_out) == (1, 10, 2)


async def test_a_daily_quota_fails_fast_with_a_helpful_message():
    calls = []

    def handler(req):
        calls.append(1)
        return daily_quota()

    g = llm(handler, model="gemini-flash-latest")
    with pytest.raises(QuotaExhaustedError) as e:
        await g.complete("x")
    msg = str(e.value)
    assert "gemini-flash-latest" in msg and "2.7 hours" in msg and "gemini-flash-lite-latest" in msg
    assert len(calls) == 1  # no pointless retries
    with pytest.raises(QuotaExhaustedError):
        await g.complete("again")
    assert len(calls) == 1  # and it remembers, instead of asking again


async def test_fallback_model_takes_over_when_the_first_is_out_of_quota():
    def handler(req: httpx.Request):
        return daily_quota() if "model-a" in str(req.url) else ok("from b")

    g = llm(handler, model="model-a", fallback_models=["model-b"])
    assert await g.complete("x") == "from b"
    assert await g.complete("y") == "from b"


async def test_short_rate_limits_are_retried(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("evalkit.llm.gemini.asyncio.sleep", no_sleep)
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(429, text="slow down") if n["c"] < 3 else ok("finally")

    assert await llm(handler).complete("x") == "finally" and n["c"] == 3


async def test_other_errors_are_reported_plainly():
    with pytest.raises(LLMError, match="400"):
        await llm(lambda r: httpx.Response(400, text="bad request")).complete("x")
    with pytest.raises(LLMError, match="no Gemini key"):
        await _nokey()


async def _nokey():
    import os

    saved = {k: os.environ.pop(k, None) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY")}
    try:
        await GeminiLLM(transport=httpx.MockTransport(lambda r: ok())).complete("x")
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


async def test_embedder_batches_and_returns_vectors():
    def handler(req: httpx.Request):
        n = req.content.decode().count('"model"')
        return httpx.Response(200, json={"embeddings": [{"values": [0.1, 0.2]}] * n})

    e = GeminiEmbedder(api_key="k", transport=httpx.MockTransport(handler))
    vecs = await e.embed(["a", "b", "c"])
    assert vecs == [[0.1, 0.2]] * 3


async def test_cache_returns_saved_answers_without_asking_again(tmp_path):
    inner = FakeLLM(lambda p: f"reply to {p}")
    cached = CachedLLM(inner, tmp_path / "c.llmcache")
    assert await cached.complete("one") == "reply to one"
    assert await cached.complete("one") == "reply to one"
    assert (inner.usage.calls, cached.hits) == (1, 1)
    again = CachedLLM(FakeLLM("different"), tmp_path / "c.llmcache")  # a later run, new process
    assert await again.complete("one") == "reply to one" and again.hits == 1
    assert await cached.complete("one", system="s") == "reply to one"  # a different system prompt is a different key
    assert inner.usage.calls == 2


def test_summary_warns_when_a_scorer_could_not_run():
    def res(cid, detail):
        return CaseResult(cid, 0, "q", Output(answer="a"), {"judge": Score(None, None, detail)}, True)

    s = summarize(
        [
            res("a", "scorer error: QuotaExhaustedError: the Gemini quota is used up"),
            res("b", "scorer error: QuotaExhaustedError: the Gemini quota is used up"),
            res("c", ""),
        ]
    )
    assert s["warnings"] == [
        {"scorer": "judge", "count": 2, "message": "QuotaExhaustedError: the Gemini quota is used up"}
    ]
    assert summarize([res("a", "")])["warnings"] == []
