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

All tools accept an optional ``tex_path`` argument pointing at any resume
``.tex`` file that uses Jake Gutierrez's template (``\\resumeItem``,
``\\resumeSubheading``, ``\\resumeProjectHeading`` macros). When omitted, tools
operate on ``DEFAULT_TEX`` (the repo's ``resume.tex``, overridable via the
``RESUME_TEX`` env var).
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
from .evaluate import check_grounding, compare_truth_risk, evaluate_bullet
from .measure import FULLNESS_REQUIREMENT_THRESHOLD, BulletNotFoundError, measure_layout, page_fill_ratio
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

# Session-level active resume: set by set_active_resume(). Falls back to
# DEFAULT_TEX when None. Not persisted to disk — every session starts with
# an explicit set_active_resume call, and a dead stdio process ends the
# session, so crash-recovery persistence is never needed.
_active_tex: Path | None = None

_INSTRUCTIONS = """\
This server implements a propose -> validate -> diff -> apply loop for
resume.tex. Rewrite generation happens in the calling agent -- this server
only scores, compares, diffs, and (with confirm=True) applies.

Resume selection: call `list_resumes` to discover all .tex resume files in the
repo, then `set_active_resume` to switch context. All tools then operate on
that file for the rest of the session without needing a `tex_path` argument.
Individual tools still accept an optional `tex_path` to override for one call.

Metric-gathering rule: `evaluate_candidate`'s `candidate_evaluation.has_metric`
reports whether a candidate bullet contains a quantified result (a count,
percentage, multiplier, or similar). A bullet with `has_action_verb`,
`has_metric`, and `has_result_clause` all true scores `xyz_score == 1.0`;
missing any one of these caps it at 0.667.

If `has_metric` is false for a candidate you're about to propose -- whether
replacing a bullet via `apply_bullet` or adding a new one via `add_bullet` --
first check other resumes in the workspace (`list_resumes`, then
`list_bullets(tex_path=...)` on each) for a bullet describing the same real
experience that already carries a metric or sharper framing -- past target
resumes often phrase the same accomplishment differently, and one version may
already fit the gap. Reusing/adapting a bullet found this way is not
fabrication (same person, same work) and should be preferred over inventing
wording. Only if nothing reusable turns up, pause and ask the user (an
interactive Q&A is fine) for a real, concrete metric for that bullet (a count
of items/systems/users, a percentage change, time saved, etc.) before calling
`compare_candidate_layout` / `apply_bullet` / `add_bullet` with
`confirm=True`. Do not invent a number: a fabricated metric will be flagged as
"high" `truth_risk` by `compare_truth_risk`, and more importantly won't be
true.

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
`confirm=True`. A `compare_candidate_layout` showing `after.lines == 2` with
a low `after.last_line_fullness` is the classic sparse-wrap case (one or a few
words spilling onto a near-empty second line) -- shorten to fit one full line
or lengthen the second line to >= 90%; do not apply it.

Phantom-blank-line rule (soft warning): `meets_fullness_requirement` only
checks the >= 90% floor -- a candidate can pass that hard gate while still
sitting at or near 100% last-line fullness, which risks rendering a phantom
near-empty wrapped line on small text differences. The `phantom_blank_line_risk`
field in the `layout`/`before`/`after` blocks reports `last_line_fullness >
measure.PHANTOM_BLANK_LINE_CEILING` (0.98). This is not enforced server-side
-- check it yourself on every `compare_candidate_layout` / `apply_bullet` /
`add_bullet` response and revise toward the recommended 0.90-0.98 zone if
`phantom_blank_line_risk` is true, even though the write would otherwise
succeed.

Overfull rule (hard gate): if the candidate causes an overfull \hbox (text
extends past the right margin), `apply_bullet` and `add_bullet` also refuse
(`applied: false` + `error`). Shorten the bullet.

Channel rule (never bypass): bullet text must only be changed through
`apply_bullet` (and `add_bullet`/`remove_bullet` for structural edits). Do not
edit the .tex file directly with a file-edit tool to change a bullet -- that
path skips the fullness hard gate and has shipped broken bullets. If
`apply_bullet`/`add_bullet` returns `applied: false`, the write did not happen;
revise the candidate and retry rather than working around the refusal.

Grounding rule (soft warning): every bullet must be self-grounding — proper
nouns and technical phrases it references must appear elsewhere in the resume
(in a role heading, another bullet, or the skills section). The `grounding`
field in `evaluate_candidate`, `apply_bullet`, and `add_bullet` responses
reports `ungrounded` terms that only appear in the candidate bullet and
nowhere else. This is a warning, not a hard gate — but ungrounded bullets
reference context the reader doesn't have (e.g. "load replay harness"
mentioned once with no explanation). Revise to either ground the term (add
context elsewhere) or rephrase to be self-contained. If a term will be
grounded by a Technical Skills addition you haven't made yet, pass it in
`pending_skills` (a list of strings) to `evaluate_candidate`/`apply_bullet`/
`add_bullet` instead of treating it as ungrounded.

Escaping rule (hard gate, both write paths): a candidate/new bullet
containing an unescaped `%`, `&`, `#`, or `_` is rejected before any compile
is attempted (by `apply_bullet`, `add_bullet`, and `compare_plan_layout`
alike) with a `ValueError`-style `error` naming the offending character(s).
Escape these as `\%`, `\&`, `\#`, `\_` in the text you pass in. Bare `$` is
fine (this template uses it for inline math, e.g. "$4k").

Draft evaluation: `evaluate_candidate` can be called with no `index`/`text`
to score a brand-new bullet that doesn't exist in the resume yet (e.g. while
drafting Phase 4 additions) — useful to get `xyz_score`/`grounding` before an
`add_bullet` target even exists. In that mode `truth_risk` is not computed
(there's no original to diff against) and the response's `bullet` field is
`null`.

Empty-block cascade: `remove_bullet`'s response includes `would_empty_block`
(true if this is the last bullet in its role/project) and, when `confirm=True`
empties a block, automatically removes that now-empty block too (reported as
`cascaded_block_removed`) — pass `cascade=False` to disable this and handle
the empty block yourself.
 """

