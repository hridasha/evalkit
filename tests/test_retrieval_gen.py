import json
from pathlib import Path

import pytest

from evalkit.core.models import TestCase
from evalkit.core.testset import load_testset
from evalkit.gen import generate_testset, redteam_cases
from evalkit.llm.base import FakeLLM, HashEmbedder
from evalkit.retrieval.index import BM25, Document, Index, chunk_documents, chunk_text, load_documents
from evalkit.retrieval.sweep import sweep

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo_rag"

DOCS = [
    Document("refunds", "# Refunds\n\nItems can be returned within 30 days for a full refund. Refunds take 7 days."),
    Document(
        "shipping", "# Shipping\n\nStandard shipping costs $4.99 and takes 5 days. Express shipping costs $14.99."
    ),
    Document("warranty", "# Warranty\n\nProducts have a 2 year warranty against defects."),
]


# ---------------------------------------------------------------------------------------------- chunking
def test_chunk_text_respects_size_and_keeps_headings_apart():
    text = "# Title\n\n" + " ".join(f"Sentence number {i} is here." for i in range(30))
    chunks = chunk_text(text, size=120, overlap=20)
    assert len(chunks) > 3 and all(len(c) <= 260 for c in chunks)
    assert chunks[0].startswith("# Title\n")
    with pytest.raises(ValueError):
        chunk_text("x", size=5)
    assert chunk_text("tiny") == ["tiny"]


def test_chunk_documents_keep_their_document_id():
    chunks = chunk_documents(DOCS, size=60, overlap=10)
    assert {c.doc_id for c in chunks} == {"refunds", "shipping", "warranty"} and all(c.id == c.doc_id for c in chunks)


def test_load_documents(tmp_path):
    (tmp_path / "a.md").write_text("hello world")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("more text")
    (tmp_path / "skip.png").write_bytes(b"x")
    assert [d.id for d in load_documents(tmp_path)] == ["a", "sub/b"]
    with pytest.raises(FileNotFoundError):
        load_documents(tmp_path / "nope")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="no .txt"):
        load_documents(tmp_path / "empty")


# ---------------------------------------------------------------------------------------------- search
def test_bm25_prefers_rare_matching_words():
    bm = BM25(["the cat sat", "the dog barked loudly", "a cat and a dog"])
    s = bm.scores("barked")
    assert s[1] > s[0] and s[1] > s[2] and bm.scores("zzz") == [0, 0, 0]


@pytest.mark.parametrize("method", ["bm25", "embed", "hybrid"])
async def test_every_search_method_finds_the_right_document(method):
    index = await Index(chunk_documents(DOCS, 200, 20), method, HashEmbedder()).build()
    hits = await index.search("how much does express shipping cost", 2)
    assert hits[0].chunk.doc_id == "shipping" and len(hits) <= 2
    assert (await index.search("2 year warranty defects", 1))[0].chunk.doc_id == "warranty"


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError):
        Index([], "magic")


async def test_sweep_ranks_setups_and_needs_relevant_ids():
    cases = [TestCase("a", "how long to get a refund", relevant=["refunds"]), TestCase("b", "express shipping price", relevant=["shipping"]),
             TestCase("c", "warranty length", relevant=["warranty"])]  # fmt: skip
    rows = await sweep(DOCS, cases, methods=["bm25", "hybrid"], chunk_sizes=[100, 300], k=2)
    assert len(rows) == 4 and rows[0].mrr >= rows[-1].mrr and all(0 <= r.hit_rate <= 1 for r in rows)
    assert rows[0].mrr > 0.8
    with pytest.raises(ValueError, match="relevant"):
        await sweep(DOCS, [TestCase("z", "q")], methods=["bm25"], chunk_sizes=[100])


async def test_the_demo_documents_are_findable():
    cases = [c for c in load_testset(DEMO / "testset.jsonl") if c.relevant and len(c.relevant) == 1]
    rows = await sweep(load_documents(DEMO / "docs"), cases, methods=["hybrid"], chunk_sizes=[400], k=4)
    assert rows[0].hit_rate >= 0.9


# ---------------------------------------------------------------------------------------------- generation
def gen_reply(prompt: str) -> str:
    if "do NOT answer" in prompt or "does NOT answer" in prompt:
        return json.dumps({"items": [{"question": "Do you sell tents on the moon?"}]})
    if "BOTH documents" in prompt:
        return json.dumps({"items": [{"question": "Combined?", "answer": "Both."}]})
    if "Rewrite each question" in prompt:
        return json.dumps({"items": [["Other words?", "Again?"]] * 10})
    return json.dumps(
        {
            "items": [
                {"question": f"Q about {prompt[prompt.index('Document ') + 10 :][:7]}?", "answer": "A"},
                {"question": "", "answer": "x"},
            ]
        }
    )


async def test_generate_testset_makes_all_kinds_of_questions():
    cases = await generate_testset(
        DOCS, FakeLLM(gen_reply), per_doc=2, multi_doc=2, unanswerable_per_doc=1, paraphrases=2
    )
    tags = [t for c in cases for t in c.tags]
    assert tags.count("single") == 3 and tags.count("unanswerable") == 3 and tags.count("multi-doc") == 2
    single = next(c for c in cases if "single" in c.tags)
    assert single.relevant and single.expected == "A" and single.paraphrases == ["Other words?", "Again?"]
    multi = next(c for c in cases if "multi-doc" in c.tags)
    assert len(multi.relevant) == 2
    assert next(c for c in cases if "unanswerable" in c.tags).should_abstain
    assert len({c.id for c in cases}) == len(cases)


async def test_generate_survives_bad_items():
    llm = FakeLLM(json.dumps({"items": ["junk", {"question": "ok?"}, 5]}))
    assert await generate_testset(DOCS[:1], llm, per_doc=2, unanswerable_per_doc=0) == []


def test_redteam_cases_all_expect_a_refusal():
    cases = redteam_cases()
    assert len(cases) >= 10 and all(c.should_abstain and "redteam" in c.tags for c in cases)
    assert len({c.id for c in cases}) == len(cases)
