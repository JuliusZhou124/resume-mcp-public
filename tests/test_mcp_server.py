from conftest import RESUME_TEX, requires_tectonic
from resume_fitter import mcp_server


def test_list_bullets_returns_all_bullets():
    result = mcp_server.list_bullets()

    assert len(result["bullets"]) == 19
    assert result["bullets"][0]["text"] == (
        "Managed 64 internal microservice deployments across staging, canary, and production Kubernetes clusters daily."
    )
    assert result["bullets"][0]["id"] == "b0"


def test_get_bullet_by_index():
    result = mcp_server.get_bullet(index=3)

    assert result["bullet"].startswith("Improved API response times by 35%")
    assert result["source"]["section"] == "Work Experience"
    assert result["evaluation"]["xyz_score"] == 1.0


def test_get_bullet_by_text():
    result = mcp_server.get_bullet(text="improved api response")

    assert result["bullet"].startswith("Improved API response times by 35%")


def test_get_bullet_requires_exactly_one_selector():
    assert "error" in mcp_server.get_bullet()
    assert "error" in mcp_server.get_bullet(index=0, text="onchain")


def test_get_bullet_index_out_of_range():
    result = mcp_server.get_bullet(index=9999)
    assert "error" in result


def test_evaluate_candidate_pure_reword_is_low_truth_risk():
    result = mcp_server.evaluate_candidate(
        index=3,
        candidate="Improved API response times by 35% by refactoring query batching and caching key endpoints with Redis.",
    )

    assert result["truth_risk"] == "low"
    assert result["changed_entities"] == []
    assert result["candidate_evaluation"]["xyz_score"] == 1.0


def test_evaluate_candidate_new_metric_is_high_truth_risk():
    result = mcp_server.evaluate_candidate(
        index=3,
        candidate="Improved API response times by 1.5x by refactoring query batching.",
    )

    assert result["truth_risk"] == "high"
    assert "1.5x" in result["changed_entities"]


def test_diff_candidate_is_read_only():
    before = RESUME_TEX.read_text()

    result = mcp_server.diff_candidate(index=0, candidate="Onchain Trading Team")

    added = [line for line in result["diff"].splitlines() if line.startswith("+")]
    assert any("\\resumeItem{Onchain Trading Team}" in line for line in added)
    assert RESUME_TEX.read_text() == before


def test_apply_bullet_without_confirm_does_not_write():
    before = RESUME_TEX.read_text()

    result = mcp_server.apply_bullet(index=0, candidate="Onchain Trading Team", confirm=False)

    assert result["applied"] is False
    assert "diff" in result
    assert RESUME_TEX.read_text() == before


@requires_tectonic
def test_apply_bullet_with_confirm_writes_to_default_tex(tmp_path, monkeypatch):
    original_b0 = mcp_server.get_bullet(index=0)["bullet"]
    # b2's text renders at ~96% line fullness, well above the 0.9 gate.
    candidate = mcp_server.get_bullet(index=2)["bullet"]

    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)

    result = mcp_server.apply_bullet(index=0, candidate=candidate, confirm=True)

    assert result["applied"] is True
    assert f"\\resumeItem{{{candidate}}}" in tex_copy.read_text()
    assert f"\\resumeItem{{{original_b0}}}" not in tex_copy.read_text()

    # the real resume.tex is untouched
    assert f"\\resumeItem{{{original_b0}}}" in RESUME_TEX.read_text()


@requires_tectonic
def test_apply_bullet_with_confirm_refuses_sparse_candidate(tmp_path, monkeypatch):
    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)

    result = mcp_server.apply_bullet(index=0, candidate="Onchain Trading Team", confirm=True)

    assert result["applied"] is False
    assert "error" in result
    assert result["layout"]["meets_fullness_requirement"] is False
    # refused -- file untouched
    assert tex_copy.read_text() == RESUME_TEX.read_text()


@requires_tectonic
def test_compare_candidate_layout_reports_page_counts():
    result = mcp_server.compare_candidate_layout(index=0, candidate="Onchain Trading Team")

    assert result["before"]["page_count"] == 1
    assert result["after"]["page_count"] == 1
    assert result["page_count_changed"] is False


