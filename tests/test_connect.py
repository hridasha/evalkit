import json
import tomllib

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from evalkit.api.app import create_api_app
from evalkit.cli import app as cli_app
from evalkit.core import tomlw
from evalkit.targets import HttpTarget, PythonTarget, build_target, resolve_config
from evalkit.targets.connect import probe
from evalkit.targets.curl import parse_curl
from evalkit.targets.detect import suggest_answer_paths, suggest_contexts_paths, suggest_usage
from evalkit.targets.htmlsel import select_text
from evalkit.targets.presets import expand
from evalkit.targets.python import dig


def target(handler, **kw):
    return HttpTarget("http://app/ask", transport=httpx.MockTransport(handler), retries=0, **kw)


# ----------------------------------------------------------------------------------------- detection
OPENAI = {"id": "chatcmpl-1", "object": "chat.completion", "model": "gpt", "choices": [{"index": 0, "finish_reason": "stop",
          "message": {"role": "assistant", "content": "Refunds take 7 days."}}], "usage": {"prompt_tokens": 12, "completion_tokens": 5}}  # fmt: skip
GEMINI = {"candidates": [{"content": {"parts": [{"text": "Refunds take 7 days."}], "role": "model"}, "finishReason": "STOP"}],
          "usageMetadata": {"promptTokenCount": 9, "candidatesTokenCount": 4}}  # fmt: skip
ANTHROPIC = {"id": "msg_1", "type": "message", "role": "assistant", "content": [{"type": "text", "text": "Refunds take 7 days."}],
             "usage": {"input_tokens": 8, "output_tokens": 6}}  # fmt: skip


@pytest.mark.parametrize(
    ("reply", "path"),
    [(OPENAI, "choices[0].message.content"), (GEMINI, "candidates[0].content.parts[0].text"),
     (ANTHROPIC, "content[0].text"), ({"data": {"reply": "Refunds take 7 days."}, "ok": True}, "data.reply"),
     ({"result": "Refunds take 7 days.", "request_id": "abc"}, "result")],
)  # fmt: skip
def test_the_answer_is_found_in_any_common_reply_shape(reply, path):
    assert suggest_answer_paths(reply, "how long?")[0]["path"] == path


def test_answer_detection_ignores_ids_urls_and_the_question_itself():
    reply = {"id": "x1", "url": "https://a.b/c", "echo": "how long do refunds take", "answer": "Seven days."}
    assert suggest_answer_paths(reply, "how long do refunds take")[0]["path"] == "answer"
    assert suggest_answer_paths("plain text reply")[0]["path"] == "" and suggest_answer_paths({"id": "1"}) == []


def test_contexts_and_usage_are_detected():
    rag = {
        "answer": "x",
        "sources": [
            {"doc_id": "refunds", "chunk": "Refunds are issued within 7 days of the return.", "similarity": 0.8}
        ],
    }
    c = suggest_contexts_paths(rag)[0]
    assert (c["path"], c["text_path"], c["id_path"], c["score_path"]) == ("sources", "chunk", "doc_id", "similarity")
    assert suggest_contexts_paths(OPENAI) == [] and suggest_contexts_paths(GEMINI) == []  # not passages
    assert suggest_usage(OPENAI) == {
        "tokens_in_path": "usage.prompt_tokens",
        "tokens_out_path": "usage.completion_tokens",
    }
    assert (
        suggest_usage(GEMINI)["tokens_in_path"] == "usageMetadata.promptTokenCount" and suggest_usage({"a": 1}) is None
    )


async def test_auto_answer_path_is_learned_and_reused():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=OPENAI)

    t = target(handler, tokens_in_path="auto", tokens_out_path="auto", input_price_per_m=1.0)
    out = await t.ask("how long?")
    assert out.answer == "Refunds take 7 days." and t.answer_path == "choices[0].message.content"
    assert (out.tokens_in, out.tokens_out) == (12, 5) and out.cost > 0
    assert (await t.ask("again")).answer == "Refunds take 7 days."


async def test_auto_contexts():
    reply = {
        "answer": "a",
        "documents": [{"id": "d1", "text": "A passage about refunds, long enough to count.", "score": 0.9}],
    }
    out = await target(lambda r: httpx.Response(200, json=reply), contexts_path="auto").ask("q")
    assert out.contexts[0].id == "d1" and out.contexts[0].score == 0.9


