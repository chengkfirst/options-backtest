# `docs/feedback/` — written by the human, read by agents

**This is the one directory an agent may not write to**, and the reason is that it holds
the only input nothing in this repository can generate: what actually happened when the
platform was used for real.

Everything else an agent reads it can also derive — the code says what the code does, the
guards say whether it holds, `requirements.md` says what is missing. None of that says
that the run history page is unreadable after twenty runs, that the number everyone
actually wants is the spread cost per trade, or that a backtest was abandoned because
setting up the data took an afternoon. An agent inventing such an observation would be
inventing evidence, which is worse than having none.

**File names:** `<YYYY-MM-DD>-<topic>.md`. Dated, because feedback is about a moment: the
same complaint before and after a change is two observations, not one that was updated.

## What makes a useful entry

Not a feature request — **what you were trying to do, and where it went wrong.** The
difference matters because a request names a solution and so hides the problem, and the
problem is the part an agent cannot reconstruct.

```markdown
# <what you were trying to do>

**Date:** YYYY-MM-DD

## What I was doing
The actual task, in your own terms.

## Where it went wrong
What happened, and what you expected. The surprise is the useful part.

## What I did instead
The workaround, if there was one. A workaround that works is evidence about priority:
it says the thing is annoying rather than blocking.

## What I think would fix it
Optional, and marked as a guess. Kept separate from the account above so that an agent
reading it can tell the observation from the proposed solution.
```

## Who reads it

- `research-lead` — first, before deciding what to research next.
- `/explore` — reads this and the latest `docs/radar/` digest before the debate, so a
  direction can be grounded in something that happened.
- `interface-dev` — the interface's failures are almost all of this kind, and almost none
  of them are visible from the code.
