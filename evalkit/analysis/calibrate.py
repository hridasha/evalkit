"""Is the LLM judge trustworthy? Compare it with a human on a small labelled sample."""

from __future__ import annotations

import csv
import io

from evalkit.analysis.stats import cohen_kappa, wilson
from evalkit.core.models import Output, TestCase
from evalkit.scorers.base import Scorer, Services


def parse_labels(text: str) -> list[dict]:
    """CSV with columns: input, answer, expected (optional), human_pass (yes/no/1/0/true/false)."""
    rows = []
    for n, row in enumerate(csv.DictReader(io.StringIO(text)), 2):
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        label = row.get("human_pass", "").lower()
        if label not in {"yes", "no", "1", "0", "true", "false", "y", "n"}:
            raise ValueError(f"row {n}: human_pass must be yes/no, got '{label}'")
        if not row.get("input") or not row.get("answer"):
            raise ValueError(f"row {n}: needs both 'input' and 'answer'")
        rows.append({**row, "human": label in {"yes", "1", "true", "y"}})
    if not rows:
        raise ValueError("the label file has no rows")
    return rows


async def calibrate_judge(rows: list[dict], judge: Scorer, services: Services) -> dict:
    humans, judges, detail = [], [], []
    for n, row in enumerate(rows):
        case = TestCase(id=f"cal-{n + 1}", input=row["input"], expected=row.get("expected") or None)
        score = await judge.score(case, Output(answer=row["answer"]), services)
        if score.passed is None:
            continue
        humans.append(row["human"])
        judges.append(score.passed)
        detail.append({"input": row["input"], "human": row["human"], "judge": score.passed, "reason": score.detail})
    if not humans:
        raise ValueError("the judge could not grade any row")
    agree = sum(1 for h, j in zip(humans, judges, strict=True) if h == j)
    kappa = cohen_kappa(humans, judges)
    low, high = wilson(agree, len(humans))
    strict = sum(1 for h, j in zip(humans, judges, strict=True) if j and not h)
    lenient = sum(1 for h, j in zip(humans, judges, strict=True) if h and not j)
    verdict = (
        "trustworthy"
        if kappa is not None and kappa >= 0.6
        else "use with care"
        if kappa is not None and kappa >= 0.4
        else "not reliable"
    )
    return {
        "n": len(humans), "agreement": agree / len(humans), "agreement_ci": [low, high], "kappa": kappa,
        "judge_too_generous": strict, "judge_too_harsh": lenient, "verdict": verdict, "rows": detail,
    }  # fmt: skip
