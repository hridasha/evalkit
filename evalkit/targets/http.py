"""Test an app over HTTP. One class covers many kinds of apps, so you rarely need to write an adapter:

- JSON APIs (any shape: `answer_path = "auto"` finds the answer for you)
- streaming replies (server-sent events or one JSON per line)
- forms and file uploads (`content_type = "form"` / `"multipart"`)
- ordinary web apps with a login and HTML pages (login, CSRF token, setup steps, read the answer with a CSS selector)
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import httpx

from evalkit.core.jsonpath import extract, fill
from evalkit.core.models import Context, Output
from evalkit.targets.detect import suggest_answer_paths, suggest_contexts_paths, suggest_usage
from evalkit.targets.htmlsel import parse_html, select

STREAM_PATHS = [
    "choices[0].delta.content", "choices[0].text", "delta.text", "candidates[0].content.parts[0].text",
    "message.content", "response", "delta", "token.text", "token", "text", "content", "output",
]  # fmt: skip
CONTENT_TYPES = {"json", "form", "multipart", "text"}


def _env(value: Any) -> Any:
    """Replace ${NAME} with the environment variable NAME in every string."""
    if isinstance(value, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: os.getenv(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_env(v) for v in value]
    return value


def _fill_url(template: str, values: dict[str, str]) -> str:
    """Like fill(), but the question placed into a URL is percent-encoded (saved setup values are used as they are)."""
    return re.sub(
        r"\{\{(\w+)\}\}",
        lambda m: (
            quote(str(values.get(m.group(1), m.group(0))), safe="")
            if m.group(1) == "input"
            else str(values.get(m.group(1), m.group(0)))
        ),
        template,
    )


def _looks_html(content_type: str, text: str) -> bool:
    return "html" in content_type.lower() or text.lstrip()[:15].lower().startswith(("<!doctype", "<html"))


class HttpTarget:
    def __init__(
        self,
        url: str,
        method: str = "POST",
        body: Any = None,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        content_type: str = "json",
        files: dict[str, str] | None = None,
        files_dir: str | None = None,
        base_url: str | None = None,
        answer_path: str | None = "auto",
        contexts_path: str | None = None,
        context_text_path: str | None = "text",
        context_id_path: str | None = "id",
        context_score_path: str | None = "score",
        tokens_in_path: str | None = None,
        tokens_out_path: str | None = None,
        input_price_per_m: float = 0.0,
        output_price_per_m: float = 0.0,
        answer_selector: str | None = None,
        answer_regex: str | None = None,
        answer_pick: str = "last",
        stream: bool = False,
        stream_path: str | None = None,
        login: dict | None = None,
        csrf: bool = False,
        setup: list[dict] | None = None,
        retries: int = 2,
        timeout: float = 60.0,
        verify: bool = True,
        follow_redirects: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if content_type not in CONTENT_TYPES:
            raise ValueError(f"content_type must be one of {sorted(CONTENT_TYPES)}")
        if answer_pick not in ("first", "last", "all"):
            raise ValueError("answer_pick must be first, last or all")
        self.method = method.upper()
        self.base_url = base_url
        self.url = url
        self.body = body if body is not None or content_type == "multipart" else {"question": "{{input}}"}
        self.headers = _env(headers or {})
        self.query = query or {}
        self.content_type = content_type
        self.files, self.files_dir = files or {}, files_dir
        self.answer_path, self.contexts_path = answer_path, contexts_path
        self.context_text_path, self.context_id_path = context_text_path, context_id_path
        self.context_score_path = context_score_path
        self.tokens_in_path, self.tokens_out_path = tokens_in_path, tokens_out_path
        self.price_in, self.price_out = input_price_per_m, output_price_per_m
        self.answer_selector, self.answer_regex, self.answer_pick = answer_selector, answer_regex, answer_pick
        self.stream, self.stream_path = stream, stream_path
        self.login_cfg, self.csrf, self.setup_steps = login, csrf, setup or []
        self.retries = max(0, retries)
        self.vars: dict[str, str] = {}
        self._ready = False
        self._lock = asyncio.Lock()
        self.client = httpx.AsyncClient(
            timeout=timeout, transport=transport, verify=verify, follow_redirects=follow_redirects
        )

    # ------------------------------------------------------------------------------------------ helpers
    def _abs(self, url: str) -> str:
        return (
            urljoin(self.base_url.rstrip("/") + "/", url)
            if self.base_url and not url.startswith(("http://", "https://"))
            else url
        )

    def _csrf_token(self, html: str | None = None) -> str | None:
        if html:
            for node in select(parse_html(html), "input[name=csrfmiddlewaretoken]"):
                if node.attrs.get("value"):
                    return node.attrs["value"]
        return self.client.cookies.get("csrftoken")

    # ------------------------------------------------------------------------------- login and setup
    async def _prepare(self) -> str | None:
        """Runs once: log in (if configured), then the setup steps. Returns an error message or None."""
        async with self._lock:
            if self._ready:
                return None
            try:
                if self.login_cfg:
                    err = await self._login(self.login_cfg)
                    if err:
                        return err
                for step in self.setup_steps:
                    err = await self._setup_step(step)
                    if err:
                        return err
            except httpx.HTTPError as exc:
                return f"setup failed: {type(exc).__name__}: {exc}"
            self._ready = True
            return None

    async def _login(self, cfg: dict) -> str | None:
        url = self._abs(_env(cfg["url"]))
        page = await self.client.get(url)
        fields = dict(_env(cfg.get("fields", {})))
        token = self._csrf_token(page.text)
        if token and "csrfmiddlewaretoken" not in fields:
            fields["csrfmiddlewaretoken"] = token
        headers = {"Referer": url}
        if token:
            headers["X-CSRFToken"] = token
        resp = await self.client.post(url, data=fields, headers=headers)
        if resp.status_code >= 400:
            return f"login failed: HTTP {resp.status_code}"
        ok_text = cfg.get("success_text")
        if ok_text and ok_text not in resp.text:
            return f"login failed: the page after logging in does not contain {ok_text!r}"
        if not ok_text and str(resp.url).rstrip("/") == url.rstrip("/"):
            return "login failed: still on the login page. Check the username and password (set via environment variables)."
        return None

    async def _setup_step(self, step: dict) -> str | None:
        method = step.get("method", "GET").upper()
        url = self._abs(_fill_url(step["url"], self.vars))
        data = _env(fill(step.get("body"), **self.vars)) if step.get("body") is not None else None
        headers = {"Referer": url}
        if method != "GET" and self.csrf:  # only websites with CSRF protection need the page fetched first
            page = await self.client.get(url)
            token = self._csrf_token(page.text)
            if token:
                headers["X-CSRFToken"] = token
                if isinstance(data, dict):
                    data.setdefault("csrfmiddlewaretoken", token)
        send = {"json": data} if step.get("json") else {"data": data}
        resp = await self.client.request(method, url, headers=headers, **send)
        if resp.status_code >= 400:
            return f"setup step {step['url']} failed: HTTP {resp.status_code}"
        if step.get("save_as"):
            if step.get("path"):  # an API: keep one field of the JSON reply (e.g. a new chat id)
                try:
                    found = extract(resp.json(), step["path"])
                except ValueError:
                    found = None
                if found is None:
                    return f"setup step {step['url']}: nothing found at '{step['path']}' in the reply"
                self.vars[step["save_as"]] = str(found)
            else:  # a website: keep the address it redirected to (e.g. /chat/7/)
                self.vars[step["save_as"]] = (
                    str(resp.url).replace(self.base_url.rstrip("/"), "") if self.base_url else str(resp.url)
                )
        return None

    # ------------------------------------------------------------------------------------- the request
    def _request_args(self, question: str) -> dict:
        values = {"uuid": uuid.uuid4().hex, **self.vars, "input": question}  # {{uuid}}: a fresh id per request
        url = self._abs(_fill_url(_env(self.url), values))
        args: dict[str, Any] = {"url": url, "headers": dict(self.headers)}
        query = _env(fill(self.query, **values)) if self.query else {}
        payload = _env(fill(self.body, **values)) if self.body is not None else None
        if self.method == "GET":
            params = {**query, **(payload if isinstance(payload, dict) else {})}
            if params:
                args["params"] = params
            return args
        if query:
            args["params"] = query
        if self.content_type == "multipart":
            data = {k: str(v) for k, v in (payload or {}).items()} if isinstance(payload, dict) else {}
            files = {}
            for field, template in self.files.items():
                path = Path(_fill_url_plain(template, values))
                if self.files_dir and not path.is_absolute():
                    path = Path(self.files_dir) / path
                if not path.is_file():
                    raise FileNotFoundError(f"file not found: {path}")
                files[field] = (
                    path.name,
                    path.read_bytes(),
                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
            args["data"], args["files"] = data, files
        elif self.content_type == "form":
            args["data"] = {k: str(v) for k, v in payload.items()} if isinstance(payload, dict) else payload
        elif self.content_type == "text":
            args["content"] = payload if isinstance(payload, str) else json.dumps(payload)
            args["headers"].setdefault("Content-Type", "text/plain")
        else:
            args["json"] = payload
        return args

    async def _send(self, question: str) -> tuple[int, str, str, Any]:
        """Returns (status, content_type, text, parsed_json_or_None); streams are folded into one object."""
        args = self._request_args(question)
        if self.csrf and self.method != "GET":
            token = self._csrf_token()
            if token is None:
                probe = await self.client.get(args["url"])
                token = self._csrf_token(probe.text)
            if token:
                args["headers"].setdefault("X-CSRFToken", token)
                args["headers"].setdefault("Referer", args["url"])
                if isinstance(args.get("data"), dict):
                    args["data"].setdefault("csrfmiddlewaretoken", token)
        if self.stream:
            return await self._send_stream(args)
        resp = await self.client.request(self.method, **args)
        ctype = resp.headers.get("content-type", "")
        if resp.status_code < 400 and ctype.startswith("text/event-stream"):  # a stream, even though not told so
            parts: list[str] = []
            state: dict = {}
            for raw in resp.text.splitlines():
                if not self._fold_line(raw, parts, state):
                    break
            return self._folded(resp.status_code, ctype, parts, state)
        try:
            return resp.status_code, ctype, resp.text, resp.json()
        except ValueError:
            return resp.status_code, ctype, resp.text, None

    def _fold_line(self, raw: str, parts: list[str], state: dict) -> bool:
        """Add one streamed line to ``parts``. Returns False when the stream says it is finished."""
        line = raw.strip()
        if not line or line.startswith((":", "event:", "id:", "retry:")):
            return True
        if line.startswith("data:"):
            line = line[5:].strip()
        if line == "[DONE]":
            return False
        try:
            obj = json.loads(line)
        except ValueError:
            parts.append(line)
            return True
        if not isinstance(obj, dict):
            return True
        if obj.get("error") and not parts:  # the app reported a failure inside the stream
            state["error"] = str(obj["error"])[:300]
        for path in [self.stream_path] if self.stream_path else STREAM_PATHS:
            piece = extract(obj, path)
            if isinstance(piece, str) and piece:
                parts.append(piece)
                break
        state["usage"] = obj.get("usage") or obj.get("usageMetadata") or state.get("usage")
        return True

    @staticmethod
    def _folded(status: int, ctype: str, parts: list[str], state: dict) -> tuple[int, str, str, Any]:
        if state.get("error") and not parts:
            return 502, ctype, f"the app returned an error in its stream: {state['error']}", None
        data: dict[str, Any] = {"text": "".join(parts)}
        if state.get("usage"):
            data["usage"] = state["usage"]
        return status, ctype, data["text"], data

    async def _send_stream(self, args: dict) -> tuple[int, str, str, Any]:
        parts: list[str] = []
        state: dict = {}
        async with self.client.stream(self.method, **args) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                return resp.status_code, resp.headers.get("content-type", ""), body, None
            async for raw in resp.aiter_lines():
                if not self._fold_line(raw, parts, state):
                    break
        return self._folded(resp.status_code, resp.headers.get("content-type", ""), parts, state)

    async def ask(self, question: str) -> Output:
        started = time.perf_counter()

        def elapsed() -> float:
            return (time.perf_counter() - started) * 1000

        if (err := await self._prepare()) is not None:
            return Output(error=err, latency_ms=elapsed())
        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                status, ctype, text, data = await self._send(question)
            except FileNotFoundError as exc:
                return Output(error=str(exc), latency_ms=elapsed())
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            else:
                if status in (429, 500, 502, 503, 504) and attempt < self.retries:
                    last_error = f"HTTP {status}"
                else:
                    return self._parse(question, status, ctype, text, data, elapsed())
            await asyncio.sleep(0.4 * 2**attempt)
        return Output(error=last_error, latency_ms=elapsed())

    # --------------------------------------------------------------------------------------- the reply
    def _parse(self, question: str, status: int, ctype: str, text: str, data: Any, latency: float) -> Output:
        if status >= 400:
            return Output(error=f"HTTP {status}: {text[:200]}", latency_ms=latency)
        if data is None:  # not JSON: a web page, or plain text
            return self._parse_text(text, ctype, latency)
        path = self.answer_path
        if self.stream and path in (None, "auto"):
            path = "text"
        if path in (None, "auto"):
            found = suggest_answer_paths(data, question, 1)
            path = found[0]["path"] if found else None
            if path is None:
                return Output(
                    error=f"could not find an answer in the reply: {str(data)[:200]}", latency_ms=latency, raw=data
                )
            self.answer_path = path  # remember it for the next questions
        answer = extract(data, path)
        if answer is None:
            return Output(error=f"no '{path}' in the reply: {str(data)[:200]}", latency_ms=latency, raw=data)
        if self.contexts_path == "auto":
            found = suggest_contexts_paths(data, 1)
            if found:
                best = found[0]
                self.contexts_path = best["path"]
                self.context_text_path, self.context_id_path, self.context_score_path = (
                    best["text_path"],
                    best["id_path"],
                    best["score_path"],
                )
            else:
                self.contexts_path = None
        if "auto" in (self.tokens_in_path, self.tokens_out_path):
            usage = suggest_usage(data)
            self.tokens_in_path = usage["tokens_in_path"] if usage else None
            self.tokens_out_path = usage["tokens_out_path"] if usage else None
        tin = int(extract(data, self.tokens_in_path) or 0) if self.tokens_in_path else 0
        tout = int(extract(data, self.tokens_out_path) or 0) if self.tokens_out_path else 0
        return Output(
            answer=answer
            if isinstance(answer, str)
            else json.dumps(answer)
            if isinstance(answer, dict | list)
            else str(answer),
            contexts=self._contexts(data),
            latency_ms=latency,
            tokens_in=tin,
            tokens_out=tout,
            cost=(tin * self.price_in + tout * self.price_out) / 1_000_000,
            raw=data,
        )

    def _parse_text(self, text: str, ctype: str, latency: float) -> Output:
        if self.answer_selector:
            try:
                found = [t for n in select(parse_html(text), self.answer_selector) if (t := n.text())]
            except ValueError as exc:
                return Output(error=str(exc), latency_ms=latency)
            if not found:
                return Output(
                    error=f"nothing on the page matches the selector {self.answer_selector!r}",
                    latency_ms=latency,
                    raw=text[:2000],
                )
            answer = (
                "\n".join(found)
                if self.answer_pick == "all"
                else found[0]
                if self.answer_pick == "first"
                else found[-1]
            )
        elif self.answer_regex:
            m = re.search(self.answer_regex, text, re.S)
            if not m:
                return Output(
                    error=f"the pattern {self.answer_regex!r} was not found in the reply",
                    latency_ms=latency,
                    raw=text[:2000],
                )
            answer = (m.group(1) if m.groups() else m.group(0)).strip()
        elif _looks_html(ctype, text):
            return Output(
                error='the reply is a web page. Set `answer_selector` (a CSS selector such as ".reply") to say where the answer is.',
                latency_ms=latency,
                raw=text[:2000],
            )
        else:
            answer = text.strip()
        return Output(answer=answer, latency_ms=latency, raw=text[:2000])

    def _contexts(self, data) -> list[Context]:
        if not self.contexts_path or self.contexts_path == "auto":
            return []
        items = extract(data, self.contexts_path) or []
        if not isinstance(items, list):
            items = [items]
        out = []
        for n, item in enumerate(items):
            if isinstance(item, dict):
                text = extract(item, self.context_text_path) if self.context_text_path else None
                cid = extract(item, self.context_id_path) if self.context_id_path else None
                score = extract(item, self.context_score_path) if self.context_score_path else None
                out.append(
                    Context(
                        text=str(text if text is not None else item),
                        id=None if cid is None else str(cid),
                        score=score if isinstance(score, int | float) else None,
                    )
                )
            else:
                out.append(Context(text=str(item), id=str(n)))
        return out

    async def aclose(self) -> None:
        await self.client.aclose()


def _fill_url_plain(template: str, values: dict[str, str]) -> str:
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), template)
