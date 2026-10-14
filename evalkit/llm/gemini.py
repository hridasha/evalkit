"""Gemini over plain HTTP (no SDK). The key comes from the GEMINI_API_KEY environment variable."""

from __future__ import annotations

import asyncio
import os
import re

import httpx

from evalkit.llm.base import LLMError, Usage

BASE = "https://generativelanguage.googleapis.com/v1beta"


def _key(api_key: str | None) -> str:
    key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise LLMError("no Gemini key: set the GEMINI_API_KEY environment variable")
    return key


def _human(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"


class QuotaExhaustedError(LLMError):
    """A daily (or very long) quota is used up, so waiting a few seconds will not help."""

    def __init__(self, model: str, retry_after: float | None):
        self.model, self.retry_after = model, retry_after
        when = "" if retry_after is None else f" It resets in about {_human(retry_after)}."
        super().__init__(
            f"the Gemini quota for '{model}' is used up (free keys allow only a few requests per day per model).{when} "
            'Use another model (for example model = "gemini-flash-lite-latest"), add `fallback_models`, or enable billing.'
        )


def _quota_error(resp: httpx.Response, model: str) -> QuotaExhaustedError | None:
    """A 429 whose reply says the per-day quota is gone, or asks us to wait longer than a minute and a half."""
    try:
        details = resp.json()["error"].get("details", [])
    except (ValueError, KeyError, AttributeError):
        return None
    retry = None
    daily = False
    for d in details:
        if str(d.get("@type", "")).endswith("RetryInfo"):
            m = re.match(r"([\d.]+)s", str(d.get("retryDelay", "")))
            retry = float(m.group(1)) if m else None
        for v in d.get("violations") or []:
            daily = daily or "PerDay" in str(v.get("quotaId", ""))
    return QuotaExhaustedError(model, retry) if daily or (retry is not None and retry > 90) else None


async def _post(client: httpx.AsyncClient, url: str, key: str, body: dict, model: str = "", retries: int = 4) -> dict:
    for attempt in range(retries + 1):
        try:
            resp = await client.post(url, json=body, headers={"x-goog-api-key": key})
        except httpx.HTTPError as exc:
            if attempt == retries:
                raise LLMError(f"Gemini request failed: {exc}") from None
        else:
            if resp.status_code < 400:
                return resp.json()
            if resp.status_code == 429 and (quota := _quota_error(resp, model)):
                raise quota
            if resp.status_code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise LLMError(f"Gemini returned {resp.status_code}: {resp.text[:300]}")
        await asyncio.sleep(min(2**attempt, 20))
    raise LLMError("unreachable")


class GeminiLLM:
    def __init__(
        self,
        model: str = "gemini-flash-latest",
        api_key: str | None = None,
        temperature: float = 0.0,
        timeout: float = 90.0,
        fallback_models: list[str] | None = None,
        max_concurrency: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.model, self.temperature = model, temperature
        self.models = [model, *(fallback_models or [])]
        self._api_key = api_key
        self.client = httpx.AsyncClient(timeout=timeout, transport=transport)
        self.usage = Usage()
        self._gate = asyncio.Semaphore(max_concurrency)
        self._exhausted: dict[str, QuotaExhaustedError] = {}

    async def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str:
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": self.temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        last: QuotaExhaustedError | None = None
        for model in self.models:
            if model in self._exhausted:  # do not hammer a model we already know is out of quota
                last = self._exhausted[model]
                continue
            try:
                async with self._gate:
                    data = await _post(
                        self.client, f"{BASE}/models/{model}:generateContent", _key(self._api_key), body, model
                    )
            except QuotaExhaustedError as exc:
                self._exhausted[model] = last = exc
                continue
            meta = data.get("usageMetadata", {})
            self.usage.calls += 1
            self.usage.tokens_in += meta.get("promptTokenCount", 0)
            self.usage.tokens_out += meta.get("candidatesTokenCount", 0)
            try:
                return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError):
                raise LLMError(f"Gemini sent no text: {str(data)[:300]}") from None
        assert last is not None
        raise last


class GeminiEmbedder:
    def __init__(
        self,
        model: str = "gemini-embedding-001",
        api_key: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.model = model
        self._api_key = api_key
        self.client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):
            batch = texts[i : i + 100]
            body = {
                "requests": [
                    {"model": f"models/{self.model}", "content": {"parts": [{"text": t or " "}]}} for t in batch
                ]
            }
            data = await _post(
                self.client, f"{BASE}/models/{self.model}:batchEmbedContents", _key(self._api_key), body, self.model
            )
            out.extend(e["values"] for e in data["embeddings"])
        return out
