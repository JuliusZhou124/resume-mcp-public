import tempfile
from pathlib import Path

import pytest

from conftest import RESUME_TEX, requires_tectonic
from resume_fitter.bullets import find_bullet
from resume_fitter.compile import compile_tex
from resume_fitter.measure import (
    BulletNotFoundError,
    _group_words_into_lines,
    _normalize,
    measure_layout,
)

ORPHAN_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orphan_sample.tex"


def test_normalize_collapses_whitespace_and_typographic_chars():
    assert _normalize("  Government’s   plan ") == "government's plan"
    assert _normalize("re-think") == "re-think"


def test_group_words_into_lines_clusters_by_y_position():
    words = [
        {"text": "a", "x0": 10, "x1": 20, "top": 100.0},
        {"text": "b", "x0": 30, "x1": 40, "top": 100.5},
        {"text": "c", "x0": 10, "x1": 20, "top": 120.0},
    ]
    lines = _group_words_into_lines(words)
    assert [[w["text"] for w in line] for line in lines] == [["a", "b"], ["c"]]


@requires_tectonic
def test_measure_single_line_bullet(compiled_resume):
    bullet = find_bullet(RESUME_TEX, index=0)  # "Onchain Team"
    metrics = measure_layout(compiled_resume.pdf_path, bullet)
    assert metrics.lines == 1
    assert metrics.has_orphan is False
    assert metrics.page_count == 1


@requires_tectonic
def test_measure_wrapping_bullet(compiled_resume):
    bullet = find_bullet(RESUME_TEX, text="Improved document processing throughput by 4x")
    metrics = measure_layout(compiled_resume.pdf_path, bullet)
    assert metrics.lines == 2
    assert metrics.has_orphan is False
    assert 0.7 < metrics.last_line_fullness <= 1.0


@requires_tectonic
def test_measure_bullet_not_found(compiled_resume):
    with pytest.raises(BulletNotFoundError):
        measure_layout(compiled_resume.pdf_path, "this text is not in the resume at all")


@requires_tectonic
def test_orphan_detection_against_crafted_fixture():
    with tempfile.TemporaryDirectory() as tmp:
        result = compile_tex(ORPHAN_FIXTURE, Path(tmp))

        normal = measure_layout(result.pdf_path, "A short bullet that fits on one line.")
        assert normal.lines == 1
        assert normal.has_orphan is False

        wrapped = measure_layout(
            result.pdf_path,
            "This bullet is crafted with a great deal of extra padding text so that "
            "it wraps cleanly across two full lines of content before it ends alone.",
        )
        assert wrapped.lines == 2
        assert wrapped.has_orphan is False

        orphaned = measure_layout(
            result.pdf_path,
            "This bullet is crafted with a great deal of extra padding text so that "
            "it wraps cleanly across two full lines of.",
        )
        assert orphaned.lines == 2
        assert orphaned.has_orphan is True
        assert orphaned.last_line_fullness < 0.15
