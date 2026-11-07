# `docs/plans/` — a piece of work with a beginning and an end

A plan and its progress file. The progress file is not optional and not a courtesy: it is
what makes the work survive a stop, and unattended work stops — a usage limit, a sleeping
machine, a crash, a compacted context.

**File names:** `<name>.md` for the plan, `<name>-progress.md` beside it, and
`<name>-report.md` when it finishes. Kebab-case, no date — a plan is amended rather than
reissued, and the dates that matter are in git.

## Why a progress file, and what goes in it

The failure it prevents is the quiet one: work done, not recorded, and then done again
differently by the next session — or worse, assumed done and not checked. So it is written
**after every item, not at the end of a phase**, and committed with the work it describes.

Each entry records:

- the item's status: `DONE`, `DONE (already fixed)`, `BLOCKED` with the reason, `SKIPPED`
- the files it touched
- **the command that verified it, and that command's output** — not "verified", the actual
  result, because a claim of verification is not evidence of one
- the commit
- **which reading was taken**, wherever the plan was ambiguous or its premise had moved on.
  This is the one that pays for itself: a plan written before the work is always wrong
  about something, and the next reader needs to know which parts were followed and which
  were judged.

A plan item marked `BLOCKED` is a finished outcome, not a failure — it says what a human
needs to decide. A guard weakened to make an item pass is not.

## Why a report as well

The progress file is written for whoever resumes the work; the report is written for
whoever reviews it. They are not the same document: the first is chronological and long,
the second is organised by question — what changed, what was verified and how, what is
blocked, what was found and not fixed, what the human has to do next.

## Here now

- [`agent-architecture-setup.md`](agent-architecture-setup.md) — the agent architecture,
  guardrails and documents. [progress](agent-setup-progress.md) ·
  [report](agent-setup-report.md)
