"""Structured bullet extraction.

Implements CONCEPT.md's ``extract_bullets()``: locates every ``\\resumeItem{...}``
in the document body (skipping the preamble and ``%``-comments) and returns a
``Bullet`` record per bullet with its plain text, source line range, and the
enclosing section / role context (derived from the nearest preceding
``\\section``, ``\\resumeSubheading``, or ``\\resumeProjectHeading``).

Scope: only ``\\resumeItem{...}`` is treated as a bullet. The plain
``\\begin{itemize}...\\item{...}`` block under "Technical Skills" is a skill
list, not a rewritable prose bullet, and is intentionally not extracted.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

_WRAP_COMMAND_RE = re.compile(r"\\(?:textbf|textit|emph|underline|small|texttt)\s*\{")

_ESCAPED_CHARS = {
    r"\%": "%",
    r"\&": "&",
    r"\$": "$",
    r"\#": "#",
    r"\_": "_",
}

# macro name -> number of brace-delimited arguments to capture
_MACRO_ARG_COUNTS = {
    r"\section": 1,
    r"\resumeSubheading": 4,
    r"\resumeProjectHeading": 2,
    r"\resumeItem": 1,
}


class BulletLookupError(ValueError):
    """Raised when a bullet can't be resolved from the source by index or text."""


class BlockLookupError(ValueError):
    """Raised when a role/project block can't be resolved by role substring."""


@dataclass(frozen=True)
class Bullet:
    id: str
    index: int
    text: str
    raw: str
    start_line: int
    end_line: int
    section: str | None
    role: str | None


@dataclass(frozen=True)
class RoleBlock:
    """The full source extent of one ``\\resumeSubheading``/``\\resumeProjectHeading``
    entry: its heading line through the last line of its ``\\resumeItem`` list
    (or, if it has no item list, through its last heading/tabular line).
    """

    role: str
    section: str | None
    heading_macro: str
    heading_start_line: int
    block_end_line: int
    item_list_start_line: int | None
    item_list_end_line: int | None
    has_item_list: bool


def strip_latex(text: str) -> str:
    """Approximate the rendered plain text of a ``\\resumeItem`` body.

    Unwraps simple formatting commands (``\\textbf{...}`` etc.), drops
    ``\\vspace{...}``, un-escapes ``\\%`` / ``\\&`` / etc., and collapses
    whitespace. Good enough for matching against PDF-extracted words; not a
    general LaTeX-to-text converter.
    """
    prev = None
    while prev != text:
        prev = text
        text = _WRAP_COMMAND_RE.sub("", text)

    text = re.sub(r"\\vspace\{[^}]*\}", "", text)
    text = text.replace("{", "").replace("}", "")

    for escaped, plain in _ESCAPED_CHARS.items():
        text = text.replace(escaped, plain)

    return " ".join(text.split())


def _strip_line_comment(line: str) -> str:
    """Remove a LaTeX ``%`` comment (but not escaped ``\\%``) from one line."""
    chars = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line):
            chars.append(line[i : i + 2])
            i += 2
            continue
        if line[i] == "%":
            break
        chars.append(line[i])
        i += 1
    return "".join(chars)


def _is_macro_at(body: str, idx: int, macro: str) -> bool:
    """True if ``macro`` occurs at ``idx`` and isn't a prefix of a longer name."""
    if body[idx : idx + len(macro)] != macro:
        return False
    end = idx + len(macro)
    return end >= len(body) or not body[end].isalpha()


def _read_args(body: str, pos: int, nargs: int) -> tuple[list[str], int, bool]:
    """Read ``nargs`` consecutive ``{...}`` groups (balanced) starting at ``pos``."""
    args = []
    for _ in range(nargs):
        while pos < len(body) and body[pos] in " \t\r\n":
            pos += 1
        if pos >= len(body) or body[pos] != "{":
            return args, pos, False
        depth = 1
        start = pos + 1
        pos += 1
        while pos < len(body) and depth > 0:
            if body[pos] == "{":
                depth += 1
            elif body[pos] == "}":
                depth -= 1
            pos += 1
        args.append(body[start : pos - 1])
    return args, pos, True


