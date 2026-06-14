"""Compile a LaTeX resume with tectonic and capture box warnings.

tectonic uses an XeTeX-derived engine and does not implement the pdfTeX-only
``\\pdfglyphtounicode`` primitive that some resume templates pull in (via
``\\input{glyphtounicode}`` / ``\\pdfgentounicode=1``) purely to make the PDF
text ATS-parsable. That has no effect on rendered layout, so a temp copy of
the source has those lines neutralized before compiling.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_PDFTEX_ONLY_PATTERNS = [
    re.compile(r"^\\input\{glyphtounicode\}", re.MULTILINE),
    re.compile(r"^\\pdfgentounicode=1", re.MULTILINE),
]

_BOX_WARNING_RE = re.compile(
    r"(Overfull|Underfull) \\hbox \("
    r"(?:([\d.]+)pt too wide|badness (\d+))"
    r"\) in paragraph at lines (\d+)--(\d+)"
)


class CompileError(RuntimeError):
    """Raised when tectonic fails to produce a PDF."""


@dataclass
class BoxWarning:
    kind: str  # "overfull" or "underfull"
    amount_pt: float | None
    badness: int | None
    src_lines: str


@dataclass
class CompileResult:
    pdf_path: Path
    log_text: str
    box_warnings: list[BoxWarning] = field(default_factory=list)

    @property
    def overfull(self) -> bool:
        return any(w.kind == "overfull" for w in self.box_warnings)


def _sanitize_for_tectonic(tex_text: str) -> str:
    for pattern in _PDFTEX_ONLY_PATTERNS:
        tex_text = pattern.sub(
            lambda m: "% " + m.group(0) + " (stripped for tectonic)", tex_text
        )
    return tex_text


def _parse_box_warnings(log_text: str) -> list[BoxWarning]:
    warnings = []
    for match in _BOX_WARNING_RE.finditer(log_text):
        kind, pt, badness, line_a, line_b = match.groups()
        warnings.append(
            BoxWarning(
                kind=kind.lower(),
                amount_pt=float(pt) if pt is not None else None,
                badness=int(badness) if badness is not None else None,
                src_lines=f"{line_a}--{line_b}",
            )
        )
    return warnings


def compile_tex(
    tex_path: Path,
    outdir: Path,
    tectonic_path: str | None = None,
    timeout: int = 120,
) -> CompileResult:
    """Compile ``tex_path`` with tectonic into ``outdir`` and return the result.

    Raises ``FileNotFoundError`` if ``tex_path`` doesn't exist and
    ``CompileError`` if tectonic is missing or the compile fails.
    """
    tex_path = Path(tex_path)
    if not tex_path.is_file():
        raise FileNotFoundError(f"resume source not found: {tex_path}")

    tectonic = shutil.which(tectonic_path or "tectonic")
    if tectonic is None:
        raise CompileError(
            f"tectonic not found: {tectonic_path or 'tectonic (searched PATH)'}"
        )

    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    sanitized = _sanitize_for_tectonic(tex_path.read_text())
    work_tex = outdir / tex_path.name
    work_tex.write_text(sanitized)

    proc = subprocess.run(
        [
            tectonic,
            "--keep-logs",
            "--chatter",
            "minimal",
            "--outdir",
            str(outdir),
            str(work_tex),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    log_path = outdir / (work_tex.stem + ".log")
    log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
    pdf_path = outdir / (work_tex.stem + ".pdf")

    if proc.returncode != 0 or not pdf_path.exists():
        tail = "\n".join(log_text.splitlines()[-25:])
        raise CompileError(
            f"tectonic failed (exit {proc.returncode}) for {tex_path}\n"
            f"--- stderr ---\n{proc.stderr}\n--- log tail ---\n{tail}"
        )

    return CompileResult(
        pdf_path=pdf_path,
        log_text=log_text,
        box_warnings=_parse_box_warnings(log_text),
    )
