"""Make a test set from your documents with an LLM: ordinary, two-document and unanswerable questions."""

from __future__ import annotations

import itertools
import random

from evalkit.core.models import TestCase
from evalkit.llm.base import LLM
from evalkit.retrieval.index import Document
from evalkit.scorers.judge import JUDGE_SYSTEM, parse_json

MAX_DOC_CHARS = 6000

SINGLE = """Read the document and write {n} question-and-answer pairs that a user of a help-desk bot might ask.

Rules:
- Each question must be fully answerable from this document alone, and make sense without seeing the document
  (never say "the document" or "the text").
- Answers are short (one or two sentences) and copy the facts from the document.
- Mix easy lookups with questions that need a number, date or condition.

Document "{doc_id}":
{text}

Reply as JSON: {{"items": [{{"question": "...", "answer": "..."}}]}}"""

MULTI = """Read two documents and write {n} question-and-answer pair(s) that can ONLY be answered by combining facts
from BOTH documents. The question must make sense without seeing the documents.

Document "{a_id}":
{a_text}

Document "{b_id}":
{b_text}

Reply as JSON: {{"items": [{{"question": "...", "answer": "..."}}]}}"""

UNANSWERABLE = """Read the document and write {n} realistic questions on the SAME TOPIC that this document does NOT answer
(the answer is not stated anywhere in it, and cannot be worked out from it). The questions must sound natural and
must not mention the document.

Document "{doc_id}":
{text}

Reply as JSON: {{"items": [{{"question": "..."}}]}}"""

PARAPHRASE = """Rewrite each question in {n} different ways that keep the exact meaning (different words and sentence
shapes; keep any names and numbers). Reply as JSON: {{"items": [["rewrite 1", "rewrite 2"], ...]}} with one list per
question, in the same order.

Questions:
{questions}"""


def _items(reply: str) -> list:
    data = parse_json(reply)
    items = data.get("items", [])
    return items if isinstance(items, list) else []


async def generate_testset(
    docs: list[Document],
    llm: LLM,
    *,
    per_doc: int = 3,
    multi_doc: int = 0,
    unanswerable_per_doc: int = 1,
    paraphrases: int = 0,
    seed: int = 7,
    max_docs: int | None = None,
) -> list[TestCase]:
    rng = random.Random(seed)
    chosen = docs[:max_docs] if max_docs else docs
    cases: list[TestCase] = []

    def add(**kw) -> None:
        cases.append(TestCase(id=f"gen-{len(cases) + 1:03d}", **kw))

    for d in chosen:
        text = d.text[:MAX_DOC_CHARS]
        for it in _items(
            await llm.complete(SINGLE.format(n=per_doc, doc_id=d.id, text=text), system=JUDGE_SYSTEM, json_mode=True)
        ):
            if isinstance(it, dict) and it.get("question") and it.get("answer"):
                add(input=str(it["question"]), expected=str(it["answer"]), relevant=[d.id], tags=["single"])
        if unanswerable_per_doc:
            for it in _items(await llm.complete(UNANSWERABLE.format(n=unanswerable_per_doc, doc_id=d.id, text=text),
                                                system=JUDGE_SYSTEM, json_mode=True)):  # fmt: skip
                q = it.get("question") if isinstance(it, dict) else it
                if q:
                    add(input=str(q), should_abstain=True, tags=["unanswerable"], meta={"topic_doc": d.id})

    pairs = list(itertools.combinations(chosen, 2))
    rng.shuffle(pairs)
    for a, b in pairs[:multi_doc]:
        reply = await llm.complete(MULTI.format(n=1, a_id=a.id, a_text=a.text[:MAX_DOC_CHARS // 2], b_id=b.id,
                                                b_text=b.text[:MAX_DOC_CHARS // 2]), system=JUDGE_SYSTEM, json_mode=True)  # fmt: skip
        for it in _items(reply)[:1]:
            if isinstance(it, dict) and it.get("question") and it.get("answer"):
                add(input=str(it["question"]), expected=str(it["answer"]), relevant=[a.id, b.id], tags=["multi-doc"])

    if paraphrases and cases:
        answerable = [c for c in cases if not c.should_abstain]
        for i in range(0, len(answerable), 10):
            batch = answerable[i : i + 10]
            listing = "\n".join(f"{n}. {c.input}" for n, c in enumerate(batch, 1))
            lists = _items(
                await llm.complete(
                    PARAPHRASE.format(n=paraphrases, questions=listing), system=JUDGE_SYSTEM, json_mode=True
                )
            )
            for c, rewrites in zip(batch, lists, strict=False):
                if isinstance(rewrites, list):
                    c.paraphrases = [str(r) for r in rewrites[:paraphrases] if r]
    return cases
