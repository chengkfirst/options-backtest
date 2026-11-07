# Audit of `adf0495` — the trailing stop and component state

**This is an audit, not a research result.** It is a finding about the code, not about the
market: no number here describes what any strategy earns, and the shape
`docs/findings/README.md` prescribes for a research entry (Data / Baselines / Result)
does not apply. The reporting format is `quant-auditor`'s: file, line, which check, how a
result would be biased, and the evidence.

**Commit:** `adf04958eac5cd65a1af6dec6df7596c171993d9`
**Date:** 2026-10-04
**Subject:** `feat: get_state / set_state, and the trailing stop that needed them`
**Audited at:** HEAD `c2f1d1f`. `backtest/signals/stop_loss/_trailing`, `_trailing_initial`
and the whole of `backtest/core/component.py` are byte-identical to the commit, so every
experiment below speaks to the code the commit introduced. Where HEAD has moved (the
checkpoint in `backtest/engine/runner.py`, the second stateful form `ratchet`) it is said.

## The six checks

| Check | Verdict |
|---|---|
| 1 · Timing | **Clean.** The mark is raised from day *T*'s close quote and the exit is an ordinary `Intent` with `execute_on=cal.shift(day, lag)` (`backtest/engine/loop.py:804`, `:820-831`), so it fills on *T+1*'s close like every other exit. No fill on the signal's bar. |
| 2 · Look-ahead | **Clean.** `_trailing` reads only `ctx["premium_ratio"]` (today's quote) and its own accumulated past (`backtest/signals/stop_loss/__init__.py:76-102`); the context's `view` is `DataView.at(day, ...)` (`loop.py:1371`). The state only ever accumulates values already seen, and the component is called from exactly one place (`loop.py:1422`) — no shadow or counterfactual path re-runs it over later prices. |
| 3 · Realism assumptions never optimised | **Clean.** The commit touches no λ (`adverse_fraction`), no latency, no cleaning rule. `fraction` is a strategy parameter, required with no default, so the platform defaults no strategy choice. |
| 4 · The statistical unit is the logical trade | **Clean.** State is keyed by `trade_id`, and `forget` is reached only after `open_trades.pop` on a full close (`loop.py:2105-2113`); `ADD`/`REDUCE` return before it, so a partial close keeps the trade's mark and is not counted as a new trade. No new statistic, and no conditional probability, is introduced. |
| 5 · Absence | **One minor defect, inside finding 2.** `_trailing` refuses to move or fire on a missing or non-finite ratio, which is right and is the conservative direction. `_trailing_initial` falls back to a bare `0.0`; that branch is unreachable from the engine today (the stop is only called when a quote exists, `loop.py:1347`), and the reachable half of the same line is finding 2. |
| 6 · Thresholds | **Clean.** No numeric cut is added. `Param("fraction", lo=0.0, hi=2.0)` is an input-validation bound on a researcher-supplied value that raises `StartupError` (`core/component.py:115-118`), the same shape the two existing stop forms already use; it is not a cut the engine applies to data. |

## Findings, in rank order

### 1 · A component's per-trade state outlives the run, and the next run of the same strategy object inherits it

```
backtest/core/component.py:210        _state, and no clearing of it at run start
backtest/engine/loop.py:2113          forget() is reached on one of the three close paths
```

**What.** `Bound._state` is a plain dict on the component object. `forget(trade_id)` is
called from exactly one place — the full-close branch of `_execute` (`loop.py:2109-2113`).
The other two ways a trade ends do not call it: `_settle` (`loop.py:2249-2252`, passive
expiry) and `_early_exercise` (`loop.py:2239`) both `open_trades.pop` and return a record
with the component's state left in place; so does a position still open when the range
ends, which the loop deliberately leaves unclosed. And `EventDrivenEngine.run` never
clears component state — it only ever *writes* it, when resuming from a checkpoint
(`loop.py:443-452`). Trade ids restart at 1 in every run (`loop.py:403`), so a second run
of the same `Strategy` object finds the leftovers waiting under ids it will reuse.

**Which check.** 4 and 5 in spirit — a stale high-water mark is a fact from another trade
read as this trade's — but mostly a kind the six checks name only by its consequence: two
runs of one configuration are not the same run.

**How the result would be biased.** Not in one direction: the contaminated trade's stop
fires *earlier* than it should when the inherited mark is above where this trade has been
(inheriting `high_water=1.03` makes a 0.5 trailing stop fire on the trade's first day),
and *later* when it is below. Nothing in the output says it happened: the trade log shows
an ordinary `stop_loss` exit at an ordinary date, and the `run_id` is unaffected because
the fingerprint hashes `ref` + params, not the object (`api/sdk.py:833-837`).

The exposed callers are the ones that re-run one strategy object several times in a
process, which is to say the comparison tools:

- `backtest/research/positioning.py:81-85` — up to five sizing methods, five runs.
- `backtest/api/sdk.py:1485-1499` (`delay_test`) and `:1521-1531` (`realism_test`) — the
  look-ahead decay test and the realism sensitivity test, three to four runs each. These
  two are the project's defences against exactly this class of error, and their later rows
  are the contaminated ones.
- `tests/test_event_driven.py:281-286` is the guard that would have caught it, and cannot:
  `build_strategy` (`tests/test_event_driven.py:36-45`) carries no components, so the
  determinism test runs on a strategy with no state.

`sweep` is not exposed: it uses `TradeTableEngine`, which refuses components outright
(`backtest/engine/vectorized.py:111-115`).

**Evidence.** Two runs, both read-only, on the synthetic chain.

*The leftovers exist.* After one ordinary run the component still holds two trades:

```python
r = run(symbol="QQQ", start="2021-01-01", end="2021-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 10, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
        components={"stop_loss": {"variant": "trailing", "params": {"fraction": 0.5}}})
r.raw.strategy.components["stop_loss"].get_state()
# {'32': {'high_water': 1.0345766441892619}, '33': {'high_water': 0.5650524910492143}}
```

(Both were open when the range ended. The same happens on the expiry and early-exercise
paths, which `forget` does not cover.)

*A carried mark changes a recorded trade.* Seeding one leftover entry — what a previous run
leaves behind — and re-running the same strategy object through a fresh engine:

```python
stop.set_state({"3": {"high_water": 0.30}})
again = EventDrivenEngine(r._market, r._chain, r._signals, r.raw.config).run(r.raw.strategy)
# fresh   trade 3: (2021-03-05, 'stop_loss', -365.54)
# carried trade 3: (2021-03-03, 'stop_loss', -281.36)
```

*And it reaches a research tool unaided.* `research.positioning.compare` run with the
**identical** sizing method three times, no seeding, on the result above:

| method | trades | total return |
|---|---|---|
| fixed first | 27 | -0.020348 |
| fixed again | **29** | **-0.021187** |
| fixed a third time | 27 | -0.020348 |

Three identical configurations, two different answers, reproduced on a rerun. A table whose
whole purpose is to attribute a difference to the sizing method reports a difference that
is not the sizing method.

This has already spread: `ratchet` (`backtest/signals/stop_loss/__init__.py:106-164`, added
in `bb3e61b`) uses the same machinery, so a carried `{"moved": True}` starts a trade with
its stop already moved.

### 2 · The high-water mark starts below entry, not at entry, on every trade

```
backtest/signals/stop_loss/__init__.py:60-73   _trailing_initial
```

**What.** `initial_state` is called lazily, on the component's *first call* for a trade
(`core/component.py:234-236`), and that first call is on the fill day, with
`premium_ratio = exit_price / entry_price - 1` computed from the **exit side** of the quote
(`loop.py:1349`). So the mark is initialised to minus the round-trip cost, never to entry.
The docstring states the opposite — "``premium_ratio`` is 0.0 at entry by construction, so
this is 0.0 in practice" — and the test that pins the rule,
`test_the_mark_starts_at_entry_and_not_at_zero` (`tests/test_component_state.py:63-72`),
feeds `0.0` by hand and so cannot see it.

**Which check.** 5 (absence and a default read as a real value, on the same line), and the
mechanism belongs to 1: the first observation available under the execution lag is not the
entry, and the form treats it as if it were.

**How the result would be biased.** The stop is **looser** than the rule it describes, by
the round-trip cost: it fires at `ratio <= high_water - fraction` rather than at
`ratio <= -fraction`. Direction: fewer and later stop exits. Nothing in the output reveals
it; the trade log shows the exit date, not the mark it was measured from.

The consequence that matters beyond the magnitude is that it falsifies the claim this very
commit substituted for the two pinned stop counts it deleted — "a give-back from a mark
that never falls is never smaller than a fall from entry, so the trailing stop must fire at
least as often — which cannot be an artefact of the data"
(`tests/test_component_state.py:214-240`). With the mark starting below entry it can fire
*less* often, and does: on the run above, at `fraction=0.5`, trade 24 is a `stop_loss` exit
under the `premium` form (2021-10-14) and a `time_exit` under `trailing` (2021-10-18). The
assertion still passes only because the data happens to give trailing more stops overall
(18 against 14) — which is the data-dependence the commit set out to remove.

**Evidence.** Instrumenting `Bound.state_for` from outside the repository to record the
mark each trade is created with, on the run in finding 1:

```
trades: 33
initial marks below entry (0.0): 33 of 33
min / max initial mark: -0.019814642072638722 / -0.019791286115920403
trade 24: first observed premium_ratio -0.019797 -> initial high_water -0.019797
```

The rule this breaks is the form's own docstring and the test named after it; the mechanism
is `conventions.md`, "The timing rule" — under the one-day lag the first price a decision
can see is not the entry price.

**On the synthetic chain.** The *magnitude* (-0.0198, i.e. about 2% of premium) is the
fixture's round-trip cost and is a statement about the fixture. The sign and the existence
are mechanical: on any chain with a spread, the exit-side ratio on the fill day is below
zero.

## What I could not determine, and what would settle it

- **How much finding 1 has already moved a published number.** Nothing records whether a
  given figure came from the first or a later run of a reused strategy object, and the
  `run_id` is identical either way. What would settle it for any particular figure: re-run
  it in a fresh process, one run per process, and compare. What would settle it in general:
  extend `tests/test_event_driven.py::TestDeterminism` to a strategy carrying a stateful
  component. Note that the two-run form only fails when the first run actually leaves
  something behind — with every trade closing by its stop or its holding cap it passes, as
  I measured — so the guard that always holds is the stronger one: at the end of a run, a
  stateful component holds state for no trade that has a record, which is the generalisation
  of `test_no_state_is_left_for_a_closed_trade` (`tests/test_component_state.py:246-256`)
  from closed-by-exit to closed-by-anything.
- **Whether finding 2 was intended as a research choice.** "Measure the give-back from the
  first price the trade could be sold at" is a defensible rule; it is simply not the rule
  the docstring, the parameter's description and the test state. Which of the two is wanted
  is the researcher's call, not mine.
- **The checkpoint interaction (HEAD only, out of this commit's scope).** Because `forget`
  misses the expiry path, `component_state` in a checkpoint (`loop.py:1115`) carries
  entries for trades that are already closed. They are restored on resume
  (`loop.py:443-452`) and are harmless while ids are not reused inside one run, but the
  snapshot grows with the expiry count rather than with the open-trade count — the opposite
  of what `forget`'s docstring promises.

## Noted, and not findings

- At `adf0495` nothing called `get_state` / `set_state`: `backtest/engine/runner.py` did not
  exist yet, and `loop.py` had no snapshot. The pair was built for a caller that arrived
  later, which is visible rather than hidden, and biases nothing. HEAD calls both.
- `Bound` stopped being `frozen`, so it is now unhashable and its `__eq__` includes
  accumulated state. No code path hashes or compares a `Bound` (the fingerprint uses `ref`
  and params, and `_state` is `repr=False`), so this neither crashes nor biases anything
  today.
- The three consequences of the one-day execution lag — a "hold 20 days" rule holding 21,
  no intraday stop, the gap through a stop exceeding the distance `risk_pct` divided by —
  are paid for in `conventions.md` and are not findings. A trailing stop tested on the close
  and filled on the next close is the convention working as written.
