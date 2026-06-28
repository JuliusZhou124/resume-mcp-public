"""Compile a LaTeX resume with tectonic and capture box warnings.

tectonic uses an XeTeX-derived engine and does not implement the pdfTeX-only
``\\pdfglyphtounicode`` primitive that some resume templates pull in (via
``\\input{glyphtounicode}`` / ``\\pdfgentounicode=1``) purely to make the PDF
text ATS-parsable. That has no effect on rendered layout, so a temp copy of
the source has those lines neutralized before compiling.

Compile caching + concurrency control
--------------------------------------
``compile_tex`` caches baseline compiles keyed on the SHA-256 of the sanitized
source content + tectonic binary path.  When many tool calls compile the same
unchanged resume (e.g. 19 parallel ``compile_and_score`` calls during an
audit), only the first call runs tectonic; the rest get the cached PDF bytes
written to their ``outdir``.  A per-key lock prevents thundering-herd
compiles, and a module-level semaphore caps the number of concurrent tectonic
subprocesses so CPU is not thrashed on cache misses.

Pass ``use_cache=False`` for one-shot compiles of modified/temporary content
(e.g. the "after" half of a before/after comparison) — those results are
unique per candidate and would only waste cache memory.
"""

from __future__ import annotations

import collections
import hashlib
import os
import re
import shutil
import subprocess
import threading
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

