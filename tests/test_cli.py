import json

import pytest
from typer.testing import CliRunner

from evalkit.cli import app

runner = CliRunner()

TESTSET = "\n".join(json.dumps(c) for c in [
    {"id": "a", "input": "What is the capital of France?", "expected": "Paris", "relevant": ["geo"]},
    {"id": "b", "input": "What is the capital of Peru?", "expected": "Lima", "relevant": ["geo"]},
    {"id": "c", "input": "What is the capital of Japan?", "expected": "Tokyo", "relevant": ["geo"]},
    {"id": "d", "input": "Who is the king of Mars?", "should_abstain": True},
])  # fmt: skip


def config(gate="", target_fn="tests.cli_app:answer"):
    return f"""
name = "geo"
testset = "set.jsonl"
[target]
type = "python"
function = "{target_fn}"
[[scorers]]
name = "contains"
[[scorers]]
name = "hit_rate"
[[scorers]]
name = "abstain"
{gate}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "set.jsonl").write_text(TESTSET)
    (tmp_path / "evalkit.toml").write_text(config())
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(__import__("pathlib").Path(__file__).resolve().parent.parent))
    return tmp_path


def run(*args):
    return runner.invoke(app, ["run", "--db", "ek.db", *args])


def test_run_prints_a_summary_and_saves_the_run(project):
    r = run()
    assert r.exit_code == 0, r.output
    assert "pass rate  100.0%  (4/4)" in r.output and "hit_rate" in r.output
    listing = runner.invoke(app, ["runs", "--db", "ek.db"]).output
    assert "geo" in listing and "100.0%" in listing


def test_gate_failure_exits_with_1_and_no_gate_ignores_it(project):
    (project / "evalkit.toml").write_text(config("[gate]\nmin_pass_rate = 1.0\n", "tests.cli_app:answer"))
    # break the app: the expected answer is wrong for one question
    (project / "set.jsonl").write_text(TESTSET.replace('"Lima"', '"Cusco"'))
    bad = run()
    assert bad.exit_code == 1 and "Quality gate: FAILED" in bad.output and "FAIL pass rate" in bad.output
    assert run("--no-gate").exit_code == 0


def test_second_run_shows_comparison_and_regression_gate(project):
    (project / "evalkit.toml").write_text(config("[gate]\nmax_regression = 0.01\nrequire_significant = false\n"))
    assert run("-v", "v1").exit_code == 0
    (project / "set.jsonl").write_text(TESTSET.replace('"Tokyo"', '"Kyoto"').replace('"Lima"', '"Cusco"'))
    r = run("-v", "v2")
    assert "Versus run #1 v1" in r.output and r.exit_code == 1 and "regression vs baseline" in r.output
    assert "no comparison" not in run("-v", "v3", "--no-baseline").output


def test_html_json_and_show_outputs(project):
    r = run("--html", "r.html", "--json", "r.json")
    assert r.exit_code == 0 and "<html" in (project / "r.html").read_text()
    data = json.loads((project / "r.json").read_text())
    assert data["summary"]["n"] == 4 and data["run_id"] == 1
    shown = runner.invoke(app, ["show", "--db", "ek.db", "--failing"])
    assert shown.exit_code == 0 and "Run #1" in shown.output


def test_compare_profile_consistency_commands(project):
    run("-v", "a")
    run("-v", "b", "--repeats", "2")
    c = runner.invoke(app, ["compare", "1", "2", "--db", "ek.db", "--html", "c.html"])
    assert c.exit_code == 0 and "Pass rate" in c.output and (project / "c.html").exists()
    p = runner.invoke(app, ["profile", "--db", "ek.db"])
    assert p.exit_code == 0 and "Cheapest setup" in p.output
    ok = runner.invoke(app, ["consistency", "2", "--db", "ek.db"])
    assert ok.exit_code == 0 and "asked more than once" in ok.output
    none = runner.invoke(app, ["consistency", "1", "--db", "ek.db"])
    assert none.exit_code == 1 and "no repeated" in none.output


def test_misconfiguration_exits_with_2_and_a_clear_message(project):
    assert runner.invoke(app, ["run", "--config", "nope.toml"]).exit_code == 2
    (project / "evalkit.toml").write_text('name = "x"\ntestset = "set.jsonl"\n[target]\ntype = "ftp"\n')
    r = run()
    assert r.exit_code == 2 and "unknown target type" in r.output
    (project / "evalkit.toml").write_text(config("[gate]\nmin_pas_rate = 1\n"))
    assert run().exit_code == 2
    (project / "evalkit.toml").write_text(config())
    assert runner.invoke(app, ["show", "42", "--db", "ek.db"]).exit_code == 2
    (project / "set.jsonl").write_text("not json")
    assert "could not read the test set" in run().output


def test_limit_scorers_redteam_and_delete(project):
    assert "pass rate" in run("--limit", "2").output
    assert "contains" in runner.invoke(app, ["scorers"]).output
    out = runner.invoke(app, ["redteam", "--out", "red.jsonl"])
    assert out.exit_code == 0 and (project / "red.jsonl").read_text().count("\n") >= 10
    assert runner.invoke(app, ["delete", "1", "--db", "ek.db", "-y"]).exit_code == 0
    assert runner.invoke(app, ["delete", "1", "--db", "ek.db", "-y"]).exit_code == 2


def test_sweep_command_on_the_demo_documents():
    from pathlib import Path

    demo = Path(__file__).resolve().parent.parent / "examples" / "demo_rag"
    r = runner.invoke(
        app,
        [
            "sweep",
            str(demo / "docs"),
            "-t",
            str(demo / "testset.jsonl"),
            "--methods",
            "bm25,hybrid",
            "--chunk-sizes",
            "200,400",
        ],
    )
    assert r.exit_code == 0 and "Best on" in r.output and "hybrid" in r.output


def test_import_testset_saves_it_for_the_dashboard(project):
    r = runner.invoke(app, ["import-testset", "set.jsonl", "--name", "geo", "--db", "ek.db"])
    assert r.exit_code == 0 and "4 questions" in r.output
    assert "geo" in runner.invoke(app, ["testsets", "--db", "ek.db"]).output
    (project / "bad.jsonl").write_text("nope")
    assert runner.invoke(app, ["import-testset", "bad.jsonl", "--db", "ek.db"]).exit_code == 2


def test_odd_characters_do_not_crash_a_narrow_console(tmp_path, monkeypatch):

    from typer.testing import CliRunner

    from evalkit.cli import app
    from evalkit.core.models import CaseResult, Output
    from evalkit.core.storage import Storage

    db = tmp_path / "x.db"
    with Storage(db) as st:
        rid = st.create_run("n", "v", {})
        res = CaseResult(case_id="a", input="q", output=Output(answer="café → ✓"), scores={}, passed=False, repeat=0)
        st.add_result(rid, res)
        from evalkit.core.summary import summarize

        st.finish_run(rid, summarize([res]))
    r = CliRunner().invoke(app, ["show", "--db", str(db), "--failing"])
    assert r.exit_code == 0
