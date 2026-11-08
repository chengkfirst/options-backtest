# `regime.stratify` reports its unit and its effective sample

**Status:** draft
**Approved:** not yet

## Goal

`docs/research/plan.md` § 5 entry 2 prescribes stratifying **the underlying's forward
21-session return** — not a strategy's P&L — with
`backtest.signals.setup.regime.stratify(trades, regimes, by=...)`. The function does the
grouping correctly, and its numbers were checked against an independent computation on all 24
cells of `docs/findings/entry-2-does-any-regime-dimension-separate-the-forward-return.md` and
agreed to within 1e-12. But its **vocabulary belongs to a different experiment**, and two of
the things entry 2's pre-registered criterion is read against are not in its output at all:

- The input must be a frame with columns `entry_date` and `net_pnl`, so a forward **return**
  is passed in a column named for a **profit and loss**.
- The output columns are `trades`, `win rate`, `mean per trade`, `median`, `total`,
  `worst trade`, `quote gaps`, `deferred exits`, `sample`. In a session stratification
  `trades` is a **session count**, `mean per trade` is a **mean return**, `total` is the sum
  of returns and has **no meaning at all**, and `quote gaps` / `deferred exits` are `NaN`
  because there was no execution. A table lifted out of a run record reads as a trade study
  and there is nothing in it that says it is not one.
- The `sample` verdict comes from `min_trades` (default 15), which is a trade-count rule. Entry
  2's floor is **200 sessions**, so the label is only meaningful if the caller happens to pass
  its own floor — and even then it labels the **nominal** count.
- **The nominal count is the wrong one to label.** Forward 21-session returns on consecutive
  sessions overlap by 20 sessions. The bucket that decided entry 2 holds **299 sessions and 54
  non-overlapping 21-session windows** — the pre-registered "at least 200 sessions" floor was
  met on a number that overstates the independent sample about five-fold, and the function that
  reports the count cannot report the other one. The platform does have the greedy non-overlap
  rule (`backtest.research.events.effective_sample`), but it is typed to `Episode`, so the
  finding above had to **fabricate `Episode` objects** with `price=1.0`, `ref_high=1.0`,
  `drawdown=0.0` to reach it, and also re-implement the rule in session units because
  `effective_sample`'s span is `months × 30` **calendar** days while the horizon here is 21
  **trading** days.

What becomes possible: a stratification whose output states the unit it counted and the
**effective** (non-overlapping) sample behind each bucket, so a pre-registered floor can be
read against the number that carries the information rather than the number that is five times
larger — **through the public API alone**, with the non-overlap rule living in one place
instead of three.

## Non-goals

- **Not a new statistic, and not a changed one.** Every number `stratify` returns today keeps
  its value. This adds columns and a unit label; a caller who reads `trades` and
  `mean per trade` gets exactly what it gets now.
- **Not a rename of the input columns.** `entry_date` and `net_pnl` stay accepted as they are.
  Renaming them would break every existing caller and `app/`'s trade frames for the sake of a
  label; an optional alternative accessor is in scope, a rename is not.
- **Not an interval, and not a floor.** This does not bootstrap anything, does not choose a
  confidence level, and does not refuse a bucket for being too small. `intervals.py`'s
  `bootstrap_min_observations` (10) and `block_bootstrap_min_blocks` (3) stay where they are
  and keep their current meaning. Deciding whether 54 non-overlapping windows support a claim
  is the analyst's, and deciding it inside the platform would hide the choice that matters.
- **Not the calendar-day / trading-day reconciliation.** `effective_sample`'s span is
  `months × 30` calendar days. This spec does not change it, because changing it moves
  published counts (the 21 and 22 in
  `docs/findings/correction-edge-window-2.1-restated.md`), and a baseline move is the owner's
  decision. The session-unit non-overlap this spec needs is a **second, explicit** horizon
  argument in sessions, not a redefinition of the first.
- **Not a merge with `docs/specs/effective-sample-returns-its-subset.md`.** That spec exposes
  *which* episodes the existing count counts. This one asks for a non-overlap count over an
  arbitrary set of **sessions** at a horizon measured in **sessions**. They touch the same
  greedy rule and should share one implementation (criterion 4 below), but either can be built
  without the other and neither subsumes it.
- **Not a change to `DEFAULT_DIMENSIONS` or any threshold.** Those are declared unfitted and
  are out of scope entirely.

## Acceptance criteria

1. **The output says what it counted.** `stratify`'s returned frame carries the unit of its
   rows — `"trade"` or `"session"` — somewhere a reader of the frame alone cannot miss it
   (a column, or the count column named for the unit). If a table copied out of the frame can
   still be mistaken for a trade study when the input was sessions, this is not done.
2. **A caller can say the unit, and the meaningless columns go away when it does.** A public
   way to declare that the rows are sessions carrying a return rather than trades carrying a
   P&L. When it is declared: `total` is **absent** rather than present and meaningless, and
   `quote gaps` / `deferred exits` are absent rather than `NaN`. If a session stratification
   still returns a `total`, this is not done.
3. **The effective sample is reported per bucket.** With a horizon given in **sessions**, each
   row carries the size of the largest non-overlapping subset of that bucket's rows at that
   horizon, computed from the session index of the data passed in. For the entry-2 case
   (`by="volatility term structure"`, 2011-01-03 to 2026-10-08, horizon 21) the inverted row
   reports **299** sessions and **54** effective. If the effective figure can only be obtained
   by writing a selection loop outside the platform, this is not done.
