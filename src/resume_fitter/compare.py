"""Before/after page-count-change detection for a candidate bullet rewrite.

Compiles ``resume.tex`` twice — once unmodified, once with a single
``\\resumeItem{...}``'s content swapped for a candidate string — and reports
layout metrics for both, plus whether the total page count changed. The
candidate must be LaTeX-ready text; this module does raw substring
substitution only (no escaping), matching the boundary of CONCEPT.md's future
``propose_rewrites()`` (which would be responsible for producing safe LaTeX).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .bullets import Bullet, find_bullet_record, find_role_block, strip_latex
from .compile import BoxWarning, compile_tex
from .measure import BulletMetrics, measure_layout, page_count
from .structure import insert_bullet_text, remove_bullet_text, remove_role_block_text


def measure_candidate_layout(
    tex_path: Path,
    modified_text: str,
    candidate_text: str,
    *,
    tectonic_path: str | None = None,
) -> tuple[BulletMetrics, bool, list[BoxWarning]]:
    """Compile ``modified_text`` and measure the rendered layout of ``candidate_text``.

    ``candidate_text`` should be plain text (e.g. via ``strip_latex``), as
    expected by ``measure_layout``. Runs a single isolated tectonic compile
    against a temp copy named after ``tex_path``; never writes to
    ``tex_path``. Used both by ``compare_candidate`` (for its "after" half)
    and by ``apply_bullet``/``add_bullet`` to gate ``confirm=True`` on
    ``BulletMetrics.meets_fullness_requirement`` before writing.
    """
    tex_path = Path(tex_path)
    with tempfile.TemporaryDirectory() as after_dir:
        modified_tex = Path(after_dir) / tex_path.name
        modified_tex.write_text(modified_text)

        after_compile = compile_tex(modified_tex, Path(after_dir) / "out", tectonic_path=tectonic_path)
        after_metrics = measure_layout(after_compile.pdf_path, candidate_text)

    return after_metrics, after_compile.overfull, after_compile.box_warnings


@dataclass
class CandidateComparison:
    before: BulletMetrics
    after: BulletMetrics
    before_overfull: bool
    after_overfull: bool
    after_box_warnings: list[BoxWarning]

    @property
    def page_count_changed(self) -> bool:
        return self.before.page_count != self.after.page_count


def substitute_bullet(tex_text: str, record: Bullet, candidate: str) -> str:
    """Return ``tex_text`` with ``record``'s ``\\resumeItem{...}`` body replaced.

    Operates on the full source string (line-agnostic, so it works whether
    the bullet's body is on one line or spans several). Raises ``ValueError``
    if the bullet's exact original ``\\resumeItem{<raw>}`` text can't be
    found, so a missed substitution never silently produces an unmodified
    file.
    """
    old = "\\resumeItem{" + record.raw + "}"
    if tex_text.count(old) < 1:
        raise ValueError(
            f"could not locate \\resumeItem{{...}} for bullet {record.id!r} "
            f"(expected near line {record.start_line})"
        )
    new = "\\resumeItem{" + candidate + "}"
    return tex_text.replace(old, new, 1)


def compare_candidate(
    tex_path: Path,
    record: Bullet,
    candidate: str,
    *,
    tectonic_path: str | None = None,
) -> CandidateComparison:
    """Compile ``tex_path`` before and after swapping ``record`` for ``candidate``.

    Two full tectonic compiles are performed (baseline + modified); this is
    not cached or optimized further.
    """
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()

    with tempfile.TemporaryDirectory() as before_dir:
        before_compile = compile_tex(tex_path, Path(before_dir), tectonic_path=tectonic_path)
        before_metrics = measure_layout(before_compile.pdf_path, record.text)

    modified_text = substitute_bullet(original_text, record, candidate)
    after_metrics, after_overfull, after_box_warnings = measure_candidate_layout(
        tex_path, modified_text, strip_latex(candidate), tectonic_path=tectonic_path
    )

    return CandidateComparison(
        before=before_metrics,
        after=after_metrics,
        before_overfull=before_compile.overfull,
        after_overfull=after_overfull,
        after_box_warnings=after_box_warnings,
    )


@dataclass
class PlanComparison:
    before_page_count: int
    after_page_count: int
    before_overfull: bool
    after_overfull: bool
    after_box_warnings: list[BoxWarning]
    applied_ops: list[str]

    @property
    def page_count_changed(self) -> bool:
        return self.before_page_count != self.after_page_count

    @property
    def fits_one_page(self) -> bool:
        return self.after_page_count == 1


def apply_ops_in_memory(tex_path: Path, tex_text: str, ops: list[dict]) -> tuple[str, list[str]]:
    """Apply a list of structural edit ``ops`` to ``tex_text`` in sequence, in memory.

    Each op is one of:
      - ``{"op": "add_bullet", "role": str, "new_bullet": str,
        "position": "end"|"start"|"after", "after_index": int}``
      - ``{"op": "remove_bullet", "index": int}`` or
        ``{"op": "remove_bullet", "text": str}``
      - ``{"op": "remove_block", "role": str}``

    Targets are re-resolved against the *evolving* text before each op (via a
    temp-file round trip), since earlier ops shift line numbers. Returns
    ``(modified_text, summaries)`` -- one human-readable summary per op, in
    order. Never writes to ``tex_path``.
    """
    tex_path = Path(tex_path)
    current_text = tex_text
    summaries: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_tex = Path(tmp) / tex_path.name

        for op in ops:
            tmp_tex.write_text(current_text)
            kind = op.get("op")

            if kind == "add_bullet":
                block = find_role_block(tmp_tex, role=op["role"])
                current_text = insert_bullet_text(
                    current_text,
                    block,
                    op["new_bullet"],
                    position=op.get("position", "end"),
                    after_index=op.get("after_index"),
                )
                summaries.append(f"add_bullet(role={block.role!r})")
            elif kind == "remove_bullet":
                record = find_bullet_record(tmp_tex, index=op.get("index"), text=op.get("text"))
                current_text = remove_bullet_text(current_text, record)
                summaries.append(f"remove_bullet(id={record.id!r}, text={record.text!r})")
            elif kind == "remove_block":
                block = find_role_block(tmp_tex, role=op["role"])
                current_text = remove_role_block_text(current_text, block)
                summaries.append(f"remove_block(role={block.role!r})")
            else:
                raise ValueError(f"unknown op type: {kind!r}")

    return current_text, summaries


def compare_plan(
    tex_path: Path,
    ops: list[dict],
    *,
    tectonic_path: str | None = None,
) -> PlanComparison:
    """Compile ``tex_path`` before and after applying ``ops`` (see ``apply_ops_in_memory``).

    Read-only: both compiles run against temporary copies, and ``tex_path``
    is never written.
    """
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()

    with tempfile.TemporaryDirectory() as before_dir:
        before_compile = compile_tex(tex_path, Path(before_dir), tectonic_path=tectonic_path)
        before_page_count = page_count(before_compile.pdf_path)

    modified_text, summaries = apply_ops_in_memory(tex_path, original_text, ops)

    with tempfile.TemporaryDirectory() as after_dir:
        modified_tex = Path(after_dir) / tex_path.name
        modified_tex.write_text(modified_text)

        after_compile = compile_tex(modified_tex, Path(after_dir) / "out", tectonic_path=tectonic_path)
        after_page_count = page_count(after_compile.pdf_path)

    return PlanComparison(
        before_page_count=before_page_count,
        after_page_count=after_page_count,
        before_overfull=before_compile.overfull,
        after_overfull=after_compile.overfull,
        after_box_warnings=after_compile.box_warnings,
        applied_ops=summaries,
    )
