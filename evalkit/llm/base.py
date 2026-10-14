from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


class LLM(Protocol):
    usage: Usage

    async def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str: ...


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class FakeLLM:
    """For tests and offline demos: `reply` is a string or a function of the prompt."""

    def __init__(self, reply: str | Callable[[str], str] = "{}"):
        self.reply = reply
        self.usage = Usage()
        self.prompts: list[str] = []

    async def complete(self, prompt: str, *, system: str | None = None, json_mode: bool = False) -> str:
        self.prompts.append(prompt)
        self.usage.calls += 1
        return self.reply(prompt) if callable(self.reply) else self.reply


_WORD = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class HashEmbedder:
    """Works offline: words (and word pairs) are hashed into a fixed-size vector.

    It measures shared wording, not meaning, so use a real embedder when paraphrases matter.
    """

    def __init__(self, dims: int = 512):
        self.dims = dims

    def _vector(self, text: str) -> list[float]:
        words = tokenize(text)
        vec = [0.0] * self.dims
        for token in words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]:
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
            vec[h % self.dims] += 1.0 if (h >> 40) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
