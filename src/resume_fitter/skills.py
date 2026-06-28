"""Technical Skills block: extraction, substitution, diff, apply, and skill-evidence check.

Parallels ``bullets.py`` + ``compare.substitute_bullet`` +
``patch.diff_bullet``/``replace_bullet``, but for the single fixed
``\\section{Technical Skills}`` block's ``\\textbf{<Category>}{: <items>}``
lines.

Scope: exactly one Technical Skills block with N category lines (currently
3: Languages, Frameworks, Developer Tools). Edits replace one category's
items string in place -- categories are never added, removed, or reordered.
This is intentionally narrower than ``bullets.py``'s general macro scan: the
items are a comma-separated skill list, not prose, so there is no XYZ/
specificity/verbosity scoring and no pdfplumber line-layout measurement here.
"""

from __future__ import annotations

import difflib
import os
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from .bullets import extract_bullets, strip_latex
from .compile import BoxWarning, compile_tex
from .compare import _before_after_compile
from .measure import page_count


# Extraction cache for the Technical Skills block, keyed on (path, mtime,
# size) and invalidated on write.  Parallels bullets._parse_cache so a
# session's repeated list_skill_categories/get_skill_category/evidence
# calls skip the file read + regex scan.  Bounded LRU (OrderedDict) so a
# long session can't accumulate stale entries; cache returns a shallow copy
# so callers can't mutate the cached list.
_MAX_SKILL_CACHE_ENTRIES = 32
_skill_cache_lock = threading.Lock()
_skill_cache: "OrderedDict[tuple, list]" = OrderedDict()


def clear_skill_parse_cache() -> None:
    """Clear the Technical Skills parse cache."""
    with _skill_cache_lock:
        _skill_cache.clear()


def _skill_key(tex_path: Path) -> tuple:
    st = os.stat(tex_path)
    return (str(tex_path), st.st_mtime_ns, st.st_size)

_SKILLS_SECTION_RE = re.compile(r"\\section\{Technical Skills\}")
_CATEGORY_LINE_RE = re.compile(r"^\s*\\textbf\{([^{}]*)\}\{:\s(.*)\}\s*\\\\\s*$")


class SkillLookupError(ValueError):
    """Raised when a skill category can't be resolved by index or name."""


@dataclass(frozen=True)
class SkillCategory:
    id: str
    index: int
    category: str
    category_literal: str
    items_raw: str
    items: str
    tokens: list[str]
    start_line: int
    end_line: int


def _split_tokens(items: str) -> list[str]:
    """Split a comma-separated items string into individual skill tokens.

    Split on commas only -- ``/`` is part of conventional names in this
    resume (``C/C++``, ``HTML/CSS``, ``Docker/Kubernetes``) and parenthetical
    qualifiers (``SQL (Postgres)``) stay attached to their token.
    """
    return [t.strip() for t in items.split(",") if t.strip()]


def extract_skill_categories_from_text(raw_text: str) -> list[SkillCategory]:
    """Text-core: return every Technical Skills category from ``raw_text``."""
    lines = raw_text.splitlines()

    section_idx = None
    for i, line in enumerate(lines):
        if _SKILLS_SECTION_RE.search(line):
            if section_idx is not None:
                raise ValueError("multiple \\section{Technical Skills} blocks found")
            section_idx = i

    if section_idx is None:
        raise ValueError("no \\section{Technical Skills} block found")

    categories: list[SkillCategory] = []
    for i in range(section_idx + 1, len(lines)):
        line = lines[i]
        if r"\end{itemize}" in line or r"\section{" in line or r"\end{document}" in line:
            break
        match = _CATEGORY_LINE_RE.match(line)
        if not match:
            continue
        category_literal, items_raw = match.group(1), match.group(2)
        categories.append(
            SkillCategory(
                id=f"s{len(categories)}",
                index=len(categories),
                category=strip_latex(category_literal),
                category_literal=category_literal,
                items_raw=items_raw,
                items=items_raw.strip(),
                tokens=_split_tokens(items_raw),
                start_line=i + 1,
                end_line=i + 1,
            )
        )

    if not categories:
        raise ValueError("no \\textbf{<Category>}{: ...} lines found in Technical Skills block")

    return categories