# -------------------------------------------------------------------------------------------- presets
def test_presets_build_the_right_requests():
    o = expand({"preset": "openai", "model": "m1", "system": "Be brief."})
    assert o["url"].endswith("/chat/completions") and o["headers"]["Authorization"] == "Bearer ${OPENAI_API_KEY}"
    assert o["body"]["messages"][0] == {"role": "system", "content": "Be brief."} and o["body"]["model"] == "m1"
    a = expand({"preset": "anthropic"})
    assert (
        a["headers"]["anthropic-version"]
        and a["body"]["messages"][0]["content"] == "{{input}}"
        and a["answer_path"] == "content[0].text"
    )
    g = expand({"preset": "gemini", "model": "gemini-x"})
    assert (
        g["url"].endswith("models/gemini-x:generateContent") and g["tokens_in_path"] == "usageMetadata.promptTokenCount"
    )
    assert expand({"preset": "ollama"})["body"]["stream"] is False and "headers" not in expand({"preset": "ollama"})
    assert expand({"preset": "groq"})["url"].startswith("https://api.groq.com")
    mine = expand({"preset": "openai", "headers": {"X-Org": "a"}, "answer_path": "custom"})
    assert mine["headers"]["X-Org"] == "a" and "Authorization" in mine["headers"] and mine["answer_path"] == "custom"
    with pytest.raises(ValueError, match="unknown preset"):
        expand({"preset": "nope"})
    assert resolve_config({"preset": "rag", "base_url": "http://x/ask"})["url"] == "http://x/ask"


async def test_a_preset_talks_to_a_fake_openai_server(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"], seen["body"] = req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json=OPENAI)

    t = build_target({"preset": "openai", "model": "m1", "transport": httpx.MockTransport(handler)})
    out = await t.ask("How long?")
    assert seen["auth"] == "Bearer sk-test" and seen["body"]["messages"][-1]["content"] == "How long?"
    assert out.answer == "Refunds take 7 days." and out.tokens_in == 12


# ----------------------------------------------------------------------------------------------- curl
def test_curl_import_swaps_keys_for_variables_and_finds_the_question():
    cmd = """curl https://api.example.com/v1/chat \\
      -H "Authorization: Bearer sk-secret-123" -H 'Content-Type: application/json' \\
      -d '{"model": "m", "messages": [{"role": "user", "content": "What is the refund policy?"}]}'"""
    cfg, env = parse_curl(cmd)
    assert (
        cfg["method"] == "POST" and cfg["body"]["messages"][0]["content"] == "{{input}}" and cfg["body"]["model"] == "m"
    )
    assert cfg["headers"] == {"Authorization": "Bearer ${API_KEY}"} and env == ["API_KEY"]
    assert "sk-secret-123" not in json.dumps(cfg)


def test_curl_variants():
    cfg, env = parse_curl(
        "curl -X POST 'https://x.io/g?key=AIzaSECRET&alt=json' -H 'Content-Type: application/json' -d '{\"q\": \"hello there\"}'"
    )
    assert (
        cfg["url"] == "https://x.io/g" and cfg["query"] == {"alt": "json", "key": "${API_KEY}"} and env == ["API_KEY"]
    )
    assert cfg["body"] == {"q": "{{input}}"}
    cfg, _ = parse_curl("curl 'https://x.io/search?q=refund+policy&page=1'")
    assert cfg["method"] == "GET" and cfg["query"]["q"] == "{{input}}" and cfg["query"]["page"] == "1"
    cfg, _ = parse_curl("curl https://x.io/f -d 'message=hello+world&lang=en'")
    assert cfg["content_type"] == "form" and cfg["body"] == {"message": "{{input}}", "lang": "en"}
    cfg, env = parse_curl('curl -u admin:pw https://x.io/a -d \'{"prompt": "hi there"}\'')
    assert cfg["headers"]["Authorization"] == "Basic ${API_KEY}" and "pw" not in json.dumps(cfg)
    cfg, _ = parse_curl('curl https://x.io/a -d \'{"prompt": "hi", "user": "bob"}\'', sample="bob")
    assert cfg["body"] == {"prompt": "hi", "user": "{{input}}"}
    cfg, _ = parse_curl("curl https://x.io/a/hello%20world", sample="hello%20world")
    assert cfg["url"] == "https://x.io/a/{{input}}"


def test_bad_curl_is_explained():
    for bad, msg in [("", "does not look like"), ("curl -X POST", "no URL"), ("curl 'https://x.io", "quote"),
                     ("curl https://x.io/a -d '{\"q\": \"hi\"}'", None)]:  # fmt: skip
        if msg is None:
            assert parse_curl(bad)[0]["body"] == {"q": "{{input}}"}
        else:
            with pytest.raises(ValueError, match=msg):
                parse_curl(bad)
    with pytest.raises(ValueError, match="not found in the request"):
        parse_curl('curl https://x.io/a -d \'{"q": "hi"}\'', sample="zzz")


# ---------------------------------------------------------------------------------------------- html
PAGE = """<html><body><script>var q="ignore me"</script><div id="chat"><div class="msg user">Hi</div>
<div class="msg bot"><p>Refunds take <b>7</b> days.</p></div><div class="msg bot">Anything else?</div></div></body></html>"""


def test_html_selectors():
    assert select_text(PAGE, ".msg.bot") == ["Refunds take 7 days.", "Anything else?"]
    assert select_text(PAGE, "#chat .msg.user") == ["Hi"] and select_text(PAGE, "div[class=msg]") == []
    assert select_text(PAGE, "p") == ["Refunds take 7 days."] and select_text(PAGE, "script") == []
    assert select_text("<ul><li>a<li>b</ul>", "li")  # sloppy html does not crash
    with pytest.raises(ValueError):
        select_text(PAGE, "div > p")


async def test_plain_text_and_html_replies():
    assert (await target(lambda r: httpx.Response(200, text="Seven days.")).ask("q")).answer == "Seven days."
    html = lambda r: httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})  # noqa: E731
    assert "answer_selector" in (await target(html).ask("q")).error
    assert (await target(html, answer_selector=".msg.bot").ask("q")).answer == "Anything else?"
    assert (
        await target(html, answer_selector=".msg.bot", answer_pick="first").ask("q")
    ).answer == "Refunds take 7 days."
    assert "matches" in (await target(html, answer_selector=".nope").ask("q")).error
    assert (await target(html, answer_regex=r'class="msg user">(\w+)<').ask("q")).answer == "Hi"


