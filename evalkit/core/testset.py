"""Reading and writing test sets (JSONL or CSV)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from evalkit.core.models import TestCase

LIST_COLUMNS = ("relevant", "tags", "paraphrases")


def _split(value: str) -> list[str]:
    return [p.strip() for p in value.split("|") if p.strip()]


def parse_testset(text: str, fmt: str = "jsonl") -> list[TestCase]:
    cases: list[TestCase] = []
    if fmt == "csv":
        for n, row in enumerate(csv.DictReader(io.StringIO(text)), 1):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for col in LIST_COLUMNS:
                row[col] = _split(row.get(col, ""))
            row["should_abstain"] = row.get("should_abstain", "").lower() in {"1", "true", "yes", "y"}
            row["expected"] = row.get("expected") or None
            cases.append(TestCase.from_dict(row, fallback_id=f"case-{n}"))
    else:
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"line {n} is not valid JSON: {exc}") from None
            cases.append(TestCase.from_dict(data, fallback_id=f"case-{len(cases) + 1}"))
    ids = [c.id for c in cases]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate test case ids: {', '.join(dupes[:5])}")
    if not cases:
        raise ValueError("the test set is empty")
    return cases


def load_testset(path: str | Path) -> list[TestCase]:
    p = Path(path)
    return parse_testset(p.read_text(encoding="utf-8-sig"), "csv" if p.suffix.lower() == ".csv" else "jsonl")


def dump_testset(cases: list[TestCase]) -> str:
    return (
        "\n".join(json.dumps({k: v for k, v in c.to_dict().items() if v not in (None, [], {}, False)}) for c in cases)
        + "\n"
    )


def save_testset(path: str | Path, cases: list[TestCase]) -> None:
    Path(path).write_text(dump_testset(cases), encoding="utf-8")
