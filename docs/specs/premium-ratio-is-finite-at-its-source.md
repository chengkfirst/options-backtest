# `premium_ratio` is guarded where it is computed, not in every consumer

**Status:** awaiting owner approval
**Approved:** not yet
**Raised by:** `engine-dev`, while fixing a `quant-auditor` finding on `engine/gaps` item 2b,
2026-11-08. **The finding is not about the change it was found in** — it is about the engine
underneath it, and was deliberately not fixed there under the one-bug-one-change rule.

## Goal

`backtest/engine/loop.py:1358-1359` computes

```python
exit_price = quote[0] if pos.quantity > 0 else quote[1]
ratio = (exit_price / trade.entry_price - 1.0) * (1 if pos.quantity > 0 else -1)
```

from a raw `quote[0]` / `quote[1]`, **with no finiteness guard**, and puts the result into
`exit_context` as `premium_ratio`. So a NaN bid puts NaN into **every slot's context** for
that session.

**The shipped forms are safe, and that is the problem.** Each guards itself:
`backtest/signals/stop_loss/__init__.py` has `_premium` and `_underlying` both testing
`math.isfinite`, `_trailing` returning `False` on a non-finite ratio, and
`_trailing_initial` falling back to `0.0`. So the invariant is held **once per consumer**
rather than once at the source, and any new form that forgets the check inherits a NaN
silently.

**What a forgotten check costs, measured.** This was found because version 1 of
`stop_loss/trailing` had the fallback and a test replica of it did not. With
`high_water = NaN`, `ratio > state["high_water"]` is always `False` and
`(nan - ratio) >= fraction` is always `False` — so **the stop silently never fires again for
that trade's whole life**, and the trade runs to a time exit or expiry with nothing raising
and nothing in the trade log saying a stop had stopped working. A backtest reports a number;
the number is wrong in the direction of fewer and later stop exits, which is the flattering
direction.

## Non-goals

- **Not a change to any form's rule.** `_premium`, `_underlying`, `_trailing` and `_ratchet`
  keep their behaviour exactly. If this spec changes any recorded number it is wrong, and
  that is one of its acceptance criteria.
- **Not a data-cleaning change.** Where a NaN quote comes from, and whether it should be
  cleaned earlier, is a separate question about the chain and its adapters. This is about
  one computation not propagating one.
- **Not a new realism assumption.** Nothing here decides what to do about a missing quote in
  economic terms; the engine already has an answer (the mark does not move and the stop does
  not fire, documented in `_trailing`'s comment as "treating a missing quote as a fall would
  exit on a data gap, which is the commonest way a trailing stop in a backtest beats one in
  an account"). This makes that answer structural instead of repeated.
- **Not a sweep of every other context field.** `underlying_move` and the greeks may have
  the same shape; if they do, that is a finding for this spec's own review, and each is
  decided on its own rather than by a blanket rule.

## Acceptance criteria, each falsifiable

1. **A non-finite `premium_ratio` never reaches a slot's context.** Either it is absent, or
   it is the documented fallback — one of those two, decided here and not per consumer.
   **Test:** a session whose quote carries a NaN bid, asserting what every slot receives.
   **Falsified by:** any slot seeing `nan`.

2. **No recorded number moves.** Both baseline configurations compare clean, unchanged:
   `tests/baselines/baseline_trades.csv` at
   `sha256 ba4b37021fcd22910dfff02c5307d0c719a016b51a639118a239d01ea1222064` and
   `baseline_event_driven_trades.csv` at `sha256 3ce65dae7e33baabb4a8bf198fe551d5e1e6d1891de3de50da3371c6070f3289`.
   **Test:** `python -m tools.baseline check`. **Falsified by:** either moving, which would
   mean the guard changed behaviour rather than centralising it.

3. **A form that omits its own check is still correct.** The point of the change: write a
   form with no `isfinite` test and show it behaves as the documented fallback rather than
   dying or going permanently inert. **Test:** a deliberately unguarded test-only form on a
   NaN-bid session. **Falsified by:** that form going inert, which is today's behaviour.

4. **The sensitivity demonstration still holds.** `engine/gaps` item 2b's finding rests on
   12 of 228 trades moving under version 1's mark, ids
   `[16, 42, 49, 126, 147, 159, 165, 186, 188, 190, 191, 227]`, version-1 net −13,767.71.
   **Test:** `tests/test_baseline_event_driven.py`. **Falsified by:** any of those changing.

5. **The per-consumer checks are either removed or justified.** If they stay after the source
   is guarded, each one's comment says why it is still needed — otherwise the invariant is
   now held twice, which is the defect this project's conventions name as one fact in two
   places. **Falsified by:** a redundant check left with no reason beside it.

## The guards that would go red

- **`baseline`**, on both configurations, if any recorded trade moves — criterion 2 run in
  reverse.
- **`tools/check_mutations.py`**, if the new guard is a mechanism with no entry. The rule in
  that file is that entries are added when a mechanism is built, not afterwards.
- **`components`**, if a form's registration or version changes as a side effect.
- **`tools/check_defaults.py`**, if the fallback is written as a hidden default rather than a
  declared one.
- **`test suite`**, on `tests/test_component_state.py` and
  `tests/test_baseline_event_driven.py`.

## Open questions, for the owner rather than for an interview

Nobody was awake to interview when this was written, so these are listed rather than asked.

1. **Absent, or a fallback value?** Making `premium_ratio` absent from the context when it
   cannot be computed is the more honest shape — `ctx.get("premium_ratio")` returning `None`
   already means "no quote today" to every form. Substituting `0.0` at the source would make
   a missing quote look like an unchanged premium, which is a number where there is none.
   **This spec does not decide it**, and it is the decision the whole change turns on.
2. **Does the same hole exist for `underlying_move` and for the greeks?** `spot` is already
   `or_else(float("nan"))` one line below, which is an explicit NaN rather than an accidental
   one — so the answer is probably yes and probably deliberate. Worth one pass.
3. **Is a non-finite quote a condition that should refuse the run rather than be absorbed?**
   The project's usual answer is that silent absorption is the failure; but a single bad bid
   in a long backtest is not obviously a startup error, and refusing mid-run has no mechanism
   today.
