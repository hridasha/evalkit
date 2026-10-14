import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from evalkit.api.app import create_api_app

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo_rag"


@pytest.fixture
def api(tmp_path):
    with TestClient(create_api_app(tmp_path / "api.db")) as client:
        yield client


@pytest.fixture
def testset_text():
    return (DEMO / "testset.jsonl").read_text(encoding="utf-8")


def config_text(url: str, gate: str = "") -> str:
    return f"""
name = "demo"
[target]
type = "http"
url = "{url}"
answer_path = "answer"
contexts_path = "contexts"
[run]
concurrency = 4
[[scorers]]
name = "contains"
[[scorers]]
name = "hit_rate"
k = 4
{gate}
"""


def upload(api, text, name="demo", filename="t.jsonl"):
    return api.post("/api/testsets", files={"file": (filename, text.encode())}, data={"name": name})


def wait(api, job_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        job = api.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def start(api, url, **extra):
    r = api.post("/api/jobs", json={"config": config_text(url, extra.pop("gate", "")), "testset": "demo", **extra})
    assert r.status_code == 201, r.text
    return wait(api, r.json()["id"])


def test_health_and_scorer_list(api):
    assert api.get("/api/health").json()["ok"]
    names = {s["name"] for s in api.get("/api/scorers").json()}
    assert {"exact", "contains", "judge", "faithfulness", "hit_rate", "abstain"} <= names


def test_testset_upload_list_get_delete(api, testset_text):
    r = upload(api, testset_text)
    assert r.status_code == 201 and r.json() == {"name": "demo", "count": 37}
    assert api.get("/api/testsets").json()[0]["count"] == 37
    assert api.get("/api/testsets/demo").json()[0]["id"] == "ref-1"
    assert upload(api, "a,b\n1,2\n", filename="x.csv").status_code == 400
    assert upload(api, "not json").status_code == 400
    assert api.delete("/api/testsets/demo").status_code == 204
    assert api.get("/api/testsets/demo").status_code == 404


def test_a_run_goes_through_the_whole_pipeline(api, testset_text, rag_url):
    upload(api, testset_text)
    job = start(api, rag_url, gate="[gate]\nmin_pass_rate = 0.5\n[gate.scorers]\nhit_rate = 0.8\n")
    assert job["status"] == "done" and job["done"] == job["total"] == 37
    res = job["result"]
    assert res["summary"]["n"] == 37 and res["gate"]["passed"] is True and res["comparison"] is None

    runs = api.get("/api/runs").json()
    assert len(runs) == 1 and runs[0]["summary"]["n"] == 37
    detail = api.get(f"/api/runs/{job['run_id']}").json()
    assert len(detail["results"]) == 37 and detail["results"][0]["output"]["contexts"]
    page = api.get(f"/api/runs/{job['run_id']}/report.html")
    assert page.status_code == 200 and "Questions" in page.text


def test_second_run_is_compared_with_the_first_and_profiled(api, testset_text, rag_url):
    upload(api, testset_text)
    first = start(api, rag_url, version="v1")
    second = start(api, rag_url, version="v2", gate="[gate]\nmax_regression = 0.05\n")
    assert second["result"]["comparison"]["baseline_run_id"] == first["run_id"]
    assert second["result"]["gate"]["passed"] is True

    cmp = api.get("/api/compare", params={"a": first["run_id"], "b": second["run_id"]}).json()
    assert cmp["verdict"] == "no clear difference" and cmp["n_paired"] == 37
    html = api.get("/api/compare/report.html", params={"a": first["run_id"], "b": second["run_id"]})
    assert "A/B comparison" in html.text
    prof = api.get("/api/profile", params={"min_pass_rate": 0.1}).json()
    assert len(prof["rows"]) == 2 and prof["recommendation"]
    assert api.get("/api/compare", params={"a": 1, "b": 999}).status_code == 404


def test_repeats_feed_the_consistency_report(api, testset_text, rag_url):
    upload(api, testset_text)
    job = start(api, rag_url, repeats=2, limit=6)
    assert job["total"] == 12
    rep = api.get(f"/api/runs/{job['run_id']}/consistency").json()
    assert rep["questions"] == 6 and rep["mean_agreement"] == 1.0  # the demo app is deterministic


def test_bad_requests_are_explained(api, testset_text, rag_url):
    upload(api, testset_text)
    assert api.post("/api/jobs", json={"config": "not [toml", "testset": "demo"}).status_code == 400
    assert api.post("/api/jobs", json={"config": config_text(rag_url), "testset": "ghost"}).status_code == 404
    bad_scorer = config_text(rag_url) + '\n[[scorers]]\nname = "magic"\n'
    r = api.post("/api/jobs", json={"config": bad_scorer, "testset": "demo"})
    assert r.status_code == 400 and "unknown scorer" in r.json()["detail"]
    py = '[target]\ntype = "python"\nfunction = "os:getcwd"\n'
    r = api.post("/api/jobs", json={"config": py, "testset": "demo"})
    assert r.status_code == 400 and "python targets are disabled" in r.json()["detail"]
    assert api.get("/api/runs/999").status_code == 404 and api.get("/api/jobs/zzz").status_code == 404


def test_an_unreachable_app_gives_errors_not_a_crash(api, testset_text):
    upload(api, testset_text)
    job = start(api, "http://127.0.0.1:1/ask", limit=3, gate="[gate]\nmax_errors = 0\n")
    assert job["status"] == "done" and job["result"]["summary"]["errors"] == 3
    assert job["result"]["gate"]["passed"] is False


def test_websocket_reports_progress_to_the_end(api, testset_text, rag_url):
    upload(api, testset_text)
    r = api.post("/api/jobs", json={"config": config_text(rag_url), "testset": "demo", "limit": 8})
    seen = []
    with api.websocket_connect(f"/ws/jobs/{r.json()['id']}") as ws:
        while True:
            try:
                seen.append(json.loads(ws.receive_text()))
            except Exception:  # noqa: BLE001, B017 - the test client raises when the server closes
                break
    assert seen and seen[-1]["status"] == "done" and seen[-1]["done"] == 8


def test_a_run_can_be_deleted(api, testset_text, rag_url):
    upload(api, testset_text)
    job = start(api, rag_url, limit=3)
    assert api.delete(f"/api/runs/{job['run_id']}").status_code == 204
    assert api.get("/api/runs").json() == []


def test_the_built_page_is_served_with_spa_fallback(tmp_path):
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html>evalkit ui</html>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("nope")
    with TestClient(create_api_app(tmp_path / "x.db", web_dir=web)) as c:
        assert "evalkit ui" in c.get("/").text and "evalkit ui" in c.get("/runs/3").text
        assert c.get("/assets/app.js").text == "console.log(1)"
        assert c.get("/api/nope").status_code == 404
        assert "nope" not in c.get("/..%2Fsecret.txt").text