mcp = FastMCP("resume-fitter", instructions=_INSTRUCTIONS)


def _tex(tex_path: str | None) -> Path:
    if tex_path:
        return Path(tex_path)
    if _active_tex is not None:
        return _active_tex
    return DEFAULT_TEX


@mcp.tool()
def list_resumes() -> dict:
    """List all resume .tex files in the repo (identified by \\resumeItem macro usage).

    Returns each file's path, last-modified time, and whether it is the
    currently active resume for this session.
    """
    repo_root = DEFAULT_TEX.parent
    results = []
    for tex in sorted(repo_root.rglob("*.tex")):
        try:
            if r"\resumeItem" not in tex.read_text(errors="ignore"):
                continue
        except OSError:
            continue
        results.append({
            "path": str(tex),
            "relative": str(tex.relative_to(repo_root)),
            "active": tex == (_active_tex or DEFAULT_TEX),
        })
    return {"resumes": results, "active": str(_active_tex or DEFAULT_TEX)}


@mcp.tool()
def set_active_resume(path: str) -> dict:
    """Set the active resume for this session.

    After calling this, all tools operate on ``path`` without needing a
    ``tex_path`` argument. ``path`` may be absolute or relative to the repo
    root. Call ``list_resumes`` first to see available files.
    """
    global _active_tex
    p = Path(path)
    if not p.is_absolute():
        p = DEFAULT_TEX.parent / p
    if not p.exists():
        return {"active": str(_active_tex or DEFAULT_TEX), "error": f"file not found: {p}"}
    _active_tex = p.resolve()
    return {"active": str(_active_tex)}


def _resolve_record(index: int | None, text: str | None, tex: Path):
    return find_bullet_record(tex, index=index, text=text)


def _resolve_skill(index: int | None, category: str | None, tex: Path):
    return find_skill_record(tex, index=index, category=category)


def _layout_gate(metrics, overfull: bool, subject: str):
    """Shared hard gate for mutating tools (apply_bullet, add_bullet).

    Returns a refusal dict (applied=False + layout + error) if the
    candidate's last line is below the fullness threshold or overfull,
    else None. ``subject`` is ``"candidate"`` or ``"new bullet"`` for the
    error message.
    """
    if not metrics.meets_fullness_requirement:
        return {
            "applied": False,
            "layout": metrics_json(metrics, overfull),
            "error": (
                f"refused: {subject}'s last line is only "
                f"{metrics.last_line_fullness:.0%} full "
                f"(requires >= {FULLNESS_REQUIREMENT_THRESHOLD:.0%}). "
                "Shorten to fit one full line, or lengthen so a wrapped "
                "second line is nearly full."
            ),
        }
    if overfull:
        return {
            "applied": False,
            "layout": metrics_json(metrics, overfull),
            "error": (
                f"refused: {subject} causes an overfull line (text exceeds "
                "the right margin). Shorten the bullet."
            ),
        }
    return None