# -------------------------------------------------------------------------------- streaming / forms
async def test_server_sent_events_are_stitched_together():
    sse = (
        'data: {"choices":[{"delta":{"content":"Refunds "}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"take 7 days."}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\n'
        "data: [DONE]\n\n"
    )
    out = await target(lambda r: httpx.Response(200, text=sse), stream=True, tokens_in_path="usage.prompt_tokens").ask(
        "q"
    )
    assert out.answer == "Refunds take 7 days." and out.tokens_in == 3


async def test_line_delimited_json_streams_and_plain_text_streams():
    nd = '{"message":{"content":"Seven "},"done":false}\n{"message":{"content":"days."},"done":true}\n'
    assert (await target(lambda r: httpx.Response(200, text=nd), stream=True).ask("q")).answer == "Seven days."
    assert (
        await target(lambda r: httpx.Response(200, text="hello\nworld\n"), stream=True).ask("q")
    ).answer == "helloworld"
    assert "HTTP 500" in (await target(lambda r: httpx.Response(500, text="x"), stream=True).ask("q")).error


async def test_form_text_query_and_url_templates():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(url=str(req.url), body=req.content.decode(), ctype=req.headers.get("content-type", ""))
        return httpx.Response(200, json={"answer": "ok"})

    await target(handler, content_type="form", body={"msg": "{{input}}", "n": 2}).ask("a b&c")
    assert "msg=a+b%26c" in seen["body"] and "x-www-form-urlencoded" in seen["ctype"]
    await target(handler, content_type="text", body="raw: {{input}}").ask("hi")
    assert seen["body"] == "raw: hi" and seen["ctype"].startswith("text/plain")
    await target(handler, method="GET", body=None, query={"q": "{{input}}", "k": "1"}).ask("x y")
    assert "q=x+y" in seen["url"] and "k=1" in seen["url"]
    t = HttpTarget("http://app/ask/{{input}}", method="GET", transport=httpx.MockTransport(handler), body={})
    await t.ask("a/b c")
    assert seen["url"] == "http://app/ask/a%2Fb%20c"


async def test_failed_requests_are_retried(monkeypatch):
    monkeypatch.setattr("evalkit.targets.http.asyncio.sleep", lambda s: _noop())
    n = {"c": 0}

    def handler(req):
        n["c"] += 1
        return httpx.Response(503) if n["c"] < 3 else httpx.Response(200, json={"answer": "finally"})

    t = HttpTarget("http://app/ask", transport=httpx.MockTransport(handler), retries=2)
    assert (await t.ask("q")).answer == "finally" and n["c"] == 3


