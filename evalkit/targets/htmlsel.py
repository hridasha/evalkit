"""A tiny CSS-selector reader for HTML replies (standard library only).

Supports: `tag`, `.class`, `#id`, `[attr]`, `[attr=value]`, combined (`div.msg.bot`) and descendants (`#chat .reply p`).
Enough to read the answer out of a normal server-rendered page.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP_TEXT = {"script", "style", "noscript", "template"}


class Node:
    def __init__(self, tag: str, attrs: dict[str, str | None] | None = None, parent: Node | None = None):
        self.tag, self.attrs, self.parent = tag, {k: (v or "") for k, v in (attrs or {}).items()}, parent
        self.children: list[Node | str] = []

    def text(self) -> str:
        parts: list[str] = []

        def walk(n: Node) -> None:
            for c in n.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in SKIP_TEXT:
                    walk(c)

        walk(self)
        return " ".join(" ".join(parts).split())


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent or self.root
        if n is not self.root and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if self.cur.tag not in SKIP_TEXT and data.strip():
            self.cur.children.append(data)


def parse_html(html: str) -> Node:
    p = _Parser()
    p.feed(html)
    p.close()
    return p.root


_COMPOUND_PART = re.compile(r"([#.][\w-]+|\[[^\]]+\])")


def _parse_compound(text: str) -> dict:
    m = re.match(r"^(\*|[a-zA-Z][\w-]*)?", text)
    tag = m.group(1) if m and m.group(1) not in (None, "*") else None
    rest = text[m.end() if m else 0 :]
    parts = _COMPOUND_PART.findall(rest)
    if "".join(parts) != rest:
        raise ValueError(f"cannot understand the selector part '{text}'")
    comp = {"tag": tag, "id": None, "classes": [], "attrs": []}
    for part in parts:
        if part[0] == "#":
            comp["id"] = part[1:]
        elif part[0] == ".":
            comp["classes"].append(part[1:])
        else:
            name, eq, value = part[1:-1].partition("=")
            comp["attrs"].append((name.strip(), value.strip().strip("'\"") if eq else None))
    return comp


def _matches(node: Node, comp: dict) -> bool:
    if comp["tag"] and node.tag != comp["tag"]:
        return False
    if comp["id"] and node.attrs.get("id") != comp["id"]:
        return False
    classes = node.attrs.get("class", "").split()
    if any(c not in classes for c in comp["classes"]):
        return False
    return all(n in node.attrs and (v is None or node.attrs[n] == v) for n, v in comp["attrs"])


def _descendants(node: Node):
    for c in node.children:
        if isinstance(c, Node):
            yield c
            yield from _descendants(c)


def select(root: Node, selector: str) -> list[Node]:
    chain = [_parse_compound(p) for p in selector.split()]
    if not chain:
        raise ValueError("the selector is empty")
    current = [n for n in _descendants(root) if _matches(n, chain[0])]
    for comp in chain[1:]:
        nxt: list[Node] = []
        seen: set[int] = set()
        for parent in current:
            for n in _descendants(parent):
                if id(n) not in seen and _matches(n, comp):
                    seen.add(id(n))
                    nxt.append(n)
        current = nxt
    return current


def select_text(html: str, selector: str) -> list[str]:
    return [t for n in select(parse_html(html), selector) if (t := n.text())]