@mcp.tool()
def list_bullets(tex_path: str | None = None) -> dict:
    """List every rewritable bullet in the resume with its id, location, and section/role context.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    bullets = extract_bullets(tex)
    return {
        "bullets": [
            {**source_json(b), "text": b.text}
            for b in bullets
        ]
    }


@mcp.tool()
def get_bullet(index: int | None = None, text: str | None = None, tex_path: str | None = None) -> dict:
    """Get a bullet's current text, source location, and heuristic evaluation.

    Select the bullet with exactly one of ``index`` (0-based source order) or
    ``text`` (case-insensitive substring match).

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_record(index, text, tex)
    except (BulletLookupError, ValueError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "source": source_json(record),
        "evaluation": evaluation_json(evaluate_bullet(record.text)),
    }


@mcp.tool()
def evaluate_candidate(
    candidate: str,
    index: int | None = None,
    text: str | None = None,
    tex_path: str | None = None,
    pending_skills: list[str] | None = None,
) -> dict:
    """Score a proposed rewrite without compiling: XYZ/specificity/verbosity plus truth-risk vs. the original.

    Cheap -- use this to iterate on wording before paying for a
    ``compare_candidate_layout`` compile. Select the bullet being replaced
    with exactly one of ``index`` or ``text`` -- or omit both to evaluate a
    brand-new bullet that doesn't exist in the resume yet as a standalone
    draft (no ``truth_risk``/``changed_entities`` are computed in that case,
    since there's no original to diff against; ``bullet``/``evaluation`` are
    ``null`` and the response includes a ``note`` explaining this).

    ``pending_skills``: optional list of skill terms about to be added to
    Technical Skills in a later step (e.g. ``["Vertex AI Studio"]``) -- terms
    matching these count as grounded even though they don't appear in the
    resume yet.

    If ``candidate_evaluation.has_metric`` is false, see the server
    instructions: ask the user for a real metric for this bullet before
    proceeding to ``compare_candidate_layout`` / ``apply_bullet`` /
    ``add_bullet`` with ``confirm=True`` -- don't invent one.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)

    if index is None and text is None:
        grounding = check_grounding(candidate, tex.read_text(), pending_skills=pending_skills)
        return {
            "bullet": None,
            "candidate": candidate,
            "evaluation": None,
            "candidate_evaluation": evaluation_json(evaluate_bullet(candidate)),
            "truth_risk": None,
            "changed_entities": [],
            "grounding": {
                "grounded": grounding.grounded,
                "ungrounded": grounding.ungrounded,
                "is_grounded": grounding.is_grounded,
            },
            "note": (
                "standalone draft evaluation -- no index/text given, so this isn't "
                "scored against an existing bullet and truth_risk is not computed."
            ),
        }

    try:
        record = _resolve_record(index, text, tex)
    except (BulletLookupError, ValueError) as exc:
        return {"error": str(exc)}

    truth_risk = compare_truth_risk(record.text, candidate)
    grounding = check_grounding(candidate, tex.read_text(), original=record.text, pending_skills=pending_skills)
    return {
        "bullet": record.text,
        "candidate": candidate,
        "evaluation": evaluation_json(evaluate_bullet(record.text)),
        "candidate_evaluation": evaluation_json(evaluate_bullet(candidate)),
        "truth_risk": truth_risk.truth_risk,
        "changed_entities": truth_risk.changed_entities,
        "grounding": {
            "grounded": grounding.grounded,
            "ungrounded": grounding.ungrounded,
            "is_grounded": grounding.is_grounded,
        },
    }


@mcp.tool()
def compare_candidate_layout(
    candidate: str, index: int | None = None, text: str | None = None, tex_path: str | None = None
) -> dict:
    """Compile the resume before/after substituting candidate for the selected bullet.

    Reports rendered line count, last-line fullness, orphan/overfull status,
    and page count for both versions, plus whether the page count changed.
    Does not write to the resume -- both compiles run in temporary
    directories. Select the bullet with exactly one of ``index`` or ``text``.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_record(index, text, tex)
        comparison = compare_candidate(tex, record, candidate)
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
def diff_candidate(
    candidate: str, index: int | None = None, text: str | None = None, tex_path: str | None = None
) -> dict:
    """Return the unified diff for substituting candidate for the selected bullet.

    Read-only -- the resume file is not modified. Select the bullet with
    exactly one of ``index`` or ``text``.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_record(index, text, tex)
        result = diff_bullet(tex, record, candidate)
    except (BulletLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "candidate": candidate,
        "diff": result.diff,
    }


@mcp.tool()
def apply_bullet(
    candidate: str,
    confirm: bool = False,
    index: int | None = None,
    text: str | None = None,
    tex_path: str | None = None,
    pending_skills: list[str] | None = None,
) -> dict:
    """Replace the selected bullet's text with candidate, writing to the resume file.

    This is the only tool that mutates the resume. It does nothing unless
    ``confirm=True`` -- without it, this behaves exactly like
    ``diff_candidate`` and returns ``applied: false`` plus the diff that
    *would* be written. Select the bullet with exactly one of ``index`` or
    ``text``. After applying, bullet indices may shift -- call
    ``list_bullets`` again before reusing an index.

    ``pending_skills``: optional list of skill terms about to be added to
    Technical Skills in a later step -- terms matching these count as
    grounded in the ``grounding`` check even though they don't appear in the
    resume yet.

    Hard fullness gate: even with ``confirm=True``, this refuses to write (and
    returns ``applied: false`` plus an ``error`` and the candidate's
    ``layout``) if the candidate's rendered last/only line would fall below
    ``measure.FULLNESS_REQUIREMENT_THRESHOLD`` (0.9). Revise the candidate
    (shorten to fit one full line, or lengthen so a wrapped second line is
    nearly full) and retry -- see the server instructions.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_record(index, text, tex)
        if confirm:
            original_text = tex.read_text()
            modified_text = substitute_bullet(original_text, record, candidate)
            after_metrics, after_overfull, _ = measure_candidate_layout(
                tex, modified_text, strip_latex(candidate)
            )
            refusal = _layout_gate(after_metrics, after_overfull, "candidate")
            if refusal is not None:
                return {
                    "bullet": record.text,
                    "candidate": candidate,
                    **refusal,
                }
            # Grounding check BEFORE writing — use original_text (pre-edit)
            # so the candidate can't self-ground in its own newly-written text.
            grounding = check_grounding(candidate, original_text, original=record.text, pending_skills=pending_skills)
            result = replace_bullet(tex, record, candidate)
        else:
            result = diff_bullet(tex, record, candidate)
    except (BulletLookupError, ValueError, FileNotFoundError, CompileError, BulletNotFoundError) as exc:
        return {"error": str(exc)}

    # For the non-confirm path, check grounding against current file state.
    if not confirm:
        grounding = check_grounding(candidate, tex.read_text(), original=record.text, pending_skills=pending_skills)
    return {
        "bullet": record.text,
        "candidate": candidate,
        "diff": result.diff,
        "applied": confirm,
        "grounding": {
            "grounded": grounding.grounded,
            "ungrounded": grounding.ungrounded,
            "is_grounded": grounding.is_grounded,
        },
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def compile_and_score(index: int | None = None, text: str | None = None, tex_path: str | None = None) -> dict:
    """Compile the resume as-is and report rendered layout metrics for the selected bullet.

    Select the bullet with exactly one of ``index`` or ``text``.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_record(index, text, tex)
        with tempfile.TemporaryDirectory() as tmp:
            compile_result = compile_tex(tex, Path(tmp))
            metrics = measure_layout(compile_result.pdf_path, record.text)
            page_fill = page_fill_ratio(compile_result.pdf_path)
    except (BulletLookupError, ValueError, FileNotFoundError, CompileError, BulletNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "source": source_json(record),
        "layout": metrics_json(metrics, compile_result.overfull),
        "page_fill": page_fill,
        "box_warnings": box_warnings_json(compile_result.box_warnings),
    }


@mcp.tool()
def list_skill_categories(tex_path: str | None = None) -> dict:
    """List every category line in the Technical Skills section with its id, items, and parsed tokens.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    categories = extract_skill_categories(tex)
    return {"categories": [skill_source_json(c) for c in categories]}


@mcp.tool()
def get_skill_category(
    index: int | None = None, category: str | None = None, tex_path: str | None = None
) -> dict:
    """Get a Technical Skills category's current items and parsed tokens.

    Select the category with exactly one of ``index`` (0-based source order)
    or ``category`` (case-insensitive substring match, e.g. "developer").

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_skill(index, category, tex)
    except (SkillLookupError, ValueError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "items": record.items,
        "tokens": record.tokens,
        "source": skill_source_json(record),
    }


@mcp.tool()
def evaluate_skill_candidate(
    new_items: str, index: int | None = None, category: str | None = None, tex_path: str | None = None
) -> dict:
    """Check a proposed Technical Skills items replacement for evidence elsewhere in the resume.

    Compares ``new_items`` (a comma-separated items string) against the
    category's current items and flags any newly added skills that don't
    appear anywhere in the resume's bullet text. Select the category with
    exactly one of ``index`` or ``category``.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_skill(index, category, tex)
        evidence = compare_skill_evidence(record, new_items, tex)
    except (SkillLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "current_items": record.items,
        "new_items": new_items,
        "evidence": skill_evidence_json(evidence),
    }


@mcp.tool()
def diff_skill_candidate(
    new_items: str, index: int | None = None, category: str | None = None, tex_path: str | None = None
) -> dict:
    """Return the unified diff for replacing a Technical Skills category's items.

    Read-only -- the resume file is not modified. Select the category with
    exactly one of ``index`` or ``category``.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_skill(index, category, tex)
        result = diff_skill(tex, record, new_items)
    except (SkillLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "category": record.category,
        "new_items": new_items,
        "diff": result.diff,
    }


@mcp.tool()
def compare_skill_layout(
    new_items: str, index: int | None = None, category: str | None = None, tex_path: str | None = None
) -> dict:
    """Compile the resume before/after replacing a Technical Skills category's items.

    Reports page count and overfull status for both versions, plus whether
    the page count changed. Does not write to the resume. Select the
    category with exactly one of ``index`` or ``category``.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_skill(index, category, tex)
        comparison = compare_skill_candidate(tex, record, new_items)
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
    new_items: str,
    confirm: bool = False,
    index: int | None = None,
    category: str | None = None,
    tex_path: str | None = None,
) -> dict:
    """Replace a Technical Skills category's items, writing to the resume file.

    This is the only tool that mutates the Technical Skills section. It does
    nothing unless ``confirm=True`` -- without it, this behaves exactly like
    ``diff_skill_candidate`` and returns ``applied: false`` plus the diff
    that *would* be written. Select the category with exactly one of
    ``index`` or ``category``.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = _resolve_skill(index, category, tex)
        if confirm:
            result = replace_skill(tex, record, new_items)
        else:
            result = diff_skill(tex, record, new_items)
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
def list_role_blocks(tex_path: str | None = None) -> dict:
    """List every role/project entry (\\resumeSubheading or \\resumeProjectHeading) with its source extent.

    Use this to find a ``role`` value for ``add_bullet``, ``remove_role_block``,
    and the ``"role"`` field of ``compare_plan_layout`` ops -- it's matched as
    a case-insensitive substring (e.g. "Gemini" or "QA Engineer").

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    blocks = extract_role_blocks(tex)
    return {"blocks": [role_block_json(b) for b in blocks]}


