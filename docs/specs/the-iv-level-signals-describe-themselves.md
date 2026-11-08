# The IV-level signals describe themselves

**Status:** draft
**Approved:** not yet

## Goal

`describe_signals()` is where a signal's meaning, unit, window and caveat travel with its
name — for a researcher picking a signal, and for the interface, which lists signals from
exactly this table. **The five signals in `registry.VOL_SIGNALS` — `iv`, `iv_rank_252`,
`iv_pct_252`, `iv_rank_63`, `iv_rv_spread` — have no entry in `SIGNAL_META`**, so
`describe_signals(("iv_rank_252",))` returns `family="custom"`, `measures="custom (no
description registered)"`, `unit="—"`, `window=None`, `caveat=""`. These are the only
signals computed from the volatility proxy rather than from prices, which makes them the
group whose reading conventions are least guessable and the group that describes itself
least.

The caveat that actually decides how to read `iv_rank_252` lives in `indicators.py`'s
docstring — *"IV rank sees only the two ends of the range, so one extreme spike depresses it
for a long time, while IV percentile asks how many days were below today"* — and it does not
travel to anyone reading the signal table. `docs/findings/entry-3-does-iv-rank-252-separate-the-forward-return.md`
depended on that property twice: it is why the signal's distribution is skewed low (median
0.242 on QQQ 2001-2026) and why a fixed-band quintile cut puts 2,655 sessions in the bottom
bucket and 244 in the top. An analyst who read only `describe_signals` would have called
those buckets a surprise instead of a known property of the indicator.

What becomes possible: choosing between `iv_rank_252` and `iv_pct_252` — which are different
statistics that answer "dear or cheap" differently — from the table the platform publishes,
rather than from the indicator module's source.

**One documentation defect comes with it, for whoever approves this.** `registry.py`'s
`SIGNAL_META` comment says *"A new signal must be registered here too, or `describe()`
raises outright — a signal nobody can state the meaning of is worse than not having the
signal."* It does not raise: `describe_signals` calls `SIGNAL_META.get(name)` and labels the
result "custom". So the rule the comment states has been unenforced for all five of these
signals. Either the comment or the behaviour is wrong, and which one is a decision, not a
cleanup — see Risks.

## Non-goals

- **Not a change to any signal's arithmetic.** `rank_in_window`, `percentile_in_window`,
  `realized_vol` and the lambdas in `VOL_SIGNALS` compute exactly what they compute today.
  A change to a value is out of scope and would be a different spec with a baseline
  conversation attached.
- **Not a new signal**, and not a rename of an existing one. `iv_rank_63` stays
  `iv_rank_63`.
- **Not a decision about whether `describe_signals` should raise on an unregistered name.**
  That is the Risks section's question and belongs to the human. This spec is satisfied by
  the five signals having metadata, whichever way that question goes.
- **Not metadata for a user's custom signal.** "custom (no description registered)" is the
  right answer for a lambda someone passed to `extra=`; it is the wrong answer for a signal
  the platform ships.
- **Not a change to `SignalMeta`'s fields.** `family`, `measures`, `unit`, `window`,
  `caveat` are enough; `window=None` is already meaningful ("there is none") and is the
  right value for `iv`.

## Acceptance criteria

1. **Every shipped signal has metadata.** For every name in `DEFAULT_SIGNALS` **and** every
   name in `VOL_SIGNALS`, `SIGNAL_META` has an entry. If `grep` can find a shipped signal
   whose `describe_signals` row says "custom", this is not done.