async def _noop():
    return None


# ------------------------------------------------------------------------------------ file uploads
async def test_file_upload(tmp_path):
    (tmp_path / "cv.pdf").write_bytes(b"%PDF-1.4 fake")
    seen = {}

    def handler(req: httpx.Request):
        seen["ctype"], seen["body"] = req.headers["content-type"], req.content
        return httpx.Response(200, json={"answer": "parsed"})

    t = target(
        handler,
        content_type="multipart",
        body={"job": "engineer"},
        files={"resume": "{{input}}"},
        files_dir=str(tmp_path),
    )
    assert (await t.ask("cv.pdf")).answer == "parsed"
    assert (
        seen["ctype"].startswith("multipart/form-data")
        and b'filename="cv.pdf"' in seen["body"]
        and b"%PDF-1.4 fake" in seen["body"]
    )
    assert b'name="job"' in seen["body"]
    assert "file not found" in (await t.ask("missing.pdf")).error


# ----------------------------------------------------------- a website with login, csrf and HTML pages
def fake_site():
    """Behaves like a small Django app: csrf cookie + hidden field, session login, a chat page that needs a login."""
    state = {"chat": None}

    def handler(req: httpx.Request) -> httpx.Response:
        path, cookies = req.url.path, req.headers.get("cookie", "")
        logged_in = "sessionid=s1" in cookies
        if path == "/login/" and req.method == "GET":
            return httpx.Response(200, text='<form><input type="hidden" name="csrfmiddlewaretoken" value="tok1"></form>',
                                  headers={"set-cookie": "csrftoken=tok1; Path=/"})  # fmt: skip
        if path == "/login/":
            form = dict(x.split("=") for x in req.content.decode().split("&"))
            if form.get("csrfmiddlewaretoken") != "tok1" or form.get("password") != "pw":
                return httpx.Response(200, text="<p>Invalid login</p><form></form>")
            return httpx.Response(302, headers={"location": "/home/", "set-cookie": "sessionid=s1; Path=/"})
        if not logged_in:
            return httpx.Response(302, headers={"location": "/login/"})
        if path == "/home/":
            return httpx.Response(200, text="<h1>Home</h1>", headers={"set-cookie": "csrftoken=tok1; Path=/"})
        if path == "/chat/new/" and req.method == "POST":
            if req.headers.get("x-csrftoken") != "tok1":
                return httpx.Response(403, text="CSRF failed")
            state["chat"] = 7
            return httpx.Response(302, headers={"location": "/chat/7/"})
        if path == "/chat/7/" and req.method == "GET":
            return httpx.Response(
                200, text='<input name="csrfmiddlewaretoken" value="tok1"><div class="reply">welcome</div>'
            )
        if path == "/chat/7/" and req.method == "POST":
            if req.headers.get("x-csrftoken") != "tok1" or b"csrfmiddlewaretoken=tok1" not in req.content:
                return httpx.Response(403, text="CSRF failed")
            msg = dict(x.split("=") for x in req.content.decode().split("&"))["message"]
            return httpx.Response(
                200, text=f'<div class="reply old">welcome</div><div class="reply">You asked: {msg}</div>'
            )
        return httpx.Response(404)

    return handler


def web(**kw):
    cfg = dict(
        base_url="http://site", url="{{chat}}", method="POST", content_type="form", body={"message": "{{input}}"}, csrf=True,
        login={"url": "/login/", "fields": {"username": "bob", "password": "${SITE_PW}"}},
        setup=[{"url": "/chat/new/", "method": "POST", "save_as": "chat"}], answer_selector=".reply", transport=httpx.MockTransport(fake_site()),
    )  # fmt: skip
    return HttpTarget(**{**cfg, **kw})


async def test_logging_in_csrf_setup_and_reading_the_answer_from_html(monkeypatch):
    monkeypatch.setenv("SITE_PW", "pw")
    t = web()
    assert (await t.ask("hello")).answer == "You asked: hello"
    assert t.vars["chat"] == "/chat/7/"
    assert (await t.ask("again")).answer == "You asked: again"


async def test_a_wrong_password_is_reported_clearly(monkeypatch):
    monkeypatch.setenv("SITE_PW", "wrong")
    out = await web().ask("hello")
    assert "login failed" in out.error


