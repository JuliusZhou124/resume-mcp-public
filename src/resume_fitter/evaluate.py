"""Heuristic, deterministic scoring for resume bullets (CONCEPT.md scoring schema).

No LLM calls. ``evaluate_bullet()`` scores a single bullet's XYZ structure,
specificity, and verbosity via regex/keyword heuristics.
``compare_truth_risk()`` compares an original bullet against a candidate
rewrite and flags newly introduced numbers/entities that the rewrite didn't
have support for in the source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_METRIC_RE = re.compile(r"\$?\d[\d,.]*\s*(?:%|x\b|\+)?", re.IGNORECASE)

_RESULT_MARKERS = {
    "by", "to", "for", "resulting", "reducing", "increasing", "improving",
    "enabling", "across", "through", "via",
}

_VAGUE_WORDS = {
    "various", "multiple", "several", "many", "things", "stuff", "helped",
    "worked", "assist", "assisted", "responsible", "tasks", "etc",
}

_IDEAL_WORD_RANGE = (12, 30)


@dataclass
class BulletEvaluation:
    word_count: int
    has_action_verb: bool
    has_metric: bool
    has_result_clause: bool
    xyz_score: float
    specificity_score: float
    verbosity_score: float


def _starts_with_action_verb(first_word: str) -> bool:
    word = first_word.strip(".,:;").lower()
    return word.endswith("ed") or word.endswith("ing")


def _extract_metrics(text: str) -> set[str]:
    return {m.group(0).strip() for m in _METRIC_RE.finditer(text) if any(c.isdigit() for c in m.group(0))}


def _extract_entities(text: str) -> set[str]:
    """Capitalized tokens not at the start of a sentence (proper nouns, tools)."""
    words = text.split()
    entities = set()
    for i, word in enumerate(words):
        stripped = word.strip(".,:;()")
        if not stripped:
            continue
        if i == 0:
            continue
        if stripped[0].isupper():
            entities.add(stripped)
    return entities


def evaluate_bullet(text: str) -> BulletEvaluation:
    """Score a bullet's XYZ structure, specificity, and verbosity (0-1 scales)."""
    words = text.split()
    word_count = len(words)

    has_action_verb = bool(words) and _starts_with_action_verb(words[0])
    has_metric = bool(_extract_metrics(text))

    lowered_words = {w.strip(".,:;()").lower() for w in words}
    has_result_clause = bool(lowered_words & _RESULT_MARKERS)

    xyz_score = sum((has_action_verb, has_metric, has_result_clause)) / 3

    specific_count = len(_extract_metrics(text)) + len(_extract_entities(text))
    vague_count = len(lowered_words & _VAGUE_WORDS)
    specificity_score = max(0.0, min(1.0, 0.5 + 0.1 * specific_count - 0.2 * vague_count))

    low, high = _IDEAL_WORD_RANGE
    if low <= word_count <= high:
        verbosity_score = 1.0
    elif word_count < low:
        verbosity_score = max(0.0, word_count / low)
    else:
        verbosity_score = max(0.0, 1 - (word_count - high) / high)

    return BulletEvaluation(
        word_count=word_count,
        has_action_verb=has_action_verb,
        has_metric=has_metric,
        has_result_clause=has_result_clause,
        xyz_score=xyz_score,
        specificity_score=specificity_score,
        verbosity_score=verbosity_score,
    )


@dataclass
class TruthRiskResult:
    truth_risk: str  # "low" | "medium" | "high"
    changed_entities: list[str]


def compare_truth_risk(original: str, candidate: str) -> TruthRiskResult:
    """Flag numbers/entities present in ``candidate`` but absent from ``original``."""
    new_metrics = _extract_metrics(candidate) - _extract_metrics(original)
    new_entities = _extract_entities(candidate) - _extract_entities(original)

    changed_entities = sorted(new_metrics | new_entities)

    if new_metrics:
        truth_risk = "high"
    elif new_entities:
        truth_risk = "medium"
    else:
        truth_risk = "low"

    return TruthRiskResult(truth_risk=truth_risk, changed_entities=changed_entities)
