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

_METRIC_RE = re.compile(r"\$?\d[\d,.]*\s*(?:\\?%|x\b|\+)?", re.IGNORECASE)

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
    # Normalize ``\%`` (LaTeX-escaped percent) to ``%`` so a candidate
    # written as ``40\%`` matches an original ``40%`` -- otherwise the
    # regex extracts bare ``40`` vs ``40%``, producing a spurious "high"
    # truth risk for an unchanged number.
    return {
        m.group(0).strip().replace(r"\%", "%")
        for m in _METRIC_RE.finditer(text)
        if any(c.isdigit() for c in m.group(0))
    }


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


@dataclass
class GroundingResult:
    """Result of checking whether a bullet's references are grounded in the resume.

    A bullet is 'self-grounding' if every proper noun / technical phrase it
    references appears somewhere else in the resume (in a role heading, another
    bullet, or the skills section).  Ungrounded terms reference context the
    reader doesn't have — e.g. 'load replay harness' mentioned in one bullet
    but never explained by the role or other bullets.
    """
    grounded: list[str]
    ungrounded: list[str]
    is_grounded: bool


# Common words that look like proper nouns (capitalized) but are actually
# generic terms, sentence-start words, or standard resume vocabulary that
# doesn't need grounding.
_GENERIC_CAPITALIZED = {
    # Days/months/seasons
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
    "Nov", "Dec", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
    "Saturday", "Sunday", "Spring", "Summer", "Fall", "Autumn", "Winter",
    # Standard resume acronyms/words
    "API", "APIs", "UI", "UX", "AI", "ML", "QA", "CI", "CD", "SDK", "CLI",
    "PR", "URL", "HTML", "CSS", "SQL", "REST", "ORM", "RPC", "GRPC",
    "PDF", "CSV", "JSON", "XML", "YAML", "TOML", "Docker", "Kubernetes",
    "Git", "React", "Node", "Redis", "MongoDB", "Postgres", "FastAPI",
    "NextJS", "NestJS", "Zod", "SWR", "TailwindCSS", "Vercel", "Gemini",
    "OpenAI", "Langchain", "Celery", "GridFS", "MUI", "Figma", "AWS",
    "GCP", "Azure", "Supabase", "Prisma", "Docker", "TypeScript",
    "JavaScript", "Python", "Java", "Swift", "Ruby", "Rust", "Go",
    "Solidity", "Viem", "NextJS", "React",
    # Common adjectives/words that happen to be capitalized
    "I", "We", "The", "A", "An", "This", "That", "These", "Those",
    "It", "They", "Our", "Their", "His", "Her", "Its",
    "Material", "Natural", "Social",
}

# Multi-word technical phrases to check: sequences of 2+ capitalized words,
# or known tech patterns like "gRPC X", "X harness", "X debug".
_MULTI_WORD_RE = re.compile(
    r"\b((?:[A-Z][a-z]+ )+[A-Z][a-z]+)\b"  # 2+ Capitalized Words
    r"|\b([a-z]+ [a-z]+ (?:harness|debug|pipeline|engine|system|service|layer|module))\b"
)


def _extract_reference_terms(text: str) -> set[str]:
    """Extract proper nouns and technical phrases that may need grounding.

    Returns multi-word capitalized phrases (e.g. 'Load Replay Harness') and
    single capitalized words that aren't generic vocabulary (e.g. 'GridFS').
    Filters out the first word of the sentence (action verb position) and
    common generic capitalized terms.
    """
    terms: set[str] = set()

    words = text.split()
    first_word = words[0].strip(".,:;()") if words else ""

    # Multi-word capitalized phrases — skip phrases that start with the
    # action verb (first word of the sentence, e.g. "Improved Node" from
    # "Improved Node.js API performance").
    for m in _MULTI_WORD_RE.finditer(text):
        phrase = m.group(0).strip(".,:;()")
        if not phrase:
            continue
        phrase_words = phrase.split()
        if phrase_words and phrase_words[0] == first_word:
            continue
        terms.add(phrase)

    # Single capitalized words (not at sentence start, not generic)
    words = text.split()
    for i, word in enumerate(words):
        stripped = word.strip(".,:;()")
        if not stripped or len(stripped) < 3:
            continue
        if i == 0:
            continue  # action verb position
        if stripped[0].isupper() and stripped not in _GENERIC_CAPITALIZED:
            terms.add(stripped)

    # Extract compact technical phrases ending in system-ish nouns.  Take at
    # most the two preceding tokens so phrases like "gRPC polling debug" and
    # "load replay harness" are flagged without swallowing generic lead-in
    # words like "tests for the ...".
    phrase_endings = {"harness", "debug", "pipeline", "engine", "system", "service", "layer", "module"}
    cleaned_tokens = [w.strip(".,:;()").strip() for w in words]
    for i, token in enumerate(cleaned_tokens):
        if token.lower() not in phrase_endings:
            continue
        start = max(0, i - 2)
        phrase_tokens = [t for t in cleaned_tokens[start : i + 1] if t]
        if len(phrase_tokens) >= 2:
            terms.add(" ".join(phrase_tokens))

    return terms


def check_grounding(
    candidate: str,
    resume_text: str,
    *,
    original: str = "",
    pending_skills: list[str] | None = None,
) -> GroundingResult:
    """Check whether a candidate bullet's references are grounded in the resume.

    ``resume_text`` is the full text of the resume (including role headings,
    other bullets, skills).  ``original`` is the bullet text being replaced
    (if any) — it's excluded from the "elsewhere in the resume" check so
    that a replacement bullet can't ground itself in the very text it's
    overwriting. ``pending_skills`` are terms (e.g. a tool name about to be
    added to Technical Skills in a later phase) that should count as
    grounded even though they don't yet appear in ``resume_text`` — without
    this, a rewrite that references a skill scheduled for a later edit is
    incorrectly flagged as ungrounded.

    Returns grounded and ungrounded term lists.  A term is 'ungrounded' if
    it doesn't appear in ``resume_text`` outside of ``original`` (case-
    insensitive substring match) and doesn't match any ``pending_skills``
    entry (case-insensitive substring match, either direction).
    """
    # Remove the original bullet text from the resume context so a
    # replacement can't ground itself in what it's replacing.
    context = resume_text
    if original and original in context:
        context = context.replace(original, "")

    candidate_terms = _extract_reference_terms(candidate)
    if not candidate_terms:
        return GroundingResult(grounded=[], ungrounded=[], is_grounded=True)

    context_lower = context.lower()
    pending_lower = [s.lower() for s in (pending_skills or []) if s]

    def _matches_pending(term_lower: str) -> bool:
        return any(term_lower in p or p in term_lower for p in pending_lower)

    grounded: list[str] = []
    ungrounded: list[str] = []
    for term in sorted(candidate_terms):
        term_lower = term.lower()
        if term_lower in context_lower or _matches_pending(term_lower):
            grounded.append(term)
        else:
            ungrounded.append(term)

    return GroundingResult(
        grounded=grounded,
        ungrounded=ungrounded,
        is_grounded=len(ungrounded) == 0,
    )