@requires_tectonic
def test_compile_and_score_reports_layout():
    expected_bullet = mcp_server.get_bullet(index=0)["bullet"]

    result = mcp_server.compile_and_score(index=0)

    assert result["bullet"] == expected_bullet
    assert result["layout"]["lines"] == 1
    assert result["layout"]["page_count"] == 1


def test_list_skill_categories_returns_three():
    result = mcp_server.list_skill_categories()

    assert len(result["categories"]) == 3
    assert result["categories"][0]["category"] == "Languages"
    assert result["categories"][0]["id"] == "s0"


def test_get_skill_category_by_name():
    result = mcp_server.get_skill_category(category="frameworks")

    assert result["category"] == "Frameworks"
    assert "React" in result["tokens"]


def test_get_skill_category_requires_exactly_one_selector():
    assert "error" in mcp_server.get_skill_category()
    assert "error" in mcp_server.get_skill_category(index=0, category="languages")


def test_evaluate_skill_candidate_flags_unevidenced():
    current = mcp_server.get_skill_category(index=2)["items"]  # Developer Tools

    evidenced = mcp_server.evaluate_skill_candidate(index=2, new_items=current + ", Kubernetes")
    assert evidenced["evidence"]["has_unevidenced"] is False
    assert evidenced["evidence"]["evidenced"] == ["Kubernetes"]

    unevidenced = mcp_server.evaluate_skill_candidate(index=2, new_items=current + ", Rust")
    assert unevidenced["evidence"]["has_unevidenced"] is True
    assert unevidenced["evidence"]["unevidenced"] == ["Rust"]


def test_diff_skill_candidate_is_read_only():
    before = RESUME_TEX.read_text()
    current = mcp_server.get_skill_category(index=2)["items"]

    result = mcp_server.diff_skill_candidate(index=2, new_items=current + ", Kubernetes")

    added = [line for line in result["diff"].splitlines() if line.startswith("+")]
    assert any("Kubernetes" in line for line in added)
    assert RESUME_TEX.read_text() == before


def test_apply_skill_category_without_confirm_does_not_write():
    before = RESUME_TEX.read_text()
    current = mcp_server.get_skill_category(index=2)["items"]

    result = mcp_server.apply_skill_category(index=2, new_items=current + ", Kubernetes", confirm=False)

    assert result["applied"] is False
    assert "diff" in result
    assert RESUME_TEX.read_text() == before


def test_apply_skill_category_with_confirm_writes(tmp_path, monkeypatch):
    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)

    current = mcp_server.get_skill_category(index=2)["items"]
    new_items = current + ", Kubernetes"

    result = mcp_server.apply_skill_category(index=2, new_items=new_items, confirm=True)

    assert result["applied"] is True
    assert "\\textbf{Developer Tools}{: " + new_items + "}" in tex_copy.read_text()

    # the real resume.tex is untouched
    assert "\\textbf{Developer Tools}{: " + current + "}" in RESUME_TEX.read_text()


def test_list_role_blocks_returns_extents():
    result = mcp_server.list_role_blocks()
    blocks = {b["role"]: b for b in result["blocks"]}

    northwind = blocks["Software Engineering Intern @ Northwind Cloud"]
    assert northwind["heading_start_line"] == 134
    assert northwind["block_end_line"] == 140
    assert northwind["has_item_list"] is True

    qa = blocks["QA Engineer @ University Robotics Club"]
    assert qa["heading_start_line"] == 166
    assert qa["block_end_line"] == 173


def test_add_bullet_without_confirm_does_not_write():
    before = RESUME_TEX.read_text()

    result = mcp_server.add_bullet(role="Northwind Cloud", new_bullet="New bullet.")

    assert result["applied"] is False
    assert r"\resumeItem{New bullet.}" in result["diff"]
    assert RESUME_TEX.read_text() == before


def test_add_bullet_unknown_role_returns_error():
    result = mcp_server.add_bullet(role="Nonexistent Role", new_bullet="New bullet.")
    assert "error" in result


