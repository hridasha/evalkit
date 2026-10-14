"""The `evalkit` command."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import typer

from evalkit import __version__
from evalkit.analysis.calibrate import calibrate_judge, parse_labels
from evalkit.analysis.compare import compare_runs
from evalkit.analysis.consistency import consistency_report
from evalkit.analysis.profile import profile_runs
from evalkit.core import report as html_report
from evalkit.core.config import Config, load_config
from evalkit.core.gate import check_gate
from evalkit.core.runner import run_eval
from evalkit.core.storage import RunNotFoundError, Storage
from evalkit.core.testset import load_testset, save_testset
from evalkit.scorers import REGISTRY

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Plug-in evaluation for AI apps.")
DbOption = typer.Option("evalkit.db", "--db", envvar="EVALKIT_DB", help="The SQLite file where runs are kept.")
ConfigOption = typer.Option("evalkit.toml", "--config", "-c", help="Config file.")


def fail(message: str, code: int = 2) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def _version(show: bool) -> None:
    if show:
        typer.echo(f"evalkit {__version__}")
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(False, "--version", callback=_version, is_eager=True)) -> None:
    """Plug-in evaluation for AI apps."""
    for stream in (sys.stdout, sys.stderr):  # answers can hold any character; never crash on a Windows console
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")


def _load(path: str) -> Config:
    try:
        return load_config(path)
    except (OSError, ValueError) as exc:
        fail(str(exc))
        raise  # unreachable, keeps type checkers happy


def _need_testset(cfg: Config, override: str | None):
    path = override or cfg.testset
    if not path:
        fail("no test set: set `testset = ...` in the config or pass --testset")
    try:
        return load_testset(path)
    except (OSError, ValueError) as exc:
        fail(f"could not read the test set: {exc}")
        raise


def _run_or_exit(store: Storage, run_id: int | None) -> dict:
    try:
        if run_id is None:
            run = store.latest_run()
            if run is None:
                fail("there are no finished runs yet. Run `evalkit run` first.")
            return run
        return store.get_run(run_id)
    except RunNotFoundError:
        fail(f"no run with id {run_id}")
        raise


def _fmt_summary(s: dict) -> str:
    lat = s["latency"]
    lines = [
        f"pass rate  {s['pass_rate']:.1%}  ({s['passed']}/{s['n']})   95% range {s['pass_ci'][0]:.0%}-{s['pass_ci'][1]:.0%}",
        f"errors     {s['errors']}",
        f"latency    p50 {_ms(lat['p50_ms'])}   p95 {_ms(lat['p95_ms'])}",
        f"cost       ${s['cost_total']:.4f} total, ${s['cost_per_case']:.5f} per question",
    ]
    for label, v in s["scorers"].items():
        mean = "-" if v["mean"] is None else f"{v['mean']:.3f}"
        rate = "-" if v["pass_rate"] is None else f"{v['pass_rate']:.0%}"
        lines.append(f"  {label:<16} mean {mean:<7} pass {rate:<5} ({v['n']} scored)")
    if "classification" in s:
        lines.append(
            f"  classification   accuracy {s['classification']['accuracy']:.1%}   macro-F1 {s['classification']['macro_f1']:.3f}"
        )
    return "\n".join(lines)


def _ms(v: float | None) -> str:
    return "-" if v is None else f"{v:,.0f} ms"


@app.command("connect")
def connect_cmd(
    curl: str | None = typer.Option(None, "--curl", help="A curl command (or @file.txt holding one)."),
    sample: str | None = typer.Option(None, "--sample", help="The example question text inside the curl command."),
    preset: str | None = typer.Option(
        None, "--preset", help="openai, anthropic, gemini, ollama, groq, openrouter, rag..."
    ),
    model: str | None = typer.Option(None, "--model"),
    base_url: str | None = typer.Option(None, "--base-url"),
    url: str | None = typer.Option(None, "--url", help="An API that takes a JSON question and replies with JSON."),
    question: str = typer.Option("What is 2 + 2?", "--question", "-q", help="The question to send as a test."),
    write: str | None = typer.Option(
        None, "--write", help="Write a starter config here (only if the file does not exist)."
    ),
) -> None:
    """Connect to any app: paste a curl command or pick a preset, send one test question, get the [target] settings."""
    from evalkit.targets.connect import probe
    from evalkit.targets.curl import parse_curl

    env_vars: list[str] = []
    try:
        if curl:
            text = Path(curl[1:]).read_text(encoding="utf-8") if curl.startswith("@") else curl
            cfg, env_vars = parse_curl(text, sample)
        elif preset:
            cfg = {
                "preset": preset,
                **({"model": model} if model else {}),
                **({"base_url": base_url} if base_url else {}),
            }
        elif url:
            cfg = {"url": url}
        else:
            fail("give one of --curl, --preset or --url")
            return
        result = asyncio.run(probe(cfg, question))
    except (OSError, ValueError) as exc:
        fail(str(exc))
        return
    typer.echo(f"Sent: {question!r}")
    if not result["ok"]:
        typer.secho(f"Failed: {result['error']}", fg=typer.colors.RED)
        for name in env_vars:
            typer.echo(f"  (this request needs the environment variable {name} to be set)")
        raise typer.Exit(1)
    typer.echo(f"Reply in {result['latency_ms']:.0f} ms. EvalKit read this answer:\n  {result['answer'][:300]!r}")
    if result["contexts"]:
        typer.echo(f"  and {len(result['contexts'])} retrieved passage(s)")
    typer.echo("\nPut this in your evalkit.toml:\n")
    typer.echo(result["toml"])
    for name in env_vars:
        typer.echo(f"Set the environment variable {name} before running (the key is never written to the file).")
    if write:
        if Path(write).exists():
            typer.echo(f"{write} already exists, so I left it alone. Copy the block above into it.")
        else:
            starter = (
                'name = "my-app"\ntestset = "questions.jsonl"\n\n'
                + result["toml"]
                + '\n[[scorers]]\nname = "contains"\n\n[gate]\nmin_pass_rate = 0.7\nmax_errors = 0\n'
            )
            Path(write).write_text(starter, encoding="utf-8")
            typer.echo(f"Wrote {write}. Next: create questions.jsonl and run `evalkit run`.")


@app.command("scorers")
def list_scorers() -> None:
    """List the scorers you can use in the config."""
    for name, cls in sorted(REGISTRY.items()):
        typer.echo(f"{name:<15} {cls.description}")


@app.command("run")
def run_cmd(
    config: str = ConfigOption,
    db: str = DbOption,
    testset: str | None = typer.Option(None, "--testset", "-t", help="Override the test set from the config."),
    version: str | None = typer.Option(
        None, "--version-label", "-v", help="A name for this version of your app, e.g. 'prompt-v2'."
    ),
    repeats: int = typer.Option(
        0, "--repeats", "-n", help="Ask every question this many times (0 = use config, default 1)."
    ),
    paraphrases: bool = typer.Option(False, "--paraphrases", help="Also ask each question's paraphrases."),
    baseline: int | None = typer.Option(
        None, "--baseline", "-b", help="Compare with this run id (default: the previous run of this name)."
    ),
    no_baseline: bool = typer.Option(False, "--no-baseline", help="Skip the comparison with an earlier run."),
    gate: bool = typer.Option(True, "--gate/--no-gate", help="Fail (exit 1) when the [gate] checks fail."),
    html: str | None = typer.Option(None, "--html", help="Also write an HTML report to this path."),
    json_out: str | None = typer.Option(None, "--json", help="Also write the summary as JSON to this path."),
    limit: int = typer.Option(0, "--limit", help="Only run the first N questions (for a quick try)."),
) -> None:
    """Ask every question of the test set, score the answers, save the run, and check the quality gate.

    Exit codes: 0 all good, 1 the quality gate failed, 2 something was misconfigured.
    """
    cfg = _load(config)
    cases = _need_testset(cfg, testset)
    if limit:
        cases = cases[:limit]
    try:
        target, scorers, services = cfg.build_target(), cfg.build_scorers(), cfg.build_services(f"{db}.llmcache")
    except (ValueError, TypeError) as exc:
        fail(str(exc))
        return
    ver = version if version is not None else cfg.version
    reps = repeats or int(cfg.run.get("repeats", 1))
    store = Storage(db)

    def progress(_r, done: int, total: int) -> None:
        if sys.stderr.isatty():
            typer.echo(f"\r  {done}/{total}", nl=False, err=True)

    async def go():
        try:
            return await run_eval(
                cases, target, scorers, services, store, name=cfg.name, version=ver, config=cfg.dump(),
                concurrency=int(cfg.run.get("concurrency", 4)), repeats=reps,
                paraphrases=paraphrases or bool(cfg.run.get("paraphrases", False)), on_result=progress,
            )  # fmt: skip
        finally:
            await target.aclose()

    typer.echo(f"Running {len(cases)} question(s) against {cfg.target.get('url') or cfg.target.get('function')} ...")
    try:
        report = asyncio.run(go())
    except KeyboardInterrupt:
        fail("stopped", 130)
        return
    if sys.stderr.isatty():
        typer.echo("", err=True)
    run = store.get_run(report.run_id)
    typer.echo(f"\nRun #{report.run_id}  {cfg.name} {ver}\n{_fmt_summary(report.summary)}")

    comparison = None
    if not no_baseline:
        base = store.get_run(baseline) if baseline else store.latest_run(cfg.name, before=report.run_id)
        if base:
            try:
                comparison = compare_runs(store.get_results(base["id"]), report.results)
                typer.echo(f"\nVersus run #{base['id']} {base['version']}: {comparison['headline']}")
            except ValueError as exc:
                typer.echo(f"\n(no comparison: {exc})")
    gate_result = None
    if cfg.gate:
        try:
            gate_result = check_gate(report.summary, cfg.gate, comparison)
        except ValueError as exc:
            fail(str(exc))
            return
        typer.echo("\nQuality gate: " + ("PASSED" if gate_result.passed else "FAILED"))
        for c in gate_result.checks:
            typer.secho(
                f"  {'ok  ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}",
                fg=None if c["ok"] else typer.colors.RED,
            )
    if html:
        Path(html).write_text(
            html_report.render_run(run, report.results, gate_result.to_dict() if gate_result else None),
            encoding="utf-8",
        )
        typer.echo(f"\nReport written to {html}")
    if json_out:
        Path(json_out).write_text(json.dumps({"run_id": report.run_id, "summary": report.summary,
                                              "gate": gate_result.to_dict() if gate_result else None,
                                              "comparison": comparison}, indent=2), encoding="utf-8")  # fmt: skip
    store.close()
    if gate and gate_result and not gate_result.passed:
        raise typer.Exit(1)


@app.command("runs")
def runs_cmd(db: str = DbOption, name: str | None = typer.Option(None, "--name"), limit: int = 20) -> None:
    """List saved runs."""
    with Storage(db) as store:
        rows = store.list_runs(name, limit)
    if not rows:
        typer.echo("No runs yet.")
        return
    typer.echo(f"{'id':>4}  {'name':<18}{'version':<18}{'pass':>7}{'n':>5}{'p95':>10}{'cost/q':>11}  status")
    for r in rows:
        s = r["summary"]
        typer.echo(f"{r['id']:>4}  {r['name'][:17]:<18}{r['version'][:17]:<18}{s.get('pass_rate', 0):>7.1%}{s.get('n', 0):>5}"
                   f"{_ms((s.get('latency') or {}).get('p95_ms')):>10}{s.get('cost_per_case', 0):>11.5f}  {r['status']}")  # fmt: skip


@app.command("show")
def show_cmd(run_id: int | None = typer.Argument(None), db: str = DbOption, failing: bool = typer.Option(False, "--failing"),
             html: str | None = typer.Option(None, "--html")) -> None:  # fmt: skip
    """Show one run (the latest by default)."""
    with Storage(db) as store:
        run = _run_or_exit(store, run_id)
        results = store.get_results(run["id"])
    typer.echo(f"Run #{run['id']}  {run['name']} {run['version']}\n{_fmt_summary(run['summary'])}")
    if failing:
        for r in results:
            if not r.passed:
                why = r.output.error or "; ".join(f"{k}: {v.detail}" for k, v in r.scores.items() if v.passed is False)
                typer.echo(f"\n[{r.case_id}] {r.input}\n  answer: {r.output.answer[:200]!r}\n  why: {why}")
    if html:
        Path(html).write_text(html_report.render_run(run, results), encoding="utf-8")
        typer.echo(f"\nReport written to {html}")


@app.command("compare")
def compare_cmd(
    a: int, b: int, db: str = DbOption, html: str | None = typer.Option(None, "--html"), alpha: float = 0.05
) -> None:
    """A/B test two runs: is B really better than A?"""
    with Storage(db) as store:
        ra, rb = _run_or_exit(store, a), _run_or_exit(store, b)
        try:
            cmp = compare_runs(store.get_results(a), store.get_results(b), alpha)
        except ValueError as exc:
            fail(str(exc))
            return
    typer.echo(cmp["headline"])
    for k, v in cmp["scorers"].items():
        typer.echo(f"  {k:<16} {v['a']:.3f} -> {v['b']:.3f}  ({v['diff']:+.3f}, p={v['p_value']:.3f})  {v['verdict']}")
    if cmp["regressed"]:
        typer.echo(f"  regressed: {', '.join(cmp['regressed'][:12])}")
    if html:
        Path(html).write_text(html_report.render_compare(cmp, ra, rb), encoding="utf-8")
        typer.echo(f"Report written to {html}")


@app.command("profile")
def profile_cmd(db: str = DbOption, name: str | None = typer.Option(None, "--name"), min_pass_rate: float = typer.Option(0.8, "--min-pass-rate"),
                last: int = typer.Option(10, "--last", help="Use the most recent N runs.")) -> None:  # fmt: skip
    """Cost and speed profile: the cheapest setup that is still good enough."""
    with Storage(db) as store:
        runs = [r for r in store.list_runs(name, last) if r["status"] == "done"]
    try:
        result = profile_runs(runs, min_pass_rate)
    except ValueError as exc:
        fail(str(exc))
        return
    typer.echo(f"{'':2}{'setup':<30}{'pass':>7}{'cost/q':>11}{'p95':>10}  notes")
    for r in result["rows"]:
        notes = ", ".join(n for n, on in (("good enough", r["good_enough"]), ("pareto", r["pareto"])) if on)
        typer.echo(
            f"{'->' if r['recommended'] else '  '}{r['label'][:29]:<30}{r['pass_rate']:>7.1%}{r['cost_per_case']:>11.5f}{_ms(r['p95_ms']):>10}  {notes}"
        )
    typer.echo("\n" + result["recommendation"])


@app.command("consistency")
def consistency_cmd(run_id: int | None = typer.Argument(None), db: str = DbOption) -> None:
    """How much do answers change between repeats / paraphrases? (Run with --repeats or --paraphrases first.)"""
    with Storage(db) as store:
        run = _run_or_exit(store, run_id)
        rep = asyncio.run(consistency_report(store.get_results(run["id"])))
    if not rep["questions"]:
        fail("this run has no repeated or paraphrased questions. Use `evalkit run --repeats 3` or `--paraphrases`.", 1)
    typer.echo(f"{rep['questions']} questions asked more than once: agreement {rep['mean_agreement']:.0%}, "
               f"similarity {rep['mean_similarity']:.2f}, flaky (pass and fail) {rep['flaky']}")  # fmt: skip
    for row in rep["rows"][:10]:
        mark = "UNSTABLE" if row["unstable"] else "ok"
        typer.echo(
            f"  {mark:<9}{row['case_id']:<14} similarity {row['similarity']:.2f}  agreement {row['agreement']:.0%}  {row['distinct']} different answers"
        )


@app.command("generate")
def generate_cmd(
    docs: str = typer.Argument(..., help="Folder of .txt / .md documents."),
    out: str = typer.Option("testset.jsonl", "--out", "-o"),
    config: str = ConfigOption,
    db: str = DbOption,
    per_doc: int = typer.Option(3, "--per-doc"),
    multi: int = typer.Option(0, "--multi-doc", help="How many questions that need two documents."),
    unanswerable: int = typer.Option(
        1, "--unanswerable", help="Questions per document that the document does NOT answer."
    ),
    paraphrases: int = typer.Option(0, "--paraphrases", help="Rewrites per question (for the consistency test)."),
    redteam: bool = typer.Option(False, "--redteam", help="Also add the built-in tricky questions."),
    max_docs: int = typer.Option(0, "--max-docs"),
) -> None:
    """Make a test set from your documents using the LLM in your config (needs GEMINI_API_KEY)."""
    from evalkit.gen import generate_testset, redteam_cases
    from evalkit.retrieval.index import load_documents

    cfg = _load(config)
    try:
        services = cfg.build_services(f"{db}.llmcache")
        llm = services.need_llm("generate")
        documents = load_documents(docs)
        cases = asyncio.run(generate_testset(documents, llm, per_doc=per_doc, multi_doc=multi, unanswerable_per_doc=unanswerable,
                                             paraphrases=paraphrases, max_docs=max_docs or None))  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - show a friendly line instead of a traceback
        fail(f"{type(exc).__name__}: {exc}")
        return
    if redteam:
        cases += redteam_cases()
    save_testset(out, cases)
    typer.echo(f"Wrote {len(cases)} questions to {out}  (review them: generated answers can be wrong!)")


@app.command("redteam")
def redteam_cmd(out: str = typer.Option("redteam.jsonl", "--out", "-o")) -> None:
    """Write the built-in red-team test set (tricky questions an app should decline)."""
    from evalkit.gen import redteam_cases

    cases = redteam_cases()
    save_testset(out, cases)
    typer.echo(f"Wrote {len(cases)} tricky questions to {out}. Score them with the 'abstain' scorer.")


@app.command("sweep")
def sweep_cmd(
    docs: str = typer.Argument(..., help="Folder of documents."),
    testset: str = typer.Option(..., "--testset", "-t", help="Test set with `relevant` document ids."),
    methods: str = typer.Option("bm25,embed,hybrid", "--methods"),
    chunk_sizes: str = typer.Option("200,400,800", "--chunk-sizes"),
    k: int = typer.Option(4, "--k"),
    gemini: bool = typer.Option(False, "--gemini", help="Also try Gemini embeddings (needs GEMINI_API_KEY)."),
) -> None:
    """Retrieval scorecard: which chunk size and search method finds the right document most often?"""
    from evalkit.llm import GeminiEmbedder, HashEmbedder
    from evalkit.retrieval.index import load_documents
    from evalkit.retrieval.sweep import sweep

    try:
        embedders = {"hash": HashEmbedder(), **({"gemini": GeminiEmbedder()} if gemini else {})}
        rows = asyncio.run(sweep(load_documents(docs), load_testset(testset), methods=[m.strip() for m in methods.split(",")],
                                 chunk_sizes=[int(c) for c in chunk_sizes.split(",")], k=k, embedders=embedders))  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        fail(f"{type(exc).__name__}: {exc}")
        return
    typer.echo(f"{'method':<8}{'embedder':<9}{'chunk':>6}{'chunks':>7}{'hit@k':>8}{'MRR':>7}{'recall':>8}")
    for r in rows:
        typer.echo(
            f"{r.method:<8}{r.embedder:<9}{r.chunk_size:>6}{r.chunks:>7}{r.hit_rate:>8.0%}{r.mrr:>7.3f}{r.recall:>8.0%}"
        )
    best = rows[0]
    typer.echo(
        f"\nBest on {best.questions} questions: {best.method} search, chunks of {best.chunk_size} characters (MRR {best.mrr:.3f})."
    )


@app.command("calibrate")
def calibrate_cmd(labels: str = typer.Argument(..., help="CSV: input, answer, expected, human_pass"), config: str = ConfigOption, db: str = DbOption,
                  scorer: str = typer.Option("judge", "--scorer", help="Label of the judge scorer to check.")) -> None:  # fmt: skip
    """Check the LLM judge against a human's pass/fail labels (Cohen's kappa)."""
    cfg = _load(config)
    try:
        judge = next(s for s in cfg.build_scorers() if s.label == scorer)
        result = asyncio.run(
            calibrate_judge(
                parse_labels(Path(labels).read_text(encoding="utf-8-sig")), judge, cfg.build_services(f"{db}.llmcache")
            )
        )
    except StopIteration:
        fail(f"the config has no scorer labelled '{scorer}'")
        return
    except Exception as exc:  # noqa: BLE001
        fail(f"{type(exc).__name__}: {exc}")
        return
    lo, hi = result["agreement_ci"]
    typer.echo(
        f"Judge agrees with the human on {result['agreement']:.0%} of {result['n']} answers (95% range {lo:.0%}-{hi:.0%})."
    )
    typer.echo(f"Cohen's kappa {result['kappa']:.2f}: {result['verdict']}. "
               f"Too generous {result['judge_too_generous']}x, too harsh {result['judge_too_harsh']}x.")  # fmt: skip


@app.command("import-testset")
def import_testset_cmd(file: str = typer.Argument(..., help="A .jsonl or .csv test set."), name: str | None = typer.Option(None, "--name"),
                       db: str = DbOption) -> None:  # fmt: skip
    """Save a test set file in the database, so the dashboard can use it."""
    try:
        cases = load_testset(file)
    except (OSError, ValueError) as exc:
        fail(f"could not read the test set: {exc}")
        return
    with Storage(db) as store:
        store.save_testset(name or Path(file).stem, cases)
    typer.echo(f"Saved '{name or Path(file).stem}' ({len(cases)} questions) in {db}")


@app.command("testsets")
def testsets_cmd(db: str = DbOption) -> None:
    """List test sets saved in the database."""
    with Storage(db) as store:
        rows = store.list_testsets()
    for r in rows or []:
        typer.echo(f"{r['name']:<24}{r['count']} questions")
    if not rows:
        typer.echo("No saved test sets (test sets can also just be files).")


@app.command("delete")
def delete_cmd(run_id: int, db: str = DbOption, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Delete a run."""
    if not yes and not typer.confirm(f"Delete run #{run_id}?"):
        raise typer.Abort()
    with Storage(db) as store:
        try:
            store.delete_run(run_id)
        except RunNotFoundError:
            fail(f"no run with id {run_id}")
    typer.echo("Deleted.")