def extract_skill_categories(tex_path: Path) -> list[SkillCategory]:
    """Return every ``\\textbf{<Category>}{: <items>}`` line in the
    ``\\section{Technical Skills}`` block, in source order.

    Cached on (path, mtime, size); call ``clear_skill_parse_cache`` to
    invalidate after a write to the same file.
    """
    key = _skill_key(tex_path)
    with _skill_cache_lock:
        cached = _skill_cache.get(key)
        if cached is not None:
            _skill_cache.move_to_end(key)
            # Return a shallow copy so callers can't mutate the cached list.
            return list(cached)
    result = extract_skill_categories_from_text(Path(tex_path).read_text())
    with _skill_cache_lock:
        _skill_cache[key] = result
        _skill_cache.move_to_end(key)
        while len(_skill_cache) > _MAX_SKILL_CACHE_ENTRIES:
            _skill_cache.popitem(last=False)
    return list(result)


def list_skill_categories(tex_path: Path) -> list[SkillCategory]:
    return extract_skill_categories(tex_path)


def find_skill_record(
    tex_path: Path,
    *,
    category: str | None = None,
    index: int | None = None,
) -> SkillCategory:
    """Resolve a skill category to its full structured record.

    Exactly one of ``category`` (case-insensitive substring of the category
    name) or ``index`` (0-based, in source order) must be given.
    """
    if (category is None) == (index is None):
        raise ValueError("specify exactly one of category or index")

    categories = extract_skill_categories(tex_path)

    if index is not None:
        if not (0 <= index < len(categories)):
            raise SkillLookupError(
                f"skill category index {index} out of range (found {len(categories)} categories)"
            )
        return categories[index]

    needle = category.lower()
    for record in categories:
        if needle in record.category.lower():
            return record

    raise SkillLookupError(f"no skill category matching: {category!r}")


def find_skill_record_from_text(
    raw_text: str,
    *,
    category: str | None = None,
    index: int | None = None,
) -> SkillCategory:
    """Text-core: resolve a skill category against in-memory ``raw_text``."""
    if (category is None) == (index is None):
        raise ValueError("specify exactly one of category or index")

    categories = extract_skill_categories_from_text(raw_text)

    if index is not None:
        if not (0 <= index < len(categories)):
            raise SkillLookupError(
                f"skill category index {index} out of range (found {len(categories)} categories)"
            )
        return categories[index]

    needle = category.lower()
    for record in categories:
        if needle in record.category.lower():
            return record

    raise SkillLookupError(f"no skill category matching: {category!r}")


def substitute_skill(tex_text: str, record: SkillCategory, new_items: str) -> str:
    """Return ``tex_text`` with ``record``'s category items replaced by ``new_items``.

    Raises ``ValueError`` if the category's exact original
    ``\\textbf{<category>}{: <items_raw>}`` text can't be found, so a missed
    substitution never silently produces an unmodified file.
    """
    old = "\\textbf{" + record.category_literal + "}{: " + record.items_raw + "}"
    if tex_text.count(old) < 1:
        raise ValueError(
            f"could not locate \\textbf{{{record.category_literal}}}{{: ...}} for "
            f"skill category {record.id!r} (expected near line {record.start_line})"
        )
    new = "\\textbf{" + record.category_literal + "}{: " + new_items + "}"
    return tex_text.replace(old, new, 1)


@dataclass
class SkillDiff:
    diff: str
    modified_text: str


