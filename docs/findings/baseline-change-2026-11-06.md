# What changed in the results when the two engine defects were fixed

**This is an audit of a change to the engine, not a research result.** No number here says
what a strategy earns. It says what moved, in which trades, and why each move follows from
the fix — which is what a person needs in order to approve, or refuse, a change to the
recorded results.

**Date:** 2026-11-06 · **Branch:** `fix/engine-state` · **Commit:** `c59fc61`
**Measured on:** the test suite's own offline fixture, hermetic and reproducible, in three
states — `before` (neither fix), `fix1` (state only), `after` (both).

---

## The headline, and it is not what was expected

**The regression baseline did not move, and `tools.baseline update` was therefore not run.**

That is not reassurance. It is the most important finding in this document:

> `tools/baseline.py:109` builds its trades with `TradeTableEngine`, and
> `backtest/engine/vectorized.py:111-115` **refuses a strategy with components outright**.
> So the baseline configuration has no components, has never had one, and **no change to
> any component can ever move it.**

The one guard whose stated job is "an unintended change in results" is structurally blind
to this entire class of change. It was checked row for row anyway — 230 trades, net
−11,822.29, identical in all three states — but that agreement carries no information.

A person should decide whether the baseline should grow a second configuration that uses
the event-driven engine with a stateful component. Until it does, a fix like this one
passes the results guard by not being visible to it.

---

## Fix 1 — state outliving its trade

### What it changes within a run: nothing

Row for row identical between `before` and `fix1`, in every configuration measured:

| configuration | trades | net |
|---|---|---|
| calls, trailing 0.5 | 136 | −5,398.79 |
| calls, premium 0.5 (control) | 136 | unchanged |
| puts, trailing 0.5 | 135 | −15,817.60 |
| puts, `dte_floor` 2, trailing 0.9 | 129 | −30,972.55 |
| the baseline configuration | 230 | −11,822.29 |

This follows from the defect's shape: within one run, trade ids are never reused, so the
missed `forget` could not bias that run's own trades. It only ever leaked **forward**.

### What it changes between runs: the thing that made results unreproducible

| | before | after |
|---|---|---|
| one strategy object run 3× — trades | 135 / **136** / 135 | 135 / 135 / 135 |
| one strategy object run 3× — net | −15,817.60 / **−15,512.19** / −15,817.60 | all −15,817.60 |
| `positioning.compare`, identical method 3× | 113 / **114** / 113 | 113 / 113 / 113 |
| trades holding state after they had a record | **20** | 0 |
| marks in a checkpoint taken with 0 open trades | **15** | 0 |

**The one divergent trade, named.** Run 1 ends with trade 136 still open, holding
`high_water = 0.817`. Run 2 reuses id 136, inherits that mark, and a 0.5 give-back fires on
its first session — producing a 136th record, `2023-12-18 → 2023-12-19, +305.41`, that run 1
never had. **No trade shared by both runs differed.** The second run was not a worse
measurement of the same thing; it was a measurement of a different thing, and `run_id` was
identical for both.

This reproduces, on the fixture, the 27 / 29 / 27 the original audit measured.

---

## Fix 2 — the high-water mark starting below entry

### The mechanism, in one line

The mark now starts 0.0198 higher (at the entry rather than at the fill day's exit-side
ratio), so `high_water − ratio ≥ fraction` is reached at a ratio 0.0198 higher — **never
later, sometimes earlier.** The direction is forced by the arithmetic, not observed.

### Every trade that moved, and how

| configuration | trades | moved | net change | what moved |
|---|---|---|---|---|
| calls, trailing 0.5 | 136 | **5** | +59.34 (3 better, 2 worse) | stop → stop, 1–3 sessions earlier |
| puts, trailing 0.5 | 135 | **4** | −406.44 (1 better, 3 worse) | 2 × `time_exit → stop_loss`, 2 earlier stops |
| puts, `dte_floor` 2, trailing 0.9 | 129 | **8** | +331.66 (7 better, 1 worse) | 3 × `expiry_otm → stop_loss`, 5 earlier stops |
| calls, **premium** 0.5 (control) | 136 | **0** | 0 | untouched, as it must be |

**Every single moved trade exited earlier, and every one by a stop.** Holding days fell in
all of them. Two examples with the whole story in the holding period:

- trade 12, entered 2022-03-09: `time_exit 2022-03-24, +18.32, 11 days` →
  `stop_loss 2022-03-15, −292.19, 4 days`
- trade 82, entered 2023-02-17: `expiry_otm 2023-03-24, −728.81, 24 days` →
  `stop_loss 2023-03-09, −639.82, 13 days`

**P&L direction is mixed** — 11 better, 6 worse across the three configurations. That is
what tightening a stop by a fixed amount does, and a change that improved every trade would
be the suspicious one.

### The claim this makes true

The commit that introduced the trailing stop rested on an argument it never tested: *a
give-back from a mark that never falls is never smaller than a fall from entry, so the
trailing stop must fire at least as often as the fixed one.* Comparing the two forms at the
same fraction, trade by trade on identical entries:

```
before: 136 trades compared; trailing exits LATER than the premium stop on 5
after:  136 trades compared; trailing exits LATER than the premium stop on 0
```

Those 5 are exactly the 5 trades that moved. The claim was false on 5 of 136 trades and is
now true on all of them.

### One trade-count change, explained

`positioning.compare` goes 113 → 114 under fix 2 because it runs with `max_concurrent=5`:
an earlier exit frees a slot and one previously blocked entry gets in. Verified directly —
the same configuration uncapped gives 135 either way.

---

## Why the two fixes are separable, checked rather than assumed

A worktree holding **only** fix 2 was built, and fix 1's three tests still failed there
(3 failed, 19 passed). Neither fix masks the other's test, so each one's evidence stands on
its own.

---

## What a person still has to decide

1. **Should the baseline see component changes at all?** Today it cannot. Adding an
   event-driven configuration with a stateful component to `tools/baseline.py` would close
   the blindness described at the top — and would itself be a baseline change.
2. **`stop_loss/trailing` is now version 2.** A saved configuration naming `@1` will refuse
   to replay, with a message saying so. There is no such file in this repository
   (`out/baseline/manifest.json` carries no component), but one may exist outside it.
3. **`register`'s version guard reads only `decide`.** `backtest/core/component.py:314`
   compares `_source(existing.decide)`, so changing a form's `initial_state` while keeping
   its version raises nothing. This defect *was* an `initial_state`, and nothing would have
   forced the bump that was made by hand. One bug, one change — it was not fixed here.

## Caveats on everything above

- Measured on the **synthetic chain**, whose IV is constant with no skew or term structure.
  These are statements about **which trades the engine produces**, which the fixture
  supports. They are **not** statements about what any strategy earns, which it does not.
- The configurations measured are the suite's, chosen because they reach all three close
  paths. A different configuration will move a different number of trades; the *direction*
  (earlier, by a stop, never later) follows from the arithmetic and does not depend on them.
- Guard state at the time of measurement: all 18 fast guards green, full suite 1499 passed.
