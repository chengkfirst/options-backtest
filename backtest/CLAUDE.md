# `backtest/` — the engine

[conventions.md](../conventions.md) is the rulebook; this is what applies here in
particular. Read the rulebook first — this file does not repeat it.

## The six that catch the most, here

**Two price sets, two time conventions.** Forcing either into one makes one of them wrong.
Raw prices and adjusted prices answer different questions, and so do the option chain's
calendar days (`dte`) and its trading days (`sessions_left`). A column carries its basis in
its name for the same reason: `sharpe` let an annualised figure be passed where a
per-period one was wanted, so it is `sharpe_annualised`.

**T's close decides, T+1's close fills.** The signal comes from the bar that has closed;
the order fills on the next session's close; the contract chosen is the execution day's,
while everything the decision reads is cut at the decision day. Three consequences are
already paid for and are **not** bugs to fix: a rule written "hold 20 days" holds 21; no
intraday stop can be modelled; and the gap through a stop routinely exceeds the distance
`risk_pct` divided by, so the position taken is larger than the budget intended. See
`conventions.md`, "The timing rule".

**`Maybe[T]`, and no bare `None`/`NaN`/`0`.** A delta read as 0 selects the wrong contract
and the run finishes normally, reporting a number. Absence is a value with a name
(`core/maybe.py`); a function that cannot answer refuses rather than approximating.

**Every threshold lives in `core/thresholds.py`**, in the architecture document's four
groups. A cut written inside a module turns a judgement ("a plateau") into something that
reads as a measurement, and then nobody can answer "what was decided for me". Every verdict
names the cut behind it.

**No hidden strategy default.** The platform never picks a strategy choice. A visible
starting value in the interface's form is fine — it is on screen, in the saved
configuration, and one edit away. What is forbidden is a value that fills itself in when a
field is *omitted* and then appears nowhere: `ExitRule` once supplied +100% / -50% to every
run that left them out. `tools/check_defaults.py` reads the dataclass field defaults, which
is where that one was hiding while a test checked `thresholds.py` and found its strategy
group correctly empty.

**A slot's logic never calls another slot.** Every influence passes through the trade
context and the action vocabulary. Direct calls are what make per-slot ablation impossible,
which is one of the two things this platform exists to do.

## Anything that changes the trades is in the run fingerprint

Two configurations sharing one `run_id` make a search look smaller than it was, so the
deflated result comes out better than it earned. The error flatters, which is the kind
nobody reports. `run_id` refuses to hash without the execution settings; a component's
`name` and `version` travel into it, and `register` refuses to re-register a name at the
same version with different code.

## `engine/loop.py` and `EventDrivenEngine.run`

**The hottest file and the hottest method in this project's history.** `run` is 573 lines
and holds the daily order, the exit precedence, the review hook, the risk brake, the
checkpoint and the shadow tracking. So: changes there are small, and they come with tests.
Splitting it is recommended work in its own right, before any parallel-engine work — not
something to do in passing while fixing something else.

One deviation from the architecture document is deliberate and recorded: the book is marked
to market at step 5, before the strategy runs, rather than last, and there is no
corporate-action step. Equivalent under the one-day lag; wrong the moment an intraday fill
lands on its signal's day. Criterion 1.32.

## What this directory may and may not do

- **May not** import upwards. `core` 0, `data` 1, `signals` 2, `portfolio` 3, `execution` 4,
  `engine`/`accounting` 5, `research` 6, `api` 7 — `LAYERS` in `tools/check_deps.py` is the
  definition, and an unregistered module fails outright rather than being skipped.
- **May not** let pricing escape the data layer. `selection`, `engine` and `research` read
  the chain's quote and greek columns and never `data.options.pricing`. That constraint is
  what lets the data source be swapped with no change above.
- **May not** edit `app/`. If a change alters anything the interface reads, write the
  `CONTRACT` expectation into the spec and tell the interface session; do not reach across.
- **Writes the failing test first.** For a bug, the test that reproduces it; for a feature,
  the test that would prove it. The seven components are defined in `core/slots.py` as
  `COMPONENTS`, and the interface's form, the gap list and `tools/status.py` are all
  generated from it — so a change there changes the interface without touching it.

## The synthetic chain is a fixture, not a data source

Constant IV, no skew, no term structure. **Option-side conclusions do not hold on it** —
which delta, where to take profit, anything about the surface. Conclusions about the
underlying can be trusted. A test that asserts an option-side number on it is asserting the
fixture's arithmetic.
