"""Tests for the perf/structure refactor: caches, text-core resolvers,
escaped-percent fix, and shared gate/compile helpers.

These are behavioral tests for the changes documented in the refactor PR:
geometry cache reuse, parse cache invalidation, ``*_from_text`` resolvers
matching their Path equivalents, ``apply_ops_in_memory`` running without temp
files, ``_METRIC_RE`` normalizing ``\\%``, and ``_before_after_compile``
sharing the compile skeleton.
"""

import os
import tempfile
from pathlib import Path

import pytest

from conftest import RESUME_TEX, requires_tectonic
from resume_fitter.bullets import (
    extract_bullets,
    extract_bullets_from_text,
    extract_role_blocks,
    extract_role_blocks_from_text,
    find_bullet_record,
    find_bullet_record_from_text,
    find_role_block,
    find_role_block_from_text,
    clear_parse_cache,
)
from resume_fitter.compare import _before_after_compile, apply_ops_in_memory, compare_plan
from resume_fitter.evaluate import _extract_metrics, compare_truth_risk
from resume_fitter.skills import (
    extract_skill_categories,
    extract_skill_categories_from_text,
    find_skill_record,
    find_skill_record_from_text,
    clear_skill_parse_cache,
)


# --- text-core / path-shell parity ----------------------------------------


def test_extract_bullets_from_text_matches_path_version():
    via_path = extract_bullets(RESUME_TEX)
    via_text = extract_bullets_from_text(RESUME_TEX.read_text())
    assert [b.text for b in via_path] == [b.text for b in via_text]
    assert [b.start_line for b in via_path] == [b.start_line for b in via_text]


def test_extract_role_blocks_from_text_matches_path_version():
    via_path = extract_role_blocks(RESUME_TEX)
    via_text = extract_role_blocks_from_text(RESUME_TEX.read_text())
    assert [b.role for b in via_path] == [b.role for b in via_text]
    assert [b.heading_start_line for b in via_path] == [
        b.heading_start_line for b in via_text
    ]


def test_extract_skill_categories_from_text_matches_path_version():
    via_path = extract_skill_categories(RESUME_TEX)
    via_text = extract_skill_categories_from_text(RESUME_TEX.read_text())
    assert [c.category for c in via_path] == [c.category for c in via_text]
    assert [c.tokens for c in via_path] == [c.tokens for c in via_text]


def test_find_bullet_record_from_text_matches_path_version():
    a = find_bullet_record(RESUME_TEX, index=0)
    b = find_bullet_record_from_text(RESUME_TEX.read_text(), index=0)
    assert a.text == b.text and a.start_line == b.start_line


def test_find_role_block_from_text_matches_path_version():
    a = find_role_block(RESUME_TEX, role="QA Engineer")
    b = find_role_block_from_text(RESUME_TEX.read_text(), role="QA Engineer")
    assert a.role == b.role and a.heading_start_line == b.heading_start_line


def test_find_skill_record_from_text_matches_path_version():
    a = find_skill_record(RESUME_TEX, index=0)
    b = find_skill_record_from_text(RESUME_TEX.read_text(), index=0)
    assert a.category == b.category and a.tokens == b.tokens


# --- parse cache invalidation ----------------------------------------------

def test_parse_cache_invalidates_on_mtime(tmp_path):
    tex = tmp_path / "r.tex"
    tex.write_text(RESUME_TEX.read_text())
    first = [b.text for b in extract_bullets(tex)]
    # Same content, same mtime -> cache hit, same result.
    assert [b.text for b in extract_bullets(tex)] == first
    # Mutate a word that appears inside a \resumeItem bullet's text.
    content = tex.read_text()
    # "microservices" appears in b0's text; swap it so the bullet text changes.
    tex.write_text(content.replace("microservices", "micro-services"))
    st = os.stat(tex)
    os.utime(tex, (st.st_atime, st.st_mtime + 2))
    changed = [b.text for b in extract_bullets(tex)]
    assert changed != first
    assert "micro-services" in changed[0]
    clear_parse_cache()


