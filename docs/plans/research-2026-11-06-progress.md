# Research day 2026-11-06 — progress

Tracks [research-2026-11-06.md](research-2026-11-06.md). Branch `research/2026-11-06`,
stacked on `chore/agent-pipeline`.

**Context this run is in.** This is stage 5 of a six-stage unattended run. Stages 1–4 are
done, each on its own branch with its own PR (#1–#4), and each left green. The time already
spent on them is the constraint on this one, and it is recorded here rather than discovered
later: the plan's own section 0.1 says the deepening loop should take most of a run, and
most of this run is behind it.

**The reading taken.** Where the plan's phases cannot all be reached, the earlier phases are
done properly and the later ones are marked `BLOCKED` with what remains, rather than all of
them being done thinly. A benchmark with four repositories read carefully is worth more than
ten skimmed, and a deepening loop of two honest rounds is worth more than eight hurried ones.

---

## Phase 0 — Starting state

`git status` on arrival: clean apart from this plan, which was untracked at
`docs/plans/research-2026-11-06.md` — already in the right place, so no move was needed.
Branch created off `chore/agent-pipeline` at `2e08cd6`.

---

## Phase 1 — Sharpening the benchmark workflow — DONE

All four items, in one commit. `.claude/workflows/benchmark.js` and the new
`docs/research/benchmarks/README.md`.

**1.1 The eighth dimension, "AI and agent integration" — DONE.** Written so that its
centre of gravity is the question that actually decides whether an automated research loop
is honest: *which data does the loop see when it judges a result* — training, validation, or
the test period — and is the number of trials counted or deflated. The dimension says in so
many words that a loop reading its test period to choose its next hypothesis is a search
whose reported result is in-sample, however the code is arranged, and asks for the file that
decides it.

**1.2 Per-repository files — DONE.** A third pipeline stage writes
`docs/research/benchmarks/<repo>.md` before the synthesis reads anything, so a later run
compares against what was found rather than re-reading the repository.

Its required sections include one the plan did not ask for and that is the reason to keep
these files at all: **"Claims that did not survive the cross-check"** — every verdict that
came back overstated, wrong or `file_not_found`, with what the file actually shows. A
benchmark that keeps only its confirmed claims teaches nothing about how it was wrong, and
the next run believes the same thing again.

**1.3 Partial clones — DONE.** `--depth 1 --filter=blob:limit=1m`, plus a sparse checkout
for trees over ~3,000 files, and **the analyst must record what was not checked out**. A
dimension answered from a partial tree is answered from a partial tree; saying so is the
difference between a limit and a mistake.

**1.4 Leads — DONE.** An optional `leads: { "<repo>": ["<claim, with the file to start
from>"] }`. Threaded into the analyst's prompt with the instruction that a lead is a guess
from a skim and never a finding: confirm it with a file and a line, correct it, or report it
wrong, and never repeat one back unverified. A skim written up as a finding is how a guess
acquires a citation.

*One mistake made and caught here.* The first version of 1.2 wrapped stage 1's return as
`{results, repo}` to carry the repository name forward — unnecessary, because `pipeline()`
already passes the original item to every stage, and it broke stage 2's contract. Reverted
before committing; the structure check would have caught it, but reading the API caught it
first.

Verified: syntax checked as the runtime wraps it (a workflow body runs inside an async
function, so a bare module check rejects its top-level `return`), plus 12 structural checks
— `meta` a pure literal, phase titles matching both ways, eight dimensions, leads read and
threaded, the partial-clone flags, the per-repository path, no `Date.now`/`Math.random`, no
filesystem access, only documented runtime hooks, every `agent()` labelled, and the
never-execute rule still in two prompts.

