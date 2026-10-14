"""Small statistics helpers, written with the standard library only."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def wilson(passes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a pass rate; sensible even with few cases."""
    if n == 0:
        return (0.0, 1.0)
    p = passes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_diff(a: Sequence[float], b: Sequence[float], rounds: int = 4000, seed: int = 7) -> dict:
    """Paired bootstrap of mean(b - a). a and b are the scores of the same cases under two versions."""
    if len(a) != len(b) or not a:
        raise ValueError("need the same, non-zero number of paired scores")
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds))
    observed = sum(diffs) / n
    lo, hi = means[int(0.025 * rounds)], means[int(0.975 * rounds) - 1]
    # two-sided p-value: how often does the resampled mean land on the other side of zero?
    below = sum(1 for m in means if m <= 0) / rounds
    above = sum(1 for m in means if m >= 0) / rounds
    return {"diff": observed, "ci_low": lo, "ci_high": hi, "p_value": min(1.0, 2 * min(below, above))}


def mcnemar_exact(a_pass: Sequence[bool], b_pass: Sequence[bool]) -> dict:
    """Exact McNemar test for paired pass/fail results. Only the cases where the versions disagree matter."""
    only_a = sum(1 for x, y in zip(a_pass, b_pass, strict=True) if x and not y)
    only_b = sum(1 for x, y in zip(a_pass, b_pass, strict=True) if y and not x)
    n = only_a + only_b
    if n == 0:
        return {"only_a": 0, "only_b": 0, "p_value": 1.0}
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return {"only_a": only_a, "only_b": only_b, "p_value": min(1.0, 2 * tail)}


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """Agreement between two judges (e.g. the LLM judge and a human), corrected for chance."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def macro_f1(truth: Sequence[str], predicted: Sequence[str]) -> dict:
    labels = sorted(set(truth) | set(predicted))
    per = {}
    for label in labels:
        tp = sum(1 for t, p in zip(truth, predicted, strict=True) if t == label and p == label)
        fp = sum(1 for t, p in zip(truth, predicted, strict=True) if t != label and p == label)
        fn = sum(1 for t, p in zip(truth, predicted, strict=True) if t == label and p != label)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        per[label] = {"precision": prec, "recall": rec, "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
                      "support": tp + fn}  # fmt: skip
    return {
        "accuracy": sum(1 for t, p in zip(truth, predicted, strict=True) if t == p) / len(truth) if truth else 0.0,
        "macro_f1": sum(v["f1"] for v in per.values()) / len(per) if per else 0.0,
        "per_label": per,
    }
