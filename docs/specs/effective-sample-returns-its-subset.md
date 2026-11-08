# `effective_sample` returns its subset, not only its count

**Status:** draft
**Approved:** not yet

## Goal

`backtest.research.events.effective_sample(episodes, months)` answers "how many of these
episodes do not overlap" with an integer. Every interval on an event study's conditional
probability needs the answer to the next question — **which** episodes those are — because
the bootstrap has to resample the non-overlapping subset, not the full overlapping set. The
full set's interval is the one the module itself calls "too narrow"
(`research/validation/intervals.bootstrap_trades`, `overlapping=True`).

Today the subset is unreachable through the public API, so an analyst who wants that interval
re-implements the greedy selection outside the platform. That happened in
`docs/findings/correction-edge-window-2.1-restated.md`: nine lines of re-implemented greedy
loop, asserted against `effective_sample`'s count to prove they agreed. The assertion passed,
which is luck rather than a guarantee — the rule exists in two places now, and the copy is in
a scratch script. If `effective_sample` ever changes its span convention, nothing fails; the
finding just quietly reports an interval on a different subset than the count it is printed
beside.

What becomes possible: an interval on a conditional event-study probability, computed on the
non-overlapping observations, **through the public API alone** — so the figure and the sample
it rests on come from one definition rather than two.

## Non-goals

- **Not a new statistic.** The greedy rule, the `months × 30` calendar span and the counts it
  produces stay exactly as they are. This exposes what the existing function already computes
  and throws away. Any change to the count is out of scope and would be a different spec.
- **Not a change to `study()`'s `table`.** The `effective` column stays an integer. A table
  cell is not where a list of timestamps belongs.
- **Not an interval helper.** This does not wrap `bootstrap_trades`, does not choose a level,
  and does not decide whether `overlapping=False` is appropriate. Those are the analyst's, and
  deciding them inside the platform would hide the choice that matters.
- **Not a second overlap convention.** `study()` measures horizons in trading days
  (`DAYS_PER_MONTH = 21`) while `effective_sample` measures overlap in calendar days
  (`months × 30`). That mismatch is real and is noted in the finding above, but reconciling it
  changes counts and is therefore a separate decision, not a side effect of this one.

## Acceptance criteria

1. **The subset is reachable.** A public call returns the non-overlapping episodes themselves
   — `Episode` objects or their `crossed` timestamps — for a given `months`. If an analyst
   still has to write a selection loop to get them, this is not done.
2. **It is the same selection the count counts.** For every `(episodes, months)` the length of
   the returned subset equals `effective_sample(episodes, months)`, and its members are drawn
   from the input in time order. If the two can ever disagree, this is not done.
3. **The rule exists once.** `effective_sample` is derived from the subset rather than
   repeating the loop, so the greedy rule has exactly one implementation in the package. If
   `grep` finds two greedy loops over episode spans under `backtest/`, this is not done.
4. **`study()` surfaces it.** `study()`'s returned dict carries the subset per horizon beside
   the existing `excluded_by_data_end` — so the analyst who has a `study()` result already does
   not need the episode list and a second call. The `table`'s `effective` column is unchanged.
   If the subset can only be obtained by re-deriving it from `study()["episodes"]`, this is not
   done.
5. **Empty and degenerate inputs behave as the count does.** No episodes returns an empty
   subset, not a raise; one episode returns that one. If the subset raises anywhere
   `effective_sample` returns 0, this is not done.
6. **The horizon mismatch is stated where it is read.** The docstring of whatever returns the
   subset says the span is `months × 30` **calendar** days while a horizon is `21 × months`
   **trading** days, so the two are not the same window. This is the one documentation
   criterion, and it is here because the finding above had to discover it by reading the
   source. If a caller can read the subset without being told, this is not done.

## The tests that prove them

| # | Test | File | kind | layer |
|---|---|---|---|---|
| 1, 2 | `test_effective_subset_matches_the_count` — on a hand-built episode list with known overlaps, and on real QQQ episodes at 1/3/6/12 months: `len(subset) == effective_sample(...)` and the members are in time order | `tests/test_research_events.py` | unit | research |
| 2 | `test_effective_subset_members_are_input_episodes` — every returned member is identical to an input element, nothing synthesised | `tests/test_research_events.py` | unit | research |
| 3 | A plan/structure check that the greedy loop appears once under `backtest/` | `tests/plan_components.py` (needle-based, as the existing rows there are) | structure | research |
| 4 | `test_study_carries_the_non_overlapping_subset` — `study()`'s dict holds the subset for each requested horizon, and the `table`'s `effective` column is byte-identical to today's | `tests/test_research_events.py` | unit | research |
| 4 | A baseline/regression check that `study()`'s `table` is unchanged on the fixture, so this is additive | the existing events study regression, whichever file holds it | regression | research |
| 5 | `test_effective_subset_on_no_episodes` and `..._on_one_episode` | `tests/test_research_events.py` | unit | research |
| 6 | **No test.** A docstring sentence cannot be falsified by a test. It is a review item for `reviewer`, named here so it is not mistaken for an untested criterion that was forgotten | — | — | — |

The exact file and function names above are suggestions where the repository does not already
fix them; a builder may place them where the existing event-study tests live. Criterion 3's
mechanism is a suggestion too — if `tools/check_plan.py` is the better home, use it.

## What it changes that the interface reads

**Nothing.** No `app/` module reads `events.study` today, and this spec adds no response field
and renames none. `CONTRACT` (`app/static/js/contract.js`) needs no entry. Stated rather than
omitted, so it is on record as considered.

## Risks and what would make us stop

- **The subset is not unique, and exposing one makes it look canonical.** Greedy-in-time-order
  is one maximal non-overlapping set; others of the same size exist, and a different tie-break
  would pick different episodes. The *count* is stable; the *membership* is a convention. An
  interval computed on the greedy subset is therefore one of several defensible intervals, and
  if that is not said plainly at the point of use, this change makes a convention look like a
  fact. **This is the main risk and the reason criterion 6 exists.**
- **It invites an interval the sample cannot support.** Making the subset one call away makes
  `bootstrap_trades(subset, overlapping=False)` easy, and at 15 non-overlapping observations
  (QQQ's 12-month figure) `bootstrap_min_observations` = 10 permits an interval that should
  probably be read as fifteen stories. The platform already refuses below ten; this does not
  change that floor and must not be read as endorsing anything above it.
- **Scope creep into the two-convention problem.** Criterion 6 documents the trading-day vs
  calendar-day mismatch instead of fixing it. If a reviewer decides documenting it is
  insufficient, **stop and write the second spec** rather than changing `effective_sample`'s
  span inside this one: that would move published counts, including the 21 and 22 in
  `docs/findings/correction-edge-window-2.1-restated.md`, and a baseline move is the owner's
  decision per `CLAUDE.md`.
- **What would make this the wrong thing to have built:** if the honest reading of a 22-episode
  event study is "report the episodes, not a statistic with a range", then the subset is not
  needed because the interval should not be computed at all. That is a defensible position. It
  is not the position `research/validation/intervals.py` takes — it builds intervals above ten
  observations and states their limits — but if the owner takes it, this spec should be closed
  rather than built.
