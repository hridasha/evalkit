"""A scorer looks at one answer and says how good it is. New scorers plug in with @register."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import ClassVar

from evalkit.core.models import Output, Score, TestCase
from evalkit.llm.base import LLM, Embedder, HashEmbedder


@dataclass
class Services:
    """What scorers may use: the judge model and the embedding model."""

    llm: LLM | None = None
    embedder: Embedder | None = None

    def need_llm(self, who: str) -> LLM:
        if self.llm is None:
            raise ValueError(f"the '{who}' scorer needs a judge model: add an [llm] section to the config")
        return self.llm

    def get_embedder(self) -> Embedder:
        if self.embedder is None:
            self.embedder = HashEmbedder()
        return self.embedder


class Scorer:
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""

    def __init__(self, label: str | None = None, **params):
        self.label = label or self.name  # lets one config use the same scorer twice with different settings
        self.params = params

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        raise NotImplementedError


REGISTRY: dict[str, type[Scorer]] = {}


def register(cls: type[Scorer]) -> type[Scorer]:
    REGISTRY[cls.name] = cls
    return cls


def build_scorers(configs: list[dict]) -> list[Scorer]:
    out: list[Scorer] = []
    for cfg in configs:
        cfg = dict(cfg)
        name = cfg.pop("name", None)
        if name not in REGISTRY:
            raise ValueError(f"unknown scorer '{name}'. Available: {', '.join(sorted(REGISTRY))}")
        try:
            out.append(REGISTRY[name](**cfg))
        except TypeError as exc:
            raise ValueError(f"bad settings for scorer '{name}': {exc}") from None
    labels = [s.label for s in out]
    dupes = sorted({x for x in labels if labels.count(x) > 1})
    if dupes:
        raise ValueError(f"two scorers share the label '{dupes[0]}'; give one a different `label`")
    return out


_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = re.compile(r"[^\w\s]")


def normalize(text: str) -> str:
    """Lowercase, drop punctuation and articles, squeeze spaces: 'The Eiffel Tower.' == 'eiffel tower'."""
    return " ".join(_ARTICLES.sub(" ", _PUNCT.sub(" ", text.lower())).split())
