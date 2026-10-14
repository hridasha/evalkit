"""A very small TOML writer, enough for a [target] block (strings, numbers, booleans, lists, inline tables)."""

from __future__ import annotations

import json
import re
from typing import Any

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON string escapes are valid TOML basic strings
    if isinstance(v, list):
        return "[" + ", ".join(value(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{key(k)} = {value(x)}" for k, x in v.items()) + " }"
    if v is None:
        return '""'
    raise TypeError(f"cannot write {type(v).__name__} to TOML")


def key(k: str) -> str:
    return k if _BARE.match(k) else json.dumps(k)


def table(name: str, data: dict) -> str:
    lines = [f"[{name}]"]
    lines += [f"{key(k)} = {value(v)}" for k, v in data.items() if v is not None]
    return "\n".join(lines) + "\n"