async def test_web_type_goes_through_build_target(monkeypatch):
    monkeypatch.setenv("SITE_PW", "pw")
    cfg = {"type": "web", "base_url": "http://site", "url": "{{chat}}", "content_type": "form", "body": {"message": "{{input}}"},
           "csrf": True, "login": {"url": "/login/", "fields": {"username": "bob", "password": "${SITE_PW}"}},
           "setup": [{"url": "/chat/new/", "method": "POST", "save_as": "chat"}], "answer_selector": ".reply",
           "transport": httpx.MockTransport(fake_site())}  # fmt: skip
    assert (await build_target(cfg).ask("hi")).answer == "You asked: hi"
    with pytest.raises(ValueError, match="bad settings"):
        build_target({"type": "http", "url": "x", "bogus": 1})


# --------------------------------------------------------------- python functions that return objects
class Person:
    def __init__(self, name):
        self.name, self.info = name, {"city": "Pune"}


class Hit:
    def __init__(self, person, score):
        self.person, self.score = person, score


def returns_objects(question):
    return {"answer_text": f"Best match for {question}", "sources": [Person("Asha"), Person("Ravi")]}


def returns_ranking(embedding):
    return [{"resume": Person("Asha"), "score": 0.9}, {"resume": Person("Ravi"), "score": 0.5}]


def to_embedding(question):
    return {"vec": question.upper()}


def test_dig_follows_dicts_lists_and_attributes():
    data = {"a": [{"b": Person("Asha")}]}
    assert dig(data, "a.0.b.name") == "Asha" and dig(data, "a.0.b.info.city") == "Pune"
    assert (
        dig(data, "a.5.b") is None and dig(data, "zzz.y") is None and dig(data, "@") is data and dig(data, None) is None
    )
    assert dig(Person("x"), "__init__") is None  # methods are never called


async def test_python_target_maps_objects_without_an_adapter():
    t = PythonTarget("tests.test_connect:returns_objects", answer_key="answer_text", contexts_key="sources",
                     context_text="info.city", context_id="name")  # fmt: skip
    out = await t.ask("python devs")
    assert (
        out.answer == "Best match for python devs"
        and [c.id for c in out.contexts] == ["Asha", "Ravi"]
        and out.contexts[0].text == "Pune"
    )


async def test_python_target_with_a_pre_step_and_a_list_result():
    t = PythonTarget("tests.test_connect:returns_ranking", pre="tests.test_connect:to_embedding", answer_key="0.resume.name",
                     contexts_key="@", context_id="resume.name", context_score="score")  # fmt: skip
    out = await t.ask("q")
    assert out.answer == "Asha" and [(c.id, c.score) for c in out.contexts] == [("Asha", 0.9), ("Ravi", 0.5)]
    bad = await PythonTarget("tests.test_connect:returns_objects", answer_key="nope").ask("q")
    assert "no 'nope'" in bad.error


# ------------------------------------------------------------------------- connect: probe, API, CLI
def test_toml_writer_round_trips():
    cfg = {"type": "http", "url": "http://x/a", "headers": {"Authorization": "Bearer ${K}"}, "retries": 2, "stream": False,
           "body": {"model": "m", "messages": [{"role": "user", "content": 'say "hi"\nnow'}]}}  # fmt: skip
    assert tomllib.loads(tomlw.table("target", cfg))["target"] == cfg


async def test_probe_reports_answer_settings_and_a_ready_to_paste_block():
    ok = await probe(
        {"preset": "openai", "transport": httpx.MockTransport(lambda r: httpx.Response(200, json=OPENAI))}, "q"
    )
    assert (
        ok["ok"]
        and ok["answer"] == "Refunds take 7 days."
        and ok["suggestions"]["answer_paths"][0]["path"] == "choices[0].message.content"
    )
    block = tomllib.loads(ok["toml"])["target"]
    assert block["answer_path"] == "choices[0].message.content" and "transport" not in ok["toml"]
    down = await probe(
        {
            "url": "http://x/a",
            "retries": 0,
            "transport": httpx.MockTransport(lambda r: httpx.Response(401, text="no key")),
        }
    )
    assert not down["ok"] and "HTTP 401" in down["error"] and down["toml"]
    assert not (await probe({"url": "http://x", "bogus": 1}))["ok"]
    with pytest.raises(ValueError, match="python"):
        await probe({"type": "python", "function": "a:b"})


@pytest.fixture
def api(tmp_path):
    with TestClient(create_api_app(tmp_path / "x.db")) as c:
        yield c


