"""The web API behind the dashboard."""

from __future__ import annotations

import asyncio
import json
import os
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from evalkit import __version__
from evalkit.analysis.compare import compare_runs
from evalkit.analysis.consistency import consistency_report
from evalkit.analysis.profile import profile_runs
from evalkit.api.jobs import JobManager
from evalkit.core import report as html_report
from evalkit.core.config import parse_config
from evalkit.core.gate import check_gate
from evalkit.core.storage import RunNotFoundError, Storage
from evalkit.core.testset import parse_testset
from evalkit.scorers import REGISTRY
from evalkit.targets.connect import probe
from evalkit.targets.curl import parse_curl
from evalkit.targets.presets import catalog


def find_web_dir() -> Path | None:
    candidates = [Path(os.environ["EVALKIT_WEB_DIR"])] if os.getenv("EVALKIT_WEB_DIR") else []
    here = Path(__file__).resolve()
    candidates += [here.parent.parent / "web_dist", here.parents[2] / "web" / "dist"]
    return next((c for c in candidates if (c / "index.html").is_file()), None)


class ConnectRequest(BaseModel):
    curl: str | None = Field(None, description="a curl command copied from docs, Postman or DevTools")
    sample: str | None = Field(None, description="the example question text inside that curl command")
    target: dict | None = Field(None, description="a [target] config (may use preset = ...)")
    question: str = "What is 2 + 2?"
    secrets: dict[str, str] = Field(
        default_factory=dict, description="values for ${ENV} names, used for this test only"
    )


class StartRun(BaseModel):
    config: str = Field(..., description="The evalkit.toml text")
    testset: str = Field(..., description="Name of a saved test set")
    version: str | None = None
    repeats: int = Field(1, ge=1, le=50)
    paraphrases: bool = False
    limit: int = Field(0, ge=0)


