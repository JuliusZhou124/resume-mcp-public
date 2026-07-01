---
name: set-role
description: Create or update a role.md file for a resume from a job posting. Paste the job posting and this skill extracts the key requirements, competencies, tools, and disqualifiers into the structured format that /tailor, /fit-bullet, and /fill-page use for relevance checking.
---

# set-role

Parses a job posting and writes a structured `role.md` next to the target
`.tex` file. Run this before `/tailor` — it's what makes relevance judgments
concrete rather than inferred from the filename.

## Invocation

`/set-role <resume-path>`

`resume-path` is required because `role.md` must live in the **same folder as
the `.tex`** — that's the convention `/tailor`, `/fit-bullet`, and `/fill-page`
rely on to auto-discover it for relevance checking. Writing it anywhere else
means those skills won't find it. Resolve the resume's folder from
`resume-path` and write `role.md` there.

## Procedure

### Step 1 — Get the posting
First, check whether a `role.md` already exists in that folder. If it does,
read it and ask the user whether to **overwrite** it from scratch or **update**
the existing file (preserving what's still accurate and revising the rest) —
don't silently clobber prior work.

Then ask the user to paste the full job posting text. Accept raw copy-paste —
no formatting required. Wait for the paste before proceeding.

### Step 2 — Extract and confirm
Parse the posting and extract:

- **Role title and company** — exact strings from the posting
- **Must-have qualifications** — hard requirements (degree, years, specific skills)
- **Preferred / bonus qualifications** — "nice to have", "plus", "preferred"
- **Core competencies** — what the team actually does, what problems they solve,
  what success looks like in the role
- **Tools and technologies explicitly named** — languages, frameworks, platforms,
  AWS services, etc.
- **What this role does NOT care about** — infer from what's absent or
  de-emphasized; helps `/tailor` avoid adding irrelevant bullets
- **Disqualifiers** — things the posting *explicitly* rules out or hard-gates
  on (e.g. "no junior candidates", "must be a US citizen", "on-site only").
  Unlike the inferred "does NOT care about" list, these are stated outright and
  flag whether the role is even a fit

Present a structured summary to the user and ask:
- "Does this match your reading of the posting?"
- "Anything missing or wrong?"

Incorporate any corrections before writing.

### Step 3 — Write the file
Write `role.md` next to the `.tex` with this structure:

```markdown
# Role: <title>
**Company:** <company>
**Source:** <URL if the user provided one, otherwise omit>

---

## Full job posting
<raw posting text pasted by user>

---

## Extracted requirements

### Must-have qualifications
- ...

### Preferred / bonus qualifications
- ...

### Core competencies
- ...

### Tools and technologies
- ...

### What this role does NOT care about
- ...

### Disqualifiers
- ...
```

Confirm the file was written. Note that `role.md` is now read automatically by
all three downstream skills for relevance checking — `/tailor <resume-path>`
for a full tailoring pass, and `/fit-bullet` and `/fill-page` whenever they
edit or add bullets for this resume.
