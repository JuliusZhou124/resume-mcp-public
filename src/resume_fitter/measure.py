"""Measure the rendered geometry of a single resume bullet from a compiled PDF."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pdfplumber

# Words within this many points of each other on the y-axis are treated as
# the same rendered line.
_LINE_TOLERANCE = 2.0

# A wrapped bullet whose last line is a single word, or falls below this
# fraction of the available text width, is flagged as an orphan.
ORPHAN_FULLNESS_THRESHOLD = 0.15

# A bullet's last (or only) rendered line must use at least this fraction of
# the available text width. Enforced as a hard gate by apply_bullet/add_bullet
# (see mcp_server.py) -- a candidate whose last line falls below this is
# refused even with confirm=True.
FULLNESS_REQUIREMENT_THRESHOLD = 0.9


class BulletNotFoundError(ValueError):
    """Raised when the requested bullet text can't be located in the rendered PDF."""


def page_count(pdf_path: Path) -> int:
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


@dataclass
class BulletMetrics:
    lines: int
    last_line_fullness: float
    has_orphan: bool
    page_count: int

    @property
    def meets_fullness_requirement(self) -> bool:
        return self.last_line_fullness >= FULLNESS_REQUIREMENT_THRESHOLD


def _group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    """Cluster words into rendered lines by y-position, then sort left-to-right."""
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        for line in lines:
            if abs(line[0]["top"] - word["top"]) <= _LINE_TOLERANCE:
                line.append(word)
                break
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


# LaTeX fonts render a plain `'` as a typographic right single quote; map it
# (and a few other common typographic substitutions) back to ASCII so source
# text matches PDF-extracted text.
_TYPOGRAPHIC_SUBSTITUTIONS = {
    "’": "'",  # right single quotation mark
    "‘": "'",  # left single quotation mark
    "“": '"',  # left double quotation mark
    "”": '"',  # right double quotation mark
    "–": "-",  # en dash
    "—": "-",  # em dash
}


def _normalize(text: str) -> str:
    for typographic, ascii_equiv in _TYPOGRAPHIC_SUBSTITUTIONS.items():
        text = text.replace(typographic, ascii_equiv)
    return " ".join(text.split()).lower()


def measure_layout(pdf_path: Path, bullet_text: str) -> BulletMetrics:
    """Locate ``bullet_text`` in the rendered PDF and measure its layout.

    ``bullet_text`` should be the bullet's full plain-text content (see
    ``bullets.find_bullet``), since line count and fullness are computed
    relative to the bullet's first and last rendered lines.
    """
    target_words = _normalize(bullet_text).split()
    if not target_words:
        raise ValueError("bullet_text is empty")

    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)

        for page in pdf.pages:
            words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            if not words:
                continue

            page_text_right = max(w["x1"] for w in words)
            lines = _group_words_into_lines(words)

            flat = [(line_idx, w) for line_idx, line in enumerate(lines) for w in line]
            flat_norm = [_normalize(w["text"]) for _, w in flat]

            n = len(target_words)
            for start in range(len(flat) - n + 1):
                if flat_norm[start : start + n] != target_words:
                    continue

                matched = flat[start : start + n]
                line_order: list[int] = []
                for line_idx, _ in matched:
                    if line_idx not in line_order:
                        line_order.append(line_idx)

                first_line_idx = line_order[0]
                last_line_idx = line_order[-1]
                bullet_left = min(
                    w["x0"] for li, w in matched if li == first_line_idx
                )
                last_line_matched = [w for li, w in matched if li == last_line_idx]
                last_line_right = max(w["x1"] for w in last_line_matched)

                span = page_text_right - bullet_left
                fullness = (last_line_right - bullet_left) / span if span > 0 else 1.0
                fullness = max(0.0, min(fullness, 1.0))

                n_lines = len(line_order)
                has_orphan = n_lines >= 2 and (
                    len(last_line_matched) == 1 or fullness < ORPHAN_FULLNESS_THRESHOLD
                )

                return BulletMetrics(
                    lines=n_lines,
                    last_line_fullness=round(fullness, 4),
                    has_orphan=has_orphan,
                    page_count=page_count,
                )

    raise BulletNotFoundError(f"bullet text not found in rendered PDF: {bullet_text!r}")
