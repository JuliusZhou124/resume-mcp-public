"""Diff generation and safe patching for bullet rewrites (CONCEPT.md's replace_bullet()).

``diff_bullet()`` is read-only: it never writes to ``tex_path``, only returns
a unified diff and the would-be modified source. ``replace_bullet()`` does
the same substitution and then writes the modified source back to
``tex_path`` -- callers decide which path to point at, so tests and dry runs
should operate on a temp copy, never the user's real resume.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from .bullets import Bullet
from .compare import substitute_bullet


@dataclass
class BulletDiff:
    diff: str
    modified_text: str


def diff_bullet(tex_path: Path, record: Bullet, candidate: str) -> BulletDiff:
    """Return a unified diff for swapping ``record``'s bullet for ``candidate``.

    Read-only: ``tex_path`` is read but never written.
    """
    tex_path = Path(tex_path)
    original_text = tex_path.read_text()
    modified_text = substitute_bullet(original_text, record, candidate)

    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            modified_text.splitlines(keepends=True),
            fromfile=str(tex_path),
            tofile=str(tex_path),
        )
    )
    return BulletDiff(diff=diff, modified_text=modified_text)


def replace_bullet(tex_path: Path, record: Bullet, candidate: str) -> BulletDiff:
    """Swap ``record``'s bullet for ``candidate`` and write the result to ``tex_path``.

    Returns the same ``BulletDiff`` as ``diff_bullet()`` for the change that
    was written.
    """
    result = diff_bullet(tex_path, record, candidate)
    Path(tex_path).write_text(result.modified_text)
    return result
