# An interval on the difference between two samples

**Status:** draft
**Approved:** not yet

## Goal

Every comparison in `docs/research/plan.md` Part A is a **difference**: entry 2's best
bucket minus worst bucket, entry 3's lowest quintile minus highest, entry 4's retraced group
minus not-retraced group. Each entry's abandonment criterion is stated on that difference —
entry 3's "a gap under 1.5%", entry 4's "the 90% interval on the difference spanning more
than 20 percentage points".

`backtest.research.validation.intervals` offers **one-sample** bootstraps only.
`bootstrap_trades` resamples one array of values; `block_bootstrap` resamples one daily
series. Neither can keep two samples paired inside a draw, which is what an interval on a
difference requires. So the three findings that needed one each wrote their own:

| finding | what it had to write | what it called it |
|---|---|---|
| `entry-2-does-any-regime-dimension-separate-the-forward-return.md` | block resample of the session positions with **both bucket means recomputed inside every draw** | "construction C" |
| `entry-3-does-iv-rank-252-separate-the-forward-return.md` | the same, plus a 50-draw paired null for the gap | "construction C" |
| `entry-4-fallen-far-and-iv-off-its-peak.md` | a paired two-group resample of a binary outcome | "the paired difference bootstrap" |

Three analysts, three scratch implementations of one statistic, and the name "construction
C" appears in two findings meaning the same thing by coincidence rather than by reference.
**What becomes possible: a criterion stated on a difference can be evaluated through the
public API, so two findings quoting "the 90% interval on the difference" are quoting the
same computation.** Today they are not, and nothing would say so.

The second thing it unlocks is the refusal. `bootstrap_trades` refuses below
`bootstrap_min_observations` (10) and says why, in a sentence an analyst can quote. A
hand-rolled difference bootstrap refuses nothing: entry 4's group A held **one** episode,
resampling it returned the same value in all 2,000 draws, and the "90% interval" that came
out — `[+0.00, +21.05]` — looked like a result. That finding had to *demonstrate* the
degeneracy (counting the distinct values the one-episode side took across draws) to show
the number was empty. **A platform function would have refused, and the demonstration would
not have been needed.**

## Non-goals

- **Not a hypothesis test.** No p-value, no null-hypothesis machinery, no "significant"
  anywhere. This returns an `Interval` on a difference, exactly as the two existing
  functions return one on a level. Whether the interval crossing zero settles anything is
  the analyst's reading and `Interval.crosses_zero` already says it.
- **Not a choice of which construction is right.** The module's existing doctrine is that
  `bootstrap_trades` is too narrow when observations overlap and `block_bootstrap` is the
  honest one for a dependent series. That stays the caller's decision, declared in the call
  and reported beside the number — entry 1 was abandoned partly because its criterion named
  a level and not a construction, and burying the choice inside a new function would
  reproduce that defect at a deeper level.
- **Not stratified or matched sampling.** Year-matched draws are
  `research.baselines.random_entry(like=...)` and stay there.
- **Not a change to the existing two functions' output, signatures or defaults.** Purely
  additive. Any change to a published interval is a baseline move and the owner's decision.
- **Not a multi-sample or ANOVA-style comparison.** Two samples. Entry 2 compares five
  buckets but its criterion is on the best-minus-worst pair, which is two.
- **Not the `effective_sample` subset problem.** That is
  `docs/specs/effective-sample-returns-its-subset.md` and is a prerequisite for using this
  on the non-overlapping subset of an event study, not part of it.

## Acceptance criteria

1. **A difference of two independent samples gets an interval through one public call.**
   Given two arrays and a statistic, a public function in
   `backtest.research.validation.intervals` returns an `Interval` whose `estimate` is
   `statistic(a) - statistic(b)` and whose bounds come from resampling **both** arrays
   inside each draw. If an analyst still has to write a resampling loop to get it, this is
   not done.
2. **It refuses a sample too small to resample, the way the existing functions do.** With
   either array shorter than `bootstrap_min_observations` it raises, and the message names
   **which side** is short and its length. If it returns an interval on a one-element
   sample, this is not done — that is the exact failure entry 4 had to detect by hand.
3. **A degenerate draw is impossible to report by accident.** The returned `Interval` states
   how many distinct values each side's statistic took across the draws, or the function
   refuses when either side takes fewer than two. One of those two, not neither. If a caller
   can obtain an interval whose width comes entirely from one side without being told, this
   is not done.
4. **The paired form exists and is distinct from the independent form.** When the two
   samples are two labellings of **one** index — entries 2 and 3's case, where every session
   belongs to exactly one bucket and the buckets partition a shared timeline — the draw must
   resample the **index** and recompute both statistics on it, not resample the two arrays
   separately. Both forms are reachable, and the caller chooses; the docstring says which
   question each answers. If only one exists, this is not done.
5. **The block form exists for a dependent series.** The paired form accepts a `block`
   length and resamples contiguous blocks of the shared index, with
   `block_bootstrap_min_blocks` enforced as `block_bootstrap` enforces it. Without this the
   function cannot express the construction entries 2 and 3 actually used, and they would go
   on hand-rolling it. If the only available draw is i.i.d., this is not done.
