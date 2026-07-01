# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

See [CONCEPT.md](CONCEPT.md) for the full product vision; this file covers
architecture, dev standards, and current constraints.

## Project Purpose

`resume-mcp` rewrites LaTeX resume bullets under both semantic constraints
(impact, truth, style) and physical layout constraints (line wraps, orphans,
page count), using a compile-and-measure feedback loop against the rendered
PDF.

It implements the full propose -> validate -> diff -> apply loop:
`resume_fitter` exposes compile/measure/extract/compare/evaluate/patch
primitives through both a CLI and an MCP server. Rewrite *generation*
(`propose_rewrites()`) is delegated to the calling agent (no in-repo LLM
call) — the agent reads a bullet via MCP, proposes candidate text itself,
and uses the same MCP tools to validate, diff, and apply it.

## Workspace layout

Place your `resume.tex` at the repo root. Optionally keep target-specific
copies in subdirectories alongside a `role.md` for relevance checking:

```
resume.tex                              # active/default resume
<target>/<company-slug>/resume.tex      # past or alternate target
<target>/<company-slug>/role.md         # job posting + requirements
```

Use `list_resumes` / `set_active_resume` (resume-fitter MCP tools) to point
the tooling at a specific `resume.tex` for the rest of a session.

## Architecture

```
resume.tex
  -> bullets.find_bullet_record()  # resolve a bullet id (index or text) to a Bullet record
  -> bullets.find_role_block()     # resolve a role/project by substring to a RoleBlock (full source extent)
  -> compile.compile_tex()         # tectonic -> PDF + parsed box warnings
  -> measure.measure_layout() / measure.page_count()  # pdfplumber geometry -> BulletMetrics / page count
  -> compare.compare_candidate()   # before/after compile for a candidate swap
  -> compare.compare_plan()        # before/after compile for a multi-op add/remove plan
  -> evaluate.evaluate_bullet() / compare_truth_risk()  # heuristic scores
  -> patch.diff_bullet() / replace_bullet()             # diff + safe write (text swap)
  -> structure.py                  # diff/apply for add/remove-bullet and remove-block edits
  -> skills.find_skill_record()    # resolve a Technical Skills category
  -> skills.compare_skill_candidate() / compare_skill_evidence() / diff_skill() / replace_skill()
  -> shapes.py                     # shared JSON shapes for both entry points
       |                                |
  -> cli.main()                    -> mcp_server.py (FastMCP, stdio)
       (argparse, prints JSON)          (list_bullets, get_bullet,
                                          evaluate_candidate,
                                          compare_candidate_layout,
                                          diff_candidate, apply_bullet,
                                          compile_and_score,
                                          list_skill_categories, get_skill_category,
                                          evaluate_skill_candidate, diff_skill_candidate,
                                          compare_skill_layout, apply_skill_category,
                                          list_role_blocks, add_bullet, remove_bullet,
                                          remove_role_block, compare_plan_layout,
                                          list_resumes, set_active_resume)
```

- `src/resume_fitter/compile.py` — runs `tectonic`, returns `CompileResult`
  (pdf path, log text, parsed overfull/underfull box warnings).
- `src/resume_fitter/measure.py` — `measure_layout(pdf_path, bullet_text)`
  locates a bullet's words in the rendered PDF via `pdfplumber` and computes
  `BulletMetrics` (lines, last_line_fullness, has_orphan, page_count, plus the
  derived `meets_fullness_requirement` property —
  `last_line_fullness >= FULLNESS_REQUIREMENT_THRESHOLD` (0.9)).
  `page_count(pdf_path)` is the shared page-count helper used by `compare.py`
  and `skills.py`. `ORPHAN_FULLNESS_THRESHOLD` (0.15, a severe near-empty
  trailing line) and `FULLNESS_REQUIREMENT_THRESHOLD` (0.9, the general
  "don't leave a sparse last line" bar) are independent constants — the
  former is informational, the latter is a hard gate (see `mcp_server.py`
  below).