def test_skill_parse_cache_invalidates_on_mtime(tmp_path):
    tex = tmp_path / "r.tex"
    tex.write_text(RESUME_TEX.read_text())
    first = [c.items for c in extract_skill_categories(tex)]
    assert [c.items for c in extract_skill_categories(tex)] == first
    content = tex.read_text()
    tex.write_text(content.replace("Python", "Pythons"))
    st = os.stat(tex)
    os.utime(tex, (st.st_atime, st.st_mtime + 2))
    changed = [c.items for c in extract_skill_categories(tex)]
    assert changed != first
    clear_skill_parse_cache()


# --- apply_ops_in_memory runs without temp files ---------------------------


def test_apply_ops_in_memory_no_tempfile_round_trips(tmp_path):
    """apply_ops_in_memory resolves targets against evolving text with zero
    temp-file round-trips. The old implementation wrote ``current_text`` to a
    temp file each op so the Path-based resolvers could read it back; the
    refactor uses ``find_*_from_text`` directly. Assert by patching
    ``tempfile.TemporaryDirectory`` / ``NamedTemporaryFile`` so any regression
    to temp-file-per-op raises instead of silently passing."""
    from unittest.mock import patch

    txt = RESUME_TEX.read_text()
    before = tmp_path / "before.tex"
    before.write_text(txt)
    ops = [{"op": "remove_block", "role": "QA Engineer"}]

    with patch("resume_fitter.compare.tempfile.TemporaryDirectory", side_effect=AssertionError("apply_ops_in_memory created a temp dir")),\
         patch("resume_fitter.compare.tempfile.NamedTemporaryFile", side_effect=AssertionError("apply_ops_in_memory created a temp file")):
        modified, summaries = apply_ops_in_memory(before, txt, ops)

    assert len(summaries) == 1
    assert len(modified) < len(txt)  # we removed a block


# --- escaped-percent truth-risk fix ----------------------------------------


def test_extract_metrics_normalizes_escaped_percent():
    assert _extract_metrics("Cut latency by 40\\% across services") == {"40%"}
    assert _extract_metrics("Cut latency by 40% across services") == {"40%"}
    assert _extract_metrics("Improved perf 4x") == {"4x"}


def test_compare_truth_risk_escaped_percent_is_low():
    original = "Cut latency by 40% across services"
    candidate = "Cut latency by 40\\% across services"
    r = compare_truth_risk(original, candidate)
    assert r.truth_risk == "low", r
    assert r.changed_entities == []


# --- geometry cache reuse --------------------------------------------------


@requires_tectonic
def test_pdf_geometry_cached_across_measurements():
    from resume_fitter.measure import (
        clear_geometry_cache,
        measure_layout,
        page_count,
        page_fill_ratio,
        _geometry_cache,
    )
    clear_geometry_cache()
    with tempfile.TemporaryDirectory() as tmp:
        from resume_fitter.compile import compile_tex
        result = compile_tex(RESUME_TEX, Path(tmp))
        pdf = result.pdf_path
        n1 = page_count(pdf)
        fill1 = page_fill_ratio(pdf)
        bullet = find_bullet_record(RESUME_TEX, index=0).text
        m1 = measure_layout(pdf, bullet)
        # Second round hits the cache (single entry, same metrics).
        assert len(_geometry_cache) == 1
        n2 = page_count(pdf)
        fill2 = page_fill_ratio(pdf)
        m2 = measure_layout(pdf, bullet)
        assert (n2, fill2) == (n1, fill1)
        assert (m2.lines, m2.last_line_fullness) == (
            m1.lines,
            m1.last_line_fullness,
        )
        assert len(_geometry_cache) == 1  # no new entry on reuse
    clear_geometry_cache()
    clear_parse_cache()


# --- shared compile skeleton ----------------------------------------------


@requires_tectonic
def test_before_after_compile_returns_two_compiles_and_cleans_up():
    original = RESUME_TEX.read_text()
    record = find_bullet_record(RESUME_TEX, index=0)
    from resume_fitter.compare import substitute_bullet
    modified = substitute_bullet(original, record, record.text)  # no-op swap
    with _before_after_compile(RESUME_TEX, modified) as (before, after):
        assert before.pdf_path.exists()
        assert after.pdf_path.exists()
        assert before.pdf_path != after.pdf_path
        before_pdf, after_pdf = before.pdf_path, after.pdf_path
    # After the context exits, both temp PDFs must be cleaned up.
    assert not before_pdf.exists()
    assert not after_pdf.exists()
    clear_parse_cache()
