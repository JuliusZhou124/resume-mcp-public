---
name: tailor
description: Full resume tailoring workflow for a specific job target. Audits every existing bullet for relevance, rewrites or removes weak/irrelevant ones, aligns Technical Skills, then fills remaining page space with high-relevance additions. This is the canonical single command for preparing a resume for a specific role. Requires a role.md file next to the .tex with the job posting and requirements.
---

# tailor

The canonical end-to-end skill for targeting a resume at a specific role.
Runs in four phases in order: **audit → rewrite/remove → skills → fill**.
Every change goes through the MCP server. Direct Edit/Write on `.tex` is
blocked by a PreToolUse hook.

## Invocation

`/tailor <resume-path>`

`resume-path` is required. The companion `role.md` must exist
next to the `.tex` — if it doesn't, stop and tell the user to create it and
paste in the job posting before running `/tailor`.

## Setup

1. `mcp__resume-fitter__set_active_resume(resume-path)`
2. Read the `role.md` file. If it's empty or has only the template scaffold,
   stop — role context is required for relevance judgments. Ask the user to
   paste the job posting first.
3. Summarize the role's **top 5 competencies** from the posting. State these
   to the user so they can correct your reading before you proceed. Wait for
   confirmation before continuing.

Do **not** write a session log, per-run summary, or tailoring history to
`CHANGES.md` — that file is reserved for MCP/skill improvement proposals only
(see CLAUDE.md). Report what changed to the user directly in the **Final
report** at the end instead.

---

## Phase 1 — Relevance audit

Call `mcp__resume-fitter__list_bullets` and `mcp__resume-fitter__compile_and_score`.

For each bullet, score it on two dimensions:
- **Relevance** (1–3): 1 = directly demonstrates a required competency,
  2 = tangentially related, 3 = irrelevant or redundant for this role
- **Quality** (`xyz_score` from `compile_and_score`)

Present a table to the user:

```
#   Score  Rel  Bullet (truncated)
b0  1.0    1    Managed 102 blockchain RPC nodes...
b3  0.667  2    Implemented agentic RAG pipeline...
b7  0.333  3    Built NextJS pages with TailwindCSS...
```

Ask the user to review. Flag any bullet where Rel=3 as a removal candidate.
Do NOT proceed with removals until the user approves.

Also call `mcp__resume-fitter__list_role_blocks` and flag any block whose
item-list line span implies only 1 bullet — note it now so Phase 2a doesn't
remove it down to zero and Phase 4 knows to give it a second bullet (see the
**Minimum 2 bullets per role/project** hard rule below).

---

## Phase 2 — Rewrite and remove

Work through the approved changes in this order:

### 2a. Remove Rel=3 bullets (approved by user)
Before removing, check if the bullet's block is already down to 2 bullets —
if removing it would leave only 1, see **Minimum 2 bullets per role/project**
below: line up a real replacement bullet (from this resume or another in the
workspace) before removing, rather than removing first and patching later.

For each approved removal: `mcp__resume-fitter__remove_bullet(index=..., confirm=true)`.
After each removal, call `mcp__resume-fitter__list_bullets` to re-sync indices.

**Empty-block cleanup**: if removing bullets empties a project or role block,
remove the whole block with `remove_role_block`. Empty `\resumeItemList`
environments break LaTeX. If a whole section (e.g., Projects) becomes empty,
remove its heading too.

### 2b. Rewrite low-quality or Rel=2 bullets (user-approved)
For bullets the user wants improved, run the `/fit-bullet` procedure inline:
- Propose a rewrite that increases both relevance and `xyz_score`.
- `evaluate_candidate` → **grounding gate**: inspect `grounding`. If
  `is_grounded` is `false` (i.e. `grounding.ungrounded` is non-empty), do
  **not** proceed to layout or apply — revise the candidate so every flagged
  term is self-contained (explained by a role heading, another bullet, or a
  skill), or add the missing skill via Phase 3 first, then re-run
  `evaluate_candidate`. Only once `is_grounded` is `true` continue to
  `compare_candidate_layout`.
- `compare_candidate_layout` → confirm the layout/fullness gate passes.
- `apply_bullet(confirm=true)` → re-check the `grounding` field in the
  response as a final guard; if it comes back `is_grounded: false`, the apply
  was refused — warn the user, revise, and retry rather than forcing it.