- `src/resume_fitter/bullets.py` — `extract_bullets()`: finds every
  `\resumeItem{...}` in the document body (skipping `%`-comments and the
  preamble), and returns a `Bullet` record per bullet with plain text,
  source line range (`start_line`/`end_line`), and section/role context
  derived from the nearest preceding `\section`, `\resumeSubheading`, or
  `\resumeProjectHeading`. `find_bullet_record()` / `find_bullet()` /
  `list_bullets()` resolve by 0-based index or text substring on top of this.
  Scope: only `\resumeItem` bullets — the Technical Skills `\item` block is
  intentionally not extracted (it's a skill list, not a rewritable bullet).
  `extract_role_blocks()` / `find_role_block()` resolve a `RoleBlock` (a
  `\resumeSubheading`/`\resumeProjectHeading` entry's full source extent,
  heading line through its `\resumeItemListEnd` or last heading/tabular line
  if it has no item list) by case-insensitive substring of its `role` string
  (e.g. "Northwind Cloud", "QA Engineer"); raises `BlockLookupError` on zero
  or multiple matches.
- `src/resume_fitter/compare.py` — `compare_candidate()` compiles
  `resume.tex` before/after an in-memory `\resumeItem{...}` swap (never
  writes to disk) and reports before/after `BulletMetrics` plus
  `page_count_changed`. `compare_plan(tex_path, ops)` / `apply_ops_in_memory()`
  do the same before/after-compile comparison for a *list* of structural
  edits (see `structure.py`) — re-resolving each op's target against the
  evolving in-memory text before applying it, so line-number drift across ops
  doesn't break later targets. Returns a `PlanComparison`
  (`before_page_count`/`after_page_count`, overfull flags,
  `page_count_changed`, `fits_one_page`, `applied_ops` summaries).
- `src/resume_fitter/evaluate.py` — `evaluate_bullet(text)` (deterministic,
  no LLM): heuristic `xyz_score` (action verb + metric + result clause),
  `specificity_score`, and `verbosity_score` based on word count.
  `compare_truth_risk(original, candidate)` flags numbers/entities present in
  a candidate but absent from the original bullet (`truth_risk`:
  low/medium/high, plus `changed_entities`). `check_grounding(candidate,
  resume_text)` checks that every proper noun / technical phrase in the
  candidate appears elsewhere in the resume.
- `src/resume_fitter/patch.py` — `diff_bullet()` (read-only, returns a
  unified diff + modified source for a `\resumeItem{...}` swap) and
  `replace_bullet()` (same substitution, writes the result back to the given
  `tex_path`). Both build on `compare.substitute_bullet()`.
- `src/resume_fitter/structure.py` — structural (line-insert/-delete) edits,
  mirroring `patch.py`'s diff/apply split: `insert_bullet_text()` adds a new
  `\resumeItem{...}` into a `RoleBlock`'s item list (`position` "end"
  (default) / "start" / "after" + `after_index`, indentation copied from an
  existing `\resumeItem` line in that block); `remove_bullet_text()` deletes
  one bullet's source line(s) (verifies the exact `\resumeItem{<raw>}` is
  present first); `remove_role_block_text()` deletes a whole role/project
  entry's `heading_start_line..block_end_line`. `diff_insert_bullet()` /
  `diff_remove_bullet()` / `diff_remove_role_block()` are read-only (unified
  diff + modified source); `apply_insert_bullet()` / `apply_remove_bullet()` /
  `apply_remove_role_block()` perform the same edit and write to `tex_path`.
