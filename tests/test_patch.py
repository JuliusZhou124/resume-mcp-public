from resume_fitter.bullets import find_bullet_record
from resume_fitter.compare import substitute_bullet
from resume_fitter.patch import diff_bullet, replace_bullet

from conftest import RESUME_TEX


def test_diff_bullet_is_read_only():
    before = RESUME_TEX.read_text()
    record = find_bullet_record(RESUME_TEX, index=0)

    diff_bullet(RESUME_TEX, record, "Onchain Trading Team")

    after = RESUME_TEX.read_text()
    assert before == after


def test_diff_bullet_contains_expected_change_lines():
    record = find_bullet_record(RESUME_TEX, index=0)

    result = diff_bullet(RESUME_TEX, record, "Onchain Trading Team")

    removed = [line for line in result.diff.splitlines() if line.startswith("-")]
    added = [line for line in result.diff.splitlines() if line.startswith("+")]
    assert any(f"\\resumeItem{{{record.raw}}}" in line for line in removed)
    assert any("\\resumeItem{Onchain Trading Team}" in line for line in added)

    expected_modified = substitute_bullet(RESUME_TEX.read_text(), record, "Onchain Trading Team")
    assert result.modified_text == expected_modified


def test_replace_bullet_writes_to_given_path(tmp_path):
    record = find_bullet_record(RESUME_TEX, index=0)

    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())

    result = replace_bullet(tex_copy, record, "Onchain Trading Team")

    assert "\\resumeItem{Onchain Trading Team}" in tex_copy.read_text()
    assert f"\\resumeItem{{{record.raw}}}" not in tex_copy.read_text()
    assert tex_copy.read_text() == result.modified_text

    # the real resume.tex is untouched
    assert f"\\resumeItem{{{record.raw}}}" in RESUME_TEX.read_text()
