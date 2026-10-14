"""Test a Python function directly: `function = "my_app.rag:answer"`. No adapter file is needed, even for Django.

The function takes the question and returns a string, or a dict with `answer` (and optionally `contexts`,
`tokens_in`, `tokens_out`, `cost`). If it returns something else (objects, other key names), say where things are:

    [target]
    type = "python"
    function = "screening.chatbot:answer_question"
    django_settings = "resume_screener.settings"   # starts Django first (use path = "C:/my/project" if needed)
    answer_key = "answer_text"                     # where the answer is in the returned value
    contexts_key = "sources"                       # a list of objects (candidates, documents...)
    context_text = "summary"                       # attributes are followed with dots: "candidate.name"
    context_id = "candidate.name"

`pre = "pkg.module:function"` runs another function on the question first (e.g. to turn it into an embedding), and
`contexts_key = "@"` means "the returned value itself is the list".
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import sys
import time
from pathlib import Path
from typing import Any

from evalkit.core.models import Context, Output


def dig(obj: Any, path: str | None) -> Any:
    """Follow `a.b.0.c` through dicts, lists and object attributes. '@' is the object itself. None if anything is missing."""
    if path in (None, ""):
        return None
    if path == "@":
        return obj
    for token in str(path).split("."):
        if obj is None:
            return None
        if isinstance(obj, dict):
            obj = obj.get(token)
        elif isinstance(obj, list | tuple) and token.lstrip("-").isdigit():
            i = int(token)
            obj = obj[i] if -len(obj) <= i < len(obj) else None
        else:
            obj = getattr(obj, token, None)
            if callable(obj):
                obj = None  # never call methods by accident
    return obj


def _load(spec: str):
    if ":" not in spec:
        raise ValueError("a function must look like 'package.module:function'")
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


class PythonTarget:
    def __init__(
        self,
        function: str,
        path: str | None = None,
        kwargs: dict | None = None,
        django_settings: str | None = None,
        pre: str | None = None,
        answer_key: str | None = None,
        contexts_key: str | None = None,
        context_text: str | None = None,
        context_id: str | None = None,
        context_score: str | None = None,
    ):
        if path:
            sys.path.insert(0, str(Path(path).resolve()))
        if django_settings:
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", django_settings)
            import django

            django.setup()
        self.fn = _load(function)
        self.pre = _load(pre) if pre else None
        self.kwargs = kwargs or {}
        self.answer_key, self.contexts_key = answer_key, contexts_key
        self.context_text, self.context_id, self.context_score = context_text, context_id, context_score

    async def _call(self, fn, arg):
        if inspect.iscoroutinefunction(fn):
            return await fn(arg, **self.kwargs) if fn is self.fn else await fn(arg)
        return await asyncio.to_thread(fn, arg, **self.kwargs) if fn is self.fn else await asyncio.to_thread(fn, arg)

    async def ask(self, question: str) -> Output:
        started = time.perf_counter()
        try:
            arg = await self._call(self.pre, question) if self.pre else question
            result = await self._call(self.fn, arg)
        except Exception as exc:  # noqa: BLE001 - the app under test may fail in any way
            return Output(error=f"{type(exc).__name__}: {exc}", latency_ms=(time.perf_counter() - started) * 1000)
        return self._to_output(result, (time.perf_counter() - started) * 1000)

    def _context(self, item: Any, n: int) -> Context:
        if self.context_text or self.context_id or self.context_score:
            text = dig(item, self.context_text) if self.context_text else item
            cid = dig(item, self.context_id) if self.context_id else None
            score = dig(item, self.context_score) if self.context_score else None
            return Context(
                text="" if text is None else str(text),
                id=str(n) if cid is None else str(cid),
                score=score if isinstance(score, int | float) else None,
            )
        if isinstance(item, dict):
            return Context(
                text=str(item.get("text", "")),
                id=None if item.get("id") is None else str(item["id"]),
                score=item.get("score"),
            )
        return Context(text=str(item), id=str(n))

    def _to_output(self, result: Any, latency: float) -> Output:
        if self.answer_key is not None:
            answer = dig(result, self.answer_key)
        elif isinstance(result, str):
            answer = result
        elif isinstance(result, dict) and "answer" in result:
            answer = result["answer"]
        else:
            return Output(
                error="the function must return a string or a dict with 'answer' (or set `answer_key` to say where the answer is)",
                latency_ms=latency,
            )
        if answer is None:
            return Output(error=f"no '{self.answer_key}' in what the function returned", latency_ms=latency)
        raw_contexts = (
            dig(result, self.contexts_key)
            if self.contexts_key
            else (result.get("contexts") if isinstance(result, dict) else None)
        )
        contexts = [self._context(c, n) for n, c in enumerate(list(raw_contexts or []))]
        meta = result if isinstance(result, dict) else {}
        return Output(
            answer=str(answer),
            contexts=contexts,
            latency_ms=latency,
            tokens_in=int(meta.get("tokens_in", 0)),
            tokens_out=int(meta.get("tokens_out", 0)),
            cost=float(meta.get("cost", 0.0)),
            raw=result if isinstance(result, str | dict | list) else repr(result)[:500],
        )

    async def aclose(self) -> None:
        return None
