---
name: spec
description: Interview the human before building a feature, then write docs/specs/<feature>.md with its goal, non-goals, acceptance criteria and the tests that will prove them. Reads the relevant code first so the questions are informed, asks at most three at a time, and waits for approval before any implementation. Use when a feature is about to be built and nobody has written down what done means.
argument-hint: "[feature name or a sentence describing it]"
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, TodoWrite
---

# /spec

Write down what "done" means, before it is built.

Without a spec, the criterion drifts to whatever got built, which makes every feature a
success and the question meaningless. That is the whole of why this exists.

## 1. Read the code first — before asking anything

**Do not open with questions.** A question whose answer is in the code spends the human's
attention on something you could have found, and after two of those they stop answering
carefully.

Find and read:

- the modules the feature would touch. `backtest/core/slots.py` for anything about the
  seven components; `backtest/api/sdk.py` for anything reaching `run()`;
  `app/static/js/contract.js` for anything the interface would read.
- `requirements.md` — is this already a criterion? Is it marked partial, with a reason? A
  reason given there is usually the answer to the hardest question you were going to ask.
- `docs/specs/` — has something like this been specified before, or superseded?
- `docs/decisions/` — was a choice already made that constrains this?
- `conventions.md` — which hard lines the feature runs into. There is usually one.

Then say, in three or four lines, what you found: what exists, what is partial and why, and
what the feature would actually have to change. **This is the most valuable thing you
produce before the questions**, because about a third of the time it ends the conversation —
the thing exists, or the reason it does not is the answer.

## 2. Ask, at most three at a time

Three, then wait. A list of eleven questions gets three answered properly and eight
answered "yes".

Ask only what the code cannot tell you, and prefer the questions whose answers change the
shape of the work:

- **The edge cases.** What should happen when the data is absent, the chain is empty, the
  sample is one trade, the configuration contradicts itself. This is where the real design
  decisions are, and where "obviously" means two people meant different things.
- **Error handling.** Refuse, warn, or proceed with a flag? This project's default is to
  refuse rather than approximate, so the question is really: is this the exception, and why.
- **What the interface shows.** Including what it shows when the answer is "cannot be
  computed" — absence is never printed as a number here, so somebody has to decide what it
  *is* printed as.
- **The non-goals.** Ask directly: what should this deliberately not do? The most useful
  answer in the file, and nobody volunteers it.

Offer your own answer with each question when you have one — "I would refuse, because the
nearest existing case does; is that right?" is easier to answer than an open question, and
it exposes your assumption where it can be corrected.

## 3. Write it

To `docs/specs/<feature>.md`, in the shape `docs/specs/README.md` gives: goal, non-goals,
acceptance criteria (numbered, each falsifiable), the tests that will prove them (one per
criterion, by name and file, with its `kind` and `layer`), what it changes that the
interface reads (every field, at the depth it is read, to be declared in `CONTRACT` — and
**"nothing"** written explicitly where that is the answer, so that it was considered rather
than forgotten), and the risks with what would make us stop.

Two things to get right:

- **A criterion that cannot be falsified belongs in the goal, not in the criteria.** "The
  interface is clear" is a goal. `requirements.md` already marks such things hand-written
  rather than pretending they were computed; do the same rather than inventing a metric.
- **A criterion with no test says so, and says why.** That is honest and useful. A test
  invented to fill the row is neither.

## 4. Stop

**Write `Approved: not yet` and stop.** Do not begin implementing, do not create a branch,
do not sketch the code "to check it is feasible" — a sketch is an implementation nobody
approved, and it anchors the review.

Say which agent the work belongs to when it is approved: `engine-dev` for `backtest/`,
`interface-dev` for `app/`. If it is both, say so explicitly and say which half comes first
and what the other half waits for.
