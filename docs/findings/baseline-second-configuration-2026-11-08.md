# What can the second baseline configuration see that the first cannot?

**This is an audit of a change to the guard, not a research result.** No number here says
what a strategy earns. It says what the regression baseline can and cannot detect.

**Date:** 2026-11-08 · **Branch:** `engine/gaps` · **Answers:**
`docs/plans/handoff-2026-11-06.md` decision 1, and the question left open at the top of
`docs/findings/baseline-change-2026-11-06.md`.

**Verdict:** the new configuration moves on **12 of its 228 trades** under the exact
component defect that moved the old file **not at all**. The old file is byte-identical.

## Data

The test suite's own offline fixture — `tests/conftest.py`: seeded GBM bars, `SEED`
20260917, the four dividends in `DIVIDENDS`, QQQ, 2022-06-01 to 2023-12-31. **The option
chain is the synthetic one** — constant IV, no skew, no term structure. So nothing here is
a claim about which delta or which give-back is right; every statement is about **which
trades the engine produces**, which the fixture supports. No network, no vendor history.

## Method

Two recorded configurations, differing in the form of one slot and the engine that can run
it:

| | `trade_table` (existing) | `event_driven` (new) |
|---|---|---|
| engine | `TradeTableEngine` | `EventDrivenEngine` |
| slot 4 | `exit.stop_loss = 0.5` | `components={"stop_loss": trailing(fraction=0.6)}` |
| entry / select / time exits | `sma_20 > sma_50`, call, dte 30 in (21, 45), delta 0.30, `hold_days=10`, `take_profit=1.0`, `dte_floor=5` | identical |
| file | `tests/baselines/baseline_trades.csv` | `tests/baselines/baseline_event_driven_trades.csv` |
| trades · net | 230 · −11,822.29 | 228 · −13,014.71 |
| closes | 149 stop / 57 tp / 24 time | 165 stop / 51 tp / 12 time |

Guard state at the time of measurement: **all 18 fast guards green; full suite 1570
passed.** The two files are not a comparison of two strategies and must not be read as one.

## Baselines

The comparison is against the old configuration in the same three-state sense the earlier
finding used. The defect replayed is `stop_loss/trailing` **version 1**, whose high-water
mark started at the fill day's exit-side ratio (0.0198 below the entry) rather than at the
entry — one of the two defects fixed on `fix/engine-state`, and the one the old baseline
was blind to.

## Result

**Under version 1's mark, 12 of the 228 trades move** (23 differing rows, because the log
is ordered by exit date and one earlier exit shifts every row after it):

| | recorded (version 2) | under version 1 |
|---|---|---|
| net, whole file | −13,014.71 | −13,767.71 |
| net, the 12 that move | −3,680.38 | −4,433.38 |
| mean holding days, whole file | 6.29 | 6.43 |

Every one of the 12 exits **earlier** and **by a stop**; holding days fall in all 12; one
`time_exit` becomes a `stop_loss`; P&L direction is **mixed — 9 better, 3 worse.** That
reproduces, on this configuration, exactly the shape the earlier finding measured on the
suite's own: the direction is forced by the arithmetic (a mark 0.0198 higher reaches the
give-back at a ratio 0.0198 higher — never later), and the P&L is not.

Two examples:

- trade 49, entered 2022-10-17: `time_exit 2022-11-01, −52.63, 11 days` →
  `stop_loss 2022-10-21, −140.81, 4 days`
- trade 16, entered 2022-06-28: `stop_loss 2022-07-12, −398.69, 9 days` →
  `stop_loss 2022-07-01, −230.43, 3 days`

**The old file moves by 0.** It cannot be made to: `TradeTableEngine._guard`
(`backtest/engine/vectorized.py:108-116`) raises on a strategy with components, so the
configuration that would show the difference is not expressible on that engine at all.
`tests/test_baseline_event_driven.py::TestTheFirstConfigurationIsStructurallyBlind` asserts
that refusal as a `limit`, so when the gap closes the test fails and says to delete it.

Demonstrated rather than asserted, three ways:

1. version 1's mark moves the file (above);
2. **which way round** it moved — no trade exits later in the recorded file than under
   version 1, which is the direction the arithmetic forces, so the file cannot quietly be
   recording version 1's answer;
3. the component fires — 165 of 228 closes (72%), with a 20% floor asserted.

Each was verified in reverse: the fault planted, the test confirmed red, the fault removed,
the test confirmed green.

## Caveats

- **The synthetic chain.** These are statements about which trades the engine produces, not
  about what any strategy earns.
- **The other defect from `fix/engine-state` is still invisible, and always will be to a
  baseline.** Fix 1 — state outliving its trade — changed no trade *within* a run; it
  leaked only **forward**, into a second run of the same strategy object. A baseline runs
  once, so no recorded file can see it. That class is held by
  `tests/test_component_state.py::TestStateDoesNotCrossRuns`, which was proved red
  pre-fix. The new configuration closes the *within-run* half of the blindness, not the
  cross-run half.
- **Two close paths are not reached.** `dte_floor=5` keeps every contract away from expiry,
  so `expiry_otm` and `early_exercise` appear in neither file. Both are covered by
  `tests/test_component_state.py` on its own configuration.
- **The fraction was chosen, and the choice is a search.** Five were measured (0.2, 0.3,
  0.4, 0.5, 0.6) and 0.6 picked as the most sensitive of them with the time exits still
  reachable. It is a strategy parameter, not a realism assumption — but a reader should
  know it was selected against this criterion, and the criterion was *sensitivity to a
  component change*, not performance.
- **12 trades, not 23 rows.** Counted by `trade_id`. The comparator itself is positional,
  which is right for a baseline — any difference is a difference — but a count of what
  moved must be a count of logical trades or it overstates itself by a factor of two.

## What it changes

`python -m tools.baseline check` now compares both and names the one that moved. `update`
writes exactly one, named on the command line. **Recording this new configuration was the
only baseline change**; `tests/baselines/baseline_trades.csv` is byte-identical,
`sha256 ba4b37021fcd22910dfff02c5307d0c719a016b51a639118a239d01ea1222064` before and after,
and `git status` reports it neither modified nor staged.

Still open, and still a person's to decide: whether the baseline should also run a
strategy object twice, which is the only way a recorded file could see cross-run state
leakage.
