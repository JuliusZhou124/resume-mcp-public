---
name: fill-page
description: Add bullets to a resume to fill remaining page space without pushing to 2 pages, ensuring every addition is relevant to the target role. Use when the user wants to add content, maximize resume density, or fill whitespace on a 1-page resume. Validates every addition through compare_plan_layout before applying.
---

# fill-page

Adds bullets to a resume to use available page space, staying at exactly 1 page
and targeting **100% < page_fill < 102%**. Every addition must pass three
gates: **relevance** to the target role, **XYZ quality** (action verb +
metric + result), and **layout** (fits 1 page, last line ≥ 90% full).
Single-line bullets at exactly 100% fullness can produce stray blank lines in
the rendered PDF; prefer the **0.90–0.98** last-line fullness zone. The MCP
server enforces layout; this skill enforces the rest.

## Hard rules

1. **Never** use Edit/Write on a `.tex` file. All writes go through
   `mcp__resume-fitter__add_bullet` (hook enforces this).
2. **Never fabricate a metric.** If `has_metric` is false, check other resumes
   in the workspace first (see Step 1a) for a real bullet describing the same
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
- **Load role context**: check if `role.md` exists in the same folder as the `.tex`.
  If it exists, read it for the target role's requirements and competencies.
  If it doesn't exist, infer the role from the filename (e.g.
  `amazon-sde-intern-aws-data-services` → Amazon SDE / AWS Data Services) and
  ask the user to confirm or add key requirements.
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

### Step 1a — Check other resumes for reusable real bullets
Before drafting anything from scratch, call `mcp__resume-fitter__list_resumes`
and `mcp__resume-fitter__list_bullets(tex_path=...)` on the other `.tex` files
in the workspace. The same real experience is often phrased differently
across past target resumes — one version may already carry a metric, a
sharper action verb, or a framing that fits the current gap better than
anything you'd draft fresh. Treat any bullet found this way as real material
(same person, same work) — it still goes through Step 2's scoring and the
relevance/grounding checks, but it is never a fabrication concern and should
be preferred over inventing new wording. Only fall back to drafting a brand
new bullet, or asking the user for a metric, if nothing reusable fits the gap.

### Step 2 — Draft and score candidates
For each confirmed area, draft an XYZ bullet: **action verb + what + metric**
— or adapt a reusable bullet found in Step 1a. Run
`mcp__resume-fitter__evaluate_candidate` on each:
- `xyz_score < 1.0` with `has_metric: false` → re-check Step 1a for a version
  with a metric; if still none, ask the user for a real number. Do NOT
  proceed without one.
- `xyz_score < 1.0` for other reasons → revise and re-score.
- Re-apply the relevance check: does the drafted bullet actually demonstrate
  what you identified in Step 1? If the draft drifted, revise it.

### Step 3 — Plan and validate
Build the full plan as a list of `add_bullet` ops. Call
`mcp__resume-fitter__compare_plan_layout(ops=[...])` with all additions at once.

- `fits_one_page: true` **and** `1.0 < page_fill < 1.02` → proceed.
- `fits_one_page: true` but `page_fill <= 1.0` → keep the plan but try to
  identify one more high-relevance addition to push past 100%. Do not add
  filler.
- `fits_one_page: true` but `page_fill >= 1.02` → drop the lowest-relevance
  bullet from the plan and re-run `compare_plan_layout`. Tell the user which
  was dropped. Repeat until page_fill falls below 102%.
- `fits_one_page: false` → drop the lowest-relevance bullet from the plan,
  re-run `compare_plan_layout`. Tell the user which was dropped. Repeat until
  it fits. Never drop a high-relevance bullet to keep a low-relevance one.

Also check each candidate's `last_line_fullness` from `compare_candidate_layout`:
single-line bullets should land in the **0.90–0.98** zone. Revise any bullet at
100% to avoid phantom blank lines.

### Step 4 — Apply in order
For each op: `mcp__resume-fitter__add_bullet(role=..., new_bullet=..., confirm=true)`.
- `applied: true` → continue.
- `applied: false` (fullness/overfull refused) → revise the bullet, re-score
  (Step 2), retry. Never skip or fall back to Edit.
- After all: call `compile_and_score` to confirm `page_count: 1` and
  `1.0 < page_fill < 1.02`.

### Step 5 — Report
State: bullets added, roles they went to, final page count, final page fill,
and any bullets dropped from the plan to stay at 1 page.

## Quick reference
| Gate | Check | Enforced by |
|------|-------|-------------|
| Relevance | Fills a competency gap for the target role | Skill (agent judgment) |
| XYZ | `xyz_score == 1.0` | Skill (soft gate) |
| Metric | `has_metric: true` | Skill (soft gate, ask user) |
| Page fill | `compare_plan_layout` → `1.0 < page_fill < 1.02` | Skill (compile check) |
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
