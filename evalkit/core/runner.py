"""Ask every question, score every answer, save the run."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field

from evalkit.core.models import CaseResult, Output, Score, TestCase
from evalkit.core.storage import Storage
from evalkit.core.summary import summarize
from evalkit.scorers.base import Scorer, Services
from evalkit.targets import Target


@dataclass
class RunReport:
    run_id: int | None
    name: str
    results: list[CaseResult] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    status: str = "done"


async def score_output(case: TestCase, output: Output, scorers: list[Scorer], services: Services) -> dict[str, Score]:
    scores: dict[str, Score] = {}
    if output.error:
        return scores
    for s in scorers:
        try:
            scores[s.label] = await s.score(case, output, services)
        except Exception as exc:  # noqa: BLE001 - one broken scorer must not stop the run
            scores[s.label] = Score(None, None, f"scorer error: {type(exc).__name__}: {exc}")
    return scores


def case_passed(output: Output, scores: dict[str, Score]) -> bool:
    return not output.error and not any(s.passed is False for s in scores.values())


def expand_cases(cases: list[TestCase], paraphrases: bool) -> list[TestCase]:
    """Optionally add each paraphrase of a question as its own case (id 'q1~p1') to test consistency."""
    if not paraphrases:
        return list(cases)
    out: list[TestCase] = []
    for c in cases:
        out.append(c)
        for n, text in enumerate(c.paraphrases, 1):
            out.append(
                dataclasses.replace(c, id=f"{c.id}~p{n}", input=text, paraphrases=[], tags=[*c.tags, "paraphrase"])
            )
    return out


async def run_eval(
    cases: list[TestCase],
    target: Target,
    scorers: list[Scorer],
    services: Services,
    storage: Storage | None = None,
    *,
    name: str = "eval",
    version: str = "",
    config: dict | None = None,
    concurrency: int = 4,
    repeats: int = 1,
    paraphrases: bool = False,
    on_result: Callable[[CaseResult, int, int], None] | None = None,
) -> RunReport:
    todo = expand_cases(cases, paraphrases)
    by_id = {c.id.split("~")[0]: c for c in cases}
    run_id = storage.create_run(name, version, config) if storage else None
    results: list[CaseResult] = []
    total = len(todo) * repeats
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(case: TestCase, repeat: int) -> CaseResult:
        async with sem:
            output = await target.ask(case.input)
            scores = await score_output(case, output, scorers, services)
        result = CaseResult(case.id, repeat, case.input, output, scores, case_passed(output, scores))
        if storage and run_id is not None:
            storage.add_result(run_id, result)
        results.append(result)
        if on_result:
            on_result(result, len(results), total)
        return result

    try:
        await asyncio.gather(*(one(c, r) for c in todo for r in range(repeats)))
    except asyncio.CancelledError:
        if storage and run_id is not None:
            storage.finish_run(run_id, summarize(results, by_id), "cancelled")
        raise
    results.sort(key=lambda r: (r.case_id, r.repeat))
    summary = summarize(results, by_id)
    if services.llm is not None:
        summary["judge_usage"] = dataclasses.asdict(services.llm.usage)
    if storage and run_id is not None:
        storage.finish_run(run_id, summary)
    return RunReport(run_id, name, results, summary)
