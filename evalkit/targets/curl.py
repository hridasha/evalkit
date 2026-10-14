"""Turn a pasted `curl ...` command into a target config. Copy it from API docs, Postman or browser DevTools."""

from __future__ import annotations

import json
import re
import shlex
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

QUESTION_KEYS = ("question", "query", "message", "prompt", "input", "text", "content", "q", "inputs", "user_input")
SECRET_HEADERS = {"authorization", "x-api-key", "api-key", "x-goog-api-key", "cookie", "x-auth-token", "apikey"}
SECRET_PARAMS = {"key", "api_key", "apikey", "token", "access_token"}
DATA_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-ascii", "--data-urlencode", "--json"}
IGNORED_FLAGS = {
    "--compressed",
    "-s",
    "--silent",
    "-S",
    "-i",
    "-v",
    "--verbose",
    "-L",
    "--location",
    "-k",
    "--insecure",
    "-g",
}


def _tokens(text: str) -> list[str]:
    text = re.sub(r"\\\s*\r?\n", " ", text.strip())  # line continuations
    text = text.replace("`\n", " ")
    try:
        return shlex.split(text, posix=True)
    except ValueError as exc:
        raise ValueError(f"could not read that curl command ({exc}). Is a quote missing?") from None


def _env_name(hint: str, used: dict[str, str]) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", hint).strip("_").upper() or "API_KEY"
    if not base.endswith(("KEY", "TOKEN", "COOKIE", "AUTH")):
        base += "_KEY"
    name, n = base, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    return name


def _hide_secret(value: str, header: str, used: dict[str, str]) -> str:
    """Never keep a real key: swap it for ${ENV_VAR} and tell the user which variable to set."""
    if "${" in value:
        return value
    scheme = ""
    if header == "authorization" and " " in value:
        scheme, _, value = value.partition(" ")
        scheme += " "
    name = _env_name("API_KEY" if header in ("authorization", "x-api-key", "api-key", "apikey") else header, used)
    used[name] = header
    return f"{scheme}${{{name}}}"


def _replace_in(obj, sample: str):
    if isinstance(obj, str):
        return "{{input}}" if obj == sample else obj.replace(sample, "{{input}}")
    if isinstance(obj, dict):
        return {k: _replace_in(v, sample) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_replace_in(v, sample) for v in obj]
    return obj


def _guess_sample(body) -> str | None:
    """The question inside an example request: a value under a typical key (the last one wins), else the longest string."""
    strings: list[tuple[str, str]] = []

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, str(k).lower())
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str) and o.strip():
            strings.append((key, o))

    walk(body)
    keyed = [s for k, s in strings if k in QUESTION_KEYS]
    if keyed:
        return keyed[-1]
    return max((s for _, s in strings), key=len, default=None)


def parse_curl(command: str, sample: str | None = None) -> tuple[dict, list[str]]:
    """Returns (target config, names of the environment variables the user must set)."""
    tokens = _tokens(command)
    if tokens and tokens[0].lower() == "curl":
        tokens = tokens[1:]
    if not tokens:
        raise ValueError("that does not look like a curl command")
    method, url, headers, data_parts, is_get_data = None, None, {}, [], False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t in ("-X", "--request"):
            method, i = nxt.upper(), i + 2
        elif t in ("-H", "--header"):
            name, _, value = nxt.partition(":")
            headers[name.strip()] = value.strip()
            i += 2
        elif t in DATA_FLAGS:
            data_parts.append(nxt)
            if t == "--json":
                headers.setdefault("Content-Type", "application/json")
            i += 2
        elif t in ("-u", "--user"):
            headers["Authorization"] = "Basic " + nxt  # replaced by a ${ENV} placeholder below
            i += 2
        elif t in ("-b", "--cookie"):
            headers["Cookie"] = nxt
            i += 2
        elif t == "--url":
            url, i = nxt, i + 2
        elif t in ("-G", "--get"):
            is_get_data, i = True, i + 1
        elif t in IGNORED_FLAGS:
            i += 1
        elif t.startswith("-") and len(t) > 1:
            i += 2 if nxt and not nxt.startswith("-") and "://" not in nxt else 1  # an option we do not need
        else:
            url = url or t
            i += 1
    if not url:
        raise ValueError("no URL found in the curl command")

    used: dict[str, str] = {}
    safe_headers = {}
    for name, value in headers.items():
        low = name.lower()
        if low in ("host", "content-length", "accept-encoding", "connection"):
            continue
        safe_headers[name] = _hide_secret(value, low, used) if low in SECRET_HEADERS else value

    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    hidden = []
    for k, v in query:
        if k.lower() in SECRET_PARAMS and "${" not in v:
            name = _env_name("API_KEY" if k.lower() in ("key", "api_key", "apikey") else k, used)
            used[name] = k
            hidden.append((k, f"${{{name}}}"))
        else:
            hidden.append((k, v))
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    secret_in_url = [(k, v) for k, v in hidden if "${" in v]
    other_query = {k: v for k, v in hidden if "${" not in v}

    content_type = next((v for k, v in safe_headers.items() if k.lower() == "content-type"), "")
    raw = "&".join(data_parts) if data_parts else None
    body, kind = None, "json"
    if raw is not None and not is_get_data:
        try:
            body = json.loads(raw)
        except ValueError:
            if "x-www-form-urlencoded" in content_type or re.match(r"^[\w.\-\[\]]+=", raw):
                body, kind = dict(parse_qsl(raw, keep_blank_values=True)), "form"
            else:
                body, kind = raw, "text"
    elif raw is not None:
        other_query.update(dict(parse_qsl(raw, keep_blank_values=True)))
    method = method or ("POST" if body is not None else "GET")

    sample = sample or _guess_sample(body if body is not None else other_query)
    cfg: dict = {"url": clean_url, "method": method}
    if sample:
        body = _replace_in(body, sample) if body is not None else None
        other_query = _replace_in(other_query, sample)
        cfg["url"] = clean_url.replace(sample, "{{input}}")
    if secret_in_url:
        other_query.update(dict(secret_in_url))
    safe_headers = {k: v for k, v in safe_headers.items() if k.lower() != "content-type" or kind == "text"}
    if safe_headers:
        cfg["headers"] = safe_headers
    if body is not None:
        cfg["body"] = body
        if kind != "json":
            cfg["content_type"] = kind
    if other_query:
        cfg["query"] = other_query
    if sample is None:
        raise ValueError("could not find the question inside that request. Pass the example question text as `sample`.")
    if "{{input}}" not in json.dumps({"u": cfg["url"], "b": body, "q": other_query}):
        raise ValueError(f"the sample text {sample!r} was not found in the request. Check it matches exactly.")
    return cfg, sorted(used)


def query_string(params: dict) -> str:
    return urlencode(params)
