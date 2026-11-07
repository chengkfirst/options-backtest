# `docs/specs/` — one file per feature, written before it is built

A spec exists so that "is it done" has an answer that was fixed before the work started.
Without one, the criterion drifts to whatever was built, which makes every feature a
success and the question meaningless.

**File name:** `<feature>.md`, kebab-case, the feature's own name — `intraday-fills.md`,
`variant-ablation.md`. Not dated: a spec is amended rather than reissued.

**Who writes one.** `/spec` interviews the human and writes it. `research-analyst` writes
one when the platform cannot answer a research question — the missing capability as a
request, rather than building it. A builder agent never writes its own spec.

**Approval.** The human approves a spec before any implementation. Nothing in the
structure can enforce that, so it is stated: a spec with no `Approved:` line has not been
approved.

## The shape of an entry

```markdown
# <feature>

**Status:** draft | approved | built | superseded by <file>
**Approved:** <date, by the human> | not yet

## Goal
One paragraph. What becomes possible that is not possible now, in terms of a question
someone wants answered — not in terms of the code.

## Non-goals
What this deliberately does not do, and what is being deferred. The most useful section
in the file: it is what stops the work growing, and what a reviewer checks scope against.

## Acceptance criteria
Numbered. Each one falsifiable — a sentence of the form "X, and if not-X then this is not
done". A criterion that cannot be falsified belongs in the goal, not here.

## The tests that prove them
One per criterion, by name and file, with its `kind` and `layer` (see `tests/kinds.py`).
A criterion with no test says so, and says why it cannot have one.

## What it changes that the interface reads
Every response field, at the depth it is read, to be declared in `CONTRACT`
(`app/static/js/contract.js`) before the interface reads it. Empty if nothing — say
"nothing", so that it was considered rather than forgotten.

## Risks and what would make us stop
What could make this the wrong thing to have built, and what would show it.
```

## Why the last two sections are there

**The `CONTRACT` section** because front and back moving separately raises nothing and has
failed three times in this project — the run history showing `undefined`, every group label
coming out empty, and the interface sending a sizing method's old name after a rename. The
spec is the earliest place that can be written down.

**"What would make us stop"** because a feature with no such condition cannot be abandoned.
It is the same rule `docs/research/plan.md` applies to a research question.
