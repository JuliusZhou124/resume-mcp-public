from dataclasses import replace

import pytest

from conftest import RESUME_TEX, requires_tectonic
from resume_fitter.bullets import find_bullet_record, find_role_block
from resume_fitter.compare import apply_ops_in_memory, compare_candidate, compare_plan, substitute_bullet
from resume_fitter.measure import BulletMetrics


def test_substitute_bullet_replaces_only_target_line():
    original = RESUME_TEX.read_text()
    record = find_bullet_record(RESUME_TEX, index=0)

    modified = substitute_bullet(original, record, "Onchain Trading Team")

    assert modified != original
    assert "\\resumeItem{Onchain Trading Team}" in modified
    assert f"\\resumeItem{{{record.raw}}}" not in modified

    # everything else is unchanged
    orig_lines = original.splitlines()
    mod_lines = modified.splitlines()
    assert len(orig_lines) == len(mod_lines)
    diff_lines = [i for i in range(len(orig_lines)) if orig_lines[i] != mod_lines[i]]
    assert diff_lines == [record.start_line - 1]


def test_substitute_bullet_raises_if_raw_not_found():
    original = RESUME_TEX.read_text()
    record = find_bullet_record(RESUME_TEX, index=0)
    bogus = replace(record, raw="this text does not appear in resume.tex")

    with pytest.raises(ValueError):
        substitute_bullet(original, bogus, "anything")


def test_substitute_bullet_never_writes_to_disk():
    before = RESUME_TEX.read_text()
    record = find_bullet_record(RESUME_TEX, index=0)
    substitute_bullet(before, record, "Onchain Trading Team")
    after = RESUME_TEX.read_text()
    assert before == after


def test_compare_candidate_page_count_changed_logic(monkeypatch):
    """page_count_changed reflects before/after page_count, computed via dataclass equality."""
    record = find_bullet_record(RESUME_TEX, index=0)

    same_page = BulletMetrics(lines=1, last_line_fullness=0.5, has_orphan=False, page_count=1)
    different_page = BulletMetrics(lines=1, last_line_fullness=0.5, has_orphan=False, page_count=2)

    from resume_fitter.compare import CandidateComparison

    unchanged = CandidateComparison(
        before=same_page, after=same_page, before_overfull=False, after_overfull=False, after_box_warnings=[]
    )
    assert unchanged.page_count_changed is False

    changed = CandidateComparison(
        before=same_page, after=different_page, before_overfull=False, after_overfull=False, after_box_warnings=[]
    )
    assert changed.page_count_changed is True


def test_apply_ops_in_memory_add_bullet():
    text = RESUME_TEX.read_text()

    modified, summaries = apply_ops_in_memory(
        RESUME_TEX, text, [{"op": "add_bullet", "role": "Northwind Cloud", "new_bullet": "New bullet."}]
    )

    assert r"\resumeItem{New bullet.}" in modified
    assert summaries == ["add_bullet(role='Software Engineering Intern @ Northwind Cloud')"]
    assert RESUME_TEX.read_text() == text


def test_apply_ops_in_memory_remove_bullet_then_remove_bullet_reresolves():
    text = RESUME_TEX.read_text()
    b1 = find_bullet_record(RESUME_TEX, index=1)
    b2 = find_bullet_record(RESUME_TEX, index=2)

    modified, summaries = apply_ops_in_memory(
        RESUME_TEX,
        text,
        [{"op": "remove_bullet", "index": 1}, {"op": "remove_bullet", "text": b2.text[:30]}],
    )

    assert b1.raw not in modified
    assert b2.raw not in modified
    assert summaries[0] == f"remove_bullet(id='b1', text={b1.text!r})"
    assert summaries[1] == f"remove_bullet(id='b1', text={b2.text!r})"


def test_apply_ops_in_memory_remove_block():
    text = RESUME_TEX.read_text()

    modified, summaries = apply_ops_in_memory(RESUME_TEX, text, [{"op": "remove_block", "role": "QA Engineer"}])

    assert "QA Engineer" not in modified
    assert summaries == ["remove_block(role='QA Engineer @ University Robotics Club')"]


def test_apply_ops_in_memory_unknown_op_raises():
    text = RESUME_TEX.read_text()

    with pytest.raises(ValueError):
        apply_ops_in_memory(RESUME_TEX, text, [{"op": "bogus"}])


@requires_tectonic
def test_compare_plan_noop_keeps_page_count():
    comparison = compare_plan(RESUME_TEX, [])

    assert comparison.before_page_count == 1
    assert comparison.after_page_count == 1
    assert comparison.fits_one_page is True
    assert comparison.page_count_changed is False
    assert comparison.applied_ops == []


@requires_tectonic
def test_compare_plan_add_and_remove_nets_out():
    ops = [
        {"op": "add_bullet", "role": "Northwind Cloud", "new_bullet": "Managed deployment infrastructure across multiple staging and production environments."},
        {"op": "add_bullet", "role": "Northwind Cloud", "new_bullet": "Built an abstraction layer to deploy services to bare-metal or cloud Kubernetes, validated against AWS EKS."},
        {"op": "remove_block", "role": "QA Engineer"},
    ]

    comparison = compare_plan(RESUME_TEX, ops)

    assert comparison.before_page_count == 1
    assert comparison.fits_one_page is True
    assert len(comparison.applied_ops) == 3


@requires_tectonic
def test_compare_candidate_end_to_end_no_page_change():
    record = find_bullet_record(RESUME_TEX, index=0)

    comparison = compare_candidate(RESUME_TEX, record, "Onchain Trading Team")

    assert comparison.before.page_count == 1
    assert comparison.after.page_count == 1
    assert comparison.page_count_changed is False
    # the candidate is a single line, much shorter than the current b0
    assert comparison.after.last_line_fullness < comparison.before.last_line_fullness
