"""MCP server exposing resume_fitter as a propose -> validate -> diff -> apply loop.

This server does not generate rewrites itself (no LLM call lives here). It is
a thin adapter over the existing deterministic primitives
(``bullets``, ``compare``, ``evaluate``, ``patch``) so an MCP client (a coding
agent) can:

1. ``list_bullets`` / ``get_bullet`` -- read the resume's structure and a
   bullet's current score.
2. ``evaluate_candidate`` -- cheaply score a candidate rewrite the agent
   proposes (no compile).
3. ``compare_candidate_layout`` -- compile before/after to check page-count
   and layout impact.
4. ``diff_candidate`` -- see the exact unified diff that would be applied.
5. ``apply_bullet`` -- write the change, gated by ``confirm=True``.

All tools operate on a single fixed resume path (``DEFAULT_TEX``, overridable
via the ``RESUME_TEX`` env var) -- there is no per-call path argument, so a
client can't be pointed at arbitrary files.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .bullets import (
    BlockLookupError,
    BulletLookupError,
    extract_bullets,
    extract_role_blocks,
    find_bullet_record,
    find_role_block,
    strip_latex,
)
from .compare import compare_candidate, compare_plan, measure_candidate_layout, substitute_bullet
from .compile import CompileError, compile_tex
from .evaluate import compare_truth_risk, evaluate_bullet
from .measure import FULLNESS_REQUIREMENT_THRESHOLD, BulletNotFoundError, measure_layout
from .patch import diff_bullet, replace_bullet
from .shapes import (
    box_warnings_json,
    evaluation_json,
    metrics_json,
    plan_comparison_json,
    role_block_json,
    skill_evidence_json,
    skill_source_json,
    source_json,
)
from .skills import (
    SkillLookupError,
    compare_skill_candidate,
    compare_skill_evidence,
    diff_skill,
    extract_skill_categories,
    find_skill_record,
    replace_skill,
)
from .structure import (
    apply_insert_bullet,
    apply_remove_bullet,
    apply_remove_role_block,
    diff_insert_bullet,
    diff_remove_bullet,
    diff_remove_role_block,
    insert_bullet_text,
)

DEFAULT_TEX = Path(os.environ.get("RESUME_TEX", Path(__file__).resolve().parents[2] / "resume.tex"))

_INSTRUCTIONS = """\
This server implements a propose -> validate -> diff -> apply loop for
resume.tex. Rewrite generation happens in the calling agent -- this server
only scores, compares, diffs, and (with confirm=True) applies.

Metric-gathering rule: `evaluate_candidate`'s `candidate_evaluation.has_metric`
reports whether a candidate bullet contains a quantified result (a count,
percentage, multiplier, or similar). A bullet with `has_action_verb`,
`has_metric`, and `has_result_clause` all true scores `xyz_score == 1.0`;
missing any one of these caps it at 0.667.

If `has_metric` is false for a candidate you're about to propose -- whether
replacing a bullet via `apply_bullet` or adding a new one via `add_bullet` --
pause and ask the user (an interactive Q&A is fine) for a real, concrete
metric for that bullet (a count of items/systems/users, a percentage change,
time saved, etc.) before calling `compare_candidate_layout` / `apply_bullet`
/ `add_bullet` with `confirm=True`. Do not invent a number: a fabricated
metric will be flagged as "high" `truth_risk` by `compare_truth_risk`, and
more importantly won't be true.

