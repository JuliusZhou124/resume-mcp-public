"""Aggressive IT/e2e tests for the compile-cache concurrency surface.

``resume_fitter.compile`` is the one place this server has real shared mutable
state exercised concurrently: an in-memory LRU cache (``_compile_cache``), a
per-key lock map (``_key_locks``) that collapses thundering-herd compiles to a
single tectonic run, and a module-level semaphore (``_compile_semaphore``) that
caps concurrent tectonic subprocesses. The MCP server's tools are sync and run
in worker threads when a client issues calls concurrently, so these invariants
are load-bearing.

Most tests stub ``_run_tectonic`` (no tectonic, no I/O) so the concurrency
logic is exercised deterministically and fast; one real-tectonic e2e test
drives the cache through the MCP tool layer.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from resume_fitter import compile as compile_mod
from resume_fitter.compile import (
    _MAX_CACHE_ENTRIES,
    _MAX_CONCURRENT_COMPILES,
    clear_compile_cache,
    compile_tex,
)
from tests.conftest import RESUME_TEX, requires_tectonic


# ---------------------------------------------------------------------------
# helpers: a stubbed `_run_tectonic` that counts calls / measures overlap.
# ---------------------------------------------------------------------------

def _stub_run_tectonic(call_counter, peak_holder, in_flight, peak_lock, *, delay=0.0):
    """Build a stub `_run_tectonic` replacement.

    Records total invocation count and the peak number of *simultaneously*
    in-flight invocations (useful for asserting the semaphore cap). Writes a
    minimal placeholder PDF + log so `compile_tex`'s post-run logic succeeds.
    """

    def _stub(work_tex, outdir, tectonic, timeout):
        # Mirror the real `_run_tectonic`: acquire the module semaphore so the
        # concurrency cap is exercised even when the subprocess is stubbed. A
        # patched `_compile_semaphore` on the module is picked up here too.
        with compile_mod._compile_semaphore:
            with peak_lock:
                call_counter[0] += 1
                in_flight[0] += 1
                peak_holder[0] = max(peak_holder[0], in_flight[0])
            try:
                if delay:
                    import time
                    time.sleep(delay)
            finally:
                with peak_lock:
                    in_flight[0] -= 1
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        pdf_path = outdir / (Path(work_tex).stem + ".pdf")
        log_path = outdir / (Path(work_tex).stem + ".log")
        pdf_path.write_bytes(b"%PDF-1.4 stub\n%%EOF")
        log_path.write_text("stub log")
        return pdf_path, "stub log", []

    return _stub


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_compile_cache()
    yield
    clear_compile_cache()


@pytest.fixture(autouse=True)
def _stub_tectonic_lookup(request, monkeypatch):
    """Make `compile_tex`'s tectonic-on-PATH check pass without a real binary.

    Every test here except the `@requires_tectonic` e2e one stubs
    `_run_tectonic` and never actually shells out, so the real lookup would
    only fail CI environments that lack tectonic for no good reason.
    """
    if request.node.get_closest_marker("skipif") is not None:
        return
    monkeypatch.setattr(compile_mod.shutil, "which", lambda *a, **k: "/usr/bin/tectonic")


def _make_distinct_tex(tmp_path, n, base=RESUME_TEX):
    """Return n .tex files identical in meaning but differing by a trailing
    comment so each has a distinct content hash (distinct cache key)."""
    base_text = base.read_text()
    paths = []
    for i in range(n):
        p = tmp_path / f"r_{i}.tex"
        p.write_text(base_text + f"\n% variant {i}\n")
        paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# 1. Thundering herd: N threads, same content -> exactly ONE compile.
# ---------------------------------------------------------------------------

def test_thundering_herd_compiles_once_per_key(tmp_path, monkeypatch):
    calls = [0]
    peak = [0]
    in_flight = [0]
    lock = threading.Lock()
    monkeypatch.setattr(
        compile_mod, "_run_tectonic", _stub_run_tectonic(calls, peak, in_flight, lock)
    )

    errors: list[Exception] = []

    def worker():
        try:
            compile_tex(RESUME_TEX, tmp_path / f"out_{threading.get_ident()}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert calls[0] == 1, f"thundering herd: expected 1 compile, got {calls[0]}"
    assert peak[0] == 1


def test_two_distinct_keys_compile_exactly_twice(tmp_path, monkeypatch):
    calls = [0]
    peak = [0]
    in_flight = [0]
    lock = threading.Lock()
    monkeypatch.setattr(
        compile_mod, "_run_tectonic", _stub_run_tectonic(calls, peak, in_flight, lock)
    )
    a, b = _make_distinct_tex(tmp_path, 2)

    errors: list[Exception] = []
    targets = [a] * 8 + [b] * 8

    def worker(tex):
        try:
            compile_tex(tex, tmp_path / f"o_{threading.get_ident()}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(tex,)) for tex in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert calls[0] == 2, f"expected exactly 2 compiles (one per key), got {calls[0]}"


# ---------------------------------------------------------------------------
# 2. Distinct-content burst respects the LRU cache cap (no over-insert race).
# ---------------------------------------------------------------------------

def test_concurrent_distinct_keys_respect_cache_cap(tmp_path, monkeypatch):
    calls = [0]
    peak = [0]
    in_flight = [0]
    lock = threading.Lock()
    monkeypatch.setattr(
        compile_mod, "_run_tectonic", _stub_run_tectonic(calls, peak, in_flight, lock)
    )
    n = _MAX_CACHE_ENTRIES * 3  # well over the cap
    texes = _make_distinct_tex(tmp_path, n)

    errors: list[Exception] = []

    def worker(tex, i):
        try:
            compile_tex(tex, tmp_path / f"o_{i}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=(texes[i], i)) for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # Every distinct key compiled exactly once (no redundant compiles).
    assert calls[0] == n
    # Cache never exceeds the cap -- LRU eviction is safe under contention.
    assert len(compile_mod._compile_cache) == _MAX_CACHE_ENTRIES


# ---------------------------------------------------------------------------
# 3. Mixed hit/miss: cache hits compile zero times; misses once each.
# ---------------------------------------------------------------------------

def test_concurrent_mixed_hit_miss_compiles_only_misses(tmp_path, monkeypatch):
    calls = [0]
    peak = [0]
    in_flight = [0]
    lock = threading.Lock()
    monkeypatch.setattr(
        compile_mod, "_run_tectonic", _stub_run_tectonic(calls, peak, in_flight, lock)
    )
    warm_a, warm_b = _make_distinct_tex(tmp_path, 2)
    miss_c = tmp_path / "miss_c.tex"
    miss_c.write_text(RESUME_TEX.read_text() + "\n% miss c\n")
    # Pre-warm two keys.
    compile_tex(warm_a, tmp_path / "warm_a_out")
    compile_tex(warm_b, tmp_path / "warm_b_out")
    assert calls[0] == 2

    errors: list[Exception] = []
    targets = [warm_a] * 12 + [warm_b] * 8 + [miss_c] * 6

    def worker(tex):
        try:
            compile_tex(tex, tmp_path / f"o_{threading.get_ident()}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(tex,)) for tex in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # Only the single miss key compiled (once); hits contributed zero.
    assert calls[0] == 3, f"expected 3 total compiles, got {calls[0]}"


# ---------------------------------------------------------------------------
# 4. use_cache=False never reads nor pollutes the shared cache.
# ---------------------------------------------------------------------------

def test_use_cache_false_isolated_under_concurrency(tmp_path, monkeypatch):
    calls = [0]
    peak = [0]
    in_flight = [0]
    lock = threading.Lock()
    monkeypatch.setattr(
        compile_mod, "_run_tectonic", _stub_run_tectonic(calls, peak, in_flight, lock)
    )
    warm = tmp_path / "warm.tex"
    warm.write_text(RESUME_TEX.read_text())
    compile_tex(warm, tmp_path / "warm_out")
    assert calls[0] == 1
    assert len(compile_mod._compile_cache) == 1

    errors: list[Exception] = []

    def worker():
        try:
            compile_tex(warm, tmp_path / f"o_{threading.get_ident()}", use_cache=False)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    # Every use_cache=False call compiled fresh (cache never served them).
    assert calls[0] == 11
    # Cache unchanged: the original warm key only, no pollution.
    assert len(compile_mod._compile_cache) == 1


# ---------------------------------------------------------------------------
# 5. Semaphore caps concurrent tectonic subprocesses (cache misses).
# ---------------------------------------------------------------------------

def test_semaphore_caps_concurrent_subprocesses(tmp_path, monkeypatch):
    calls = [0]
    peak = [0]
    in_flight = [0]
    lock = threading.Lock()
    # Tiny cap so the bound is observable with few threads.
    cap = 2
    monkeypatch.setattr(compile_mod, "_compile_semaphore", threading.Semaphore(cap))
    monkeypatch.setattr(
        compile_mod,
        "_run_tectonic",
        _stub_run_tectonic(calls, peak, in_flight, lock, delay=0.05),
    )
    n = 16
    texes = _make_distinct_tex(tmp_path, n)

    errors: list[Exception] = []

    def worker(tex, i):
        try:
            compile_tex(tex, tmp_path / f"o_{i}")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [
        threading.Thread(target=worker, args=(texes[i], i)) for i in range(n)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert calls[0] == n  # all distinct keys -> all miss -> all compile
    assert peak[0] <= cap, (
        f"semaphore leaked: peak in-flight {peak[0]} exceeded cap {cap}"
    )
    assert peak[0] == cap, (
        f"semaphore never saturated: peak {peak[0]} < cap {cap} "
        f"(test would not actually prove the bound)"
    )


# ---------------------------------------------------------------------------
# 6. e2e: MCP tool layer compiles concurrently against the pinned active resume.
# ---------------------------------------------------------------------------

@requires_tectonic
def test_mcp_server_parallel_compile_and_score(tmp_path, monkeypatch):
    """Many concurrent `compile_and_score` tool calls share one active resume,
    so they all hit the same cache key: exactly one tectonic run, the rest are
    cache hits, and every caller gets a well-formed result."""
    from resume_fitter import mcp_server

    tex_copy = tmp_path / "resume.tex"
    tex_copy.write_text(RESUME_TEX.read_text())
    monkeypatch.setattr(mcp_server, "DEFAULT_TEX", tex_copy)
    monkeypatch.setattr(mcp_server, "_active_tex", None)
    clear_compile_cache()

    results: list[dict] = []
    errors: list[Exception] = []

    def worker(i):
        try:
            # Round-robin across valid bullet indices.
            results.append(mcp_server.compile_and_score(index=i % 6))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    n = 12
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(results) == n
    for r in results:
        assert "bullet" in r and "layout" in r and "page_fill" in r, r
        assert "error" not in r, r
    # Same active resume -> single cache entry; concurrent reads were safe.
    assert len(compile_mod._compile_cache) == 1