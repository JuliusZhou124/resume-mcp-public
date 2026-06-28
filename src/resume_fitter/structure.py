"""Structural edits to ``resume.tex``: inserting a new ``\\resumeItem{...}``
into a role/project's bullet list, and removing a whole ``\\resumeItem{...}``
bullet or a whole ``\\resumeSubheading``/``\\resumeProjectHeading`` entry
(heading through its bullet list).

Mirrors ``compare.substitute_bullet`` + ``patch.diff_bullet``/``replace_bullet``:
the ``insert_*``/``remove_*`` functions are in-memory string transforms
(raise ``ValueError`` on any miss), ``diff_*`` is read-only, and ``apply_*``
performs the same transform and writes the result to ``tex_path``.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from .bullets import Bullet, RoleBlock, find_unescaped_specials

_DEFAULT_INDENT = " " * 8


@dataclass
class StructureEdit:
    diff: str
    modified_text: str


def _block_indent(tex_text: str, block: RoleBlock) -> str:
    """Indentation to use for a new ``\\resumeItem`` line in ``block``.

    Copied from an existing ``\\resumeItem`` line in the block's item list, if
    any; otherwise falls back to ``_DEFAULT_INDENT`` (this file's convention).
    """
    if block.item_list_start_line is None or block.item_list_end_line is None:
        return _DEFAULT_INDENT

    lines = tex_text.splitlines(keepends=True)
    for line in lines[block.item_list_start_line : block.item_list_end_line - 1]:
        if r"\resumeItem{" in line:
            return line[: len(line) - len(line.lstrip())]
    return _DEFAULT_INDENT


def insert_bullet_text(
    tex_text: str,
    block: RoleBlock,
    new_bullet_raw: str,
    *,
    position: str = "end",
    after_index: int | None = None,
) -> str:
    """Return ``tex_text`` with ``\\resumeItem{<new_bullet_raw>}`` inserted into
    ``block``'s ``\\resumeItemListStart``...``\\resumeItemListEnd``.

    ``position`` is one of:
      - ``"end"`` (default): after the block's last existing bullet.
      - ``"start"``: before the block's first existing bullet.
      - ``"after"``: after the bullet at ``after_index`` (0-based, within
        this block's bullets only).

    Raises ``ValueError`` if ``block`` has no item list, ``position`` is
    invalid, or ``after_index`` is out of range.
    """
    if not block.has_item_list or block.item_list_start_line is None or block.item_list_end_line is None:
        raise ValueError(f"role block {block.role!r} has no \\resumeItemListStart/End to insert into")

    unsafe = find_unescaped_specials(new_bullet_raw)
    if unsafe:
        raise ValueError(
            f"new bullet contains unescaped LaTeX special character(s) {unsafe} -- "
            "escape as \\%, \\&, \\#, or \\_ (this is the same check apply_bullet "
            "runs, so both write paths reject unescaped input identically)"
        )

    if position not in ("start", "end", "after"):
        raise ValueError(f"invalid position: {position!r} (expected 'start', 'end', or 'after')")
    if position == "after" and after_index is None:
        raise ValueError("position='after' requires after_index")

    lines = tex_text.splitlines(keepends=True)
    indent = _block_indent(tex_text, block)
    line_ending = "\n" if lines and lines[0].endswith("\n") else ""
    new_line = f"{indent}\\resumeItem{{{new_bullet_raw}}}{line_ending}"

    list_start_idx = block.item_list_start_line - 1
    list_end_idx = block.item_list_end_line - 1

    item_indices = [
        idx for idx in range(list_start_idx + 1, list_end_idx) if r"\resumeItem{" in lines[idx]
    ]

    if position == "start":
        insert_at = list_start_idx + 1
    elif position == "end":
        insert_at = (item_indices[-1] + 1) if item_indices else list_start_idx + 1
    else:
        if not (0 <= after_index < len(item_indices)):
            raise ValueError(
                f"after_index {after_index} out of range (block has {len(item_indices)} bullets)"
            )
        insert_at = item_indices[after_index] + 1

    lines.insert(insert_at, new_line)
    return "".join(lines)


def remove_bullet_text(tex_text: str, record: Bullet) -> str:
    """Return ``tex_text`` with ``record``'s ``\\resumeItem{...}`` line(s) removed.

    Raises ``ValueError`` if the source span at ``record.start_line``..
    ``record.end_line`` doesn't contain ``record``'s exact original
    ``\\resumeItem{<raw>}`` text.
    """
    lines = tex_text.splitlines(keepends=True)
    start_idx = record.start_line - 1
    end_idx = record.end_line - 1

    span = "".join(lines[start_idx : end_idx + 1])
    if "\\resumeItem{" + record.raw + "}" not in span:
        raise ValueError(
            f"could not locate \\resumeItem{{...}} for bullet {record.id!r} "
            f"at lines {record.start_line}-{record.end_line}"
        )

    return "".join(lines[:start_idx] + lines[end_idx + 1 :])


def remove_role_block_text(tex_text: str, block: RoleBlock) -> str:
    """Return ``tex_text`` with ``block``'s entire source extent removed
    (``heading_start_line``..``block_end_line``, inclusive)."""
    lines = tex_text.splitlines(keepends=True)
    start_idx = block.heading_start_line - 1
    end_idx = block.block_end_line - 1

    if not (0 <= start_idx <= end_idx < len(lines)):
        raise ValueError(f"invalid line range for role block {block.role!r}")

    return "".join(lines[:start_idx] + lines[end_idx + 1 :])


def _unified_diff(tex_path: Path, original_text: str, modified_text: str) -> StructureEdit:
    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            modified_text.splitlines(keepends=True),
            fromfile=str(tex_path),
            tofile=str(tex_path),
        )
    )
    return StructureEdit(diff=diff, modified_text=modified_text)


def diff_insert_bullet(
    tex_path: Path,
    block: RoleBlock,
    new_bullet_raw: str,
    *,
    position: str = "end",
    after_index: int | None = None,
) -> StructureEdit:
    """Read-only: return the unified diff for inserting a new bullet into ``block``."""
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()
    modified_text = insert_bullet_text(
        original_text, block, new_bullet_raw, position=position, after_index=after_index
    )
    return _unified_diff(tex_path, original_text, modified_text)


def apply_insert_bullet(
    tex_path: Path,
    block: RoleBlock,
    new_bullet_raw: str,
    *,
    position: str = "end",
    after_index: int | None = None,
) -> StructureEdit:
    """Insert a new bullet into ``block`` and write the result to ``tex_path``."""
    result = diff_insert_bullet(tex_path, block, new_bullet_raw, position=position, after_index=after_index)
    Path(tex_path).write_text(result.modified_text)
    return result


def diff_remove_bullet(tex_path: Path, record: Bullet) -> StructureEdit:
    """Read-only: return the unified diff for removing ``record``'s bullet."""
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()
    modified_text = remove_bullet_text(original_text, record)
    return _unified_diff(tex_path, original_text, modified_text)


def apply_remove_bullet(tex_path: Path, record: Bullet) -> StructureEdit:
    """Remove ``record``'s bullet and write the result to ``tex_path``."""
    result = diff_remove_bullet(tex_path, record)
    Path(tex_path).write_text(result.modified_text)
    return result


def diff_remove_role_block(tex_path: Path, block: RoleBlock) -> StructureEdit:
    """Read-only: return the unified diff for removing ``block`` entirely."""
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()
    modified_text = remove_role_block_text(original_text, block)
    return _unified_diff(tex_path, original_text, modified_text)


def apply_remove_role_block(tex_path: Path, block: RoleBlock) -> StructureEdit:
    """Remove ``block`` entirely and write the result to ``tex_path``."""
    result = diff_remove_role_block(tex_path, block)
    Path(tex_path).write_text(result.modified_text)
    return result
