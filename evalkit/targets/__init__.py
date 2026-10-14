"""A target is the app being tested. Give it a question, get back an Output."""

from __future__ import annotations

from typing import Protocol

from evalkit.core.models import Output
from evalkit.targets.http import HttpTarget
from evalkit.targets.presets import expand
from evalkit.targets.python import PythonTarget


class Target(Protocol):
    async def ask(self, question: str) -> Output: ...

    async def aclose(self) -> None: ...


def resolve_config(cfg: dict) -> dict:
    """Apply `preset = "..."` (if any) so the result is a plain http/python config."""
    cfg = dict(cfg)
    if "preset" in cfg:
        cfg = {"type": cfg.get("type", "http"), **expand({k: v for k, v in cfg.items() if k != "type"})}
    return cfg


def build_target(cfg: dict) -> Target:
    cfg = resolve_config(cfg)
    kind = cfg.get("type", "http")
    rest = {k: v for k, v in cfg.items() if k != "type"}
    try:
        if kind in ("http", "web"):  # "web" = an ordinary website: use login, csrf, setup, answer_selector
            return HttpTarget(**rest)
        if kind == "python":
            return PythonTarget(**rest)
    except TypeError as exc:
        raise ValueError(f"bad settings for the {kind} target: {exc}") from None
    raise ValueError(f"unknown target type '{kind}' (use 'http', 'web' or 'python')")


__all__ = ["HttpTarget", "PythonTarget", "Target", "build_target", "resolve_config"]
