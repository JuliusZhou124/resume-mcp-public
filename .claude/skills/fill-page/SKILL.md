---
name: fill-page
description: Add bullets to a resume to fill remaining page space without pushing to 2 pages, ensuring every addition is relevant to the target role. Use when the user wants to add content, maximize resume density, or fill whitespace on a 1-page resume. Validates every addition through compare_plan_layout before applying.
---

# fill-page

Adds bullets to a resume to use available page space, staying at exactly 1 page
and targeting **101% < page_fill < 103%**. Every addition must pass three
gates: **relevance** to the target role, **XYZ quality** (action verb +
metric + result), and **layout** (fits 1 page, last line ≥ 90% full).
Single-line bullets at exactly 100% fullness can produce stray blank lines in
the rendered PDF; prefer the **0.90–0.98** last-line fullness zone. The MCP
server enforces layout; this skill enforces the rest.

## Hard rules

1. **Never** use Edit/Write on a `.tex` file. All writes go through
   `mcp__resume-fitter__add_bullet` (hook enforces this).
2. **Never fabricate a metric.** If `has_metric` is false, harvest from other
   resumes in the workspace first (Step 2) for a real bullet describing the same
   work with a metric already attached. Only ask the user for a new number if
   nothing reusable turns up.
3. **Only add bullets that are relevant to the target role.** Filler that
   doesn't advance the role's story wastes space and dilutes the resume.
4. **Never apply a plan** until `compare_plan_layout` confirms `fits_one_page: true`.

## Invocation

`/fill-page [resume-path]`

If `resume-path` is given, call `set_active_resume` first.

## Procedure

### Step 0 — Orient and load role context
- If a path was given: `mcp__resume-fitter__set_active_resume(path)`.
- **Load role context** — this drives the relevance gate, so resolve it before
  drafting anything:
  - If `role.md` exists in the same folder as the `.tex`, read it for the
    target role's requirements and competencies.
  - If it doesn't exist, infer the role from the filename (e.g.
    `amazon-sde-intern-aws-data-services` → Amazon SDE / AWS Data Services) and
    ask the user to confirm or add the key requirements.
  - **If the user can't supply any role context at all** (no `role.md`, filename
    is uninformative, and they don't know the target): don't refuse and don't
    invent a role. Proceed with **XYZ quality as the primary gate**, and
    explicitly flag every addition in your report as **"relevance not
    verified"** so the user knows the role-fit check was skipped. Relevance is
    a real gate everywhere else — degrade it visibly, never silently.
- Call `mcp__resume-fitter__compile_and_score` — note current page count and
  all bullet scores. If already at 2 pages, stop and suggest removing content
  first (`remove_bullet` / `remove_role_block`).
- Call `mcp__resume-fitter__list_role_blocks` and
  `mcp__resume-fitter__list_bullets` to map the full resume.

### Step 1 — Relevance-first gap analysis
Look for **competency gaps** relative to the target role — things the role
requires that the current bullets don't demonstrate. Prioritize:
- Required skills or tools with no supporting bullet
- Domain experience the role values that isn't shown
- Roles with few bullets where a real accomplishment is hiding

Do NOT propose adding a bullet just to fill space. Every proposed addition must
answer: "What does this demonstrate that a recruiter/interviewer for this role
would value and that the resume doesn't already show?" — **except** the
minimum-bullet-count check below, which is the one case where a bullet is
added regardless of relevance.

**Minimum 2 bullets per role/project (hard rule):** from `list_role_blocks`,
flag any block whose item-list line span implies only 1 bullet. Every
real role/project gets a second bullet even if it doesn't advance the
target-role story — a single-bullet entry reads as thin or padded. When the
best available second bullet isn't directly relevant to the role, still pick
the **highest-ROI** real candidate: score every option with
`evaluate_candidate` and prefer the highest `xyz_score` with the fewest
`grounding.ungrounded` terms, not just the first one that clears the bar.

Present a short list of proposed areas to the user. Confirm which ones
correspond to real work before drafting any bullets.

### Step 2 — Harvest reusable real bullets from other resumes
**Do this before drafting anything from scratch** — it's the cheapest source of
metric-bearing, battle-tested wording, and it's easy to skip straight to
drafting, so make it a deliberate stop. Call
`mcp__resume-fitter__list_resumes` and
`mcp__resume-fitter__list_bullets(tex_path=...)` on the other `.tex` files
in the workspace. The same real experience is often phrased differently
across past target resumes — one version may already carry a metric, a
sharper action verb, or a framing that fits the current gap better than
anything you'd draft fresh. Treat any bullet found this way as real material
(same person, same work) — it still goes through Step 3's scoring and the
relevance/grounding checks, but it is never a fabrication concern and should
be preferred over inventing new wording. Only fall back to drafting a brand
new bullet, or asking the user for a metric, if nothing reusable fits the gap.

