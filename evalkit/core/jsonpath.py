"""Tiny path language for pulling a value out of a JSON answer: `data.answer`, `choices[0].text`, `docs[].text`."""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d*)\]")


def extract(data: Any, path: str | None) -> Any:
    """Follow `path`. `[]` fans out over a list. Returns None when something on the way is missing."""
    if not path:
        return data
    current = [data]
    fanned = False
    for name, index in _TOKEN.findall(path):
        nxt: list[Any] = []
        for item in current:
            if name:
                if isinstance(item, dict) and name in item:
                    nxt.append(item[name])
            elif index == "":
                if isinstance(item, list):
                    nxt.extend(item)
                    fanned = True
            elif isinstance(item, list) and int(index) < len(item):
                nxt.append(item[int(index)])
        current = nxt
        if not current:
            return None
    return current if fanned else current[0]


def fill(template: Any, **values: str) -> Any:
    """Copy a JSON template, replacing `{{name}}` in every string."""
    if isinstance(template, str):
        whole = re.fullmatch(r"\{\{(\w+)\}\}", template)
        if whole and whole.group(1) in values:
            return values[whole.group(1)]
        return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values.get(m.group(1), m.group(0))), template)
    if isinstance(template, dict):
        return {k: fill(v, **values) for k, v in template.items()}
    if isinstance(template, list):
        return [fill(v, **values) for v in template]
    return template