def create_api_app(db_path: str | Path = "evalkit.db", web_dir: Path | None = None) -> FastAPI:
    db = str(db_path)
    store = Storage(db)
    jobs = JobManager(store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await jobs.cancel_all()
        store.close()

    app = FastAPI(title="EvalKit", version=__version__, lifespan=lifespan)
    app.state.store, app.state.jobs = store, jobs
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5190", "http://127.0.0.1:5190"],  # the Vite dev server
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def run_or_404(run_id: int) -> dict:
        try:
            return store.get_run(run_id)
        except RunNotFoundError:
            raise HTTPException(404, f"no run with id {run_id}") from None

    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "version": __version__,
            "db": db,
            "demo_url": os.getenv("EVALKIT_DEMO_URL"),
            "llm_ready": bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
        }

    @app.get("/api/scorers")
    def scorers():
        return [{"name": n, "description": c.description} for n, c in sorted(REGISTRY.items())]

    # ---------------------------------------------------------------------------------- connect an app
    @app.get("/api/presets")
    def presets():
        return catalog()

    @app.post("/api/connect")
    async def connect(body: ConnectRequest):
        """Send one real question to an app and report what came back, plus the settings that work."""
        env_vars: list[str] = []
        try:
            if body.curl:
                cfg, env_vars = parse_curl(body.curl, body.sample)
            elif body.target:
                cfg = dict(body.target)
            else:
                raise ValueError("send either a curl command or a target")
            if cfg.pop("transport", None) is not None or cfg.get("type") == "python":
                raise ValueError("only http/web targets can be tested from the dashboard")
            saved = {k: os.environ.get(k) for k in body.secrets}
            os.environ.update({k: v for k, v in body.secrets.items() if k.replace("_", "").isalnum()})
            try:
                result = await probe(cfg, body.question)
            finally:  # the keys typed for this test are never kept
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        result["env_vars"] = [{"name": n, "set": bool(os.getenv(n))} for n in env_vars]
        return result

    # ------------------------------------------------------------------------------------------ runs
    @app.get("/api/runs")
    def list_runs(name: str | None = None, limit: int = 100):
        return [{**r, "config": None} for r in store.list_runs(name, min(limit, 500))]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: int):
        run = run_or_404(run_id)
        return {**run, "results": [r.to_dict() for r in store.get_results(run_id)]}

    @app.delete("/api/runs/{run_id}", status_code=204)
    def delete_run(run_id: int):
        run_or_404(run_id)
        store.delete_run(run_id)

    @app.get("/api/runs/{run_id}/report.html", response_class=HTMLResponse)
    def run_report(run_id: int):
        run = run_or_404(run_id)
        gate = None
        cfg_gate = (run["config"] or {}).get("gate")
        if cfg_gate:
            gate = check_gate(run["summary"], cfg_gate).to_dict()
        return html_report.render_run(run, store.get_results(run_id), gate)

    @app.get("/api/compare")
    def compare(a: int, b: int, alpha: float = 0.05):
        run_or_404(a)
        run_or_404(b)
        try:
            return compare_runs(store.get_results(a), store.get_results(b), alpha)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/compare/report.html", response_class=HTMLResponse)
    def compare_report(a: int, b: int):
        ra, rb = run_or_404(a), run_or_404(b)
        try:
            return html_report.render_compare(compare_runs(store.get_results(a), store.get_results(b)), ra, rb)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/profile")
    def profile(name: str | None = None, min_pass_rate: float = 0.8, last: int = 10):
        runs = [r for r in store.list_runs(name, min(last, 100)) if r["status"] == "done"]
        try:
            return profile_runs(runs, min_pass_rate)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/runs/{run_id}/consistency")
    async def consistency(run_id: int):
        run_or_404(run_id)
        return await consistency_report(store.get_results(run_id))

    # ------------------------------------------------------------------------------------- test sets
    @app.get("/api/testsets")
    def list_testsets():
        return store.list_testsets()

    @app.get("/api/testsets/{name}")
    def get_testset(name: str):
        try:
            return [c.to_dict() for c in store.get_testset(name)]
        except KeyError:
            raise HTTPException(404, f"no test set called '{name}'") from None

    @app.post("/api/testsets", status_code=201)
    async def upload_testset(file: UploadFile = File(...), name: str | None = Form(None)):
        fmt = "csv" if (file.filename or "").lower().endswith(".csv") else "jsonl"
        try:
            cases = parse_testset((await file.read()).decode("utf-8-sig"), fmt)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, str(exc)) from None
        tname = (name or Path(file.filename or "testset").stem).strip() or "testset"
        store.save_testset(tname, cases)
        return {"name": tname, "count": len(cases)}

    @app.delete("/api/testsets/{name}", status_code=204)
    def delete_testset(name: str):
        store.delete_testset(name)

    # ------------------------------------------------------------------------------------------ jobs
    @app.post("/api/jobs", status_code=201)
    async def start_job(body: StartRun):
        try:
            cfg = parse_config(tomllib.loads(body.config))
            if cfg.target.get("type") == "python" and os.getenv("EVALKIT_ALLOW_PYTHON_TARGET") != "1":
                raise ValueError("python targets are disabled in the dashboard (they run code). Use an http target.")
            cases = store.get_testset(body.testset)
            if body.limit:
                cases = cases[: body.limit]
            job = jobs.start(cfg, cases, version=body.version, repeats=body.repeats, paraphrases=body.paraphrases)
        except KeyError:
            raise HTTPException(404, f"no test set called '{body.testset}'") from None
        except tomllib.TOMLDecodeError as exc:
            raise HTTPException(400, f"the config is not valid TOML: {exc}") from None
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from None
        return job.snapshot()

    @app.get("/api/jobs")
    def list_jobs():
        return [j.snapshot() for j in sorted(jobs.jobs.values(), key=lambda j: -j.started)]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in jobs.jobs:
            raise HTTPException(404, "no such job")
        return jobs.jobs[job_id].snapshot()

    @app.delete("/api/jobs/{job_id}", status_code=204)
    def cancel_job(job_id: str):
        if job_id not in jobs.jobs:
            raise HTTPException(404, "no such job")
        jobs.cancel(job_id)

    @app.websocket("/ws/jobs/{job_id}")
    async def job_socket(ws: WebSocket, job_id: str):
        await ws.accept()
        job = jobs.jobs.get(job_id)
        if job is None:
            await ws.close(code=4404)
            return
        last = None
        try:
            while True:
                payload = json.dumps(job.snapshot())
                if payload != last:
                    await ws.send_text(payload)
                    last = payload
                if job.status != "running":
                    break
                await asyncio.sleep(0.4)
            await ws.close()
        except WebSocketDisconnect:
            return

    # ------------------------------------------------------------------------------------- the page
    web = web_dir or find_web_dir()
    app.state.web_dir = web
    if web:
        if (web / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "ws/")):
                raise HTTPException(404)
            target = (web / path).resolve()
            if path and target.is_file() and web.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(web / "index.html")

    return app