- `src/resume_fitter/skills.py` — a parallel, narrower
  propose -> validate -> diff -> apply path for the single fixed
  `\section{Technical Skills}` block (3 `\textbf{<Category>}{: <items>}`
  lines: Languages, Frameworks, Developer Tools). `extract_skill_categories()`
  / `find_skill_record()` resolve a `SkillCategory` by 0-based index or
  case-insensitive category-name substring. `substitute_skill()` /
  `diff_skill()` / `replace_skill()` mirror the bullet equivalents (exact
  `\textbf{<category>}{: <items_raw>}` match, read-only diff, write-on-apply).
  `compare_skill_evidence()` flags newly-added skill tokens (vs. the
  category's current items) that don't appear anywhere in any
  `\resumeItem` bullet's text — `/`-split and parenthetical sub-parts count
  as evidence (e.g. a bullet's `Docker/Kubernetes` evidences adding bare
  `Kubernetes`). `compare_skill_candidate()` is a before/after-compile check
  (page count + overfull only — no per-line pdfplumber measurement, since a
  skills line isn't a prose bullet to locate). Edits only replace one
  category's items string in place; categories are never added, removed, or
  reordered, and there's no xyz/specificity/verbosity scoring for skills.
- `src/resume_fitter/shapes.py` — shared JSON-shaping helpers
  (`source_json`, `metrics_json`, `evaluation_json`, `box_warnings_json`,
  `skill_source_json`, `skill_evidence_json`, `role_block_json`,
  `structure_diff_json`, `plan_comparison_json`) used by both `cli.py` and
  `mcp_server.py` so the two report identical shapes for the same
  dataclasses.
- `src/resume_fitter/cli.py` — argparse entry point, prints the JSON result.
  Bullet-only (no skills/structure flags).
- `src/resume_fitter/mcp_server.py` — FastMCP server (stdio transport), a
  thin adapter over the primitives above. `list_resumes` / `set_active_resume`
  let a client discover every `.tex` resume in the workspace and switch the
  server's active file for the rest of the session (individual tools still
  accept an optional `tex_path` override for one call). Bullet tools:
  `list_bullets`, `get_bullet`, `evaluate_candidate` (no compile),
  `compare_candidate_layout` (before/after compile), `diff_candidate`
  (read-only diff), `apply_bullet` (the only mutating tool — no-ops unless
  `confirm=True`), `compile_and_score`. Technical Skills tools:
  `list_skill_categories`, `get_skill_category`, `evaluate_skill_candidate`
  (evidence check, no compile), `diff_skill_candidate` (read-only diff),
  `compare_skill_layout` (before/after compile, page count + overfull),
  `apply_skill_category` (the only mutating skills tool — no-ops unless
  `confirm=True`). Structural tools (page-budget-aware add/remove, for fitting
  the resume to 1 page): `list_role_blocks` (every role/project entry's source
  extent), `add_bullet` (insert a new `\resumeItem` into a role's list by
  `role` substring + `position`), `remove_bullet` (delete one whole
  `\resumeItem`), `remove_role_block` (delete a whole role/project entry —
  heading through its item list) — `add_bullet`/`remove_bullet`/
  `remove_role_block` are each the only mutating tool for their edit type,
  no-ops unless `confirm=True`. `compare_plan_layout(ops)` is read-only and
  takes a list of `add_bullet`/`remove_bullet`/`remove_block` ops, applies
  them in memory, and reports before/after page count + `fits_one_page` — use
  it to check that an "add bullets to role X, remove role block Y" plan nets
  out to 1 page before calling the mutating tools.

  The server also sets a top-level `instructions` string (FastMCP's
  `instructions=` constructor arg) establishing two rules:
  - **Metric-gathering**: if `evaluate_candidate`'s
    `candidate_evaluation.has_metric` is false for a bullet about to be
    applied/added, the calling agent should pause and ask the user
    (interactive Q&A is fine) for a real, concrete metric for that bullet
    before `compare_candidate_layout`/`apply_bullet`/`add_bullet` with
    `confirm=True` — not invent one.
  - **Fullness (hard gate)**: every bullet's last/only rendered line must be
    >= `measure.FULLNESS_REQUIREMENT_THRESHOLD` (0.9) full —
    `metrics_json`'s `meets_fullness_requirement` field reports this for
    `before`/`after`/`layout` blocks everywhere `BulletMetrics` is returned.
    Unlike the metric rule (instructions-only), this is enforced
    **server-side**: `apply_bullet` and `add_bullet` compile the candidate's
    modified text via `compare.measure_candidate_layout()` *before* writing,
    and refuse (`applied: false` + `error` + `layout`, no write) if the
    candidate's last line is below 90% — even with `confirm=True`. This
    cannot be bypassed by the calling agent; revise the candidate (shorten to
    one full line, or lengthen so a wrapped second line is nearly full) and
    retry.

`propose_rewrites()` is realized as this MCP surface plus an agent's own
generation — there is no in-repo LLM call.

Behaviors added on top of the surface above (see `CHANGES.md` for rationale):
- `evaluate_candidate` works with no `index`/`text` — scores a brand-new
  bullet as a standalone draft (`truth_risk`/`changed_entities` omitted,
  `bullet`/`evaluation` null) so additions can be scored before a target
  block exists.
- `evaluate_candidate`/`apply_bullet`/`add_bullet` accept `pending_skills`;
  terms matching a skill that's about to be added (case-insensitive
  substring, either direction) count as grounded, avoiding false
  "ungrounded" warnings mid-tailoring.
- `remove_bullet` reports `would_empty_block` and, with `confirm=True` +
  `cascade=True` (default), also removes the now-empty role/project block
  (reported as `cascaded_block_removed`) so deleting a role's last bullet
  doesn't leave an empty `\resumeItemListStart/End` that breaks LaTeX.
- `bullets.find_unescaped_specials()` rejects unescaped `%`/`&`/`#`/`_` with
  one shared `ValueError` on both the `apply_bullet` and `add_bullet`/
  `compare_plan_layout` paths before any compile.

## Editing workflows (skills)

The MCP primitives are driven by skills in `.claude/skills/` — prefer these
over calling tools ad hoc, and NEVER edit resume `.tex` bullet/skill text with
the `Edit`/`Write` file tools (it bypasses the fullness/grounding/layout
gates):

