import pytest

from conftest import RESUME_TEX
from resume_fitter.bullets import (
    BlockLookupError,
    find_bullet_record,
    find_role_block,
)
from resume_fitter.structure import (
    apply_insert_bullet,
    apply_remove_bullet,
    apply_remove_role_block,
    diff_insert_bullet,
    diff_remove_bullet,
    diff_remove_role_block,
    insert_bullet_text,
    remove_bullet_text,
    remove_role_block_text,
)


def test_extract_role_blocks_line_extents():
    northwind = find_role_block(RESUME_TEX, role="Northwind Cloud")
    assert northwind.heading_start_line == 134
    assert northwind.block_end_line == 140
    assert northwind.item_list_start_line == 137
    assert northwind.item_list_end_line == 140
    assert northwind.has_item_list is True
    assert northwind.section == "Work Experience"

    qa = find_role_block(RESUME_TEX, role="QA Engineer")
    assert qa.heading_start_line == 166
    assert qa.block_end_line == 173
    assert qa.has_item_list is True

    trailmap = find_role_block(RESUME_TEX, role="Trailmap")
    assert trailmap.heading_start_line == 181
    assert trailmap.block_end_line == 187
    assert trailmap.heading_macro == r"\resumeProjectHeading"

    lendwise = find_role_block(RESUME_TEX, role="Lendwise")
    assert lendwise.heading_start_line == 189
    assert lendwise.block_end_line == 195


def test_find_role_block_ambiguous_and_not_found():
    with pytest.raises(BlockLookupError):
        find_role_block(RESUME_TEX, role="nonexistent role")

    # role strings only, so check a substring that hits two project role titles.
    with pytest.raises(BlockLookupError):
        find_role_block(RESUME_TEX, role="TypeScript")


def test_insert_bullet_text_end_start_after():
    text = RESUME_TEX.read_text()
    northwind = find_role_block(RESUME_TEX, role="Northwind Cloud")
    b0 = find_bullet_record(RESUME_TEX, index=0)
    b1 = find_bullet_record(RESUME_TEX, index=1)

    end_result = insert_bullet_text(text, northwind, "New end bullet.", position="end")
    end_lines = end_result.splitlines()
    assert end_lines[137] == f"        \\resumeItem{{{b0.raw}}}"
    assert end_lines[138] == f"        \\resumeItem{{{b1.raw}}}"
    assert end_lines[139] == r"        \resumeItem{New end bullet.}"
    assert end_lines[140] == r"    \resumeItemListEnd"

    start_result = insert_bullet_text(text, northwind, "New start bullet.", position="start")
    start_lines = start_result.splitlines()
    assert start_lines[137] == r"        \resumeItem{New start bullet.}"
    assert start_lines[138] == f"        \\resumeItem{{{b0.raw}}}"

    after_result = insert_bullet_text(text, northwind, "New after bullet.", position="after", after_index=0)
    after_lines = after_result.splitlines()
    assert after_lines[137] == f"        \\resumeItem{{{b0.raw}}}"
    assert after_lines[138] == r"        \resumeItem{New after bullet.}"


def test_insert_bullet_text_after_index_out_of_range():
    text = RESUME_TEX.read_text()
    northwind = find_role_block(RESUME_TEX, role="Northwind Cloud")

    with pytest.raises(ValueError):
        insert_bullet_text(text, northwind, "x", position="after", after_index=5)


def test_insert_bullet_text_requires_item_list():
    text = RESUME_TEX.read_text()
    education = find_role_block(RESUME_TEX, role="State University")

    assert education.has_item_list is False
    with pytest.raises(ValueError):
        insert_bullet_text(text, education, "x")


def test_remove_bullet_text_deletes_exact_line():
    text = RESUME_TEX.read_text()
    record = find_bullet_record(RESUME_TEX, index=0)

    modified = remove_bullet_text(text, record)

    assert f"\\resumeItem{{{record.raw}}}" not in modified
    assert len(text.splitlines()) - len(modified.splitlines()) == 1


def test_remove_bullet_text_raises_on_stale_record():
    import dataclasses

    text = RESUME_TEX.read_text()
    record = find_bullet_record(RESUME_TEX, index=0)
    stale = dataclasses.replace(record, raw="this text does not appear in resume.tex")

    with pytest.raises(ValueError):
        remove_bullet_text(text, stale)


def test_remove_role_block_text_removes_whole_entry():
    text = RESUME_TEX.read_text()
    qa = find_role_block(RESUME_TEX, role="QA Engineer")

    modified = remove_role_block_text(text, qa)

    assert "QA Engineer" not in modified
    assert "University Robotics Club" not in modified
    # neighboring entries are untouched
    assert "Open Source Collective" in modified
    assert modified.count(r"\resumeItemListStart") == text.count(r"\resumeItemListStart") - 1
    assert modified.count(r"\resumeItemListEnd") == text.count(r"\resumeItemListEnd") - 1
    assert len(text.splitlines()) - len(modified.splitlines()) == qa.block_end_line - qa.heading_start_line + 1


def test_diff_functions_are_read_only():
    before = RESUME_TEX.read_text()
    northwind = find_role_block(RESUME_TEX, role="Northwind Cloud")
    record = find_bullet_record(RESUME_TEX, index=0)
    qa = find_role_block(RESUME_TEX, role="QA Engineer")

    insert_diff = diff_insert_bullet(RESUME_TEX, northwind, "New bullet.")
    assert "+        \\resumeItem{New bullet.}" in insert_diff.diff

    remove_diff = diff_remove_bullet(RESUME_TEX, record)
    assert f"-        \\resumeItem{{{record.raw}}}" in remove_diff.diff

    block_diff = diff_remove_role_block(RESUME_TEX, qa)
    assert "-    \\resumeSubheading" in block_diff.diff

    assert RESUME_TEX.read_text() == before


def test_apply_functions_write_to_given_path_only(tmp_path):
    before = RESUME_TEX.read_text()

    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(before)

    northwind = find_role_block(tex_copy, role="Northwind Cloud")
    apply_insert_bullet(tex_copy, northwind, "New bullet.")
    assert r"\resumeItem{New bullet.}" in tex_copy.read_text()

    record = find_bullet_record(tex_copy, text="New bullet.")
    apply_remove_bullet(tex_copy, record)
    assert r"\resumeItem{New bullet.}" not in tex_copy.read_text()

    qa = find_role_block(tex_copy, role="QA Engineer")
    apply_remove_role_block(tex_copy, qa)
    assert "QA Engineer" not in tex_copy.read_text()

    # the real resume.tex is untouched
    assert RESUME_TEX.read_text() == before
