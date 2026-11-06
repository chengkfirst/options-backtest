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

---

## Phase 2 — The radar digest, by hand — 2.1 DONE, 2.2 taken as the digest's own list

**2.1 — DONE.** `.claude/scheduled/scout.md` run once in this session; no scheduled task
created, no pull request. `docs/radar/2026-W45.md` (five items, with the `Dropped` section
and both handoff lists) and five lines appended to `docs/radar/seen.md`. Dropped items were
deliberately **not** appended, as the prompt requires, so a thing dropped for being early can
be reported later.

The two focus areas were covered, and the options-data one produced the most useful thing in
the digest. Summarised here because it changes what Phase 3 should look at and what the
discussion brief will have to ask:

- **The adapter is already aimed at a format that exists.** HistoricalData.net ships one row
  per contract per day with 34 columns, and five renames (`quote_date`, `underlying`,
  `expiration`, `type`, `underlying_close`) line it up with `SCHEMA`; `bid`, `ask`, `volume`,
  `open_interest`, `iv` and the greeks already match by name. The "three steps to bring your
  own data" design in `adapters.py` was not hypothetical.
- **The cheapest option is not the cheapest.** ORATS at $599 is deeper (2007) but puts a call
  and a put on **one row** with no `right` column, which `column_map` cannot express, and its
  underlying is reverse-engineered from put-call parity rather than the traded price the
  schema asks for — so moneyness computed from it is not the quantity a reader of the
  configuration thinks it is.
- **Nobody sells a historical `available_at`.** Every real backtest will therefore carry the
  adapter's "assumed equal to `asof`, and recorded as an assumption" flag. That vindicates the
  fallback design and means the flag belongs on screen rather than in a log.

**The finding worth the whole digest**, and it is not about cost: **both vendors that publish
a snapshot time take it before the close** — ORATS at 15:46 ET, 14 minutes early, with the
stated reason that option quote quality degrades into the close; Cboe ships both 15:45 and
true-close columns.

That bites directly on *T's close decides, T+1's close fills*, and **not in the conservative
direction**. The chain carries its own `underlying_price`, the spot at the moment of the
snapshot. If a regime signal is computed from T's close and the contract is then selected
from a chain priced fourteen minutes earlier, contract selection (2b) is picking a delta
against greeks and a spot that predate the information the decision used. Criterion 1.20
("signal and fill never share a quote") is satisfied; the **reverse** defect — a quote
strictly *earlier* than its signal — has no guard. On a day that moves in the last quarter
hour, the backtest selects and prices on a chain that no longer existed at the decision.

Recorded as an input to `/explore` by the digest, and it belongs in the discussion brief as a
decision: pick one spot as authoritative and check it, or carry both and make the
configuration say which. Cboe is the only vendor that could turn this into a sensitivity test
rather than an argument, which is part of what its price buys.

**2.2 — taken as the digest's own "candidates for /benchmark" list**, rather than a separate
selection. The reading, stated as 0.1 requires: the plan asks for at most four further
repositories chosen on the criterion "would teach us something on at least two of the eight
dimensions", and the digest's handoff list is exactly that judgement made by the agent that
had just read them. Duplicating the choice would have been a second, worse-informed pass.

Verified: `tools/check_language.py` → `OK  286 files, all English`.