@mcp.tool()
def add_bullet(
    role: str,
    new_bullet: str,
    position: str = "end",
    after_index: int | None = None,
    confirm: bool = False,
    tex_path: str | None = None,
    pending_skills: list[str] | None = None,
) -> dict:
    """Insert a new \\resumeItem{new_bullet} into a role/project's bullet list.

    ``role`` is a case-insensitive substring matched against the role blocks
    from ``list_role_blocks`` (e.g. "Gemini") and must match exactly one
    entry. ``position`` is "end" (default), "start", or "after" (paired with
    ``after_index``, the 0-based index of an existing bullet *within this
    block* to insert after).

    ``pending_skills``: optional list of skill terms about to be added to
    Technical Skills in a later step -- terms matching these count as
    grounded in the ``grounding`` check even though they don't appear in the
    resume yet.

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

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        block = find_role_block(tex, role=role)
        if confirm:
            original_text = tex.read_text()
            modified_text = insert_bullet_text(
                original_text, block, new_bullet, position=position, after_index=after_index
            )
            after_metrics, after_overfull, _ = measure_candidate_layout(
                tex, modified_text, strip_latex(new_bullet)
            )
            refusal = _layout_gate(after_metrics, after_overfull, "new bullet")
            if refusal is not None:
                return {
                    "role": block.role,
                    "new_bullet": new_bullet,
                    **refusal,
                }
            # Grounding check BEFORE writing — use original_text (pre-edit)
            # so the new bullet can't self-ground in its own newly-written text.
            grounding = check_grounding(new_bullet, original_text, pending_skills=pending_skills)
            result = apply_insert_bullet(tex, block, new_bullet, position=position, after_index=after_index)
        else:
            result = diff_insert_bullet(tex, block, new_bullet, position=position, after_index=after_index)
            grounding = check_grounding(new_bullet, tex.read_text(), pending_skills=pending_skills)
    except (BlockLookupError, ValueError, FileNotFoundError, CompileError, BulletNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "role": block.role,
        "new_bullet": new_bullet,
        "diff": result.diff,
        "applied": confirm,
        "grounding": {
            "grounded": grounding.grounded,
            "ungrounded": grounding.ungrounded,
            "is_grounded": grounding.is_grounded,
        },
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def remove_bullet(
    index: int | None = None,
    text: str | None = None,
    confirm: bool = False,
    cascade: bool = True,
    tex_path: str | None = None,
) -> dict:
    """Remove an entire \\resumeItem{...} bullet from the resume.

    Select the bullet with exactly one of ``index`` or ``text`` (as in
    ``get_bullet``). This is the only tool that deletes a single bullet. It
    does nothing unless ``confirm=True`` -- without it, returns
    ``applied: false`` plus the diff that *would* be written. After applying,
    bullet indices shift -- call ``list_bullets`` again before reusing an
    index.

    Empty-block cascade: removing a role/project's last remaining bullet
    leaves an empty ``\\resumeItemListStart``/``End`` with no ``\\item``, which
    breaks LaTeX compilation ("perhaps a missing \\item"). ``would_empty_block``
    in the response reports whether this bullet is the last one in its
    block. When true and ``cascade=True`` (default), ``confirm=True`` also
    removes the now-empty role block entirely (reported as
    ``cascaded_block_removed``); set ``cascade=False`` to remove just the
    bullet and leave the empty block for the caller to handle (e.g. via
    ``remove_role_block``).

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        record = find_bullet_record(tex, index=index, text=text)
        would_empty_block = bool(record.role) and len(
            [b for b in extract_bullets(tex) if b.role == record.role]
        ) == 1

        cascaded_block_removed: str | None = None
        if confirm:
            result = apply_remove_bullet(tex, record)
            if would_empty_block and cascade:
                block = find_role_block(tex, role=record.role)
                apply_remove_role_block(tex, block)
                cascaded_block_removed = block.role
        else:
            result = diff_remove_bullet(tex, record)
    except (BlockLookupError, BulletLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "bullet": record.text,
        "diff": result.diff,
        "applied": confirm,
        "would_empty_block": would_empty_block,
        "cascaded_block_removed": cascaded_block_removed,
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def remove_role_block(role: str, confirm: bool = False, tex_path: str | None = None) -> dict:
    """Remove an entire role/project entry: its heading through its bullet list.

    ``role`` is a case-insensitive substring matched against the role blocks
    from ``list_role_blocks`` (e.g. "QA Engineer") and must match exactly one
    entry. This is the only tool that deletes a whole experience/project
    entry. It does nothing unless ``confirm=True`` -- without it, returns
    ``applied: false`` plus the diff that *would* be written. After applying,
    bullet indices and role blocks shift -- call ``list_bullets`` /
    ``list_role_blocks`` again before reusing an index or role.

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        block = find_role_block(tex, role=role)
        if confirm:
            result = apply_remove_role_block(tex, block)
        else:
            result = diff_remove_role_block(tex, block)
    except (BlockLookupError, ValueError, FileNotFoundError) as exc:
        return {"error": str(exc)}

    return {
        "role": block.role,
        "diff": result.diff,
        "applied": confirm,
        "note": None if confirm else "Set confirm=true to write this change to the resume.",
    }


@mcp.tool()
def compare_plan_layout(ops: list[dict], tex_path: str | None = None) -> dict:
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

    ``tex_path``: optional absolute path to a resume .tex file using Jake
    Gutierrez's template. Defaults to the repo's resume.tex.
    """
    tex = _tex(tex_path)
    try:
        comparison = compare_plan(tex, ops)
    except (BlockLookupError, BulletLookupError, ValueError, FileNotFoundError, CompileError) as exc:
        return {"error": str(exc)}

    return plan_comparison_json(comparison)


def run() -> None:
    mcp.run()


if __name__ == "__main__":
    run()
