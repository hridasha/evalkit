"""Evaluation runs started from the dashboard. They run in the background and report progress."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field

from evalkit.analysis.compare import compare_runs
from evalkit.core.config import Config
from evalkit.core.gate import check_gate
from evalkit.core.models import TestCase
from evalkit.core.runner import run_eval
from evalkit.core.storage import Storage


@dataclass
class Job:
    id: str
    name: str
    version: str
    total: int
    status: str = "running"  # running | done | failed | cancelled
    started: float = field(default_factory=time.time)
    finished: float | None = None
    done: int = 0
    passed: int = 0
    run_id: int | None = None
    result: dict | None = None
    error: str | None = None
    task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id, "name": self.name, "version": self.version, "status": self.status, "started": self.started,
            "finished": self.finished, "done": self.done, "total": self.total, "passed": self.passed, "run_id": self.run_id,
            "result": self.result, "error": self.error,
        }  # fmt: skip


class JobManager:
    def __init__(self, store: Storage):
        self.store = store
        self.jobs: dict[str, Job] = {}

    def start(
        self,
        cfg: Config,
        cases: list[TestCase],
        *,
        version: str | None = None,
        repeats: int = 1,
        paraphrases: bool = False,
    ) -> Job:
        target, scorers, services = (
            cfg.build_target(),
            cfg.build_scorers(),
            cfg.build_services(f"{self.store.path}.llmcache"),
        )  # raises ValueError on bad config
        total = (
            len(cases)
            * repeats
            * (1 + (sum(len(c.paraphrases) for c in cases) / max(1, len(cases)) if paraphrases else 0))
        )
        job = Job(uuid.uuid4().hex[:10], cfg.name, version if version is not None else cfg.version, int(total))

        def progress(result, done: int, total_: int) -> None:
            job.done, job.total = done, total_
            job.passed += 1 if result.passed else 0

        async def go() -> None:
            try:
                report = await run_eval(
                    cases, target, scorers, services, self.store, name=job.name, version=job.version, config=cfg.dump(),
                    concurrency=int(cfg.run.get("concurrency", 4)), repeats=repeats, paraphrases=paraphrases, on_result=progress,
                )  # fmt: skip
                job.run_id = report.run_id
                base = self.store.latest_run(job.name, before=report.run_id)
                comparison = compare_runs(self.store.get_results(base["id"]), report.results) if base else None
                if comparison:
                    comparison["baseline_run_id"] = base["id"]
                gate = check_gate(report.summary, cfg.gate, comparison).to_dict() if cfg.gate else None
                job.result = {"summary": report.summary, "gate": gate, "comparison": comparison}
                job.status = "done"
            except asyncio.CancelledError:
                job.status = "cancelled"
                raise
            except Exception as exc:  # noqa: BLE001 - never take the server down
                job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            finally:
                job.finished = time.time()
                await target.aclose()

        job.task = asyncio.create_task(go())
        self.jobs[job.id] = job
        return job

    def cancel(self, job_id: str) -> None:
        job = self.jobs[job_id]
        if job.task and not job.task.done():
            job.task.cancel()

    async def cancel_all(self) -> None:
        for j in self.jobs.values():
            if j.task and not j.task.done():
                j.task.cancel()
        await asyncio.gather(*(j.task for j in self.jobs.values() if j.task), return_exceptions=True)