### Step 3 — Draft and score candidates
For each confirmed gap, either adapt a reusable bullet harvested in Step 2 or
draft a fresh XYZ bullet: **action verb + what + metric**. Run
`mcp__resume-fitter__evaluate_candidate` on each (pass `pending_skills=[...]`
if you intend to also add matching skills this session, so terms aren't
falsely flagged as ungrounded):
- **Grounding gate:** inspect `grounding.is_grounded` / `grounding.ungrounded`.
  If `is_grounded` is false, the bullet claims a skill or tool the resume
  doesn't yet back up. Revise before proceeding — reword to drop the
  ungrounded term, or, if the skill is genuinely real and about to be added,
  pass it via `pending_skills` and re-score. Do not carry an ungrounded bullet
  into the plan. This matches the grounding gate in `/fit-bullet` and
  `/tailor`.
- `xyz_score < 1.0` with `has_metric: false` → re-check Step 2 for a version
  with a metric; if still none, ask the user for a real number. Do NOT
  proceed without one.
- `xyz_score < 1.0` for other reasons → revise and re-score.
- Re-apply the relevance check: does the bullet actually demonstrate what you
  identified in Step 1? If the draft drifted, revise it.

### Step 4 — Plan and validate
Build the full plan as a list of `add_bullet` ops. Call
`mcp__resume-fitter__compare_plan_layout(ops=[...])` with all additions at once.

- `fits_one_page: true` **and** `1.01 < page_fill < 1.03` → proceed.
- `fits_one_page: true` but `page_fill <= 1.01` → keep the plan but try to
  identify one more high-relevance addition to push past 101%. Do not add
  filler.
- `fits_one_page: true` but `page_fill >= 1.03` → drop the lowest-relevance
  bullet from the plan and re-run `compare_plan_layout`. Tell the user which
  was dropped. Repeat until page_fill falls below 103%.
- `fits_one_page: false` → drop the lowest-relevance bullet from the plan,
  re-run `compare_plan_layout`. Tell the user which was dropped. Repeat until
  it fits. Never drop a high-relevance bullet to keep a low-relevance one.

Also check each candidate's `last_line_fullness` from `compare_candidate_layout`:
single-line bullets should land in the **0.90–0.98** zone. Revise any bullet at
100% to avoid phantom blank lines.

### Step 5 — Apply in order
For each op: `mcp__resume-fitter__add_bullet(role=..., new_bullet=..., confirm=true)`.
- `applied: true` → continue.
- `applied: false` (fullness/overfull refused) → **diagnose before revising**,
  the same way `/fit-bullet` does. Read the returned `error` + `layout`, or
  call `mcp__resume-fitter__compare_candidate_layout` to see why it was
  rejected:
  - `lines == 2`, sparse `last_line_fullness` (**sparse wrap**): shorten to one
    full line, or lengthen the second line to ≥ 90%.
  - `lines == 1`, `last_line_fullness < 0.9` (**sparse single**): add concrete
    detail until the single line nearly fills the width.
  - overfull box: shorten the bullet.
  Then re-score (Step 3) and retry. Never skip or fall back to Edit.
- After all: call `compile_and_score` to confirm `page_count: 1` and
  `1.0 < page_fill < 1.03`.

### Step 6 — Report
State: bullets added, roles they went to, final page count, final page fill,
and any bullets dropped from the plan to stay at 1 page. If role context was
unavailable (Step 0), label every addition **"relevance not verified."**

## Quick reference
| Gate | Check | Enforced by |
|------|-------|-------------|
| Relevance | Fills a competency gap for the target role | Skill (agent judgment) |
| Grounding | `grounding.is_grounded == true` (no ungrounded terms) | Skill (soft gate) |
| XYZ | `xyz_score == 1.0` | Skill (soft gate) |
| Metric | `has_metric: true` | Skill (soft gate, ask user) |
| Page fill | `compare_plan_layout` → `1.01 < page_fill < 1.03` | Skill (compile check) |
| Plan fits | `compare_plan_layout` → `fits_one_page: true` | Skill (compile check) |
| Safe zone | Single-line bullets at 0.90–0.98 fullness | Skill (layout judgment) |
| Per-bullet layout | `add_bullet(confirm=true)` fullness + overfull | Server (hard gate) |
| Apply | `add_bullet(confirm=true)` — the only writer | Hook blocks Edit |

## Role context convention
Place a `role.md` file next to the `.tex` to give both skills
persistent role context without re-stating it each session. Example:

```
fall-2026/amazon-sde-intern-aws-data-services/role.md
```

Contents: key requirements, valued skills, domain competencies for the target
role. Both `/fit-bullet` and `/fill-page` read this file automatically.
