"""The few shapes everything else passes around."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TestCase:
    """One question to ask the app, and what a good answer looks like."""

    __test__ = False  # not a pytest class

    id: str
    input: str
    expected: str | None = None  # the right answer (text or a class label)
    relevant: list[str] = field(default_factory=list)  # ids of the documents that hold the answer
    should_abstain: bool = False  # the right answer is "I don't know"
    tags: list[str] = field(default_factory=list)
    paraphrases: list[str] = field(default_factory=list)  # same question in other words
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict, fallback_id: str = "") -> TestCase:
        known = {f for f in cls.__dataclass_fields__}
        extra = {k: v for k, v in d.items() if k not in known}
        case = cls(
            id=str(d.get("id") or fallback_id),
            input=str(d.get("input") if d.get("input") is not None else d.get("question", "")),
            expected=None if d.get("expected") is None else str(d["expected"]),
            relevant=[str(x) for x in d.get("relevant") or []],
            should_abstain=bool(d.get("should_abstain", False)),
            tags=[str(x) for x in d.get("tags") or []],
            paraphrases=[str(x) for x in d.get("paraphrases") or []],
            meta={**(d.get("meta") or {}), **extra},
        )
        if not case.input:
            raise ValueError(f"test case '{case.id}' has no input")
        return case


@dataclass
class Context:
    """A passage the app says it used."""

    text: str
    id: str | None = None
    score: float | None = None


@dataclass
class Output:
    """What the app said back."""

    answer: str = ""
    contexts: list[Context] = field(default_factory=list)
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    error: str | None = None
    raw: Any = None

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "contexts": [asdict(c) for c in self.contexts],
            "latency_ms": round(self.latency_ms, 2),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost": self.cost,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Output:
        return cls(
            answer=d.get("answer", ""),
            contexts=[Context(**c) for c in d.get("contexts", [])],
            latency_ms=d.get("latency_ms", 0.0),
            tokens_in=d.get("tokens_in", 0),
            tokens_out=d.get("tokens_out", 0),
            cost=d.get("cost", 0.0),
            error=d.get("error"),
        )


@dataclass
class Score:
    """One scorer's verdict on one answer."""

    value: float | None  # 0..1, or None when the scorer does not apply to this case
    passed: bool | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {"value": self.value, "passed": self.passed, "detail": self.detail}


@dataclass
class CaseResult:
    case_id: str
    repeat: int
    input: str
    output: Output
    scores: dict[str, Score] = field(default_factory=dict)
    passed: bool = False

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "repeat": self.repeat,
            "input": self.input,
            "output": self.output.to_dict(),
            "scores": {k: v.to_dict() for k, v in self.scores.items()},
            "passed": self.passed,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CaseResult:
        return cls(
            case_id=d["case_id"],
            repeat=d.get("repeat", 0),
            input=d.get("input", ""),
            output=Output.from_dict(d["output"]),
            scores={k: Score(**v) for k, v in d.get("scores", {}).items()},
            passed=d.get("passed", False),
        )
