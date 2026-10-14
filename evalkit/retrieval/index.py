"""A small search engine over documents: chunking, keyword search (BM25), meaning search (embeddings) and a mix.

It is used by the retrieval scorecard and by the demo RAG app, so EvalKit can test retrieval choices on your data.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from evalkit.llm.base import Embedder, HashEmbedder, cosine, tokenize

DOC_SUFFIXES = {".txt", ".md", ".markdown", ".rst"}


@dataclass
class Document:
    id: str
    text: str


@dataclass
class Chunk:
    doc_id: str
    text: str
    n: int  # position inside the document

    @property
    def id(self) -> str:
        return self.doc_id


def load_documents(folder: str | Path) -> list[Document]:
    root = Path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a folder")
    docs = [
        Document(p.relative_to(root).with_suffix("").as_posix(), p.read_text(encoding="utf-8-sig", errors="replace"))
        for p in sorted(root.rglob("*"))
        if p.suffix.lower() in DOC_SUFFIXES and p.is_file()
    ]
    docs = [d for d in docs if d.text.strip()]
    if not docs:
        raise ValueError(f"no .txt or .md documents found in {root}")
    return docs


def chunk_text(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    """Split on paragraph/sentence edges into pieces of about `size` characters, overlapping a little."""
    if size < 20:
        raise ValueError("chunk size must be at least 20 characters")
    overlap = max(0, min(overlap, size // 2))
    pieces = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if s.strip()]
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > size:
            chunks.append(current)
            current = (current[-overlap:].split(" ", 1)[-1] + " " + piece) if overlap else piece
        else:
            current = f"{current}{'' if current.endswith(chr(10)) else ' '}{piece}".strip()
        if piece.startswith("#"):  # keep a heading on its own line so it never sticks to the next sentence
            current += "\n"
        while len(current) > size * 2:  # one huge sentence
            chunks.append(current[:size])
            current = current[size - overlap :]
    if current.strip():
        chunks.append(current)
    return chunks or [text.strip()]


def chunk_documents(docs: list[Document], size: int = 400, overlap: int = 50) -> list[Chunk]:
    return [Chunk(d.id, t, n) for d in docs for n, t in enumerate(chunk_text(d.text, size, overlap))]


class BM25:
    def __init__(self, texts: list[str], k1: float = 1.5, b: float = 0.75):
        self.docs = [tokenize(t) for t in texts]
        self.k1, self.b = k1, b
        self.avg = sum(len(d) for d in self.docs) / max(1, len(self.docs))
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(set(d))
        n = len(self.docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}
        self.tf = [Counter(d) for d in self.docs]

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, doc in zip(self.tf, self.docs, strict=True):
            norm = self.k1 * (1 - self.b + self.b * len(doc) / (self.avg or 1))
            out.append(sum(self.idf.get(w, 0) * tf[w] * (self.k1 + 1) / (tf[w] + norm) for w in q if w in tf))
        return out


@dataclass
class Hit:
    chunk: Chunk
    score: float


class Index:
    """method: 'bm25' (keywords), 'embed' (meaning) or 'hybrid' (both, merged by reciprocal rank fusion)."""

    def __init__(self, chunks: list[Chunk], method: str = "hybrid", embedder: Embedder | None = None):
        if method not in ("bm25", "embed", "hybrid"):
            raise ValueError("method must be bm25, embed or hybrid")
        self.chunks, self.method = chunks, method
        self.embedder = embedder or HashEmbedder()
        self.bm25 = BM25([c.text for c in chunks]) if method in ("bm25", "hybrid") else None
        self.vectors: list[list[float]] = []

    async def build(self) -> Index:
        if self.method in ("embed", "hybrid"):
            self.vectors = await self.embedder.embed([c.text for c in self.chunks])
        return self

    async def search(self, query: str, k: int = 5) -> list[Hit]:
        n = len(self.chunks)
        rankings: list[list[int]] = []
        raw: dict[int, float] = {}
        if self.bm25:
            s = self.bm25.scores(query)
            rankings.append(sorted(range(n), key=lambda i: -s[i]))
            raw.update({i: s[i] for i in range(n)})
        if self.method in ("embed", "hybrid"):
            [qv] = await self.embedder.embed([query])
            s2 = [cosine(qv, v) for v in self.vectors]
            rankings.append(sorted(range(n), key=lambda i: -s2[i]))
            if self.method == "embed":
                raw.update({i: s2[i] for i in range(n)})
        if len(rankings) == 1:
            order = rankings[0][:k]
            return [Hit(self.chunks[i], raw[i]) for i in order]
        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, i in enumerate(ranking[: max(50, k)], 1):
                fused[i] = fused.get(i, 0.0) + 1 / (60 + rank)
        return [Hit(self.chunks[i], s) for i, s in sorted(fused.items(), key=lambda kv: -kv[1])[:k]]
