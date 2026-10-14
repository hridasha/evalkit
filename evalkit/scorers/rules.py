"""Scorers that need no model: exact text, regex, retrieval metrics, labels, speed, cost, "I don't know"."""

from __future__ import annotations

import re

from evalkit.core.models import Output, Score, TestCase
from evalkit.llm.base import cosine
from evalkit.scorers.base import Scorer, Services, normalize, register


def _passed(value: float, threshold: float) -> bool:
    return value >= threshold


@register
class Exact(Scorer):
    name = "exact"
    description = "The answer equals the expected answer (ignoring case, punctuation and articles)."

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        if case.expected is None:
            return Score(None, None, "no expected answer")
        ok = normalize(output.answer) == normalize(case.expected)
        return Score(1.0 if ok else 0.0, ok, "" if ok else f"expected '{case.expected}'")


@register
class Contains(Scorer):
    name = "contains"
    description = "The answer contains the expected text (or any of `any_of`)."

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        wanted = self.params.get("any_of") or ([case.expected] if case.expected else [])
        if not wanted:
            return Score(None, None, "no expected answer")
        answer = normalize(output.answer)
        ok = any(normalize(w) in answer for w in wanted)
        return Score(1.0 if ok else 0.0, ok, "" if ok else f"missing '{wanted[0]}'")


@register
class Regex(Scorer):
    name = "regex"
    description = "The answer matches `pattern` (add `must_match = false` to require that it does NOT match)."

    def __init__(self, pattern: str, must_match: bool = True, flags: str = "i", label: str | None = None):
        super().__init__(label)
        self.rx = re.compile(pattern, sum(getattr(re, f.upper()) for f in flags))
        self.must_match = must_match

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        found = bool(self.rx.search(output.answer))
        ok = found == self.must_match
        return Score(
            1.0 if ok else 0.0, ok, "" if ok else ("pattern not found" if self.must_match else "pattern found")
        )


@register
class Similarity(Scorer):
    name = "similarity"
    description = "Embedding similarity between the answer and the expected answer (threshold default 0.75)."

    def __init__(self, threshold: float = 0.75, label: str | None = None):
        super().__init__(label)
        self.threshold = threshold

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        if case.expected is None:
            return Score(None, None, "no expected answer")
        a, b = await services.get_embedder().embed([output.answer, case.expected])
        value = max(0.0, cosine(a, b))
        return Score(value, _passed(value, self.threshold), f"similarity {value:.2f}")


def _ranked_ids(output: Output, k: int | None) -> list[str]:
    ids = [c.id for c in output.contexts if c.id is not None]
    return ids[:k] if k else ids


@register
class HitRate(Scorer):
    name = "hit_rate"
    description = "A relevant document is among the first `k` retrieved (hit@k)."

    def __init__(self, k: int = 5, label: str | None = None):
        super().__init__(label)
        self.k = k

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        if not case.relevant:
            return Score(None, None, "no relevant documents listed")
        got = set(_ranked_ids(output, self.k))
        ok = bool(got & set(case.relevant))
        return Score(1.0 if ok else 0.0, ok, "" if ok else f"none of {case.relevant} in the first {self.k}")


@register
class MRR(Scorer):
    name = "mrr"
    description = "1 / rank of the first relevant document (mean over cases = MRR)."

    def __init__(self, k: int = 10, threshold: float = 0.5, label: str | None = None):
        super().__init__(label)
        self.k, self.threshold = k, threshold

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        if not case.relevant:
            return Score(None, None, "no relevant documents listed")
        rr = 0.0
        for rank, cid in enumerate(_ranked_ids(output, self.k), 1):
            if cid in case.relevant:
                rr = 1 / rank
                break
        return Score(rr, _passed(rr, self.threshold), f"reciprocal rank {rr:.2f}")


@register
class Recall(Scorer):
    name = "recall"
    description = "The share of relevant documents found in the first `k` (recall@k)."

    def __init__(self, k: int = 5, threshold: float = 1.0, label: str | None = None):
        super().__init__(label)
        self.k, self.threshold = k, threshold

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        if not case.relevant:
            return Score(None, None, "no relevant documents listed")
        got = set(_ranked_ids(output, self.k))
        value = len(got & set(case.relevant)) / len(set(case.relevant))
        return Score(value, _passed(value, self.threshold), f"found {value:.0%} of the relevant documents")


@register
class Classification(Scorer):
    name = "classification"
    description = "The answer is the expected label. The run summary adds accuracy and macro-F1."

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        if case.expected is None:
            return Score(None, None, "no expected label")
        ok = normalize(output.answer) == normalize(case.expected)
        return Score(
            1.0 if ok else 0.0, ok, "" if ok else f"got '{output.answer.strip()[:40]}', want '{case.expected}'"
        )


@register
class Latency(Scorer):
    name = "latency"
    description = "The answer arrived within `max_ms` (default 3000)."

    def __init__(self, max_ms: float = 3000.0, label: str | None = None):
        super().__init__(label)
        self.max_ms = max_ms

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        ok = output.latency_ms <= self.max_ms
        return Score(1.0 if ok else 0.0, ok, f"{output.latency_ms:.0f} ms (limit {self.max_ms:.0f})")


@register
class Cost(Scorer):
    name = "cost"
    description = "One question cost at most `max_usd`."

    def __init__(self, max_usd: float = 0.01, label: str | None = None):
        super().__init__(label)
        self.max_usd = max_usd

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        ok = output.cost <= self.max_usd
        return Score(1.0 if ok else 0.0, ok, f"${output.cost:.5f} (limit ${self.max_usd})")


REFUSAL = re.compile(
    r"\b(i (do not|don't|dont) know|i (am|'m) not sure|i cannot (find|answer)|i can't (find|answer)|"
    r"no (information|answer|mention)|not (mentioned|provided|found|available|covered|in the (documents?|context|provided))|"
    r"unable to (find|answer)|cannot be determined|insufficient information|there is no)\b",
    re.I,
)


@register
class Abstain(Scorer):
    name = "abstain"
    description = "Says 'I don't know' when the documents have no answer (and does not refuse when they do)."

    def __init__(self, check_over_refusal: bool = True, label: str | None = None):
        super().__init__(label)
        self.check_over_refusal = check_over_refusal

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        refused = bool(REFUSAL.search(output.answer))
        if case.should_abstain:
            return Score(1.0 if refused else 0.0, refused, "" if refused else "should have said it does not know")
        if not self.check_over_refusal:
            return Score(None, None, "")
        return Score(0.0 if refused else 1.0, not refused, "refused a question it could answer" if refused else "")