@requires_tectonic
def test_add_bullet_with_confirm_writes(tmp_path, monkeypatch):
    # b2's text renders at ~96% line fullness, well above the 0.9 gate.
    new_bullet = mcp_server.get_bullet(index=2)["bullet"]

    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)

    result = mcp_server.add_bullet(role="Northwind Cloud", new_bullet=new_bullet, confirm=True)

    assert result["applied"] is True
    assert f"\\resumeItem{{{new_bullet}}}" in tex_copy.read_text()
    assert RESUME_TEX.read_text() != tex_copy.read_text()


@requires_tectonic
def test_add_bullet_with_confirm_refuses_sparse_bullet(tmp_path, monkeypatch):
    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)

    result = mcp_server.add_bullet(role="Northwind Cloud", new_bullet="New bullet.", confirm=True)

    assert result["applied"] is False
    assert "error" in result
    assert result["layout"]["meets_fullness_requirement"] is False
    # refused -- file untouched
    assert tex_copy.read_text() == RESUME_TEX.read_text()


def test_remove_bullet_without_confirm_does_not_write():
    before = RESUME_TEX.read_text()
    b0 = mcp_server.get_bullet(index=0)["bullet"]

    result = mcp_server.remove_bullet(index=0)

    assert result["applied"] is False
    assert result["bullet"] == b0
    assert f"-        \\resumeItem{{{b0}}}" in result["diff"]
    assert RESUME_TEX.read_text() == before


def test_remove_bullet_with_confirm_writes(tmp_path, monkeypatch):
    b0 = mcp_server.get_bullet(index=0)["bullet"]

    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)

    result = mcp_server.remove_bullet(index=0, confirm=True)

    assert result["applied"] is True
    assert f"\\resumeItem{{{b0}}}" not in tex_copy.read_text()
    assert f"\\resumeItem{{{b0}}}" in RESUME_TEX.read_text()


def test_remove_role_block_without_confirm_does_not_write():
    before = RESUME_TEX.read_text()

    result = mcp_server.remove_role_block(role="QA Engineer")

    assert result["applied"] is False
    assert "QA Engineer" in result["diff"]
    assert RESUME_TEX.read_text() == before


def test_remove_role_block_with_confirm_writes(tmp_path, monkeypatch):
    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)

    result = mcp_server.remove_role_block(role="QA Engineer", confirm=True)

    assert result["applied"] is True
    assert "QA Engineer" not in tex_copy.read_text()
    assert "QA Engineer" in RESUME_TEX.read_text()


def test_remove_role_block_ambiguous_role_returns_error():
    result = mcp_server.remove_role_block(role="@ Purdue")
    assert "error" in result


@requires_tectonic
def test_compare_plan_layout_reports_fit():
    result = mcp_server.compare_plan_layout(ops=[])

    assert result["before"]["page_count"] == 1
    assert result["after"]["page_count"] == 1
    assert result["fits_one_page"] is True
    assert result["page_count_changed"] is False
    assert result["applied_ops"] == []


@requires_tectonic
def test_compare_plan_layout_add_and_remove():
    ops = [
        {"op": "add_bullet", "role": "Northwind Cloud", "new_bullet": "Managed deployment infrastructure across multiple staging and production environments."},
        {"op": "add_bullet", "role": "Northwind Cloud", "new_bullet": "Built an abstraction layer to deploy services to bare-metal or cloud Kubernetes, validated against AWS EKS."},
        {"op": "remove_block", "role": "QA Engineer"},
    ]

    result = mcp_server.compare_plan_layout(ops=ops)

    assert "fits_one_page" in result
    assert len(result["applied_ops"]) == 3


def test_compare_plan_layout_bad_op_returns_error():
    result = mcp_server.compare_plan_layout(ops=[{"op": "bogus"}])
    assert "error" in result


@requires_tectonic
def test_compare_skill_layout_reports_page_counts():
    current = mcp_server.get_skill_category(index=2)["items"]
    tokens = current.split(", ")
    reordered = ", ".join(reversed(tokens))

    result = mcp_server.compare_skill_layout(index=2, new_items=reordered)

    assert result["before"]["page_count"] == 1
    assert result["after"]["page_count"] == 1
    assert result["page_count_changed"] is False
