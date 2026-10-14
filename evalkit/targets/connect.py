"""'Test connection': send one real question, show what came back, and work out the settings to use."""

from __future__ import annotations

import json
from typing import Any

from evalkit.core import tomlw
from evalkit.targets import build_target, resolve_config
from evalkit.targets.detect import suggest_all
from evalkit.targets.http import HttpTarget


def _preview(raw: Any, limit: int = 4000) -> str:
    text = raw if isinstance(raw, str) else json.dumps(raw, indent=2, ensure_ascii=False, default=str)
    return text[:limit] + ("\n…" if len(text) > limit else "")


async def probe(cfg: dict, question: str = "What is 2 + 2?") -> dict:
    """Builds the target, asks one question, and reports. Never raises for a bad app: errors are part of the result."""
    cfg = resolve_config(cfg)
    if cfg.get("type") == "python":
        raise ValueError("python targets are not tested from here (they run code). Use `evalkit run`.")
    try:
        target = build_target(cfg)
    except (ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc), "target": cfg}
    try:
        out = await target.ask(question)
    finally:
        await target.aclose()

    result: dict[str, Any] = {
        "ok": out.error is None,
        "error": out.error,
        "latency_ms": round(out.latency_ms, 1),
        "answer": out.answer,
        "contexts": [{"id": c.id, "text": c.text[:300], "score": c.score} for c in out.contexts],
        "tokens_in": out.tokens_in,
        "tokens_out": out.tokens_out,
        "reply": _preview(out.raw) if out.raw is not None else "",
        "suggestions": suggest_all(out.raw, question)
        if isinstance(out.raw, dict | list | str) and not out.error
        else None,
    }
    final = {k: v for k, v in cfg.items() if k != "transport"}  # never write test plumbing into the settings
    if isinstance(target, HttpTarget):
        final["answer_path"] = (
            target.answer_path if target.answer_path not in (None, "auto") else final.get("answer_path", "auto")
        )
        if target.contexts_path and target.contexts_path != "auto":
            final.update(contexts_path=target.contexts_path, context_text_path=target.context_text_path,
                         context_id_path=target.context_id_path, context_score_path=target.context_score_path)  # fmt: skip
        if target.tokens_in_path and target.tokens_in_path != "auto":
            final.update(tokens_in_path=target.tokens_in_path, tokens_out_path=target.tokens_out_path)
        elif final.get("tokens_in_path") == "auto":
            final.pop("tokens_in_path", None)
            final.pop("tokens_out_path", None)
        if final.get("contexts_path") == "auto":
            final.pop("contexts_path", None)
    result["target"] = final
    result["toml"] = tomlw.table(
        "target", {"type": final.get("type", "http"), **{k: v for k, v in final.items() if k != "type"}}
    )
    return result
