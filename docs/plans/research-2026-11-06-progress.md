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

---

## Phase 3 — The benchmark — DONE, in two batches, with its own faults named

**All six fixed repositories were read**: optopsy, RD-Agent, nautilus_trader, qlib (batch 1)
and gs-quant, ArcticDB (batch 2). 56 agents across the two runs.

*A correction, recorded because it was the human's and not mine.* The first run was launched
with **four** repositories, dropping gs-quant and ArcticDB as a scope reduction I had decided
on and had not yet written down. The human caught it before I recorded it. The second batch
was run with their leads from 3.2 and both syntheses merged into one, which is what should
have happened from the start — a reduction nobody has been told about is not a reduction, it
is an omission.

**Both runs hit a session limit mid-flight** — 12 agents lost in batch 1, 4 in batch 2 — and
both were **resumed from cache** rather than re-run, so 39 completed analyses replayed and
only the failed agents ran again.

### The benchmark's most important finding is about the benchmark

Both reports open by declaring that **this run's cross-check did not happen**, and they are
right. The merged report carries it at the top: **0 of 48 dimensions are twice-verified.**
Every claim is graded `[re-opened]` / `[record]` / `[one reader]`, and where the two reports
graded the same claim differently the **weaker** grade was kept.

That is the behaviour wanted. A report that had quietly presented single-reader claims as
cross-checked would have been worse than no report, and the thing that made the difference
was that the workflow asks for verdicts explicitly, so their absence was visible.

### Four faults in the workflow I wrote, all found by running it

| Fault | What it did | Fixed |
|---|---|---|
| `could_not_determine` dropped between stages | stage 2 rebuilt its payload from `claims` alone, so the closing "what could not be established" section **could only ever be empty** — and an empty section reads as "nothing was unclear" | carried through as `unknown` |
| truncation ordering | `claims` serialised **before** `checked`, and a 60,000-character slice cut the payload mid-way through the first repository — so the synthesis got 74 claims and no verdicts | verdicts and unknowns serialise first; the bulky part is what gets cut, and the prompt says to grade down if it looks truncated |
| the cross-check read only their code | a claim has two halves — `claim` about them, `better_than_ours` about **us** — and the verifier only ever opened the cited file in the clone. So "they do X, which we do not" could be confirmed with the second half unread | the verifier is now told to open the corresponding file **here** for any `better_than_ours` of `they` or `we`, and that a claim true about them and false about us is not `confirmed` |
| ArcticDB claims mislabelled by dimension | recorded, not fixed — it is in the merged report | — |

**The fourth fault is not hypothetical, and finding it is the single best thing this
benchmark produced.** An analyst reported that a named unexplained residual was a discipline
this project lacks. `backtest/accounting/attribution.py:47` already reads
`COMPONENTS = ("delta", "gamma", "vega", "theta", "residual")`, with a trust threshold at
`:36-45` the claim never mentioned, and `:57` counting the trades that cross it. **We were
ahead of what was being recommended to us**, and no cross-check as previously defined could
ever have caught it.

### Findings worth carrying into the discussion brief

- **RD-Agent's loop judges on its test period.** `conf_combined_factors.yaml:43-45,77-80`
  sets `backtest.start_time = test_start` while `segments.test = [2017-01-01, null]`; those
  metrics are `IMPORTANT_METRICS` in `developer/feedback.py:17-21`, which the model reads and
  answers with "Replace Best Result" / "New Hypothesis" (`:107-111`). The loop count **is**
  tracked (`loop.py:133-134`) and **nothing is deflated by it**. That is dimension 8's
  central question, answered, and it is the clearest available argument for what an honest
  automated loop here would have to do differently.
- **optopsy states its exit rule on one path and violates it on the other**:
  `simulator.py:731` sorts by `entry_date`, `:879-881` by `exit_date` with a comment about
  realising P&L correctly. It is the live proof of why this project holds two engines
  row-identical by a test.
- **15 "worth learning" entries survived** (of 20 candidates; 5 were cross-batch duplicates
  and one was the residual claim, reduced to its surviving half), ordered cheapest first:
  five afternoons, eight weeks, one month.
