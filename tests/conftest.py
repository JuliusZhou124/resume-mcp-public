import shutil
import tempfile
from pathlib import Path

import pytest

from resume_fitter.compile import compile_tex

RESUME_TEX = Path(__file__).resolve().parent.parent / "resume.tex"
ORPHAN_TEX = Path(__file__).resolve().parent / "fixtures" / "orphan_sample.tex"

requires_tectonic = pytest.mark.skipif(
    shutil.which("tectonic") is None, reason="tectonic not found on PATH"
)


@pytest.fixture(scope="session")
def compiled_resume():
    """Compile resume.tex once and reuse the PDF across tests."""
    if shutil.which("tectonic") is None:
        pytest.skip("tectonic not found on PATH")

    with tempfile.TemporaryDirectory() as tmp:
        result = compile_tex(RESUME_TEX, Path(tmp))
        yield result
