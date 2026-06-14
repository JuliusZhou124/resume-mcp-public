import dataclasses

from conftest import RESUME_TEX, requires_tectonic
from resume_fitter.skills import (
    SkillLookupError,
    compare_skill_candidate,
    compare_skill_evidence,
    diff_skill,
    extract_skill_categories,
    find_skill_record,
    replace_skill,
    substitute_skill,
)


def test_extract_parses_three_categories():
    categories = extract_skill_categories(RESUME_TEX)

    assert [c.id for c in categories] == ["s0", "s1", "s2"]
    assert [c.category for c in categories] == ["Languages", "Frameworks", "Developer Tools"]
    assert [c.start_line for c in categories] == [203, 204, 205]


def test_tokens_keep_slash_and_paren_tokens():
    categories = extract_skill_categories(RESUME_TEX)
    languages, frameworks = categories[0], categories[1]

    assert "C/C++" in languages.tokens
    assert "HTML/CSS" in languages.tokens
    assert "SQL (Postgres)" in languages.tokens

    # Frameworks' raw items string preserves the trailing space before "}"
    assert frameworks.items_raw.endswith("Agile ")
    assert frameworks.items == frameworks.items_raw.strip()
    assert frameworks.tokens[-1] == "Agile"


def test_find_skill_record_by_name_and_index():
    by_name = find_skill_record(RESUME_TEX, category="developer")
    by_index = find_skill_record(RESUME_TEX, index=2)

    assert by_name.category == "Developer Tools"
    assert by_index.category == "Developer Tools"
    assert by_name == by_index

    try:
        find_skill_record(RESUME_TEX, index=99)
        assert False, "expected SkillLookupError"
    except SkillLookupError:
        pass

    try:
        find_skill_record(RESUME_TEX, category="nonexistent")
        assert False, "expected SkillLookupError"
    except SkillLookupError:
        pass

    try:
        find_skill_record(RESUME_TEX, category="developer", index=2)
        assert False, "expected ValueError"
    except ValueError:
        pass

    try:
        find_skill_record(RESUME_TEX)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_substitute_skill_exact_match_and_idempotent_failure():
    record = find_skill_record(RESUME_TEX, index=1)  # Frameworks, trailing-space case
    tex_text = RESUME_TEX.read_text()

    modified = substitute_skill(tex_text, record, "FastAPI, Spring-Boot, React")
    assert "\\textbf{Frameworks}{: FastAPI, Spring-Boot, React}" in modified
    assert "\\textbf{Frameworks}{: " + record.items_raw + "}" not in modified

    stale_record = dataclasses.replace(record, items_raw="stale items")
    try:
        substitute_skill(tex_text, stale_record, "new items")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_diff_skill_is_read_only():
    before = RESUME_TEX.read_text()
    record = find_skill_record(RESUME_TEX, index=2)  # Developer Tools

    result = diff_skill(RESUME_TEX, record, record.items + ", Kubernetes")

    removed = [line for line in result.diff.splitlines() if line.startswith("-")]
    added = [line for line in result.diff.splitlines() if line.startswith("+")]
    assert any("\\textbf{Developer Tools}{: " + record.items_raw + "}" in line for line in removed)
    assert any("Kubernetes" in line for line in added)
    assert RESUME_TEX.read_text() == before


def test_compare_skill_evidence_reorder_only():
    record = find_skill_record(RESUME_TEX, index=2)  # Developer Tools
    reordered = ", ".join(reversed(record.tokens))

    evidence = compare_skill_evidence(record, reordered, RESUME_TEX)

    assert evidence.added == []
    assert evidence.removed == []
    assert evidence.has_unevidenced is False


def test_compare_skill_evidence_evidenced_addition():
    record = find_skill_record(RESUME_TEX, index=2)  # Developer Tools

    new_items = record.items + ", Kubernetes"
    evidence = compare_skill_evidence(record, new_items, RESUME_TEX)

    assert evidence.added == ["Kubernetes"]
    assert evidence.evidenced == ["Kubernetes"]
    assert evidence.unevidenced == []
    assert evidence.has_unevidenced is False


def test_compare_skill_evidence_unevidenced_addition():
    record = find_skill_record(RESUME_TEX, index=2)  # Developer Tools

    new_items = record.items + ", Rust"
    evidence = compare_skill_evidence(record, new_items, RESUME_TEX)

    assert evidence.added == ["Rust"]
    assert evidence.unevidenced == ["Rust"]
    assert evidence.has_unevidenced is True


def test_replace_skill_writes_to_given_path(tmp_path):
    record = find_skill_record(RESUME_TEX, index=2)  # Developer Tools

    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())

    new_items = record.items + ", Kubernetes"
    result = replace_skill(tex_copy, record, new_items)

    assert "\\textbf{Developer Tools}{: " + new_items + "}" in tex_copy.read_text()
    assert tex_copy.read_text() == result.modified_text

    # the real resume.tex is untouched
    assert "\\textbf{Developer Tools}{: " + record.items_raw + "}" in RESUME_TEX.read_text()


@requires_tectonic
def test_compare_skill_candidate_reorder_only_page_count_unchanged():
    record = find_skill_record(RESUME_TEX, index=2)  # Developer Tools
    reordered = ", ".join(reversed(record.tokens))

    comparison = compare_skill_candidate(RESUME_TEX, record, reordered)

    assert comparison.before_page_count == 1
    assert comparison.after_page_count == 1
    assert comparison.page_count_changed is False