- **Licences:** optopsy AGPL-3.0 (do not borrow), nautilus_trader LGPL-3.0 (link, do not
  copy), qlib and RD-Agent MIT, gs-quant Apache-2.0, ArcticDB BUSL-1.1 (current tree not
  borrowable). And: **this platform has no LICENSE file of its own.**

**3.3 — the adoption notes are in the merged report** rather than added afterwards: each
"worth learning" entry carries its file, what we would change, a rough cost, and a plan entry
where one is defensible — six entries say "no plan entry directly" rather than inventing one.

---

## Phase 3B — The deepening loop — BLOCKED

**Not run.** Not because it was judged unnecessary — because the time it needs is gone.

The plan's own 0.1 says this phase should take most of a run, and this run is stage 5 of six
in a longer unattended job. A session limit was hit during Phase 3 and cost roughly two
hours, which both benchmark batches then had to be resumed through. Eight rounds of
pick → investigate → challenge → record, at three agents a round, could not be started with
the discussion brief and the handoff still owed.

**What a person needs to know.** The brief in Phase 6 is therefore written at
**benchmark depth, not deep-dive depth**, and says so in its own section 2. The questions the
loop would have taken are already named and ready — the plan's own examples are the right
ones, and the benchmark sharpened two of them:

1. How an honest automated research loop would sit on `research/holdout.py` and
   `research/tracker.py`, given that RD-Agent's judges on its test period and deflates
   nothing by its tracked loop count.
2. Which spot is authoritative when the vendor's chain is snapped 14 minutes before the
   close — and whether the missing guard is a guard or a configuration field.
3. The smallest multi-leg design that keeps `TradeRecord`'s guarantees.
4. What an MCP server for this platform should and must not expose.

## Phase 4 — The `/explore` debate — BLOCKED

Same reason. Its output feeds section 5 of the brief, so the brief derives its five
directions from the benchmark, the digest and `plan.md` instead, **and marks them as not
having been argued against by a sceptic**. That label is the point: a direction that has
survived no opposition is a preference, and the brief says so rather than presenting five
scored candidates as though they had been contested.

## Phase 5 — The revised research plan — BLOCKED

`docs/research/plan.md` is unchanged from its 2026-11-06 draft. What the benchmark would
change about it is in the brief's decisions instead, which is where the human will act on it
— but the plan itself still orders its entries by the reasoning it was written with, and a
reader should take the brief as the newer document where they disagree.

## Phase 6 — The discussion brief — DONE

`docs/research/discussion-2026-11-06.md`, 390 lines (the cap is 400), seven sections exactly
as 6.2 specifies. Written by `research-lead`, which is the agent whose standing instruction
is not to be agreeable, and it was not:

- It opens by saying the platform **has answered nothing** — `docs/findings/` holds two
  audits and zero research results, and all plan entries are unrun. Verified: the directory
  holds `audit-adf0495.md`, `baseline-change-2026-11-06.md` and a README.
- It says three gates hold everything, all three cheap and none needing development: the
  strategy thesis, the hold-out (**`plan.md:725` records it `NOT FIXED`** — verified), and
  whether real option data is bought.
- It **refuses the full automated research loop** and recommends only the honesty machinery
  behind it — a write-once hold-out receipt and a cross-session trial ledger — on the grounds
  that RD-Agent demonstrates the failure of the thing itself.
- It recommends **$199 and not $799**, because the cheap purchase is a test of the adapter
  path rather than a data purchase.
- It names the one place it thinks a **guard** is missing rather than a question unasked: the
  authoritative spot, when the chain is snapped before the close.
- It nominates its own weakest item for argument — that a deflated Sharpe is the wrong tool
  and an anytime-valid e-process the right one — saying plainly that it rests on one abstract
  read, cannot be tested because there is no return stream yet, and should be killed in the
  backlog if the project will in practice test each candidate once and stop.

It also carries both of the run's health warnings into the document the human will act from:
the benchmark's cross-check did not run, and the "name a residual" recommendation was false
about us.

**6.1's reviewer pass was run** — see below. **6.4's Chinese summary** is in the final message
of the session, not in any file, as 6.4 requires.

Verified: every checkable claim in the brief spot-checked against the code
(`attribution.py:47` has the residual; `plan.md:725` says `NOT FIXED`; `docs/findings/` holds
two audits); seven sections; 390 lines; `check_language` → `OK  291 files, all English`.

