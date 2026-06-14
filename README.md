# resume-fitter

An MCP server (and CLI) that helps you rewrite LaTeX resume bullets under two
kinds of constraints at once:

- **Semantic constraints** — impact (XYZ formatting: *accomplished X, measured
  by Y, by doing Z*), specificity, verbosity, and truthfulness (don't let an
  edit quietly invent a new metric, tool, or company).
- **Physical layout constraints** — line wraps, last-line "fullness", orphaned
  words, overfull boxes, and overall page count, measured against the
  *actually rendered PDF*, not just the source text.

It compiles your `resume.tex` with [tectonic](https://tectonic-typesetting.github.io/),
measures the rendered PDF with [pdfplumber](https://github.com/jsvine/pdfplumber),
and exposes the result through a small set of MCP tools so an agent (or you,
via the CLI) can run a **propose -> validate -> diff -> apply** loop on
individual bullets, the Technical Skills line, or whole role/project entries.

Generating the *rewrite itself* is left to the calling agent — this server
only scores, compares, diffs, and (with `confirm=True`) applies. There is no
in-repo LLM call.

## How it works

```text
resume.tex
  -> extract a bullet, skill category, or role/project block
  -> agent proposes candidate text
  -> compile + measure the candidate (before/after PDF comparison)
  -> score (xyz / specificity / verbosity / truth risk / fullness)
  -> diff (read-only)
  -> apply (writes resume.tex, only with confirm=True)
```

Two server-enforced rules shape this loop:

- **Metric-gathering**: if a candidate bullet has no quantified result
  (`has_metric: False`), the calling agent is instructed to ask you for a real
  number before applying — not invent one.
- **Fullness (hard gate)**: every bullet's last (or only) rendered line must
  be at least 90% full (`FULLNESS_REQUIREMENT_THRESHOLD` in `measure.py`).
  `apply_bullet` and `add_bullet` compile the candidate first and **refuse to
  write** — even with `confirm=True` — if this fails.

## Setup

Requires Python 3.10+ and [tectonic](https://tectonic-typesetting.github.io/)
on your `PATH`.

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
```

Run the test suite (the example `resume.tex` in this repo is used as the
fixture):

```bash
PYTHONPATH="" venv/bin/python -m pytest -q
```

Tests marked `requires_tectonic` are skipped automatically if `tectonic`
isn't installed.

## Using your own resume (THIS IS JAKE'S RESUME NATIVE)

This repo ships with `resume.tex`, a fictional example resume ("Alex Morgan")
built on the [sb2nov/Jake Gutierrez LaTeX resume template](https://github.com/sb2nov/resume),
using the same custom macros (`\resumeItem`, `\resumeSubheading`,
`\resumeProjectHeading`, etc.). To use your own resume:

1. Replace `resume.tex` with your own LaTeX resume, built on the same macros
   (or adapt `src/resume_fitter/bullets.py` / `skills.py` if your macro names
   differ).
2. Point the server at it via the `RESUME_TEX` environment variable, or just
   replace `resume.tex` in place.

The server always operates on a single fixed file (`RESUME_TEX`, defaulting
to `resume.tex` in the repo root) — an MCP client can't point it at arbitrary
paths.

## Configuring the MCP server

Copy `.mcp.json.example` to `.mcp.json` and fill in the absolute path to your
venv's Python:

```bash
cp .mcp.json.example .mcp.json
```

```json
{
  "mcpServers": {
    "resume-fitter": {
      "command": "/absolute/path/to/resume-mcp-public/venv/bin/python",
      "args": ["-m", "resume_fitter.mcp_server"],
      "env": { "PYTHONPATH": "src" }
    }
  }
}
```

Claude Code auto-discovers `.mcp.json` in the repo root. Alternatively:

```bash
claude mcp add resume-fitter -- venv/bin/python -m resume_fitter.mcp_server
```

To run the server directly (for debugging — normally launched by an MCP
client):

```bash
PYTHONPATH=src venv/bin/python -m resume_fitter.mcp_server
```

## CLI

The CLI covers the bullet-level loop (skills/structural editing are
MCP-only for now):

```bash
PYTHONPATH=src venv/bin/python -m resume_fitter.cli resume.tex --bullet-index 0 --pretty
PYTHONPATH=src venv/bin/python -m resume_fitter.cli resume.tex --bullet "some text" --pretty
PYTHONPATH=src venv/bin/python -m resume_fitter.cli resume.tex --bullet-index 0 \
  --candidate "Rewrote this bullet to be more specific." --pretty
PYTHONPATH=src venv/bin/python -m resume_fitter.cli resume.tex --bullet-index 0 \
  --candidate "..." --apply
```

`--candidate` adds before/after layout metrics, scoring, a diff, and a
truth-risk check; `--apply` (requires `--candidate`) writes the change to
`resume.tex`.

## MCP tools

### Bullets (`\resumeItem{...}`)

| Tool | Description |
| --- | --- |
| `list_bullets` | List every bullet with its id, index, text, and section/role context. |
| `get_bullet` | Fetch one bullet by index or text substring, with its current evaluation. |
| `evaluate_candidate` | Score a candidate bullet (xyz/specificity/verbosity/truth-risk) without compiling. |
| `compare_candidate_layout` | Compile before/after and report layout metrics + page-count change. |
| `diff_candidate` | Read-only unified diff of the proposed swap. |
| `apply_bullet` | Write the swap to `resume.tex`. No-op unless `confirm=True`; refuses if the fullness gate fails. |
| `compile_and_score` | Compile the current resume and report a bullet's layout metrics. |

### Technical Skills (`\textbf{<Category>}{: <items>}`)

| Tool | Description |
| --- | --- |
| `list_skill_categories` | List the three skill categories (Languages, Frameworks, Developer Tools). |
| `get_skill_category` | Fetch one category's current items by index or name. |
| `evaluate_skill_candidate` | Check which newly-added skill tokens are already evidenced by bullet text. |
| `diff_skill_candidate` | Read-only diff of a proposed items-string change. |
| `compare_skill_layout` | Compile before/after and report page count + overfull boxes. |
| `apply_skill_category` | Write the new items string to `resume.tex`. No-op unless `confirm=True`. |

### Structure (role/project blocks, for page-budget edits)

| Tool | Description |
| --- | --- |
| `list_role_blocks` | List every role/project entry's source line extent and whether it has an item list. |
| `add_bullet` | Insert a new `\resumeItem` into a role's item list. No-op unless `confirm=True`; refuses if the fullness gate fails. |
| `remove_bullet` | Delete one whole `\resumeItem`. No-op unless `confirm=True`. |
| `remove_role_block` | Delete a whole role/project entry (heading through its item list). No-op unless `confirm=True`. |
| `compare_plan_layout` | Apply a list of `add_bullet`/`remove_bullet`/`remove_block` ops in memory and report before/after page count + whether the plan fits on one page. |

Use `compare_plan_layout` to check that a multi-step plan (e.g. "add two
bullets to role X, remove role block Y") nets out to one page *before*
calling the mutating tools with `confirm=True`.

## Known constraints

- Only `\resumeItem{...}` macro invocations are treated as bullets; the
  Technical Skills line is handled separately by `skills.py`.
- Bullet `id`/`index` values are positional (`b0`, `b1`, ...) and aren't
  stable across edits that add/remove/reorder bullets — call `list_bullets`
  again after any apply before reusing an index.
- tectonic doesn't implement `\pdfglyphtounicode`; `compile_tex` compiles a
  temp copy with `\input{glyphtounicode}` / `\pdfgentounicode=1` commented
  out. This has no effect on visual layout.
- If your environment sets a global `PYTHONPATH` (e.g. from a ROS install),
  override it to empty when running pytest: `PYTHONPATH=""`.

## License

MIT — see [LICENSE](LICENSE). The LaTeX resume template is based on
[sb2nov/resume](https://github.com/sb2nov/resume) (Jake Gutierrez), also MIT.
