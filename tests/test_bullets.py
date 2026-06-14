import pytest

from conftest import ORPHAN_TEX, RESUME_TEX
from resume_fitter.bullets import (
    BulletLookupError,
    extract_bullets,
    find_bullet,
    list_bullets,
    strip_latex,
)


def test_list_bullets_excludes_preamble_and_comments():
    bullets = list_bullets(RESUME_TEX)

    # \resumeSubItem's definition contains a literal `\resumeItem{#1}` in the
    # preamble -- it must not show up as a bullet.
    assert "#1" not in bullets

    # Commented-out experience bullets (e.g. the IT Support section) must
    # not be picked up either.
    assert not any("Communicate with managers" in b for b in bullets)

    assert len(bullets) > 0


def test_strip_latex_unwraps_formatting_and_escapes():
    assert strip_latex(r"Utilized: NextJS, Zod \vspace{-2pt}") == "Utilized: NextJS, Zod"
    assert strip_latex(r"reducing app rerenders by 40\%.") == "reducing app rerenders by 40%."
    assert strip_latex(r"\textbf{Languages}{: Java, Python}") == "Languages: Java, Python"


def test_find_bullet_by_index():
    bullet = find_bullet(RESUME_TEX, index=0)
    assert bullet == (
        "Managed 102 blockchain RPC nodes across bare-metal and multi-cloud Kubernetes cluster deployments."
    )


def test_find_bullet_by_text_substring():
    bullet = find_bullet(RESUME_TEX, text="improved api performance")
    assert bullet.startswith("Improved API performance by 33%")


def test_find_bullet_index_out_of_range():
    with pytest.raises(BulletLookupError):
        find_bullet(RESUME_TEX, index=9999)


def test_find_bullet_text_not_found():
    with pytest.raises(BulletLookupError):
        find_bullet(RESUME_TEX, text="this text does not appear anywhere")


def test_find_bullet_requires_exactly_one_selector():
    with pytest.raises(ValueError):
        find_bullet(RESUME_TEX)
    with pytest.raises(ValueError):
        find_bullet(RESUME_TEX, text="x", index=0)


def test_extract_bullets_line_numbers():
    bullets = {b.text[:40]: b for b in extract_bullets(RESUME_TEX)}

    gemini_b0 = bullets["Managed 102 blockchain RPC nodes across "]
    assert gemini_b0.start_line == 143
    assert gemini_b0.end_line == 143

    migrated = bullets["Migrated www.cco.purdue.edu from .NET to"]
    assert migrated.start_line == 150

    improved = bullets["Improved API performance by 33% refactor"]
    assert improved.start_line == 151


def test_extract_bullets_section_and_role_context():
    bullets = {b.text[:40]: b for b in extract_bullets(RESUME_TEX)}

    gemini_b0 = bullets["Managed 102 blockchain RPC nodes across "]
    assert gemini_b0.section == "Work Experience"
    assert gemini_b0.role == "Incoming SWE Intern @ Gemini"

    improved = bullets["Improved API performance by 33% refactor"]
    assert improved.section == "Work Experience"
    assert improved.role == "Full Stack Developer @ Purdue Center for Career Opportunities"

    project = bullets["Built photorealistic world model engine "]
    assert project.section == "Projects"
    assert project.role.startswith("Raize")


def test_extract_bullets_ids_are_source_order():
    bullets = extract_bullets(RESUME_TEX)
    assert [b.id for b in bullets] == [f"b{i}" for i in range(len(bullets))]
    assert [b.index for b in bullets] == list(range(len(bullets)))


def test_extract_bullets_skips_technical_skills():
    bullets = extract_bullets(RESUME_TEX)
    assert not any("Languages: Java" in b.text for b in bullets)


def test_extract_bullets_skips_preamble_and_comments():
    bullets = extract_bullets(RESUME_TEX)
    assert not any(b.text == "#1" for b in bullets)
    assert not any("Communicate with managers" in b.text for b in bullets)


def test_extract_bullets_returns_empty_for_file_without_resumeitem():
    assert extract_bullets(ORPHAN_TEX) == []