2. **The rows are not placeholders.** Each of the five carries a `unit` that states the
   range or the quantity (`iv_rank_252`: "0–1, where 1 is the top of the trailing range"),
   a `window` equal to the look-back the signal actually uses (252, 252, 63, 20 for
   `iv_rv_spread`'s realised leg, `None` for `iv`), and a non-empty `caveat`. A row with an
   empty `caveat` on a signal whose indicator docstring states a reading trap is not done.
3. **`iv_rank_252` and `iv_pct_252` are distinguishable from the table alone.** Their
   `measures` and `caveat` together say that the rank sees only the range's two ends and is
   depressed for up to a year by one spike, while the percentile counts days below and is
   not. If a reader of the table alone would still have to open `indicators.py` to choose
   between them, this is not done.
4. **The family is not "custom".** These five are a group — the registry's own comment calls
   them "IV level signals" and says why the group matters. Whether the family string is
   `"volatility"` or a new `"iv"` is the builder's call, but it is not `"custom"`, and
   `describe_signals`'s grouping order handles whatever is chosen without an unordered row
   falling to the end by accident.
5. **The warm-up is stated where it bites.** `rank_in_window`'s `min_periods` is
   `window // 2`, so `iv_rank_252` has values from 126 observations onward and is a
   126-to-251-session rank until the full window fills — six months of QQQ history in
   practice. The `caveat` says so, in the same way `sma_200`'s says "unavailable for the
   first 200 sessions, which is not the same as 0". If the table lets a reader believe every
   `iv_rank_252` value is a 252-session rank, this is not done.
6. **A signal with no metadata is still visible, not silent.** Whatever happens for an
   unregistered *custom* name, it is the same before and after this change unless the human
   decides otherwise under Risks. If this spec's implementation changes the behaviour for a
   user-supplied signal as a side effect, this is not done.

## The tests that prove them

- **Criterion 1** — `tests/` unit, layer 4: parametrise over
  `set(DEFAULT_SIGNALS) | set(VOL_SIGNALS)` and assert each name is in `SIGNAL_META`. This
  is the one test that keeps the gap from reopening when the next signal is added, which is
  how it opened.
- **Criteria 2 and 5** — unit, layer 4: for each of the five, assert `unit` is non-empty,
  `caveat` is non-empty, and `window` equals the integer the lambda passes to its indicator
  (read from one place, not retyped in the test).
- **Criterion 3** — unit, layer 4: assert the two rows' `caveat` strings differ and that
  each mentions its own mechanism. A string test is weak; it is honest about being a check
  that someone wrote something specific, not that what they wrote is true.
- **Criterion 4** — unit, layer 4: assert no shipped signal's row has `family == "custom"`,
  and that `describe_signals()` over all shipped names returns one row per name with no
  `NaN` in `family` or `measures`.
- **Criterion 6** — unit, layer 4: `describe_signals(("not_a_signal",))` behaves as it does
  today (one row, labelled custom) unless the human has decided otherwise; the test names
  the decision it encodes.

## What it changes that the interface reads

**Nothing new, and that is the point.** The interface already reads the
`describe_signals()` table; `app/static/js/contract.js` declares its fields and they do not
change. What changes is the *content* of five rows that currently read "custom (no
description registered)". A reviewer should still look at the signal list in
`app/static/states.html`, because five rows moving out of the "custom" group changes the
grouping order the table is sorted by — `describe_signals` sorts on
`{"price": 0, "trend": 1, "momentum": 2, "volatility": 3, "custom": 9}` and then on
`window`, so a new family string with no entry in that map falls to 9 and the IV signals
would sit under a "custom" heading while claiming not to be custom.

## Risks and what would make us stop

- **The real defect might be the missing raise, not the missing metadata.** If the human
  decides `describe_signals` should raise on an unregistered shipped signal — which is what
  the comment claims it does — then this spec is the smaller half of that change and should
  be merged with it rather than built first. **That decision is the human's and this spec
  does not take it.**
- **Metadata is prose, and prose can be wrong.** A `caveat` is unenforceable: nothing checks
  that what it says is true of the computation, and a wrong caveat is worse than none because
  it travels with authority. The mitigation is to copy the indicator docstrings rather than
  paraphrase them, and to say in the `measures` field which indicator computes the value.
- **What would make this the wrong thing to have built:** if the interface is going to stop
  reading `describe_signals` in favour of per-signal documentation pages, five metadata rows
  are a dead end. Nothing in `docs/` suggests that today, and the table has three readers
  (the interface, the notebook, `SignalSet.require`'s error message), so the risk is low and
  stated rather than assumed away.
- **It is small, and small is the risk.** This is an hour of work and no number moves, which
  makes it the kind of change that gets bundled into an unrelated commit. It should be its
  own commit, with the message saying that five shipped signals had no self-description and
  that a finding depended on a property none of them stated.