def _scan_macros(body: str) -> list[tuple[str, list[str], int, int]]:
    """Find every recognized macro call in ``body``, in document order.

    Returns ``(macro, args, start_offset, end_offset)`` tuples sorted by
    ``start_offset``.
    """
    events = []
    for macro, nargs in _MACRO_ARG_COUNTS.items():
        search_from = 0
        while True:
            idx = body.find(macro, search_from)
            if idx == -1:
                break
            if not _is_macro_at(body, idx, macro):
                search_from = idx + 1
                continue
            args, end_pos, ok = _read_args(body, idx + len(macro), nargs)
            if ok:
                events.append((macro, args, idx, end_pos))
            search_from = idx + 1
    events.sort(key=lambda e: e[2])
    return events


def _load_body(tex_path: Path):
    """Strip comments from the document body and return helpers shared by the
    bullet- and role-block-extraction passes.

    Returns ``(stripped_lines, line_numbers, body, offset_to_line)`` where
    ``stripped_lines``/``line_numbers`` are parallel lists (the document body,
    one entry per source line, with ``%``-comments removed, and that line's
    1-based source line number), ``body`` is those lines joined with ``"\\n"``
    (the string ``_scan_macros`` operates on), and ``offset_to_line`` maps a
    character offset into ``body`` back to a 1-based source line number.
    """
    raw_lines = Path(tex_path).read_text().splitlines()

    body_start_idx = 0
    for i, line in enumerate(raw_lines):
        if r"\begin{document}" in line:
            body_start_idx = i + 1
            break

    stripped_lines = [_strip_line_comment(line) for line in raw_lines[body_start_idx:]]
    line_numbers = list(range(body_start_idx + 1, len(raw_lines) + 1))

    body = "\n".join(stripped_lines)
    line_starts = []
    offset = 0
    for line in stripped_lines:
        line_starts.append(offset)
        offset += len(line) + 1  # +1 for the joining "\n"

    def offset_to_line(off: int) -> int:
        idx = bisect_right(line_starts, off) - 1
        idx = max(0, min(idx, len(line_numbers) - 1))
        return line_numbers[idx]

    return stripped_lines, line_numbers, body, offset_to_line


def extract_bullets(tex_path: Path) -> list[Bullet]:
    """Return every ``\\resumeItem`` in source order with location and context."""
    _, _, body, offset_to_line = _load_body(tex_path)

    bullets: list[Bullet] = []
    current_section: str | None = None
    current_role: str | None = None

    for macro, args, start, end in _scan_macros(body):
        if macro == r"\section":
            current_section = strip_latex(args[0])
            current_role = None
        elif macro == r"\resumeSubheading":
            title = strip_latex(args[0])
            org = strip_latex(args[2]) if len(args) > 2 else ""
            current_role = f"{title} @ {org}" if org else title
        elif macro == r"\resumeProjectHeading":
            current_role = strip_latex(args[0])
        elif macro == r"\resumeItem":
            bullets.append(
                Bullet(
                    id=f"b{len(bullets)}",
                    index=len(bullets),
                    text=strip_latex(args[0]),
                    raw=args[0],
                    start_line=offset_to_line(start),
                    end_line=offset_to_line(end - 1),
                    section=current_section,
                    role=current_role,
                )
            )

    return bullets


def list_bullets(tex_path: Path) -> list[str]:
    """Return the plain-text content of every ``\\resumeItem`` in source order."""
    return [bullet.text for bullet in extract_bullets(tex_path)]