# Maximum concurrent tectonic subprocesses.
#
# tectonic is I/O/startup-bound, NOT CPU-bound: a single compile runs at
# ~7% CPU for ~10-13s (TeX format + bundle load dominates the wall time), and
# N concurrent compiles finish in roughly the wall time of one (measured on an
# 8-core box: 1->13s, 6->15s, 12->17s, 18->21s).  Sizing the cap to core count
# therefore needlessly serializes parallel callers -- e.g. an agent (or a
# non-stdio MCP client) that fires `compare_candidate_layout` for every bullet
# at once -- into ~13s waves: 18 compiles at a cap of 4 take ~58s and blow past
# a 30s client tool-call timeout, whereas a cap of ~16 lets them finish in
# ~21s.  We therefore cap on *memory* (~200MB RSS per process), not CPU, with
# an env override (`RESUME_FITTER_MAX_COMPILES`).
def _default_max_concurrent_compiles() -> int:
    env = os.environ.get("RESUME_FITTER_MAX_COMPILES")
    if env and env.strip().isdigit() and int(env) > 0:
        return int(env)
    # ~200MB RSS/process; bound by available memory when we can read it
    # (Linux), keeping a floor of 4 and a ceiling of 16 (diminishing returns
    # past that, since startup I/O serializes on the disk/bundle anyway).
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    avail_mb = int(line.split()[1]) // 1024
                    return max(4, min(16, avail_mb // 300))
    except OSError:
        pass
    return 16


_MAX_CONCURRENT_COMPILES = _default_max_concurrent_compiles()

# In-memory compile cache: content_hash -> (pdf_bytes, log_text, box_warnings).
# OrderedDict with LRU eviction so a long editing session (many distinct .tex
# versions) doesn't accumulate dead entries.  Each entry is ~50-200KB of PDF
# bytes; capping at 8 keeps peak cache memory under ~1.6MB.
_MAX_CACHE_ENTRIES = 8
_compile_cache: collections.OrderedDict[str, tuple[bytes, str, list["BoxWarning"]]] = (
    collections.OrderedDict()
)
_cache_lock = threading.Lock()

# Per-key locks prevent thundering-herd compiles: when N threads request the
# same content simultaneously (e.g. several parallel tool calls all needing the
# unchanged baseline), only the first runs tectonic; the rest wait on the key
# lock and then pick up the cached result.  This IS exercised in production:
# MCP clients that issue tool calls concurrently run each sync tool in its own
# worker thread, so multiple compiles overlap.
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()

# Caps total concurrent tectonic processes across all keys (cache misses), so
# a burst of distinct-candidate compiles can't spawn unbounded subprocesses.
_compile_semaphore = threading.Semaphore(_MAX_CONCURRENT_COMPILES)


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


def _cache_key(sanitized: str, tectonic: str) -> str:
    """SHA-256 of length-prefixed sanitized source content + tectonic binary path.

    Length-prefixing provides domain separation so no byte-boundary ambiguity
    between the two fields is possible (theoretically unexploitable here, but
    cheap and correct).
    """
    sanitized_bytes = sanitized.encode("utf-8")
    tectonic_bytes = tectonic.encode("utf-8")
    h = hashlib.sha256()
    h.update(len(sanitized_bytes).to_bytes(8, "big"))
    h.update(sanitized_bytes)
    h.update(len(tectonic_bytes).to_bytes(8, "big"))
    h.update(tectonic_bytes)
    return h.hexdigest()


def _get_key_lock(key: str) -> threading.Lock:
    """Return (creating if needed) the per-key lock for ``key``."""
    with _key_locks_guard:
        if key not in _key_locks:
            _key_locks[key] = threading.Lock()
        return _key_locks[key]


def clear_compile_cache() -> None:
    """Clear the in-memory compile cache and per-key locks.

    Primarily for tests; production code relies on content-hash keying for
    automatic invalidation (a mutated ``.tex`` file has a different hash, so
    the next compile is a fresh miss).
    """
    with _cache_lock:
        _compile_cache.clear()
    with _key_locks_guard:
        _key_locks.clear()


def _run_tectonic(
    work_tex: Path, outdir: Path, tectonic: str, timeout: int,
) -> tuple[Path, str, list[BoxWarning]]:
    """Run tectonic on ``work_tex`` and return (pdf_path, log_text, box_warnings).

    The subprocess call is guarded by ``_compile_semaphore`` so at most
    ``_MAX_CONCURRENT_COMPILES`` tectonic processes run at once.
    """
    with _compile_semaphore:
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
            f"tectonic failed (exit {proc.returncode}) for {work_tex}\n"
            f"--- stderr ---\n{proc.stderr}\n--- log tail ---\n{tail}"
        )

    return pdf_path, log_text, _parse_box_warnings(log_text)


def compile_tex(
    tex_path: Path,
    outdir: Path,
    tectonic_path: str | None = None,
    timeout: int = 120,
    *,
    use_cache: bool = True,
) -> CompileResult:
    """Compile ``tex_path`` with tectonic into ``outdir`` and return the result.

    Raises ``FileNotFoundError`` if ``tex_path`` doesn't exist and
    ``CompileError`` if tectonic is missing or the compile fails.

    When ``use_cache`` is True (the default), the result is cached keyed on
    the sanitized source content.  Concurrent calls compiling the same
    content share a single tectonic run (per-key lock); cache hits skip
    tectonic entirely and write the cached PDF bytes to ``outdir``.  Pass
    ``use_cache=False`` for one-shot compiles of modified content (e.g. the
    "after" half of a before/after comparison) to avoid polluting the cache
    with entries that will never be reused.
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

    if use_cache:
        key = _cache_key(sanitized, tectonic)
        key_lock = _get_key_lock(key)
        with key_lock:
            # Double-check cache after acquiring the per-key lock: another
            # thread may have populated it while we waited.
            with _cache_lock:
                cached = _compile_cache.get(key)
                if cached is not None:
                    _compile_cache.move_to_end(key)  # mark most-recently-used
            if cached is not None:
                pdf_bytes, log_text, box_warnings = cached
                pdf_path = outdir / (tex_path.stem + ".pdf")
                pdf_path.write_bytes(pdf_bytes)
                return CompileResult(
                    pdf_path=pdf_path,
                    log_text=log_text,
                    box_warnings=list(box_warnings),  # copy: don't alias cached list
                )

            # Cache miss — compile under the key lock so concurrent
            # requests for the same content wait rather than thundering.
            work_tex = outdir / tex_path.name
            work_tex.write_text(sanitized)
            pdf_path, log_text, box_warnings = _run_tectonic(
                work_tex, outdir, tectonic, timeout,
            )
            with _cache_lock:
                _compile_cache[key] = (pdf_path.read_bytes(), log_text, box_warnings)
                # LRU eviction: evict oldest entry if over cap.
                while len(_compile_cache) > _MAX_CACHE_ENTRIES:
                    _compile_cache.popitem(last=False)
            return CompileResult(
                pdf_path=pdf_path,
                log_text=log_text,
                box_warnings=list(box_warnings),  # copy: cache holds the original
            )

    # No caching — just compile.
    work_tex = outdir / tex_path.name
    work_tex.write_text(sanitized)
    pdf_path, log_text, box_warnings = _run_tectonic(
        work_tex, outdir, tectonic, timeout,
    )
    return CompileResult(
        pdf_path=pdf_path,
        log_text=log_text,
        box_warnings=box_warnings,
    )
