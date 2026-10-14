"""Work out where the answer, the retrieved passages and the token counts are in an unknown JSON reply.

This is what lets EvalKit connect to a new API without writing an adapter: it looks at one real reply and
suggests the paths (`answer_path = "choices[0].message.content"`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

ANSWER_KEYS = {
    "answer": 6, "reply": 6, "response": 5, "generated_text": 5, "output": 5, "completion": 4, "result": 4,
    "text": 4, "content": 4, "message": 3, "summary": 2, "body": 1,
}  # fmt: skip
SKIP_KEYS = {
    "id", "model", "role", "object", "type", "status", "created", "error", "finish_reason", "stop_reason", "name",
    "version", "url", "email", "timestamp", "time", "date", "index", "code", "title", "source", "filename", "doc_id",
    "uuid", "token", "key", "path", "mime_type", "language", "created_at", "done_reason",
}  # fmt: skip
CONTEXT_WORDS = (
    "context", "source", "document", "doc", "passage", "chunk", "citation", "reference", "retrieved", "hit", "snippet",
    "evidence", "excerpt",
)  # fmt: skip
NOT_CONTEXT = ("choice", "candidate", "message", "part", "tool", "usage")
TEXT_KEYS = ("text", "content", "page_content", "chunk", "passage", "snippet", "body", "excerpt", "document")
ID_KEYS = ("id", "doc_id", "document_id", "source", "filename", "file", "title", "name", "url")
SCORE_KEYS = ("score", "similarity", "relevance", "distance", "rank_score", "confidence")
USAGE_PAIRS = [
    ("prompt_tokens", "completion_tokens"), ("input_tokens", "output_tokens"),
    ("promptTokenCount", "candidatesTokenCount"), ("prompt_eval_count", "eval_count"),
]  # fmt: skip
_URL = re.compile(r"^(https?://|www\.)\S+$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SIMPLE_KEY = re.compile(r"^[A-Za-z0-9_\-]+$")


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _walk(data: Any, path: str = "", depth: int = 0) -> Iterator[tuple[str, Any]]:
    """Every leaf (and every list) with its path. Only the first few items of a list are visited."""
    if depth > 8:
        return
    if isinstance(data, dict):
        for k, v in data.items():
            if not _SIMPLE_KEY.match(str(k)):
                continue
            yield from _walk(v, _join(path, str(k)), depth + 1)
    elif isinstance(data, list):
        yield path, data
        for i, v in enumerate(data[:3]):
            yield from _walk(v, f"{path}[{i}]", depth + 1)
    else:
        yield path, data


def _last_key(path: str) -> str:
    return re.sub(r"\[\d*\]", "", path).rsplit(".", 1)[-1].lower()


def suggest_answer_paths(data: Any, question: str = "", limit: int = 5) -> list[dict]:
    """Best guesses first. Each: {path, preview, score}."""
    if isinstance(data, str):
        return [{"path": "", "preview": data[:120], "score": 10.0}] if data.strip() else []
    q = question.strip().lower()
    found = []
    for path, v in _walk(data):
        if not isinstance(v, str) or not v.strip():
            continue
        text = v.strip()
        key = _last_key(path)
        if key in SKIP_KEYS or _URL.match(text) or _UUID.match(text) or text.lower() == q:
            continue
        score = ANSWER_KEYS.get(key, 0) + min(len(text), 400) / 100
        low = path.lower()
        if any(w in low for w in ("choices", "candidates", "message")):
            score += 2
        if any(w in low for w in CONTEXT_WORDS):
            score -= 5
        if re.search(r"\[[1-9]\d*\]", path):  # the 2nd, 3rd... item of a list is rarely "the" answer
            score -= 2
        found.append({"path": path, "preview": text[:120], "score": round(score, 2)})
    found.sort(key=lambda s: -s["score"])
    return found[:limit]


def _element_fields(items: list) -> dict | None:
    first = next((i for i in items if i is not None), None)
    if isinstance(first, str):
        return {"text_path": None, "id_path": None, "score_path": None, "preview": first[:100]}
    if not isinstance(first, dict):
        return None
    text_key = next((k for k in TEXT_KEYS if isinstance(first.get(k), str)), None)
    if text_key is None:
        return None
    return {
        "text_path": text_key,
        "id_path": next((k for k in ID_KEYS if isinstance(first.get(k), str | int)), None),
        "score_path": next((k for k in SCORE_KEYS if isinstance(first.get(k), int | float)), None),
        "preview": str(first[text_key])[:100],
    }


def suggest_contexts_paths(data: Any, limit: int = 3) -> list[dict]:
    """Lists that look like retrieved passages / sources. Each: {path, text_path, id_path, score_path, count, preview}."""
    found = []
    for path, v in _walk(data):
        if not isinstance(v, list) or not v or not path:
            continue
        low = path.lower()
        if any(w in low for w in NOT_CONTEXT):
            continue
        fields = _element_fields(v)
        if fields is None:
            continue
        named = any(w in low for w in CONTEXT_WORDS)
        if not named and not (fields["text_path"] and len(fields["preview"]) > 40):
            continue
        found.append(
            {"path": path, "count": len(v), "score": (3 if named else 0) + (1 if fields["id_path"] else 0), **fields}
        )
    found.sort(key=lambda s: -s["score"])
    return [{k: v for k, v in s.items() if k != "score"} for s in found[:limit]]


def suggest_usage(data: Any) -> dict | None:
    """Where the token counts are, e.g. {tokens_in_path: 'usage.prompt_tokens', tokens_out_path: ...}."""
    leaves = {path: v for path, v in _walk(data) if isinstance(v, int | float) and not isinstance(v, bool)}
    for a, b in USAGE_PAIRS:
        pa = next((p for p in leaves if p == a or p.endswith("." + a)), None)
        if pa is None:
            continue
        pb = pa[: -len(a)] + b
        if pb in leaves:
            return {"tokens_in_path": pa, "tokens_out_path": pb}
    return None


def suggest_all(data: Any, question: str = "") -> dict:
    return {
        "answer_paths": suggest_answer_paths(data, question),
        "contexts_paths": suggest_contexts_paths(data),
        "usage": suggest_usage(data),
    }