def find_bullet_record(
    tex_path: Path,
    *,
    text: str | None = None,
    index: int | None = None,
) -> Bullet:
    """Resolve a bullet identifier to its full structured record.

    Exactly one of ``text`` (case-insensitive substring of the bullet's
    rendered text) or ``index`` (0-based, in source order) must be given.
    """
    if (text is None) == (index is None):
        raise ValueError("specify exactly one of text or index")

    bullets = extract_bullets(tex_path)

    if index is not None:
        if not (0 <= index < len(bullets)):
            raise BulletLookupError(
                f"bullet index {index} out of range (found {len(bullets)} bullets)"
            )
        return bullets[index]

    needle = text.lower()
    for bullet in bullets:
        if needle in bullet.text.lower():
            return bullet

    raise BulletLookupError(f"no bullet matching text: {text!r}")


def find_bullet(
    tex_path: Path,
    *,
    text: str | None = None,
    index: int | None = None,
) -> str:
    """Resolve a bullet identifier to its plain-text content. See ``find_bullet_record``."""
    return find_bullet_record(tex_path, text=text, index=index).text


# Markers (besides "\resumeItemListEnd") that close a role/project block that
# has no \resumeItem list of its own -- the next heading, the end of the
# enclosing \resumeSubHeadingListStart...End, or a new \section.
_BLOCK_STOP_MARKERS = (
    r"\resumeSubheading",
    r"\resumeProjectHeading",
    r"\resumeSubHeadingListEnd",
    r"\section{",
)


def extract_role_blocks(tex_path: Path) -> list[RoleBlock]:
    """Return every ``\\resumeSubheading``/``\\resumeProjectHeading`` entry in
    source order, with its full source extent (heading line through its
    ``\\resumeItemListEnd``, or through its last heading/tabular line if it
    has no item list).
    """
    stripped_lines, line_numbers, body, offset_to_line = _load_body(tex_path)
    line_to_idx = {ln: i for i, ln in enumerate(line_numbers)}

    current_section: str | None = None
    blocks: list[RoleBlock] = []

    for macro, args, start, _end in _scan_macros(body):
        if macro == r"\section":
            current_section = strip_latex(args[0])
            continue
        if macro not in (r"\resumeSubheading", r"\resumeProjectHeading"):
            continue

        if macro == r"\resumeSubheading":
            title = strip_latex(args[0])
            org = strip_latex(args[2]) if len(args) > 2 else ""
            role = f"{title} @ {org}" if org else title
        else:
            role = strip_latex(args[0])

        heading_start_line = offset_to_line(start)
        start_idx = line_to_idx[heading_start_line]

        item_list_start_line: int | None = None
        item_list_end_line: int | None = None
        block_end_line = heading_start_line
        has_item_list = False

        for idx in range(start_idx + 1, len(stripped_lines)):
            line = stripped_lines[idx]
            ln = line_numbers[idx]

            if any(marker in line for marker in _BLOCK_STOP_MARKERS):
                break

            block_end_line = ln

            if item_list_start_line is None and r"\resumeItemListStart" in line:
                item_list_start_line = ln
            if r"\resumeItemListEnd" in line:
                item_list_end_line = ln
                has_item_list = True
                break

        blocks.append(
            RoleBlock(
                role=role,
                section=current_section,
                heading_macro=macro,
                heading_start_line=heading_start_line,
                block_end_line=block_end_line,
                item_list_start_line=item_list_start_line,
                item_list_end_line=item_list_end_line,
                has_item_list=has_item_list,
            )
        )

    return blocks


def find_role_block(tex_path: Path, *, role: str) -> RoleBlock:
    """Resolve a role/project block by a case-insensitive substring of its
    ``role`` string (e.g. ``"Gemini"`` or ``"QA Engineer"``).

    Raises ``BlockLookupError`` if zero or more than one block matches --
    ambiguity must be resolved by the caller with a more specific substring.
    """
    blocks = extract_role_blocks(tex_path)
    needle = role.lower()
    matches = [b for b in blocks if needle in b.role.lower()]

    if not matches:
        raise BlockLookupError(f"no role block matching: {role!r}")
    if len(matches) > 1:
        raise BlockLookupError(
            f"role {role!r} matches multiple entries: " + ", ".join(b.role for b in matches)
        )
    return matches[0]