4. **The greedy rule exists once.** The non-overlap selection is one implementation under
   `backtest/`, shared with `backtest.research.events.effective_sample` — which keeps its
   calendar-day span and its `Episode` signature unchanged. If `grep` finds two greedy
   non-overlap loops under `backtest/`, this is not done.
5. **No fabricated domain object is needed to reach it.** The effective count for a set of
   sessions is reachable without constructing an `Episode` (whose `price`, `ref_high`,
   `drawdown` and `ended` fields have no meaning for a regime bucket). If an analyst still has
   to pass `price=1.0, drawdown=0.0` to get a count, this is not done.
6. **The sample verdict reads against the effective count, and says which it used.** When a
   horizon is given, `sample`'s "adequate / too small" verdict is computed on the **effective**
   figure and its text names which count it judged. A floor met on 299 sessions and failed on
   54 non-overlapping windows must not read as "adequate" with no qualification. If the verdict
   is silent about which number it judged, this is not done.
7. **Nothing already built changes its output.** Called exactly as it is called today — no
   unit, no horizon — `stratify` returns a frame byte-identical to today's, including the
   `total` column and the `min_trades` default of 15. If any existing caller's output moves,
   this is not done.
8. **The overlap caveat travels with the number.** The docstring of whatever reports the
   effective count states that the horizon is in **trading sessions**, that it is a different
   convention from `effective_sample`'s `months × 30` calendar days, and that the greedy subset
   is one of several maximal sets of the same size — so its *membership* is a convention even
   where its *size* is stable.

## The tests that prove them

| # | Test | File | kind | layer |
|---|---|---|---|---|
| 1, 2 | `test_stratify_session_unit_drops_the_pnl_only_columns` — a session stratification carries the unit and has no `total`, `quote gaps` or `deferred exits`; a trade stratification still has all three | `tests/test_signals_regime.py` | unit | signals |
| 3 | `test_stratify_reports_the_effective_sample` — on a hand-built frame of consecutive sessions with a known overlap structure, each bucket's effective figure equals the greedy count computed by hand; and on the real QQQ term-structure bucket, 299 sessions → 54 effective at horizon 21 | `tests/test_signals_regime.py` | unit | signals |
| 3 | `test_effective_equals_sessions_when_the_horizon_is_one` — horizon 1 makes effective equal the nominal count, the degenerate case that catches an off-by-one | `tests/test_signals_regime.py` | unit | signals |
| 4 | A structure check that the greedy non-overlap loop appears once under `backtest/` | `tests/plan_components.py` (needle-based, as its existing rows are) | structure | research |
| 5 | `test_session_effective_needs_no_episode` — the public call takes a session index and a horizon and nothing else | `tests/test_research_events.py` | unit | research |
| 6 | `test_sample_verdict_names_the_count_it_judged` — a bucket above the floor nominally and below it effectively does not read "adequate" unqualified | `tests/test_signals_regime.py` | unit | signals |
| 7 | `test_stratify_default_call_is_unchanged` — the existing fixture trade frame produces today's columns and today's values, `total` included | `tests/test_signals_regime.py` | regression | signals |
| 7 | The existing regime/stratify regression, whichever file holds it, left passing unmodified | the existing suite | regression | signals |
| 8 | **No test.** A docstring sentence cannot be falsified by a test; it is a review item for `reviewer`, named here so it is not mistaken for a forgotten criterion | — | — | — |

File and function names are suggestions where the repository does not already fix them; a
builder may place them where the existing regime and event-study tests live.

## What it changes that the interface reads

**Nothing.** No `app/` module reads `signals.setup.regime.stratify` today, and criterion 7
requires the default call's output to be byte-identical, so no response field is added,
removed or renamed. `CONTRACT` (`app/static/js/contract.js`) needs no entry. Stated rather
than omitted, so it is on record as considered.

## Risks and what would make us stop

- **It makes a pre-registered floor look like it was always meant in effective units.** It was
  not: entry 2's floor says "200 sessions" and was met on 299 nominal. Reporting the effective
  count beside it is honest; **retro-fitting the criterion to it is not**, and if this change
  is used to re-score an already-run entry without a new plan entry and a new date, it has
  done harm. The finding that motivated it says so, and so does this spec.
- **A second overlap convention in the package.** After this there are two horizons with the
  same name and different units — `months × 30` calendar days in `effective_sample`, sessions
  here. Criterion 8 documents it rather than fixing it, exactly as
  `docs/specs/effective-sample-returns-its-subset.md` criterion 6 does. **If a reviewer judges
  documenting it insufficient, stop and write the reconciliation spec** rather than changing
  either span inside this one.
- **The greedy subset is a convention, not a fact.** Greedy-in-time-order is one maximal
  non-overlapping set; others of the same size exist and a different tie-break picks different
  members. The size is stable; which sessions they are is not. Reporting a single number makes
  a convention look canonical, which is why criterion 8 exists.
- **Scope creep into a general stratifier.** The obvious next step is to let `stratify` take
  any series on any index and group it by anything, which is a different and much larger
  function. Everything above is additive to the existing signature; if the work starts moving
  toward a general grouping utility, it has left this spec.
- **What would make this the wrong thing to have built:** if the project's answer to "299
  overlapping sessions" is that a stratification of overlapping forward returns should not be
  reported as a bucket mean at all — that the unit of analysis must be the non-overlapping
  observation from the start, so the nominal count never appears — then this spec is decorating
  a statistic that should not exist, and the right change is to the plan's stratification
  entries rather than to `stratify`. That is a defensible position and it is the owner's; this
  spec should be closed rather than built if it is taken.
