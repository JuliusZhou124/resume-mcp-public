# Resume Auto-Fitter & XYZ Formatter Concept

A local-first agent workflow that rewrites LaTeX resume bullets under both
semantic and physical layout constraints.

The core value is not just "rewrite my resume." It is: rewrite bullets so they
are impact-oriented, truthful, stylistically consistent, and cleanly fitted into
the rendered PDF.

## Product Thesis

Most resume tools optimize text only. LaTeX resumes also have physical layout
constraints: a bullet may be semantically stronger but visually worse if it
creates an orphaned word, spills onto an extra line, breaks a section, or changes
the page count.

This tool gives an AI agent a feedback loop:

```text
extract bullet -> rewrite candidate -> compile PDF -> measure layout -> score -> revise/apply
```

That loop lets the agent reason about the actual rendered document instead of
guessing from tokens.

## Core Goals

1. **Constrained Resume Editing**
   Rewrite bullets while respecting impact structure, truthfulness, style, and
   PDF layout.

2. **XYZ Formatting**
   Improve bullets toward the XYZ formula: Accomplished [X] as measured by [Y],
   by doing [Z]. This is one scoring dimension, not the entire product.

3. **Clean PDF Fit**
   Optimize for professional typography rather than forcing every line flush to
   the margin. The goal is to avoid awkward wraps, orphaned words, overfull
   boxes, excessive hyphenation, and page overflow.

4. **Truth Preservation**
   Flag risky edits instead of silently inventing metrics, company names, tools,
   or outcomes.

5. **Diff-Based Control**
   Show proposed changes as diffs before applying them. Resume edits need user
   review because small wording changes can alter meaning.

## Recommended MVP: Local MCP / CLI Workflow

Start with a local-first workflow for users who already maintain LaTeX resumes.
This is more feasible than a standalone web app because the user already has the
source files, local LaTeX environment, and editor preview.

### Target User

Technical students, engineers, and job seekers who:

- already maintain a LaTeX resume,
- care about strong impact bullets,
- care about fitting content into one page,
- are comfortable reviewing diffs in an editor.

### Basic Flow

```text
resume.tex
  -> parse sections and bullets
  -> rewrite selected bullets
  -> compile PDF
  -> measure rendered layout
  -> score candidates
  -> show diff
  -> apply approved edit
```

### Agent Tools

An MCP server or CLI can expose a small set of tools:

1. `extract_bullets(resume_path)`
   Returns structured bullet locations, surrounding section context, and source
   text.

2. `evaluate_bullet(bullet, context)`
   Scores impact structure, XYZ fit, specificity, verbosity, and truth risk.

3. `compile_and_score(resume_path, bullet_id)`
   Compiles the resume and returns PDF layout metrics for the selected bullet.

4. `propose_rewrites(bullet, constraints)`
   Generates candidate rewrites that preserve facts and fit the requested style.

5. `replace_bullet(resume_path, bullet_id, new_text)`
   Applies a selected edit safely.

## Layout Scoring

The PDF scorer is the key differentiator. It should measure the rendered output,
not just the LaTeX source.

Useful metrics:

- rendered line count per bullet,
- final-line fullness,
- orphaned words,
- overfull or underfull boxes,
- section overflow,
- page-count changes,
- excessive hyphenation,
- spacing or indentation anomalies.

Example structured result:

```json
{
  "xyz_score": 0.86,
  "line_fit_score": 0.94,
  "truth_risk": "medium",
  "changed_entities": ["latency"],
  "layout": {
    "lines": 2,
    "last_line_fullness": 0.91,
    "has_orphan": false,
    "overfull": false,
    "page_count_changed": false
  }
}
```

The scorer can be implemented with `latexmk` or `tectonic` for compilation and
`PyMuPDF`, `pdfplumber`, or similar tooling for PDF text geometry.

## Layout Philosophy

Avoid promising that every line will be perfectly flush against the right
margin. That can produce worse typography and unnatural text.

Prefer soft targets:

- no single-word final lines,
- no obviously short final lines,
- no overfull boxes,
- stable page count,
- readable spacing,
- minimal awkward hyphenation,
- section layout remains visually balanced.

## Truth and Style Constraints

The agent should preserve the user's resume style and avoid unsupported claims.

Preserve:

- tense,
- punctuation style,
- bullet length norms,
- LaTeX macros,
- section order,
- role-specific vocabulary,
- existing quantified facts.

Flag:

- newly invented metrics,
- changed company or product names,
- changed technologies,
- stronger claims than the source supports,
- vague impact language with no measurable result.

## Future Option: Dedicated Web App

A standalone web app could come later, but it should not be the first build.

Potential web app shape:

- chat/prompt panel,
- LaTeX source editor,
- live PDF preview,
- diff review,
- backend compilation and PDF scoring.

This adds complexity around secure LaTeX compilation, file upload, templates,
state management, preview latency, and infrastructure. Build it only after the
local scorer and agent loop prove useful.

## First Technical Milestone

Build the smallest useful scorer:

```text
input: resume.tex + bullet id
output: rendered line count, final-line fullness, orphan status, page-count change
```

Once this exists, the agent can iterate on real rendered feedback instead of
guessing.

## Next Steps

1. Build the LaTeX compile-and-measure script.
2. Add bullet extraction with stable source locations.
3. Define the structured scoring schema.
4. Implement candidate rewrite generation with truth-risk checks.
5. Add safe patching and diff review.
6. Keep the public example repo in sync via the `sync-to-public.yml` workflow.
