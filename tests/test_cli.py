import json

import pytest

from conftest import RESUME_TEX, requires_tectonic
from resume_fitter.cli import main


@requires_tectonic
def test_cli_bullet_by_index(capsys):
    exit_code = main([str(RESUME_TEX), "--bullet-index", "0"])
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["bullet"] == (
        "Managed 64 internal microservice deployments across staging, canary, and production Kubernetes clusters daily."
    )
    assert out["layout"]["lines"] == 1
    assert out["layout"]["page_count"] == 1
    assert out["notes"]["page_count_changed"] is None


@requires_tectonic
def test_cli_emits_source_block(capsys):
    exit_code = main([str(RESUME_TEX), "--bullet-index", "0"])
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["source"]["id"] == "b0"
    assert out["source"]["index"] == 0
    assert out["source"]["start_line"] == 138
    assert out["source"]["end_line"] == 138
    assert out["source"]["section"] == "Work Experience"
    assert out["source"]["role"] == "Software Engineering Intern @ Northwind Cloud"
    assert out["source"]["context"] == "Work Experience > Software Engineering Intern @ Northwind Cloud"


@requires_tectonic
def test_cli_bullet_by_text(capsys):
    exit_code = main([str(RESUME_TEX), "--bullet", "improved api response"])
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["bullet"].startswith("Improved API response times by 35%")


def test_cli_bullet_index_out_of_range(capsys):
    exit_code = main([str(RESUME_TEX), "--bullet-index", "9999"])
    assert exit_code == 1

    err = json.loads(capsys.readouterr().err)
    assert "out of range" in err["error"]


def test_cli_bullet_text_not_found(capsys):
    exit_code = main([str(RESUME_TEX), "--bullet", "nonexistent bullet text"])
    assert exit_code == 1

    err = json.loads(capsys.readouterr().err)
    assert "no bullet matching" in err["error"]


def test_cli_requires_one_selector():
    with pytest.raises(SystemExit):
        main([str(RESUME_TEX)])


@requires_tectonic
def test_cli_candidate_mode_emits_before_after(capsys):
    exit_code = main(
        [str(RESUME_TEX), "--bullet-index", "0", "--candidate", "Onchain Trading Team"]
    )
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["bullet"] == (
        "Managed 64 internal microservice deployments across staging, canary, and production Kubernetes clusters daily."
    )
    assert out["candidate"] == "Onchain Trading Team"
    assert "layout" not in out
    assert out["before"]["page_count"] == 1
    assert out["after"]["page_count"] == 1
    assert out["notes"]["page_count_changed"] is False


@requires_tectonic
def test_cli_emits_evaluation_block(capsys):
    exit_code = main([str(RESUME_TEX), "--bullet-index", "2"])
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["evaluation"]["has_action_verb"] is True
    assert out["evaluation"]["has_metric"] is True
    assert out["evaluation"]["has_result_clause"] is True
    assert out["evaluation"]["xyz_score"] == 1.0


@requires_tectonic
def test_cli_candidate_mode_emits_diff_without_writing(capsys):
    before = RESUME_TEX.read_text()

    exit_code = main(
        [str(RESUME_TEX), "--bullet-index", "0", "--candidate", "Onchain Trading Team"]
    )
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    added = [line for line in out["diff"].splitlines() if line.startswith("+")]
    assert any("\\resumeItem{Onchain Trading Team}" in line for line in added)
    assert out["notes"]["applied"] is False
    assert RESUME_TEX.read_text() == before


@requires_tectonic
def test_cli_apply_writes_to_tex_path(tmp_path, capsys):
    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())

    exit_code = main(
        [str(tex_copy), "--bullet-index", "0", "--candidate", "Onchain Trading Team", "--apply"]
    )
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["notes"]["applied"] is True
    assert "\\resumeItem{Onchain Trading Team}" in tex_copy.read_text()

    # the real resume.tex is untouched
    assert (
        "\\resumeItem{Managed 64 internal microservice deployments across staging, canary, "
        "and production Kubernetes clusters daily.}"
    ) in RESUME_TEX.read_text()


def test_cli_apply_requires_candidate():
    with pytest.raises(SystemExit):
        main([str(RESUME_TEX), "--bullet-index", "0", "--apply"])


@requires_tectonic
def test_cli_candidate_mode_emits_truth_risk(capsys):
    exit_code = main(
        [
            str(RESUME_TEX),
            "--bullet-index",
            "2",
            "--candidate",
            "Improved API performance by 1.5x refactoring multipart payloads.",
        ]
    )
    assert exit_code == 0

    out = json.loads(capsys.readouterr().out)
    assert out["notes"]["truth_risk"] == "high"
    assert "1.5x" in out["notes"]["changed_entities"]
    assert "candidate_evaluation" in out
