"""Retrieval scorecard: try several retrieval setups on your documents and questions, and rank them."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from evalkit.core.models import TestCase
from evalkit.llm.base import Embedder
from evalkit.retrieval.index import Document, Index, chunk_documents


@dataclass
class SweepRow:
    method: str
    chunk_size: int
    overlap: int
    embedder: str
    k: int
    questions: int
    hit_rate: float
    mrr: float
    recall: float
    chunks: int

    def to_dict(self) -> dict:
        return self.__dict__.copy()


async def evaluate_setup(
    docs: list[Document], cases: list[TestCase], *, method: str, chunk_size: int, overlap: int, k: int,
    embedder: Embedder, embedder_name: str = "hash",
) -> SweepRow:  # fmt: skip
    chunks = chunk_documents(docs, chunk_size, overlap)
    index = await Index(chunks, method, embedder).build()
    scored = [c for c in cases if c.relevant]
    if not scored:
        raise ValueError("no test case lists `relevant` documents, so retrieval cannot be scored")
    hits = rr_sum = rec_sum = 0.0
    for case in scored:
        found = [h.chunk.doc_id for h in await index.search(case.input, k)]
        wanted = set(case.relevant)
        hits += 1.0 if wanted & set(found) else 0.0
        rr_sum += next((1 / r for r, d in enumerate(found, 1) if d in wanted), 0.0)
        rec_sum += len(wanted & set(found)) / len(wanted)
    n = len(scored)
    return SweepRow(method, chunk_size, overlap, embedder_name, k, n, hits / n, rr_sum / n, rec_sum / n, len(chunks))


async def sweep(
    docs: list[Document],
    cases: list[TestCase],
    *,
    methods: list[str],
    chunk_sizes: list[int],
    overlap: int = 50,
    k: int = 5,
    embedders: dict[str, Embedder] | None = None,
) -> list[SweepRow]:
    """Every combination of method x chunk size (x embedder), best first by MRR then hit rate."""
    from evalkit.llm.base import HashEmbedder

    embedders = embedders or {"hash": HashEmbedder()}
    rows = []
    for method, size, (ename, emb) in itertools.product(methods, chunk_sizes, embedders.items()):
        if method == "bm25" and ename != next(iter(embedders)):
            continue  # keyword search does not use an embedder; run it once
        rows.append(await evaluate_setup(docs, cases, method=method, chunk_size=size, overlap=overlap, k=k, embedder=emb,
                                         embedder_name="-" if method == "bm25" else ename))  # fmt: skip
    return sorted(rows, key=lambda r: (-r.mrr, -r.hit_rate, -r.recall))
