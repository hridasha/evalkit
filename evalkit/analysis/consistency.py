"""Does the app give the same answer when asked again, or asked in different words?"""

from __future__ import annotations

from collections import Counter, defaultdict

from evalkit.analysis.stats import mean
from evalkit.core.models import CaseResult
from evalkit.llm.base import Embedder, HashEmbedder, cosine
from evalkit.scorers.base import normalize


async def consistency_report(
    results: list[CaseResult], embedder: Embedder | None = None, unstable_below: float = 0.7
) -> dict:
    """Group results by question (repeats and paraphrases share a base id) and measure how much answers agree.

    - agreement: share of answers equal to the most common answer (1.0 = always the same).
    - similarity: average meaning-similarity between every pair of answers.
    - flip: the question passes sometimes and fails other times (a flaky answer).
    """
    embedder = embedder or HashEmbedder()
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        if not r.output.error:
            groups[r.case_id.split("~")[0]].append(r)
    rows = []
    for cid, rs in sorted(groups.items()):
        if len(rs) < 2:
            continue
        answers = [normalize(r.output.answer) for r in rs]
        top = Counter(answers).most_common(1)[0][1]
        vecs = await embedder.embed([r.output.answer for r in rs])
        sims = [cosine(vecs[i], vecs[j]) for i in range(len(vecs)) for j in range(i + 1, len(vecs))]
        verdicts = {r.passed for r in rs}
        rows.append({
            "case_id": cid, "answers": len(rs), "distinct": len(set(answers)), "agreement": top / len(rs),
            "similarity": mean(sims), "flip": len(verdicts) > 1,
            "sample": [r.output.answer[:160] for r in rs[:3]],
        })  # fmt: skip
    for row in rows:
        row["unstable"] = row["flip"] or row["similarity"] < unstable_below
    return {
        "questions": len(rows),
        "mean_agreement": mean([r["agreement"] for r in rows]),
        "mean_similarity": mean([r["similarity"] for r in rows]),
        "flaky": sum(1 for r in rows if r["flip"]),
        "unstable": [r["case_id"] for r in rows if r["unstable"]],
        "rows": sorted(rows, key=lambda r: (r["similarity"], r["agreement"])),
    }
