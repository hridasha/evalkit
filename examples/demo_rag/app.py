"""A small document Q&A app to practise on. Run it, then point EvalKit at it:

    python -m uvicorn examples.demo_rag.app:app --port 8100

Its settings are environment variables, so you can start a better and a worse version and compare them:

    RAG_METHOD    bm25 | embed | hybrid            (default hybrid)
    RAG_CHUNK     characters per chunk            (default 400)
    RAG_TOP_K     chunks given to the answerer    (default 4)
    RAG_ANSWERER  extractive | gemini             (default extractive: offline, no key needed)
    RAG_MIN_OVERLAP  how much of the question a sentence must share to be used (default 0.4; lower = answers more)
    RAG_SENTENCES    how many sentences to return (default 1)
    RAG_VERSION   a label shown in /health
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel

from evalkit.llm import GeminiEmbedder, GeminiLLM, HashEmbedder
from evalkit.llm.base import tokenize
from evalkit.retrieval.index import Index, chunk_documents, load_documents
from evalkit.scorers.judge import sentences

DOCS = Path(os.getenv("RAG_DOCS", Path(__file__).parent / "docs"))
STOP = set(
    [
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "for",
        "with",
        "as",
        "by",
        "at",
        "it",
        "this",
        "that",
        "from",
        "do",
        "does",
        "did",
        "i",
        "you",
        "my",
        "me",
        "we",
        "your",
        "our",
        "can",
        "could",
        "would",
        "should",
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "much",
        "many",
        "long",
        "if",
        "any",
        "there",
        "their",
        "about",
    ]
)
NO_ANSWER = "I don't know based on the documents."


class Ask(BaseModel):
    question: str


def stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def content_words(text: str) -> list[str]:
    return [stem(w) for w in tokenize(text) if w not in STOP]


def extractive_answer(question: str, contexts: list[str], min_overlap: float = 0.4, n_sentences: int = 1) -> str:
    """Pick the document sentence(s) sharing the most words with the question; decline if none shares enough."""
    q = set(content_words(question))
    if not q:
        return NO_ANSWER
    scored = []
    for rank, ctx in enumerate(contexts):
        for s in sentences(ctx):
            scored.append((len(q & set(content_words(s))) / len(q), -rank, s))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    if not scored or scored[0][0] < min_overlap:
        return NO_ANSWER
    keep = [s for score, _, s in scored[:n_sentences] if score >= min_overlap]
    return " ".join(dict.fromkeys(keep))


def build_app() -> FastAPI:
    method = os.getenv("RAG_METHOD", "hybrid")
    chunk = int(os.getenv("RAG_CHUNK", "400"))
    top_k = int(os.getenv("RAG_TOP_K", "4"))
    answerer = os.getenv("RAG_ANSWERER", "extractive")
    min_overlap = float(os.getenv("RAG_MIN_OVERLAP", "0.4"))
    n_sentences = int(os.getenv("RAG_SENTENCES", "1"))
    version = os.getenv("RAG_VERSION", f"{method}-{chunk}-k{top_k}-{answerer}")
    state: dict = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        embedder = GeminiEmbedder() if os.getenv("RAG_EMBEDDER") == "gemini" else HashEmbedder()
        chunks = chunk_documents(load_documents(DOCS), chunk, overlap=min(50, chunk // 4))
        state["index"] = await Index(chunks, method, embedder).build()
        state["llm"] = GeminiLLM() if answerer == "gemini" else None
        yield

    app = FastAPI(title="Demo RAG", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"ok": True, "version": version, "method": method, "chunk": chunk, "top_k": top_k, "answerer": answerer}

    @app.post("/ask")
    async def ask(body: Ask):
        hits = await state["index"].search(body.question, top_k)
        contexts = [{"id": h.chunk.doc_id, "text": h.chunk.text, "score": round(h.score, 4)} for h in hits]
        texts = [c["text"] for c in contexts]
        if state["llm"]:
            prompt = (
                "Answer the question using ONLY the context. If the context does not contain the answer, reply exactly: "
                f"{NO_ANSWER}\n\nContext:\n" + "\n---\n".join(texts) + f"\n\nQuestion: {body.question}\nAnswer briefly."
            )
            llm = state["llm"]
            before_in, before_out = llm.usage.tokens_in, llm.usage.tokens_out
            answer = (await llm.complete(prompt)).strip()
            tin, tout = llm.usage.tokens_in - before_in, llm.usage.tokens_out - before_out
        else:
            answer = extractive_answer(body.question, texts, min_overlap, n_sentences)
            tin = len(re.findall(r"\S+", body.question + " ".join(texts))) * 4 // 3  # rough token estimate
            tout = len(answer.split()) * 4 // 3
        return {"answer": answer, "contexts": contexts, "usage": {"input_tokens": tin, "output_tokens": tout}}

    return app


app = build_app()