def test_connect_endpoint_with_a_target_and_a_curl(api, rag_url):
    r = api.post(
        "/api/connect",
        json={"target": {"url": rag_url, "contexts_path": "auto"}, "question": "How long do refunds take?"},
    ).json()
    assert r["ok"] and "7 business days" in r["answer"] and len(r["contexts"]) == 4 and "[target]" in r["toml"]
    cmd = f"""curl {rag_url} -H 'Content-Type: application/json' -H 'X-Api-Key: sk-abc' -d '{{"question": "How much does standard shipping cost?"}}'"""
    r = api.post("/api/connect", json={"curl": cmd}).json()
    assert r["ok"] and r["answer"] and r["env_vars"] == [{"name": "API_KEY", "set": False}]
    assert "sk-abc" not in json.dumps(r)


def test_connect_endpoint_errors_and_presets(api):
    assert api.post("/api/connect", json={}).status_code == 400
    assert api.post("/api/connect", json={"curl": "curl"}).status_code == 400
    assert api.post("/api/connect", json={"target": {"type": "python", "function": "os:getcwd"}}).status_code == 400
    assert {p["name"] for p in api.get("/api/presets").json()} >= {"openai", "anthropic", "gemini", "ollama", "rag"}


def test_connect_secrets_are_used_for_the_test_and_forgotten(api, monkeypatch):
    import os

    seen = {}

    def fake_probe(cfg, question):
        async def go():
            seen["during"] = os.environ.get("MY_TEST_KEY")
            return {"ok": True, "toml": "", "contexts": [], "answer": "x", "error": None}

        return go()

    monkeypatch.setattr("evalkit.api.app.probe", fake_probe)
    monkeypatch.delenv("MY_TEST_KEY", raising=False)
    api.post("/api/connect", json={"target": {"url": "http://x"}, "secrets": {"MY_TEST_KEY": "abc"}})
    assert seen["during"] == "abc" and os.environ.get("MY_TEST_KEY") is None


def test_connect_cli(rag_url, tmp_path):
    out = tmp_path / "evalkit.toml"
    r = CliRunner().invoke(
        cli_app, ["connect", "--url", rag_url, "-q", "How long do refunds take?", "--write", str(out)]
    )
    assert r.exit_code == 0, r.output
    assert "EvalKit read this answer" in r.output and "[target]" in r.output and out.exists()
    cfg = tomllib.loads(out.read_text())
    assert cfg["target"]["url"] == rag_url and cfg["scorers"][0]["name"] == "contains"
    again = CliRunner().invoke(cli_app, ["connect", "--url", rag_url, "--write", str(out)])
    assert "left it alone" in again.output
    bad = CliRunner().invoke(cli_app, ["connect", "--url", "http://127.0.0.1:1/x"])
    assert bad.exit_code == 1 and "Failed" in bad.output
    assert CliRunner().invoke(cli_app, ["connect"]).exit_code == 2


@pytest.mark.anyio
async def test_event_stream_is_folded_even_without_stream_flag_and_errors_are_errors():
    sse = 'data: {"delta": "Seven "}\n\ndata: {"delta": "days."}\n\ndata: {"done": true}\n\n'
    ok = await target(lambda r: httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})).ask("q")
    assert ok.answer == "Seven days." and not ok.error
    bad = 'data: {"error": "503 UNAVAILABLE high demand"}\n\n'
    out = await target(lambda r: httpx.Response(200, text=bad, headers={"content-type": "text/event-stream"})).ask("q")
    assert out.error and "503 UNAVAILABLE" in out.error


@pytest.mark.anyio
async def test_uuid_placeholder_is_fresh_each_request():
    seen = []

    def handler(r):
        seen.append(json.loads(r.content)["thread_id"])
        return httpx.Response(200, json={"answer": "ok"})

    t = target(handler, body={"thread_id": "{{uuid}}", "question": "{{input}}"})
    await t.ask("a")
    await t.ask("b")
    assert len(set(seen)) == 2 and all(len(s) == 32 for s in seen)


@pytest.mark.anyio
async def test_api_setup_step_keeps_a_field_of_the_json_reply():
    def handler(r):
        if r.url.path == "/chat":
            return httpx.Response(200, json={"id": "abc"})
        return httpx.Response(200, json={"answer": f"in {r.url.path}"})

    t = HttpTarget("http://app/chat/{{chat}}/message", transport=httpx.MockTransport(handler), retries=0,
                   base_url="http://app", setup=[{"method": "POST", "url": "/chat", "save_as": "chat", "path": "id"}])
    assert (await t.ask("q")).answer == "in /chat/abc/message"