6. **The construction travels with the number.** `Interval.name` or `caveat` records which
   form produced it (independent / paired / paired-block, and the block length), so a
   finding that prints `Interval.verdict()` cannot print an interval without its
   construction. If two different constructions can produce identical output text, this is
   not done.
7. **It reproduces the three findings' published numbers.** On entry 3's equal-count
   quintile gap the paired-block form returns **−0.444 pt, 90% [−1.804, +0.992]** at
   `block=21`, 2,000 draws, seed 20260917; on entry 2's term-structure gap it returns
   **+3.04 pt, 90% [+1.30, +4.80]**. If either disagrees, then either the scratch
   implementations were wrong or this one is, and **the discrepancy must be resolved before
   this is called done** — it is the only criterion here that can detect an error in work
   already published.
8. **Nothing existing moves.** `bootstrap_trades` and `block_bootstrap` return byte-identical
   results on the existing fixtures. If any published interval changes, this is not done and
   is a baseline question for the owner.

## The tests that prove them

| # | Test | File | kind | layer |
|---|---|---|---|---|
| 1 | `test_difference_interval_estimate_is_the_difference` — on two constructed arrays with a known gap, `estimate == statistic(a) - statistic(b)` exactly | `tests/test_research_intervals.py` | unit | research |
| 1 | `test_difference_interval_brackets_a_known_gap` — two normal samples with a planted gap; the 90% interval contains it on a fixed seed | `tests/test_research_intervals.py` | unit | research |
| 2 | `test_difference_interval_refuses_a_short_side` — 20 against 1, and 1 against 20; both raise, and the message names the short side and its length | `tests/test_research_intervals.py` | unit | research |
| 3 | `test_difference_interval_reports_distinct_draw_values` — a side with one distinct value either refuses or is reported as such; asserted on the exact shape entry 4 hit | `tests/test_research_intervals.py` | unit | research |
| 4 | `test_paired_form_differs_from_independent_on_a_shared_index` — the same two buckets of one timeline give different widths under the two forms, and the paired one is wider | `tests/test_research_intervals.py` | unit | research |
| 5 | `test_paired_block_enforces_the_minimum_blocks` — fewer than `block_bootstrap_min_blocks` whole blocks raises, with `block_bootstrap`'s wording | `tests/test_research_intervals.py` | unit | research |
| 6 | `test_verdict_names_the_construction` — the two forms' `verdict()` strings differ, and the block length appears in the block form's | `tests/test_research_intervals.py` | unit | research |
| 7 | `test_reproduces_the_published_entry_2_and_entry_3_gaps` — on cached QQQ/VXN/VIX3M data, to the published decimals | `tests/test_research_intervals.py` | regression | research |
| 8 | The existing interval tests, unchanged, plus the guard suite's own baseline comparison | existing files | regression | research |

Criterion 7's test needs the cached cross-market data and may have to be marked slow or
skipped where the cache is absent; if it is skipped in CI, say so at the skip rather than
letting the criterion look tested.

## What it changes that the interface reads

**Nothing.** No `app/` module reads `research.validation.intervals` today, no response field
is added and none is renamed, so `CONTRACT` (`app/static/js/contract.js`) needs no entry.
Stated rather than omitted, so it is on record as considered.

## Risks and what would make us stop

- **It makes a comparison easy that the sample usually cannot support, which is the main
  risk.** Every Part A entry that used a hand-rolled difference was **abandoned**: entry 3
  on a gap of −0.44 pt, entry 4 on a group of one. A one-call difference interval invites
  the comparison without the arithmetic that made its weakness visible, and criteria 2 and 3
  exist for exactly that reason. If a reviewer thinks they are insufficient, add a floor on
  the **effective** sample rather than the nominal one — which needs
  `docs/specs/effective-sample-returns-its-subset.md` first.
- **The paired form's correctness depends on the two samples really partitioning one
  index**, which the function cannot check. Handed two unrelated arrays with equal lengths
  it will happily pair them by position and return a confident, meaningless interval. The
  docstring must say so; a test cannot.
- **Criterion 7 could fail, and that is a finding rather than a bug in this spec.** If the
  new implementation disagrees with a published figure, one of them is wrong and **three
  existing findings may need a correction**. That is the most valuable thing this spec can
  produce and the reason criterion 7 is not optional. It also means the work must not be
  shipped by changing the criterion to match whatever came out.
- **Scope creep towards a test battery.** A difference interval is one step from a
  permutation test, which is plan entries 10 and 11 and has an unresolved design question
  (how the null is constructed) that the plan says must be settled by a person first. This
  spec must not become that; if it starts to, stop and write the permutation spec separately.
- **What would make this the wrong thing to have built:** if the project's position is that a
  comparison of two event-study cells should be reported as the observations and never as a
  statistic with a range — which is `research/events.py`'s own doctrine at small samples, and
  which entry 4 ended up following — then the right fix is not a new interval function but a
  convention that criteria are never written on a difference's interval. That is a research
  method decision (group 4), it is the owner's, and if it is taken this spec should be closed
  rather than built.