def diff_skill(tex_path: Path, record: SkillCategory, new_items: str) -> SkillDiff:
    """Return a unified diff for replacing ``record``'s items with ``new_items``.

    Read-only: ``tex_path`` is read but never written.
    """
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()
    modified_text = substitute_skill(original_text, record, new_items)

    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            modified_text.splitlines(keepends=True),
            fromfile=str(tex_path),
            tofile=str(tex_path),
        )
    )
    return SkillDiff(diff=diff, modified_text=modified_text)


def replace_skill(tex_path: Path, record: SkillCategory, new_items: str) -> SkillDiff:
    """Replace ``record``'s items with ``new_items`` and write the result to ``tex_path``."""
    result = diff_skill(tex_path, record, new_items)
    Path(tex_path).write_text(result.modified_text)
    return result


@dataclass
class SkillEvidence:
    added: list[str]
    removed: list[str]
    evidenced: list[str]
    unevidenced: list[str]
    has_unevidenced: bool


def _evidence_variants(token: str) -> list[str]:
    """Sub-strings of ``token`` worth checking against bullet text.

    Includes the token itself, its parenthetical qualifier and the text
    outside it (``"SQL (Postgres)"`` -> ``"SQL"``, ``"Postgres"``), and any
    ``/``-separated parts (``"Docker/Kubernetes"`` -> ``"Docker"``,
    ``"Kubernetes"``).
    """
    variants = [token]
    paren_match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", token)
    if paren_match:
        variants.append(paren_match.group(1).strip())
        variants.append(paren_match.group(2).strip())

    expanded = list(variants)
    for variant in variants:
        if "/" in variant:
            expanded.extend(part.strip() for part in variant.split("/") if part.strip())

    return [v for v in expanded if v]


def compare_skill_evidence(record: SkillCategory, new_items: str, tex_path: Path) -> SkillEvidence:
    """Compare ``new_items`` against ``record``'s current items and flag any
    newly-added skill tokens that don't appear anywhere in the resume's
    ``\\resumeItem`` bullet text (case-insensitive)."""
    old_tokens = record.tokens
    new_tokens = _split_tokens(new_items)

    old_set = {t.lower() for t in old_tokens}
    new_set = {t.lower() for t in new_tokens}

    added = [t for t in new_tokens if t.lower() not in old_set]
    removed = [t for t in old_tokens if t.lower() not in new_set]

    haystack = " ".join(b.text for b in extract_bullets(tex_path)).lower()

    evidenced = []
    unevidenced = []
    for token in added:
        if any(variant.lower() in haystack for variant in _evidence_variants(token)):
            evidenced.append(token)
        else:
            unevidenced.append(token)

    return SkillEvidence(
        added=added,
        removed=removed,
        evidenced=evidenced,
        unevidenced=unevidenced,
        has_unevidenced=bool(unevidenced),
    )


@dataclass
class SkillComparison:
    before_page_count: int
    after_page_count: int
    before_overfull: bool
    after_overfull: bool
    after_box_warnings: list[BoxWarning]

    @property
    def page_count_changed(self) -> bool:
        return self.before_page_count != self.after_page_count


def compare_skill_candidate(
    tex_path: Path,
    record: SkillCategory,
    new_items: str,
    *,
    tectonic_path: str | None = None,
) -> SkillComparison:
    """Compile ``tex_path`` before and after replacing ``record``'s items with ``new_items``.

    Unlike ``compare.compare_candidate`` (per-bullet pdfplumber line
    measurement), this only reports page count and overfull status -- a
    skills line is a comma list, not a prose bullet to locate and measure.
    """
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()
    modified_text = substitute_skill(original_text, record, new_items)

    with _before_after_compile(tex_path, modified_text, tectonic_path=tectonic_path) as (before_compile, after_compile):
        return SkillComparison(
            before_page_count=page_count(before_compile.pdf_path),
            after_page_count=page_count(after_compile.pdf_path),
            before_overfull=before_compile.overfull,
            after_overfull=after_compile.overfull,
            after_box_warnings=after_compile.box_warnings,
        )