**Self-grounding rule**: every bullet must be self-contained. If a bullet
references a system, tool, or concept (e.g. "load replay harness", "gRPC
polling debug"), that term must appear elsewhere in the resume — in a role
heading, another bullet, or the skills section. Bullets that reference
unexplained context read like fragments of a conversation, not standalone
resume achievements. The `grounding` field in `evaluate_candidate` /
`apply_bullet` / `add_bullet` flags `ungrounded` terms. Revise before applying.
After Phase 2, call `compile_and_score` to confirm page count and updated scores.

---

## Phase 3 — Technical Skills alignment

Call `mcp__resume-fitter__list_skill_categories`.

For each category (Languages, Frameworks, Developer Tools):
- Cross-reference with the role's required/preferred tools from `role.md`
- Check what's evidenced in bullets via `mcp__resume-fitter__evaluate_skill_candidate`
- Propose additions of evidenced, role-relevant skills the current list is missing
- Propose removals of skills that are evidenced but irrelevant to this role
  (removing clutter improves signal)

Show proposed changes to the user. For each approved change, in this order:
1. `mcp__resume-fitter__diff_skill_candidate(category=..., new_items=...)` —
   read-only; show the user the exact before/after diff of the category's
   items string so they can see precisely what will change before anything is
   written.
2. `mcp__resume-fitter__compare_skill_layout(category=..., new_items=...)` —
   confirm the change introduces no overfull / page-count regression.
3. `mcp__resume-fitter__apply_skill_category(category=..., new_items=..., confirm=true)`
   — the only mutating skills tool; no-ops without `confirm=true`.

---

## Phase 4 — Fill remaining space

Run the `/fill-page` procedure inline (do not invoke it as a separate skill —
execute its steps directly so the role context and audit findings carry forward):

1. **Survey the workspace for reusable material first (required).** Before
   drafting anything new, call `list_resumes` and then
   `list_bullets(tex_path=...)` on every *other* `.tex` resume in the
   workspace. Past target resumes often phrase the same real work differently
   — one version may already carry a metric or framing that fills the current
   gap better than anything drafted fresh. A bullet found this way is real
   material (same person, same work), not a fabrication risk — prefer
   reusing/adapting it over inventing new wording or asking the user for a
   number. Keep these reusable bullets on hand as you assess gaps in the next
   steps; only draft new or ask the user if nothing reusable fits.
2. `list_role_blocks` to map the active resume's structure.
3. Identify competency gaps — required skills from `role.md` not yet
   demonstrated by any bullet after Phase 2 — and match each gap against the
   reusable bullets surfaced in step 1 before considering a fresh draft.
4. Propose additions (reused or drafted), confirm with user, write as XYZ bullets.
5. `evaluate_candidate` on each → `compare_plan_layout` for the full set.
6. Apply via `add_bullet(confirm=true)`.
7. Target **101% < page_fill < 103%**. Keep adding high-relevance bullets until
   the page is just overfull but still on one page, but never add filler.
8. Prefer the **0.90–0.98** last-line fullness zone for single-line bullets;
   revise any bullet at 100% to avoid phantom blank lines.
9. Final `compile_and_score` — confirm `page_count: 1`,
   `1.01 < page_fill < 1.03`, and all scores.

---

## Render PDF

After the final `compile_and_score`, render the PDF by running:

```python
import tempfile, shutil
from resume_fitter.compile import compile_tex

tex_path = "<active resume path>"
pdf_out = tex_path.replace(".tex", ".pdf")
with tempfile.TemporaryDirectory() as tmp:
    result = compile_tex(tex_path, tmp)
    if result.pdf_path:
        shutil.copy(result.pdf_path, pdf_out)
```

Execute this via Bash:
```bash
PYTHONPATH=src venv/bin/python -c "
import tempfile, shutil
from resume_fitter.compile import compile_tex
tex = '<active resume path>'
with tempfile.TemporaryDirectory() as tmp:
    r = compile_tex(tex, tmp)
    if r.pdf_path:
        shutil.copy(r.pdf_path, tex.replace('.tex', '.pdf'))
        print('PDF written to', tex.replace('.tex', '.pdf'))
    else:
        print('FAILED:', r.log[-500:])
"
```

This uses the `compile_tex` wrapper (not raw `tectonic`) so the
`glyphtounicode` workaround is applied automatically.

---

## Final report

After all four phases:
- Page count and page fill
- Bullets added / rewritten / removed
- Technical Skills changes
- Remaining competency gaps (things the role wants that couldn't be added
  because no real accomplishment exists for them — flag these honestly)


## Quick reference

| Phase | What | Key tools |
|-------|------|-----------|
| Setup | Set active resume, read role.md, confirm top-5 competencies | `set_active_resume` |
| Audit | Score every bullet on relevance + XYZ | `compile_and_score`, `list_bullets` |
| Rewrite/remove | Fix or cut weak/irrelevant bullets; cleanup empty blocks | `apply_bullet`, `remove_bullet`, `remove_role_block` |
| Skills | Align Technical Skills to role | `apply_skill_category`, `evaluate_skill_candidate` |
| Fill | Add high-relevance content to reach 101% < page_fill < 103% | `compare_plan_layout`, `add_bullet` |
## Hard rules (same as fit-bullet / fill-page)

- Never Edit/Write `.tex` directly — hook blocks it.
- Never fabricate a metric — ask the user.
- Never apply without passing relevance + XYZ + layout gates.
- Never add a bullet just to fill space — every addition must fill a competency gap.
- Never apply a bullet with ungrounded references — check the `grounding` field
  and revise if `is_grounded` is false. Bullets must be self-contained.

- **Preserve high-signal credentials.** High-profile internships and notable
  hackathon wins are strong positive signals. Do not remove them unless the
  role explicitly disqualifies the domain or keeping them would materially
  hurt the resume (e.g., push to 2 pages, create ungrounded claims). When in
  doubt, keep them and reframe for relevance rather than cut them.

- **Minimum 2 bullets per role/project.** No work-experience or project block
  should end up with only 1 bullet, even if a second bullet isn't directly
  relevant to the target role. A single-bullet entry reads as thin or padded;
  every real role/project deserves at least two lines of evidence. This
  applies in both directions:
  - **Phase 2a removals**: before removing a Rel=3 bullet, check whether doing
    so would drop its block to 1 bullet (`list_role_blocks` item-list line
    span). If so, find a real replacement bullet first — check other resumes
    in the workspace (`list_resumes` / `list_bullets(tex_path=...)`) for the
    same role/project's other real accomplishments — before removing, so the
    block never sits at 1 bullet between steps.
  - **Phase 4 fill**: after the audit/rewrite phases, check every block for
    bullet count. Any block at 1 bullet gets a second one even if no
    competency gap calls for it.
  - **Highest ROI when not directly relevant**: when the second bullet won't
    be directly relevant to the target role, still pick the best real
    candidate among the options — score each with `evaluate_candidate` and
    prefer the one with the highest `xyz_score` and fewest `grounding.ungrounded`
    terms, not just the first one that satisfies the minimum.