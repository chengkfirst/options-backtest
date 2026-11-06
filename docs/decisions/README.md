# `docs/decisions/` — one file per decision, never edited once made

A decision record is the answer to "why is it like this", asked by someone six months
later who is about to change it. The commit message carries that for a change; this
carries it for a choice that spans many changes, or that was made by **not** doing
something.

**A decision is never updated.** It records what was known, considered and chosen at the
time, and that does not stop being true when the choice is reversed. A reversal is a new
file that says `Supersedes: <file>`, and the old one gains one line: `Superseded by
<file>`. Editing the original destroys the only evidence of how the thinking changed,
which is the thing most worth having.

**File name:** `NNNN-<slug>.md` — a four-digit sequence, then a kebab-case summary of the
choice: `0001-one-day-execution-lag.md`, `0002-no-event-bus.md`. The number is the order
decided, which fixes the reading order; the slug is what makes it findable.

## The shape of an entry

```markdown
# NNNN · <the choice, as a statement>

**Date:** YYYY-MM-DD
**Status:** accepted | superseded by NNNN-<slug>.md
**Supersedes:** NNNN-<slug>.md | nothing

## Context
What was true that made this need deciding. Including what was *not* known — a decision
made under uncertainty reads as careless later unless the uncertainty is on the record.

## Options
Each one considered, with what is good about it. An options list where only the chosen one
is described is a justification, not a decision record, and nobody can reopen it.

## The choice, and why
Which, and the reason — including the reason the others were not chosen, which is usually
the more useful half.

## Consequences
What this makes easy, what it makes hard, and **what becomes wrong if a condition
changes**. That last part is what lets a future reader know the decision is now due for
review rather than guessing.

## What would reopen this
The concrete condition. "Intraday option quotes become available" is one; "if it turns out
to be slow" is not.
```

## Candidates already in the codebase

Three choices are recorded in `conventions.md` as prose and would each be a decision file.
Not converted tonight — moving them would duplicate them, and one fact belongs in exactly
one place:

- **T's close decides, T+1's close fills**, against the document's intraday snapshot. The
  realised holding is `hold_days` plus the lag, no intraday stop can be modelled, and
  `risk_pct` assumes a loss equal to the stop distance that a close-to-close fill routinely
  exceeds. Reopened by intraday option quotes.
- **Marking to market at step 5 rather than last**, which deviates from the document's
  daily order and has no corporate-action step. Equivalent under a one-day lag; wrong the
  moment an intraday fill lands on its signal's day.
- **No event bus**; layers call each other directly, so the document's `on_event` protocol
  has nothing to receive and `decide` is what the engine calls.