Fullness rule (hard gate): every bullet's last (or only) rendered line must
fill at least 90% of the available text width
(`measure.FULLNESS_REQUIREMENT_THRESHOLD`) -- `meets_fullness_requirement` in
the `layout`/`before`/`after` blocks reports this. `apply_bullet` and
`add_bullet` enforce it server-side: even with `confirm=True`, they refuse to
write (returning `applied: false` plus an `error` and the candidate's
`layout`) if the candidate's last line would fall below 90%. This cannot be
bypassed -- if refused, revise the candidate (shorten so it fits one full
line, lengthen so a wrapped second line is nearly full, or restructure) and
retry. Use `compare_candidate_layout` first to check `after.lines` and
`after.last_line_fullness` before calling `apply_bullet`/`add_bullet` with
`confirm=True`.
"""

mcp = FastMCP("resume-fitter", instructions=_INSTRUCTIONS)


def _resolve_record(index: int | None, text: str | None):
    return find_bullet_record(DEFAULT_TEX, index=index, text=text)


def _resolve_skill(index: int | None, category: str | None):
    return find_skill_record(DEFAULT_TEX, index=index, category=category)


@mcp.tool()
def list_bullets() -> dict:
    """List every rewritable bullet in the resume with its id, location, and section/role context."""
    bullets = extract_bullets(DEFAULT_TEX)
    return {
        "bullets": [
            {**source_json(b), "text": b.text}
            for b in bullets
        ]
    }


@mcp.tool()
def get_bullet(index: int | None = None, text: str | None = None) -> dict:
    """Get a bullet's current text, source location, and heuristic evaluation.

    Select the bullet with exactly one of ``index`` (0-based source order) or
    ``text`` (case-insensitive substring match).
    """
    try:
        record = _resolve_record(index, text)
    except (BulletLookupError, ValueError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "source": source_json(record),
        "evaluation": evaluation_json(evaluate_bullet(record.text)),
    }


@mcp.tool()
def evaluate_candidate(candidate: str, index: int | None = None, text: str | None = None) -> dict:
    """Score a proposed rewrite without compiling: XYZ/specificity/verbosity plus truth-risk vs. the original.

    Cheap -- use this to iterate on wording before paying for a
    ``compare_candidate_layout`` compile. Select the bullet being replaced
    with exactly one of ``index`` or ``text``.

    If ``candidate_evaluation.has_metric`` is false, see the server
    instructions: ask the user for a real metric for this bullet before
    proceeding to ``compare_candidate_layout`` / ``apply_bullet`` /
    ``add_bullet`` with ``confirm=True`` -- don't invent one.
    """
    try:
        record = _resolve_record(index, text)
    except (BulletLookupError, ValueError) as exc:
        return {"error": str(exc)}

    truth_risk = compare_truth_risk(record.text, candidate)
    return {
        "bullet": record.text,
        "candidate": candidate,
        "evaluation": evaluation_json(evaluate_bullet(record.text)),
        "candidate_evaluation": evaluation_json(evaluate_bullet(candidate)),
        "truth_risk": truth_risk.truth_risk,
        "changed_entities": truth_risk.changed_entities,
    }


@mcp.tool()
def compare_candidate_layout(candidate: str, index: int | None = None, text: str | None = None) -> dict:
    """Compile the resume before/after substituting candidate for the selected bullet.

    Reports rendered line count, last-line fullness, orphan/overfull status,
    and page count for both versions, plus whether the page count changed.
    Does not write to the resume -- both compiles run in temporary
    directories. Select the bullet with exactly one of ``index`` or ``text``.
    """
    try:
        record = _resolve_record(index, text)
        comparison = compare_candidate(DEFAULT_TEX, record, candidate)
    except (BulletLookupError, ValueError, FileNotFoundError, CompileError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "candidate": candidate,
        "before": metrics_json(comparison.before, comparison.before_overfull),
        "after": metrics_json(comparison.after, comparison.after_overfull),
        "page_count_changed": comparison.page_count_changed,
        "box_warnings": box_warnings_json(comparison.after_box_warnings),
    }


@mcp.tool()
def diff_candidate(candidate: str, index: int | None = None, text: str | None = None) -> dict:
    """Return the unified diff for substituting candidate for the selected bullet.

    Read-only -- the resume file is not modified. Select the bullet with
    exactly one of ``index`` or ``text``.
    """
    try:
        record = _resolve_record(index, text)
        result = diff_bullet(DEFAULT_TEX, record, candidate)
    except (BulletLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "candidate": candidate,
        "diff": result.diff,
    }


@mcp.tool()
def apply_bullet(candidate: str, confirm: bool = False, index: int | None = None, text: str | None = None) -> dict:
    """Replace the selected bullet's text with candidate, writing to the resume file.

    This is the only tool that mutates the resume. It does nothing unless
    ``confirm=True`` -- without it, this behaves exactly like
    ``diff_candidate`` and returns ``applied: false`` plus the diff that
    *would* be written. Select the bullet with exactly one of ``index`` or
    ``text``. After applying, bullet indices may shift -- call
    ``list_bullets`` again before reusing an index.

    Hard fullness gate: even with ``confirm=True``, this refuses to write (and
    returns ``applied: false`` plus an ``error`` and the candidate's
    ``layout``) if the candidate's rendered last/only line would fall below
    ``measure.FULLNESS_REQUIREMENT_THRESHOLD`` (0.9). Revise the candidate
    (shorten to fit one full line, or lengthen so a wrapped second line is
    nearly full) and retry -- see the server instructions.
    """
    try:
        record = _resolve_record(index, text)
        if confirm:
            original_text = DEFAULT_TEX.read_text()
            modified_text = substitute_bullet(original_text, record, candidate)
            after_metrics, after_overfull, _ = measure_candidate_layout(
                DEFAULT_TEX, modified_text, strip_latex(candidate)
            )
            if not after_metrics.meets_fullness_requirement:
                return {
                    "bullet": record.text,
                    "candidate": candidate,
                    "applied": False,
                    "layout": metrics_json(after_metrics, after_overfull),
                    "error": (
                        f"refused: candidate's last line is only "
                        f"{after_metrics.last_line_fullness:.0%} full "
                        f"(requires >= {FULLNESS_REQUIREMENT_THRESHOLD:.0%}). "
                        "Shorten to fit one full line, or lengthen so a wrapped "
                        "second line is nearly full."
                    ),
                }
            result = replace_bullet(DEFAULT_TEX, record, candidate)
        else:
            result = diff_bullet(DEFAULT_TEX, record, candidate)
    except (BulletLookupError, ValueError, FileNotFoundError, CompileError, BulletNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "candidate": candidate,
        "diff": result.diff,
        "applied": confirm,
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def compile_and_score(index: int | None = None, text: str | None = None) -> dict:
    """Compile the resume as-is and report rendered layout metrics for the selected bullet.

    Select the bullet with exactly one of ``index`` or ``text``.
    """
    try:
        record = _resolve_record(index, text)
        with tempfile.TemporaryDirectory() as tmp:
            compile_result = compile_tex(DEFAULT_TEX, Path(tmp))
            metrics = measure_layout(compile_result.pdf_path, record.text)
    except (BulletLookupError, ValueError, FileNotFoundError, CompileError, BulletNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "source": source_json(record),
        "layout": metrics_json(metrics, compile_result.overfull),
        "box_warnings": box_warnings_json(compile_result.box_warnings),
    }


@mcp.tool()
def list_skill_categories() -> dict:
    """List every category line in the Technical Skills section with its id, items, and parsed tokens."""
    categories = extract_skill_categories(DEFAULT_TEX)
    return {"categories": [skill_source_json(c) for c in categories]}


@mcp.tool()
def get_skill_category(index: int | None = None, category: str | None = None) -> dict:
    """Get a Technical Skills category's current items and parsed tokens.

    Select the category with exactly one of ``index`` (0-based source order)
    or ``category`` (case-insensitive substring match, e.g. "developer").
    """
    try:
        record = _resolve_skill(index, category)
    except (SkillLookupError, ValueError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "items": record.items,
        "tokens": record.tokens,
        "source": skill_source_json(record),
    }


@mcp.tool()
def evaluate_skill_candidate(new_items: str, index: int | None = None, category: str | None = None) -> dict:
    """Check a proposed Technical Skills items replacement for evidence elsewhere in the resume.

    Compares ``new_items`` (a comma-separated items string) against the
    category's current items and flags any newly added skills that don't
    appear anywhere in the resume's bullet text. Select the category with
    exactly one of ``index`` or ``category``.
    """
    try:
        record = _resolve_skill(index, category)
        evidence = compare_skill_evidence(record, new_items, DEFAULT_TEX)
    except (SkillLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "current_items": record.items,
        "new_items": new_items,
        "evidence": skill_evidence_json(evidence),
    }


@mcp.tool()
def diff_skill_candidate(new_items: str, index: int | None = None, category: str | None = None) -> dict:
    """Return the unified diff for replacing a Technical Skills category's items.

    Read-only -- the resume file is not modified. Select the category with
    exactly one of ``index`` or ``category``.
    """
    try:
        record = _resolve_skill(index, category)
        result = diff_skill(DEFAULT_TEX, record, new_items)
    except (SkillLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "new_items": new_items,
        "diff": result.diff,
    }


@mcp.tool()
def compare_skill_layout(new_items: str, index: int | None = None, category: str | None = None) -> dict:
    """Compile the resume before/after replacing a Technical Skills category's items.

    Reports page count and overfull status for both versions, plus whether
    the page count changed. Does not write to the resume. Select the
    category with exactly one of ``index`` or ``category``.
    """
    try:
        record = _resolve_skill(index, category)
        comparison = compare_skill_candidate(DEFAULT_TEX, record, new_items)
    except (SkillLookupError, ValueError, FileNotFoundError, CompileError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "new_items": new_items,
        "before": {"page_count": comparison.before_page_count, "overfull": comparison.before_overfull},
        "after": {"page_count": comparison.after_page_count, "overfull": comparison.after_overfull},
        "page_count_changed": comparison.page_count_changed,
        "box_warnings": box_warnings_json(comparison.after_box_warnings),
    }


@mcp.tool()
def apply_skill_category(
    new_items: str, confirm: bool = False, index: int | None = None, category: str | None = None
) -> dict:
    """Replace a Technical Skills category's items, writing to the resume file.

    This is the only tool that mutates the Technical Skills section. It does
    nothing unless ``confirm=True`` -- without it, this behaves exactly like
    ``diff_skill_candidate`` and returns ``applied: false`` plus the diff
    that *would* be written. Select the category with exactly one of
    ``index`` or ``category``.
    """
    try:
        record = _resolve_skill(index, category)
        if confirm:
            result = replace_skill(DEFAULT_TEX, record, new_items)
        else:
            result = diff_skill(DEFAULT_TEX, record, new_items)
    except (SkillLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "new_items": new_items,
        "diff": result.diff,
        "applied": confirm,
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def list_role_blocks() -> dict:
    """List every role/project entry (\\resumeSubheading or \\resumeProjectHeading) with its source extent.

    Use this to find a ``role`` value for ``add_bullet``, ``remove_role_block``,
    and the ``"role"`` field of ``compare_plan_layout`` ops -- it's matched as
    a case-insensitive substring (e.g. "Gemini" or "QA Engineer").
    """
    blocks = extract_role_blocks(DEFAULT_TEX)
    return {"blocks": [role_block_json(b) for b in blocks]}


@mcp.tool()
def add_bullet(
    role: str, new_bullet: str, position: str = "end", after_index: int | None = None, confirm: bool = False
) -> dict:
    """Insert a new \\resumeItem{new_bullet} into a role/project's bullet list.

    ``role`` is a case-insensitive substring matched against the role blocks
    from ``list_role_blocks`` (e.g. "Gemini") and must match exactly one
    entry. ``position`` is "end" (default), "start", or "after" (paired with
    ``after_index``, the 0-based index of an existing bullet *within this
    block* to insert after).

    This is the only tool that adds a bullet. It does nothing unless
    ``confirm=True`` -- without it, returns ``applied: false`` plus the diff
    that *would* be written. After applying, bullet indices shift -- call
    ``list_bullets`` again before reusing an index.

    Hard fullness gate: even with ``confirm=True``, this refuses to write (and
    returns ``applied: false`` plus an ``error`` and the new bullet's
    ``layout``) if its rendered last/only line would fall below
    ``measure.FULLNESS_REQUIREMENT_THRESHOLD`` (0.9). Revise ``new_bullet``
    (shorten to fit one full line, or lengthen so a wrapped second line is
    nearly full) and retry -- see the server instructions.
    """
    try:
        block = find_role_block(DEFAULT_TEX, role=role)
        if confirm:
            original_text = DEFAULT_TEX.read_text()
            modified_text = insert_bullet_text(
                original_text, block, new_bullet, position=position, after_index=after_index
            )
            after_metrics, after_overfull, _ = measure_candidate_layout(
                DEFAULT_TEX, modified_text, strip_latex(new_bullet)
            )
            if not after_metrics.meets_fullness_requirement:
                return {
                    "role": block.role,
                    "new_bullet": new_bullet,
                    "applied": False,
                    "layout": metrics_json(after_metrics, after_overfull),
                    "error": (
                        f"refused: new bullet's last line is only "
                        f"{after_metrics.last_line_fullness:.0%} full "
                        f"(requires >= {FULLNESS_REQUIREMENT_THRESHOLD:.0%}). "
                        "Shorten to fit one full line, or lengthen so a wrapped "
                        "second line is nearly full."
                    ),
                }
            result = apply_insert_bullet(DEFAULT_TEX, block, new_bullet, position=position, after_index=after_index)
        else:
            result = diff_insert_bullet(DEFAULT_TEX, block, new_bullet, position=position, after_index=after_index)
    except (BlockLookupError, ValueError, FileNotFoundError, CompileError, BulletNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "role": block.role,
        "new_bullet": new_bullet,
        "diff": result.diff,
        "applied": confirm,
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def remove_bullet(index: int | None = None, text: str | None = None, confirm: bool = False) -> dict:
    """Remove an entire \\resumeItem{...} bullet from the resume.

    Select the bullet with exactly one of ``index`` or ``text`` (as in
    ``get_bullet``). This is the only tool that deletes a single bullet. It
    does nothing unless ``confirm=True`` -- without it, returns
    ``applied: false`` plus the diff that *would* be written. After applying,
    bullet indices shift -- call ``list_bullets`` again before reusing an
    index.
    """
    try:
        record = find_bullet_record(DEFAULT_TEX, index=index, text=text)
        if confirm:
            result = apply_remove_bullet(DEFAULT_TEX, record)
        else:
            result = diff_remove_bullet(DEFAULT_TEX, record)
    except (BulletLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "diff": result.diff,
        "applied": confirm,
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def remove_role_block(role: str, confirm: bool = False) -> dict:
    """Remove an entire role/project entry: its heading through its bullet list.

    ``role`` is a case-insensitive substring matched against the role blocks
    from ``list_role_blocks`` (e.g. "QA Engineer") and must match exactly one
    entry. This is the only tool that deletes a whole experience/project
    entry. It does nothing unless ``confirm=True`` -- without it, returns
    ``applied: false`` plus the diff that *would* be written. After applying,
    bullet indices and role blocks shift -- call ``list_bullets`` /
    ``list_role_blocks`` again before reusing an index or role.
    """
    try:
        block = find_role_block(DEFAULT_TEX, role=role)
        if confirm:
            result = apply_remove_role_block(DEFAULT_TEX, block)
        else:
            result = diff_remove_role_block(DEFAULT_TEX, block)
    except (BlockLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "role": block.role,
        "diff": result.diff,
        "applied": confirm,
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def compare_plan_layout(ops: list[dict]) -> dict:
    """Check whether a combination of add/remove edits keeps the resume at 1 page.

    Read-only -- never writes to the resume, even though it models the same
    edits ``add_bullet``/``remove_bullet``/``remove_role_block`` would make.
    ``ops`` is a list of edit operations, applied in order:

      - ``{"op": "add_bullet", "role": "...", "new_bullet": "...",
        "position": "end"|"start"|"after", "after_index": 0}``
      - ``{"op": "remove_bullet", "index": 0}`` or
        ``{"op": "remove_bullet", "text": "..."}``
      - ``{"op": "remove_block", "role": "..."}``

    Use this before calling the mutating tools, e.g. to check whether adding
    2-3 bullets to one role while removing another role block nets out to 1
    page. Returns before/after page count and overfull status,
    ``page_count_changed``, ``fits_one_page``, and a per-op summary.
    """
    try:
        comparison = compare_plan(DEFAULT_TEX, ops)
    except (BlockLookupError, BulletLookupError, ValueError, FileNotFoundError, CompileError) as exc:
        return {"error": str(exc)}

    return plan_comparison_json(comparison)


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
