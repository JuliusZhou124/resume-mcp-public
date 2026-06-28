"""Tests for the self-grounding bullet validator and page fill ratio scorer."""

from conftest import RESUME_TEX, requires_tectonic

from resume_fitter.evaluate import check_grounding
from resume_fitter.measure import page_fill_ratio


# -- check_grounding ----------------------------------------------------------


def test_grounding_returns_grounded_for_self_contained_bullet():
    """A bullet whose terms all appear elsewhere in the resume is grounded."""
    # "Django" appears in b2 ("Migrated ... from PHP to Django")
    candidate = "Improved Django API performance by 33%."
    result = check_grounding(candidate, RESUME_TEX.read_text())
    assert result.is_grounded
    assert "Django" in result.grounded
    assert result.ungrounded == []


def test_grounding_flags_unexplained_reference():
    """A bullet referencing 'grant scoring harness' (not in the resume) is ungrounded."""
    candidate = "Authored 15+ tests for the grant scoring harness, hardening production reliability."
    result = check_grounding(candidate, RESUME_TEX.read_text())
    assert not result.is_grounded
    assert "grant scoring harness" in result.ungrounded


def test_grounding_flags_mixed_case_technical_phrase():
    """Mixed-case internal terms like gRPC polling debug must be flagged."""
    candidate = "Authored 15+ tests for gRPC polling debug by hardening production reliability."
    result = check_grounding(candidate, RESUME_TEX.read_text())
    assert not result.is_grounded
    assert "gRPC polling debug" in result.ungrounded


def test_grounding_excludes_original_bullet_from_context():
    """A replacement can't ground itself in the very text it's replacing."""
    original = (
        "Built Grant Scoring Pipeline for tagging production proposals across regions."
    )
    candidate = (
        "Authored 15+ tests for the Grant Scoring Pipeline by hardening reliability."
    )
    resume_text = f"{original}\nOther bullet about React and FastAPI."

    result = check_grounding(candidate, resume_text, original=original)

    assert not result.is_grounded
    assert "Grant Scoring Pipeline" in result.ungrounded


def test_grounding_no_reference_terms_is_trivially_grounded():
    """A bullet with no proper nouns or technical phrases is trivially grounded."""
    candidate = "Improved performance by 33% by refactoring code."
    result = check_grounding(candidate, RESUME_TEX.read_text())
    assert result.is_grounded
    assert result.grounded == []
    assert result.ungrounded == []


def test_grounding_generic_terms_not_flagged():
    """Generic capitalized words (API, React, Docker) are not flagged as ungrounded."""
    candidate = "Built React components with improved API design using Docker."
    result = check_grounding(candidate, RESUME_TEX.read_text())
    # React, API, Docker are in the generic list or appear in the resume
    assert result.is_grounded


# -- page_fill_ratio ----------------------------------------------------------


@requires_tectonic
def test_page_fill_ratio_returns_float_between_0_and_2(compiled_resume):
    """page_fill_ratio returns a float in a reasonable range for a 1-page resume."""
    fill = page_fill_ratio(compiled_resume.pdf_path)
    assert isinstance(fill, float)
    assert 0.0 <= fill <= 2.0


@requires_tectonic
def test_page_fill_ratio_near_1_for_full_resume(compiled_resume):
    """A well-filled 1-page resume should have page_fill near 1.0."""
    fill = page_fill_ratio(compiled_resume.pdf_path)
    assert fill > 0.8, f"expected page_fill > 0.8 for a full resume, got {fill}"


@requires_tectonic
def test_page_fill_ratio_empty_pdf(tmp_path):
    """A PDF with minimal content should return a low fill ratio."""
    from resume_fitter.compile import compile_tex
    from pathlib import Path

    tex = tmp_path / "minimal.tex"
    tex.write_text(r"\documentclass{article}\pagestyle{empty}\begin{document}Hello world.\end{document}")
    result = compile_tex(tex, tmp_path / "out")
    fill = page_fill_ratio(result.pdf_path)
    # A single-line document has very low fill — well under 50%.
    assert fill < 0.5, f"expected low page_fill for minimal content, got {fill}"
