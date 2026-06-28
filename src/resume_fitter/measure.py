"""Measure the rendered geometry of a single resume bullet from a compiled PDF."""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
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

# Above this fraction, a bullet's last (or only) line is so close to the
# right margin that small rendering differences can push it onto a phantom
# blank second line. This is a soft warning (see
# BulletMetrics.phantom_blank_line_risk), not a hard gate -- the recommended
# zone is FULLNESS_REQUIREMENT_THRESHOLD..PHANTOM_BLANK_LINE_CEILING.
PHANTOM_BLANK_LINE_CEILING = 0.98

# Per-PDF geometry cache: extracted words are expensive (pdfplumber opens the
# file and walks every glyph); a single editing session re-measures the same
# baseline PDF repeatedly (compile_and_score -> compare -> apply gate), so we
# cache the per-page extracted layout keyed on (resolved path, mtime, size).
# Bounded LRU so long sessions with many distinct temp PDFs don't accumulate.
_MAX_GEOMETRY_CACHE_ENTRIES = 32
_geometry_cache: "OrderedDict[tuple, PdfGeometry]" = OrderedDict()
_geometry_lock = threading.Lock()


@dataclass(frozen=True)
class _PageGeometry:
    """Layout of one page, extracted once and reused across measurements."""
    page_text_right: float          # right extent of content on this page
    lines: list[list[dict]]         # words grouped into rendered lines
    flat: list[tuple[int, dict]]    # (line_idx, word) in reading order
    flat_norm: list[str]            # normalized text of each flat word
    min_top: float
    max_bottom: float
    height: float                   # page height in points (page_fill_ratio)


@dataclass
class PdfGeometry:
    """Cached geometry of a compiled PDF: page count + per-page layout."""
    page_count: int
    pages: list[_PageGeometry]

    @property
    def last_page(self) -> _PageGeometry:
        return self.pages[-1]


def clear_geometry_cache() -> None:
    """Clear the PDF geometry cache (e.g. between isolated tests)."""
    with _geometry_lock:
        _geometry_cache.clear()


def _geometry_cache_key(pdf_path: Path) -> tuple:
    """Identity key for a PDF file: path + mtime + size (cheap invalidate)."""
    st = os.stat(pdf_path)
    return (str(pdf_path), st.st_mtime_ns, st.st_size)



class BulletNotFoundError(ValueError):
    """Raised when the requested bullet text can't be located in the rendered PDF."""

def _extract_page(page) -> _PageGeometry:
    """Extract all geometry one pdfplumber page will ever need, once."""
    words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
    if not words:
        return _PageGeometry(
            page_text_right=0.0, lines=[], flat=[], flat_norm=[],
            min_top=0.0, max_bottom=0.0, height=float(page.height),
        )
    page_text_right = max(w["x1"] for w in words)
    lines = _group_words_into_lines(words)
    flat = [(line_idx, w) for line_idx, line in enumerate(lines) for w in line]
    flat_norm = [_normalize(w["text"]) for _, w in flat]
    return _PageGeometry(
        page_text_right=page_text_right,
        lines=lines, flat=flat, flat_norm=flat_norm,
        min_top=min(w["top"] for w in words),
        max_bottom=max(w["bottom"] for w in words),
        height=float(page.height),
    )


def pdf_geometry(pdf_path: Path) -> PdfGeometry:
    """Return the cached :class:`PdfGeometry` for ``pdf_path``.

    Opens the PDF and extracts words exactly once per (path, mtime, size);
    repeated calls with the same compiled PDF skip pdfplumber entirely.
    Thread-safe; bounded LRU eviction so long sessions don't accumulate.
    """
    pdf_path = Path(pdf_path)
    key = _geometry_cache_key(pdf_path)
    with _geometry_lock:
        cached = _geometry_cache.get(key)
        if cached is not None:
            _geometry_cache.move_to_end(key)
            return cached
    with pdfplumber.open(pdf_path) as pdf:
        geom = PdfGeometry(
            page_count=len(pdf.pages),
            pages=[_extract_page(p) for p in pdf.pages],
        )
    with _geometry_lock:
        _geometry_cache[key] = geom
        _geometry_cache.move_to_end(key)
        while len(_geometry_cache) > _MAX_GEOMETRY_CACHE_ENTRIES:
            _geometry_cache.popitem(last=False)
    return geom


def page_count(pdf_path: Path) -> int:
    return pdf_geometry(pdf_path).page_count


def page_fill_ratio(pdf_path: Path) -> float:
    """Measure how much of the usable page height is filled with content.

    Returns a float in [0, 1+]: the fraction of the usable vertical area
    (between the top and bottom margins) occupied by rendered text.  Values
    near 1.0 mean the page is completely full; values >1.0 mean content
    spills past the bottom margin (LaTeX may still keep it on one page if
    the spill is small).  For multi-page PDFs, only the last page is
    measured.
    """
    page = pdf_geometry(pdf_path).last_page
    if not page.flat:
        return 0.0
    # Jake Gutierrez template uses 0.7in margins (~50.4pt).  We use the
    # actual content extent rather than hard-coded margins: the fill
    # ratio is how far down the page the last line sits relative to the
    # page height minus a standard bottom margin.
    bottom_margin = 50.4  # 0.7in in points
    usable_bottom = page.height - bottom_margin
    usable_top = page.min_top  # content starts at the first word
    span = usable_bottom - usable_top
    if span <= 0:
        return 1.0
    fill = (page.max_bottom - usable_top) / span
    return round(max(0.0, fill), 4)


@dataclass
class BulletMetrics:
    lines: int
    last_line_fullness: float
    has_orphan: bool
    page_count: int

    @property
    def meets_fullness_requirement(self) -> bool:
        return self.last_line_fullness >= FULLNESS_REQUIREMENT_THRESHOLD

    @property
    def phantom_blank_line_risk(self) -> bool:
        """True when the last line is so full it risks a phantom blank line.

        Soft warning, not a hard gate: revise toward the 0.90-0.98 zone
        rather than leaving a bullet at or near 100% fullness.
        """
        return self.last_line_fullness > PHANTOM_BLANK_LINE_CEILING


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
    # LaTeX ligatures
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
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
    n = len(target_words)

    geom = pdf_geometry(pdf_path)
    for page in geom.pages:
        flat = page.flat
        flat_norm = page.flat_norm
        if len(flat) < n:
            continue

        for start in range(len(flat) - n + 1):
            if flat_norm[start] != target_words[0]:
                continue
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

            span = page.page_text_right - bullet_left
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
                page_count=geom.page_count,
            )

    raise BulletNotFoundError(f"bullet text not found in rendered PDF: {bullet_text!r}")
