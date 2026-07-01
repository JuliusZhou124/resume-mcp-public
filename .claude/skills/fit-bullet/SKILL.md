---
name: fit-bullet
description: Edit a LaTeX resume bullet through the resume-fitter MCP propose->validate->diff->apply loop, enforcing XYZ quality, relevance to the target role, and the 90% last-line fullness gate. Use whenever the user wants to reword, tighten, or rewrite a resume bullet. NEVER edit resume .tex bullet text with the Edit or Write file tools — always route through this skill.
---

# fit-bullet

Rewrites a single `\resumeItem` bullet through the `resume-fitter` MCP server.
MCP tools are the **only** sanctioned way to change bullet text. The Edit/Write
file tools bypass the server-side fullness and overfull hard gates — they are
**forbidden** for bullet text changes (a PreToolUse hook now enforces this).

## Hard rules

1. **Never** use Edit/Write/Bash/sed on a `.tex` file to change bullet text.
2. **Never fabricate a metric.** If `has_metric` is false, STOP — first check
   other resumes for the same work already carrying a metric (Step 3), and only
   then ask the user.
3. **Never apply** until relevance, XYZ, and fullness all pass.
4. If `apply_bullet` returns `applied: false`, the write did NOT happen. Revise
   and retry — do not fall back to Edit.

## Invocation

`/fit-bullet [resume-path] [bullet-id-or-text] [optional: rewrite goal]`

All args optional. If `resume-path` is given, call `set_active_resume` first.
If the bullet is unspecified, call `list_bullets` and ask the user which one.

## Procedure

### Step 0 — Select resume, load role context, select bullet
- If a path was given: `mcp__resume-fitter__set_active_resume(path)`.
- **Load role context**: check if `role.md` exists in the same folder as the `.tex`
  (`role.md` in the same folder). If it exists, read it — it contains the
  target role's key requirements and competencies. If it doesn't exist, infer
  the role from the filename (e.g. `amazon-sde-intern-aws-data-services` →
  Amazon SDE targeting AWS Data Services) and note this for the relevance check.
- Resolve the bullet with `get_bullet(index=...)` or `get_bullet(text="...")`.
  Confirm the match with the user if resolved by a loose substring.
- Note the current `evaluation.xyz_score` as a baseline.

### Step 1 — Propose
Draft a rewrite as XYZ: **action verb + what you did + quantified result/scope**.
Roughly 12–30 words. Do not call any apply tool yet.

### Step 2 — Relevance gate (before scoring)
Before evaluating XYZ, ask: **does this bullet demonstrate something the target
role explicitly cares about?**

Use the role context from Step 0. Concretely:
- Does it show a skill, tool, or domain the job requires?
- Does it add information a recruiter/interviewer for this role would value?
- Or does it mostly repeat what other bullets already demonstrate?

If relevance is low or unclear, flag it to the user and ask whether to proceed,
reframe the bullet for better fit, or skip it. Do not apply a low-relevance
bullet silently — relevance is a gate, not a suggestion.

### Step 3 — Score (no compile)
Call `mcp__resume-fitter__evaluate_candidate(candidate=..., index=...)`. If a
term in this bullet refers to a skill you're adding to Technical Skills in the
same session, pass `pending_skills=[...]` so an as-yet-unapplied skill counts as
grounded — otherwise it shows up as a false "ungrounded" warning below.

**XYZ:**
- `xyz_score == 1.0` → proceed.
- `has_action_verb: false` or `has_result_clause: false` → revise and re-score.
- `has_metric: false` → **STOP.** Don't invent a number. Before asking the user,
  check whether you've already described this same real work elsewhere: call
  `list_resumes`, then `list_bullets(tex_path=...)` on the other resumes and look
  for a bullet about the same accomplishment that already carries a metric or
  sharper framing. Past targets often phrase the same work differently and one
  version may already fill the gap — reusing/adapting it is the same person and
  same work, not fabrication, and is preferred over inventing wording. Only if
  nothing reusable turns up, ask the user for a real, concrete number (a count of
  systems/users/items, a percentage change, time saved, etc.).
- Soft exception: only proceed below 1.0 if the user explicitly accepts 0.667
  because no metric genuinely exists. Surface the trade-off first.

**Grounding (soft gate — surface it, never silently skip it):** check
`grounding.is_grounded`. If it's false, the bullet references something
(`grounding.ungrounded` lists the terms) that doesn't appear in the resume's
skills or other bullets. List those terms to the user and revise until every
reference is grounded — or let the user explicitly override (e.g. it's a real
skill they want to add). The point is the user decides with the ungrounded terms
in front of them; don't bury them.

Check `truth_risk`. A bare number vs `40\%`/`40\\%` is the known LaTeX `\%`
tokenization false positive — ignore it. Any genuinely new entity/number →
confirm with the user it's true.

### Step 4 — Validate layout (compile)
Call `mcp__resume-fitter__compare_candidate_layout(candidate=..., index=...)`.
Inspect `after`:
- `meets_fullness_requirement: true` → proceed.
- `meets_fullness_requirement: false` → diagnose:
  - `lines == 2`, sparse `last_line_fullness` (**sparse wrap**): shorten to one
    full line or lengthen the second line to ≥ 90%. Re-score and re-validate.
  - `lines == 1`, `last_line_fullness < 0.9` (**sparse single**): add detail.
- `page_count_changed: true` → tell the user; this is a structural problem.

### Step 5 — Show diff
Call `mcp__resume-fitter__diff_candidate(candidate=..., index=...)` and show it.
State: `xyz_score`, `after.last_line_fullness`, `page_count_changed`, relevance.

### Step 6 — Apply
Call `mcp__resume-fitter__apply_bullet(candidate=..., index=..., confirm=true)`.
Pass the same `pending_skills=[...]` you used in Step 3 if a skill referenced in
this bullet is being added in the same session, so the server's grounding check
doesn't flag it.
- `applied: true` → done.
- `applied: false` → read `error` + `layout`, revise, return to Step 3. Never Edit.

### Step 7 — Re-sync (do not skip)
Bullet indices are positional, so a prior apply/add/remove can silently shift
them — reusing a stale index doesn't error, it edits the *wrong bullet*. Before
you reference any index again in this session (a second `/fit-bullet`, or any
later edit), **always re-run `list_bullets` first and re-resolve the target.**
Resolving by text instead of index sidesteps the drift entirely.

## Quick reference
| Gate | Check | Enforced by |
|------|-------|-------------|
| Relevance | Bullet advances the target role's story | Skill (agent judgment) |
| XYZ | `xyz_score == 1.0` | Skill (soft gate) |
| Metric | `has_metric: true` | Skill (soft gate, reuse from other resume or ask user) |
| Grounding | `grounding.is_grounded` (surface `grounding.ungrounded`; `pending_skills=[...]` for same-session skills) | Skill (soft gate, surface to user) |
| Fullness | `after.meets_fullness_requirement == true` | Server (hard gate) |
| Overfull | No overfull box warnings | Server (hard gate) |
| Apply | `apply_bullet(confirm=true)` — the only writer | Hook blocks Edit |
