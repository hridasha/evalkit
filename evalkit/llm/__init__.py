"""The language model and embedding model EvalKit itself needs (for judging and for similarity)."""

from __future__ import annotations

from pathlib import Path

from evalkit.llm.base import LLM, Embedder, FakeLLM, HashEmbedder, LLMError, Usage
from evalkit.llm.cache import CachedLLM
from evalkit.llm.gemini import GeminiEmbedder, GeminiLLM, QuotaExhaustedError


def build_llm(cfg: dict | None, cache_path: str | Path | None = None) -> LLM | None:
    """Answers are remembered on disk next to the run database; `cache = false` in [llm] turns that off."""
    cfg = dict(cfg or {})
    kind = cfg.pop("type", None)
    use_cache = bool(cfg.pop("cache", True))
    if kind in (None, "none"):
        return None
    if kind == "gemini":
        llm = GeminiLLM(**cfg)
        return CachedLLM(llm, cache_path) if use_cache and cache_path else llm
    raise ValueError(f"unknown llm type '{kind}' (use 'gemini' or leave it out)")


def build_embedder(cfg: dict | None) -> Embedder:
    cfg = dict(cfg or {})
    kind = cfg.pop("type", "hash")
    if kind == "hash":
        return HashEmbedder(**cfg)
    if kind == "gemini":
        return GeminiEmbedder(**cfg)
    raise ValueError(f"unknown embedder type '{kind}' (use 'hash' or 'gemini')")


__all__ = [
    "LLM", "Embedder", "FakeLLM", "GeminiEmbedder", "GeminiLLM", "HashEmbedder", "LLMError", "Usage",
    "CachedLLM", "QuotaExhaustedError", "build_embedder", "build_llm",
]  # fmt: skip