- `/set-role` — turn a pasted job posting into a `role.md` (requirements,
  competencies, tools, disqualifiers) next to the target `.tex`. The other
  skills read `role.md` for relevance checking.
- `/fit-bullet` — reword/tighten/rewrite one bullet through the
  propose -> validate -> diff -> apply loop (XYZ quality + relevance + 90%
  last-line fullness gate).
- `/fill-page` — add high-relevance bullets to fill remaining page space
  without spilling to 2 pages; every addition is validated via
  `compare_plan_layout` before applying.
- `/tailor` — the canonical end-to-end pass for a job target: audit every
  bullet for relevance, rewrite/remove weak ones, align Technical Skills,
  then fill remaining space. Requires a `role.md`.

`CHANGES.md` is a living list of proposed/resolved MCP+skill improvements
surfaced while running these (three sentences each: what + why). Do NOT write
session logs, per-run summaries, or tailoring history into `CHANGES.md` — it
is for MCP/skill improvement proposals only.

## Development Standards

- Python 3.10, stdlib + `pdfplumber` + `mcp`. Type hints + `@dataclass` result
  types. No web framework, no ORM, no heavy abstractions — keep it scriptable.
- Run the CLI:
  ```
  PYTHONPATH=src venv/bin/python -m resume_fitter.cli resume.tex --bullet "some text"
  PYTHONPATH=src venv/bin/python -m resume_fitter.cli resume.tex --bullet-index 0 --pretty
  ```
- Run the MCP server directly (for debugging — normally launched by an MCP
  client):
  ```
  PYTHONPATH=src venv/bin/python -m resume_fitter.mcp_server
  ```
  Claude Code auto-discovers it via `.mcp.json` at the repo root (absolute
  path to `venv/bin/python`, `PYTHONPATH=src`). Alternatively:
  `claude mcp add resume-fitter -- venv/bin/python -m resume_fitter.mcp_server`.
- Run tests:
  ```
  venv/bin/python -m pytest -q
  ```
  `pytest.ini` sets `pythonpath = src tests` for imports.
- Dependencies live in `requirements.txt`; install with
  `venv/bin/pip install -r requirements.txt`.
- `tectonic` must be on `PATH` for compilation (used instead of
  `latexmk`/`pdflatex` — no full TeX Live assumed).
- Tests that require `tectonic` are marked with `requires_tectonic` and skip
  cleanly if it's unavailable.

## Known Constraints / Gotchas

- **tectonic vs. pdfTeX-only primitives**: this resume template's preamble has
  `\input{glyphtounicode}` / `\pdfgentounicode=1` (ATS copy-paste metadata).
  tectonic's engine doesn't implement `\pdfglyphtounicode`, so `compile_tex`
  compiles a temp copy with those two lines commented out. This has no effect
  on visual layout.
- **Typographic substitution**: LaTeX renders a plain `'` as a curly `'` (and
  similar for quotes/dashes). `measure._normalize` maps these back to ASCII
  so source-text bullet matching works against PDF-extracted text.
- **`bullets.py` scope**: only `\resumeItem{...}` macro invocations in the
  document body are treated as bullets; LaTeX `%`-comments and preamble
  `\newcommand` definitions are excluded. Bullet `id`/`index` are positional
  (`b0`, `b1`, ...) — not stable across edits that add/remove/reorder
  bullets. `apply_bullet`/`replace_bullet` write for real, but since they
  only substitute one bullet's text (never adding/removing a `\resumeItem`),
  indices don't shift from a single apply. Still, call `list_bullets` again
  before reusing an index after any apply, in case the resume changed out of
  band.
- **Orphan/fullness thresholds**: `ORPHAN_FULLNESS_THRESHOLD = 0.15` in
  `measure.py` is a tunable constant, not a derived value.
- **`_METRIC_RE` vs. LaTeX `\%` escapes**: `evaluate._METRIC_RE` matches a
  number directly followed by `%`/`x`/`+`, but candidate text written for
  `resume.tex` must escape percent signs as `\%` (otherwise tectonic treats
  `%` as a comment and the compile breaks). A candidate's `40\%` therefore
  extracts as the bare metric `"40"`, which won't match an original's `40%`
  (extracted as `"40%"`), so `compare_truth_risk` reports a spurious "high"
  risk / changed entity for an unchanged number — treat this as a known false
  positive, not a real truth risk, when the only "changed entity" is a bare
  number whose escaped form matches the original's metric.
- **Target page fill**: aim for 101%–103% fill. Slightly overfull is fine
  if the tool still reports `page_count: 1`.
