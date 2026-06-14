from pathlib import Path

import pytest

from conftest import RESUME_TEX, requires_tectonic
from resume_fitter.compile import CompileError, compile_tex


def test_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        compile_tex(tmp_path / "does_not_exist.tex", tmp_path)


def test_missing_tectonic_binary_raises(tmp_path):
    with pytest.raises(CompileError, match="tectonic not found"):
        compile_tex(RESUME_TEX, tmp_path, tectonic_path="/no/such/tectonic")


@requires_tectonic
def test_compiles_to_pdf(compiled_resume):
    assert compiled_resume.pdf_path.exists()
    assert compiled_resume.pdf_path.suffix == ".pdf"


@requires_tectonic
def test_box_warnings_is_a_list(compiled_resume):
    assert isinstance(compiled_resume.box_warnings, list)
    # overfull is derived from box_warnings and must agree
    assert compiled_resume.overfull == any(
        w.kind == "overfull" for w in compiled_resume.box_warnings
    )


@requires_tectonic
def test_compile_failure_includes_log_tail(tmp_path):
    bad_tex = tmp_path / "broken.tex"
    bad_tex.write_text(r"\documentclass{article}\begin{document}\nonexistentcommand\end{document}")

    with pytest.raises(CompileError) as exc_info:
        compile_tex(bad_tex, tmp_path / "out")

    assert "log tail" in str(exc_info.value)
