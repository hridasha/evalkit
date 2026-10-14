"""Scorers that ask a language model: an overall judge and a claim-by-claim hallucination check."""

from __future__ import annotations

import json
import re

from evalkit.core.models import Output, Score, TestCase
from evalkit.llm.base import tokenize
from evalkit.scorers.base import Scorer, Services, register
from evalkit.scorers.rules import REFUSAL

JUDGE_SYSTEM = (
    "You are a strict, fair grader of answers from an AI app. Grade only what is written. "
    "Reply with JSON only, no other text."
)

CRITERIA = {
    "correctness": "Does the answer agree with the reference answer on every important fact? (Different wording is fine.)",
    "completeness": "Does the answer cover everything the reference answer covers?",
    "faithfulness": "Is every claim in the answer supported by the provided context? Unsupported claims lower the score.",
    "relevance": "Does the answer address the question that was asked, without padding?",
}


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in prose or code fences; take the first {...} block."""
    text = text.strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"the judge did not return JSON: {text[:120]!r}") from None
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("the judge did not return a JSON object")
    return data


def format_contexts(output: Output, limit: int = 6000) -> str:
    text = "\n\n".join(f"[{n}] {c.text}" for n, c in enumerate(output.contexts, 1))
    return text[:limit] or "(no context was given)"


@register
class Judge(Scorer):
    name = "judge"
    description = "An LLM grades the answer 1-5 on `criteria` (correctness, completeness, faithfulness, relevance)."

    def __init__(self, criteria: list[str] | None = None, threshold: float = 0.7, label: str | None = None):
        super().__init__(label)
        self.criteria = criteria or ["correctness"]
        unknown = [c for c in self.criteria if c not in CRITERIA]
        if unknown:
            raise ValueError(f"unknown judge criteria {unknown}; choose from {sorted(CRITERIA)}")
        self.threshold = threshold

    def prompt(self, case: TestCase, output: Output) -> str:
        rubric = "\n".join(f'- "{c}": {CRITERIA[c]}' for c in self.criteria)
        parts = [f"Question:\n{case.input}", f"Answer to grade:\n{output.answer}"]
        if case.expected:
            parts.append(f"Reference answer:\n{case.expected}")
        if "faithfulness" in self.criteria:
            parts.append(f"Context the app was given:\n{format_contexts(output)}")
        keys = ", ".join(f'"{c}": <1-5>' for c in self.criteria)
        return "\n\n".join(parts) + (
            f"\n\nGrade each criterion from 1 (bad) to 5 (perfect):\n{rubric}\n\n"
            f'Reply as JSON: {{{keys}, "reason": "<one short sentence>"}}'
        )

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        llm = services.need_llm(self.name)
        if "correctness" in self.criteria and not case.expected:
            return Score(None, None, "no reference answer to judge against")
        data = parse_json(await llm.complete(self.prompt(case, output), system=JUDGE_SYSTEM, json_mode=True))
        grades = []
        for c in self.criteria:
            g = float(data.get(c, 0))
            if not 1 <= g <= 5:
                raise ValueError(f"the judge gave {c}={g}, outside 1-5")
            grades.append(g)
        value = (sum(grades) / len(grades) - 1) / 4  # 1..5 -> 0..1
        return Score(value, value >= self.threshold, str(data.get("reason", ""))[:300])


CLAIMS_PROMPT = """Check an AI answer against the context it was given.

Context:
{context}

Answer:
{answer}

1. Split the answer into separate factual claims.
2. For each claim say whether the context supports it ("supported") or not ("unsupported").
   A claim from general knowledge that the context does not state counts as "unsupported".

Reply as JSON: {{"claims": [{{"claim": "<text>", "verdict": "supported" | "unsupported"}}]}}"""


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if len(tokenize(p)) >= 3]


STOP = set(
    [
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "for",
        "with",
        "as",
        "by",
        "at",
        "it",
        "this",
        "that",
        "from",
    ]
)


def lexical_support(sentence: str, context: str, threshold: float = 0.6) -> bool:
    """Offline fallback: a sentence counts as supported when most of its content words appear in the context."""
    words = [w for w in tokenize(sentence) if w not in STOP]
    if not words:
        return True
    have = set(tokenize(context))
    return sum(1 for w in words if w in have) / len(words) >= threshold


@register
class Faithfulness(Scorer):
    name = "faithfulness"
    description = "Share of the answer's claims supported by the retrieved text. mode = 'llm' (default) or 'lexical'."

    def __init__(self, mode: str = "llm", threshold: float = 0.8, label: str | None = None):
        super().__init__(label)
        if mode not in ("llm", "lexical"):
            raise ValueError("mode must be 'llm' or 'lexical'")
        self.mode, self.threshold = mode, threshold

    async def score(self, case: TestCase, output: Output, services: Services) -> Score:
        if not output.contexts:
            return Score(None, None, "the app returned no context to check against")
        if case.should_abstain or REFUSAL.search(output.answer):
            return Score(None, None, "the answer declines to answer, so there is nothing to check")
        if self.mode == "lexical":
            context = " ".join(c.text for c in output.contexts)
            claims = [(s, lexical_support(s, context)) for s in sentences(output.answer)]
        else:
            llm = services.need_llm(self.name)
            data = parse_json(
                await llm.complete(
                    CLAIMS_PROMPT.format(context=format_contexts(output), answer=output.answer),
                    system=JUDGE_SYSTEM,
                    json_mode=True,
                )
            )
            claims = [(str(c.get("claim", "")), str(c.get("verdict", "")).lower() == "supported")
                      for c in data.get("claims", []) if isinstance(c, dict)]  # fmt: skip
        if not claims:
            return Score(None, None, "no claims found in the answer")
        value = sum(1 for _, ok in claims if ok) / len(claims)
        bad = [c for c, ok in claims if not ok]
        detail = f"{len(claims) - len(bad)}/{len(claims)} claims supported" + (
            f"; unsupported: {bad[0][:120]}" if bad else ""
        )
        return Score(value, value >= self.threshold, detail)