@app.command("snapshot")
def snapshot_cmd(
    source: str = typer.Argument(..., help="A SQLite file, a folder, or a postgresql:// URL."),
    copy: str = typer.Argument(..., help="Where to put the copy (a path, or a new database name for Postgres)."),
    docker_container: str | None = typer.Option(None, "--docker-container", help="Run pg tools inside this container."),
) -> None:
    """Make a read-only copy of an app's data, so evaluations never touch the real thing."""
    from evalkit.core.snapshot import SnapshotError, snapshot

    try:
        info = snapshot(source, copy, docker_container)
    except SnapshotError as exc:
        fail(str(exc))
    typer.echo(f"Copied {info['kind']} data ({info['bytes']:,} bytes). The original was only read, never changed.")
    typer.echo(f"Point the app under test at: {info['copy']}")


@app.command("ui")
def ui_cmd(port: int = typer.Option(8800, "--port", "-p"), host: str = typer.Option("127.0.0.1", "--host"),
           db: str = DbOption, open_browser: bool = typer.Option(True, "--open/--no-open")) -> None:  # fmt: skip
    """Open the dashboard."""
    import threading
    import webbrowser

    import uvicorn

    from evalkit.api.app import create_api_app, find_web_dir

    web = find_web_dir()
    if web is None:
        typer.echo("The dashboard is not built yet (run `npm install && npm run build` in web/). The API still works.")
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
    typer.echo(f"EvalKit dashboard: {url}   (runs: {db})")
    if open_browser and web:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_api_app(db, web_dir=web), host=host, port=port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    app(prog_name=os.path.basename(__file__))
