# Is there a region where "fallen far, and IV off its peak" resolves the tension?

**Date:** 2026-11-08
**Answers:** `docs/research/plan.md` § 5, **Entry 4** (hypothesis, test and abandonment
criterion all dated 2026-11-06, before any number existed)
**Verdict:** **The two groups cannot be told apart, because one of them holds a single
episode — and the reason is structural, not a shortage of history.** On QQQ's 10%
correction episodes from entry 1, measured at the crossing date as entry 4's test
prescribes, VXN had retraced **≥ 20% from its within-episode maximum in 1 of the 20
classifiable episodes** (2008-10-30), against 19 that had not. The split is therefore
**1 against 19**, and **limb 1 of the pre-registered criterion — either group below 8
episodes — fires on every range and both price bases.** Limb 2 fires too, on the only
construction that can be computed at all: the span of the 90% interval on the difference is
**21.05 pt**, above the 20-point limit, and that construction is **degenerate** (the
one-episode side contributes no width; the platform **refuses** the legitimate interval at
1 observation against a floor of 10). **Entry 4 is abandoned, decided by limb 1.** The
plan said "expect the latter"; the former fired, and harder. The point estimate of the
difference is **+10.53 pt** in the hypothesis's direction and so nominally clears its +5 pt
clause — **and it is one episode**: had that episode's six months gone the other way, the
difference would be **−89.47 pt**. The structural fact behind all of it: **the day QQQ
first closes 10% below a rolling high is, by construction, close to the day VXN is at its
episode high** — on **45%** of crossing days VXN's episode-window peak *is* the crossing
day. Taking the one window that has an any-day analogue: VXN sits ≥ 20% off its
trailing-252-session peak on **89.9%** of ordinary sessions but on only **55.0%** of
crossing days, and on the episode window the crossing-day figure is **5.0%** — so the
conditional is far *below* the unconditional either way, and the hypothesis's two halves
are near-disjoint **at this measurement date**. Answering the
question as asked needs a measurement date that moves with VXN, which is a different
question and a new plan entry.

---

## Data

- **Source.** QQQ daily OHLC from `backtest.data.ingest.fetch_ohlcv` through
  `backtest.data.store.ParquetCache`, assembled by `backtest.data.underlying.build_bars`.
  VXN from `backtest.data.multi_asset.load_cross_market` (`^VXN`, same cache, same source,
  already scaled by 0.01). `CrossMarket.unavailable` is **empty**: no series was missing
  and nothing was dropped.
- **Symbol: QQQ only. No SPY run of any kind.**
  `docs/decisions/0001-the-hold-out-part-b-and-the-option-data.md` § 1 reserves SPY until
  the final check, and that reservation is held by the document alone today (`HoldOut`
  takes dates, not symbols). Entry 4's test names QQQ only, so **nothing here is
  unevaluated for want of SPY.**
- **No data after 2026-11-07.** The fetch range ends 2026-11-07, the last QQQ bar in it is
  2026-11-06, and
  `research.holdout.check(HoldOut(2026-11-08, 2099-12-31), start=1999-03-10, end=2026-11-06)`
  returns `{'touched': False, 'note': 'the run ends before the hold-out … begins'}`.
  **No `unlock=` was needed or passed.**
- **Option chain: none.** Not the synthetic one, not any other. No option was priced
  anywhere in this finding and `run()` was never called. **What this finding therefore
  cannot claim:** nothing here supports any option-side conclusion — not which delta, not
  which expiry, not where a take-profit or a stop belongs, and in particular **not that
  buying a call after a correction once IV has come off its peak is cheaper, better or
  worse than buying it at the peak.** That is the option-P&L question, it is plan entry 20,
  and it is blocked: the synthetic chain prices every option off the same VXN series this
  split is computed from, so a Black-Scholes price on a retraced-VXN day is cheaper **by
  construction** and a confirming number is guaranteed by arithmetic before any market
  behaviour enters. The step from "QQQ was higher six months later" to "the option made
  money" is **not taken anywhere in this file and is not supported by it.**
- **Ranges.** Entry 4 inherits entry 1's episode set, so it inherits entry 1's ranges.
  **Primary: 1999-03-10 → 2024-12-31**, the range entry 1 pre-registered. The **extended**
  range to the last bar (**2026-11-06**) is reported beside it for every figure, exactly as
  entry 1 did, and neither was chosen after the fact.
- **Two price bases.** `UnderlyingBars.adjusted()` is documented "for signals and returns",
  so **adjusted is primary** — prescribed by the module — and **`traded` is reported beside
  it on every cell**, following entries 1 to 3. **No conclusion differs between the bases.**
  VXN is not price data and does not change with the basis; only the episode dates (very
  slightly) and the forward returns do.

### Which of entry 1's numbers this conditions on, and what the VXN requirement costs

Entry 4 conditions on **entry 1's 10% threshold, 6-month horizon cell, primary range,
adjusted basis**: `docs/findings/entry-1-the-six-month-window-after-a-correction.md`
reports **27 episodes, 26 with a complete 6-month follow-up, 19 non-overlapping, up 84.62%
against an unconditional 72.26%, excess +12.35 pt.** Those exact figures reproduce here.

**The split cannot be computed on all 26.** VXN begins **2001-01-23** and the first six
10% episodes are **1999-06-14, 1999-08-04, 2000-01-06, 2000-01-28, 2000-03-15 and
2000-04-03** — every one of them before VXN exists. So **6 of the 26 are unclassifiable**,
and the split is computed on **20**.

That loss is **not a random subsample**, and it flatters every number below:

| cell | n | effective | 6m up | baseline up | excess |
|---|---|---|---|---|---|
| entry 1 as published | 26 | 19 | 84.62% | 72.26% | **+12.35 pt** |
| restricted to the VXN-classifiable | **20** | **17** | **90.00%** | 72.26% | **+17.74 pt** |
| the 6 dropped for want of VXN | 6 | — | 66.67% | 72.26% | −5.59 pt |

**Requiring VXN removes the dot-com crash — which is where "fallen far" was most severely
tested — and moves the excess +5.38 pt before any split is applied.** Both of entry 4's
groups inherit that. The 90% interval on the restricted cell's rate is
[0.80, 1.00] (`bootstrap_trades`, `overlapping=True`, the module's own "too narrow"
caveat attached).

## Method

**No `run()` call, and that is not a workaround.** The plan says so itself: *"conditioning
the measurement date on a second series is not a parameter `research/events.py` takes, so
this is a short analysis script over `study()["episodes"]` and `study()["outcomes"]` rather
than a platform call. That is analysis, not a new capability — no spec."* **No spec was
written for that gap.** Nothing here reaches inside an engine: every number comes from
`data.ingest`, `data.store`, `data.underlying`, `data.multi_asset`, `research.events`,
`research.validation.intervals`, `research.baselines`, `research.holdout`,
`research.tracker`, `signals.registry` and `core.thresholds`, all public.

```python
from datetime import date
import numpy as np, pandas as pd
from backtest.data.ingest import fetch_ohlcv
from backtest.data.multi_asset import load_cross_market
from backtest.data.store import CacheKey, ParquetCache
from backtest.data.underlying import build_bars
from backtest.research.events import effective_sample, study
from backtest.research.validation.intervals import block_bootstrap, bootstrap_trades

START, END = date(1999, 3, 10), date(2026, 11, 7)
THRESHOLD, RETRACE, HORIZON = 0.10, 0.20, 6        # all three are the plan's
cache = ParquetCache()
key = CacheKey("ohlcv", "QQQ", {"start": START, "end": END,
                                "source": "yfinance", "prices": "raw+adj"})
bars = build_bars("QQQ", cache.get_or_load(key, lambda: fetch_ohlcv("QQQ", START, END)))
vxn = load_cross_market(START, END, cache=cache).frame["vxn"].dropna()

g = bars.adjusted                                   # PRIMARY basis
frame = pd.DataFrame({"close": g("close"), "high": g("high"),
                      "low": g("low")}).loc[:pd.Timestamp(2024, 12, 31)]   # PRIMARY range

s = study(frame, THRESHOLD, horizons_months=(1, 3, 6, 12))
six = s["outcomes"]; six = six[six["horizon_months"] == HORIZON].set_index("crossed")
base6 = float(s["baseline"].set_index("horizon_months").loc[HORIZON, "up"])

for ep in s["episodes"]:                            # reading R1, the primary
    win = vxn.loc[(vxn.index >= ep.ref_high_date) & (vxn.index <= ep.crossed)]
    at = vxn.reindex([ep.crossed]).iloc[0]
    retrace = (win.max() - at) / win.max()          # nan when VXN does not reach back
    # group A if retrace >= RETRACE, group B otherwise; outcome = six.loc[ep.crossed]
# then bars.traded, then the full range, then readings R2/R3/R4. Nothing else was varied.
```

**Real OHLC, not a close series.** Every `study()` call returned `intraday = True` and the
`basis` note *"events detected on the close; further falls and the deepest drawdown
measured on the intraday low"*. `reference_window=252` and `reset_fraction=0.5` are the
module's defaults and were not moved. `DAYS_PER_MONTH = 21`, so "6 months" is 126 sessions.

### The one ambiguity in the entry's own words, declared as a set rather than chosen

Entry 4 says *"VXN at the measurement date has retraced 20% from its **within-episode
maximum**"*. The measurement date is the episode's crossing date — that is where
`events.measure()` starts every outcome, and entry 4 does not move it. But "within-episode
maximum" has more than one reading, and `Episode` carries three dates
(`ref_high_date`, `crossed`, `ended`). All four readings below were written into the
script's header **before any split was computed**, and **all four are reported**:

| reading | VXN peak taken over | admissible? |
|---|---|---|
| **R1 — primary** | `[ref_high_date, crossed]` — the fall into the correction | **yes**: every date is at or before the measurement date |
| R2 | `[crossed, ended]` — the episode proper | **no — look-ahead**: the window extends past the measurement date |
| R3 | `[ref_high_date, ended]` — the whole stretch | **no — look-ahead**, same cause |
| R4 | the 252 sessions before `crossed` | causal, but **not the entry's words** — a trailing window is not "within-episode" |

**R1 is primary and it is not a choice**: it is the only one of the four that is both
knowable at the measurement date and able to be non-zero. The strictly literal fifth
reading — the maximum over `[crossed, crossed]`, i.e. the episode proper *as known at the
measurement date* — is **VXN at the crossing date itself**, so the retracement is
identically **0** for every episode, group A is **empty**, and the test is **vacuous**.
That is arithmetic and needed no run. R2 and R3 return **identical** numbers on every
episode, because VXN's maximum over any window reaching past the crossing date is always
after it.

**Nothing was tuned.** The 10% threshold, the 20% retracement, the 6-month horizon, the
8-episode floor and the 20-point span limit are all entry 4's, fixed 2026-11-06. **No
threshold, window, horizon or definition was moved at any point, and no episode was
dropped except the six the data cannot classify.** Both ranges and both bases are reported
in full rather than selected.

**Timing, and what was not run.** The condition and the outcome are **both** measured at
the crossing day's close, which is `study()`'s own convention and is **not implementable**
(`execution_lag_days` is 1 by default). The t+1 variant was **not computed**, and the
reason is not convenience: limb 1 fires on the **counts**, and a one-session shift in the
outcome window cannot move an episode between groups, because group membership is decided
by VXN at t. It is recorded here as a caveat rather than patched with a variant.

**Look-ahead, checked rather than cited.** Two checks, both passing:

1. Every VXN date entering R1 is at or before the crossing date — asserted episode by
   episode, **0 violations**.
2. VXN truncated at 2015-06-30 and R1 recomputed on the 9 episodes it shares with the full
   series: **largest absolute difference 0.000e+00**. The series is not revised, so the
   signal a real-time user would have had is the one used.

**The platform's own arithmetic was used wherever it exists, and its refusals are quoted
verbatim rather than worked around.** `effective_sample` gave every effective count;
`bootstrap_trades` and `block_bootstrap` gave every interval that could be given;
`events.baseline` gave the unconditional rate; `baselines.random_entry` gave the random
control. The one quantity with no platform home — an interval on a **two-sample
difference** — was written in the run record and is **shown to be degenerate** rather than
quoted as a result. That gap is now in its third consecutive finding and is filed as a
spec: `docs/specs/intervals-on-a-difference-of-two-samples.md`.

**Guard state:** `backtest.core.run_id.guards_were_green()` →
`{'state': 'green', 'when': '2026-11-07T23:44:15', 'note': 'the guards passed on this exact
code'}`. **Green.** Code version `c4f003a+dirty`; the record matches by version string and
every uncommitted file is under `docs/` or `research/` — **nothing under `backtest/`,
`app/` or `tools/` was touched by this session** — so the green record covers the code that
produced these numbers. `python -m tools.check` was **not** run: no code changed, and it
rewrites a shared guard artefact.

**Trials recorded** through `backtest.research.tracker.count_trials`, one idea tag:
`"fallen far and IV off its peak: VXN retracement at a 10% correction crossing vs QQQ's
6-month outcome (plan entry 4)"`.

- **As the plan declared it (entry 4: 1).** `Trials.total = 1`, one distinct configuration,
  `run_id` **`af9f436b1c79b12e`**.
- **As it was actually computed.** `Trials.total = 17` — **16 distinct
  `(range, price basis, reading)` configurations plus 1 grouping** (the ≥20% / <20% split
  itself). All 16 ids are in `research/2026-11-08-task-1c-entry4/trials.out`, each printed
  beside its configuration, and **all 16 are distinct** (the record asserts it). Whether a
  pre-registered split should be counted as a grouping at all is a method judgement this
  session may not make, so both counts are published rather than a chosen one.
- **The range, the price basis and the timing are inside the `run_id` payload**, with the
  reading and the split — the fix for the undercount entry 2's analyst hit:
  `settings = {"range": …, "price_basis": …, "timing": …, "reading": …, "split": ">=20% vs
  <20%"}`. Checked rather than assumed: one reading on two ranges gives **2** distinct ids,
  one reading on two bases gives **2**, and the 16 configurations give **16**.
- `docs/requirements.md` 4.3 is `partial` ("counted but unused"), so **nothing consumes
  either count automatically and a person must carry it into the reading.** No Deflated
  Sharpe is computed: there is no Sharpe here, only a probability.

**Local run record:** `research/2026-11-08-task-1c-entry4/` — `probe.py`, `entry4.py`,
`entry4b.py`, `entry4c.py`, `trials.py`, each with its `.out`. Not version controlled.

## Baselines

Six, because the difference of two small rates says nothing on its own.

1. **The unconditional 6-month up rate, beside every conditional one.** `events.baseline`
   — "enter on any day at random, same horizons" — gives **72.26%** on the primary range
   (n = 6,370 overlapping windows), **71.79%** traded, **73.44%** extended adjusted,
   **72.96%** extended traded. **Both groups are compared against the same one**, which is
   what entry 4's test requires, and it is the reason the difference of excesses equals the
   difference of up rates.
2. **The unconditional rate's own interval.** `block_bootstrap(block=126)` on the daily
   6-month up-indicator: **72.26%, 90% [63.30%, 79.89%]** — a span of **16.59 pt**. This
   **cancels** in the A − B difference (one common baseline) but it bounds **each group's
   excess**, and it is wider than most of the excesses quoted below.
3. **Entry 1's cell restricted to the classifiable episodes** — the "structure without the
   split" baseline: 20 episodes, 90.00% up, excess +17.74 pt. The right comparison for each
   group's excess, because it is what the split was applied to.
4. **Random entry, same count, same horizon, unstratified.** `baselines.random_entry`, 50
   draws (the registry default `random_entry_draws`, not reduced), `n_entries` = the
   group's own episode count, the runner returning the drawn dates' 6-month up rate.
5. **Random entry, year-stratified** — the same call with `like=` the group's own crossing
   dates, so the draw's calendar-year distribution matches the group's. This removes the
   "those were good years" explanation, which matters more here than anywhere: group A's
   single episode is 2008.
6. **Leave-one-out on group B**, every episode in turn — the analogue of
   `one_year_share` (0.70) and `single_trade_share` (0.35), which are defined on P&L and
   have no meaning for a probability. Group A **cannot have one**: n = 1.

`delta_equivalent` does not apply (no option position). `r.baselines()` does not apply (no
run). Buy-and-hold does not apply (no strategy); the unconditional any-day rate is its
event-study equivalent, and `events.py` makes it mandatory rather than optional.

## Result

### The split, every range × basis × reading

`nA` / `nB` are episode counts with a complete 6-month follow-up; `eff` is
`effective_sample(..., 6)`. "diff" is group A's up rate minus group B's, which **is** the
difference of excesses because the baseline is common. **Limb 1 of the criterion is the
last column.**

| range | basis | reading | nA | effA | upA | excessA | nB | effB | upB | excessB | unclassifiable | **diff** | limb 1 (either < 8) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **prereg** | **adjusted** | **R1** | **1** | **1** | **100.00%** | **+27.74 pt** | **19** | **16** | **89.47%** | **+17.21 pt** | **6** | **+10.53 pt** | **FIRES** |
| prereg | adjusted | R2 | 5 | 5 | 60.00% | −12.26 pt | 15 | 13 | 100.00% | +27.74 pt | 6 | **−40.00 pt** | **FIRES** |
| prereg | adjusted | R3 | 5 | 5 | 60.00% | −12.26 pt | 15 | 13 | 100.00% | +27.74 pt | 6 | **−40.00 pt** | **FIRES** |
| prereg | adjusted | R4 | 11 | 10 | 90.91% | +18.65 pt | 9 | 8 | 88.89% | +16.63 pt | 6 | +2.02 pt | does not fire |
| prereg | traded | R1 | 1 | 1 | 100.00% | +28.21 pt | 19 | 16 | 89.47% | +17.68 pt | 6 | +10.53 pt | **FIRES** |
| prereg | traded | R2 | 5 | 5 | 60.00% | −11.79 pt | 15 | 13 | 100.00% | +28.21 pt | 6 | −40.00 pt | **FIRES** |
| prereg | traded | R3 | 5 | 5 | 60.00% | −11.79 pt | 15 | 13 | 100.00% | +28.21 pt | 6 | −40.00 pt | **FIRES** |
| prereg | traded | R4 | 11 | 10 | 90.91% | +19.12 pt | 9 | 8 | 88.89% | +17.10 pt | 6 | +2.02 pt | does not fire |
| extended | adjusted | R1 | 1 | 1 | 100.00% | +26.56 pt | 22 | 19 | 90.91% | +17.47 pt | 6 | +9.09 pt | **FIRES** |
| extended | adjusted | R2 | 7 | 7 | 71.43% | −2.01 pt | 16 | 14 | 100.00% | +26.56 pt | 6 | −28.57 pt | **FIRES** |
| extended | adjusted | R3 | 7 | 7 | 71.43% | −2.01 pt | 16 | 14 | 100.00% | +26.56 pt | 6 | −28.57 pt | **FIRES** |
| extended | adjusted | R4 | 12 | 11 | 91.67% | +18.23 pt | 11 | 10 | 90.91% | +17.47 pt | 6 | +0.76 pt | does not fire |
| extended | traded | R1 | 1 | 1 | 100.00% | +27.04 pt | 22 | 19 | 90.91% | +17.95 pt | 6 | +9.09 pt | **FIRES** |
| extended | traded | R2 | 7 | 7 | 71.43% | −1.53 pt | 16 | 14 | 100.00% | +27.04 pt | 6 | −28.57 pt | **FIRES** |
| extended | traded | R3 | 7 | 7 | 71.43% | −1.53 pt | 16 | 14 | 100.00% | +27.04 pt | 6 | −28.57 pt | **FIRES** |
| extended | traded | R4 | 12 | 11 | 91.67% | +18.71 pt | 11 | 10 | 90.91% | +17.95 pt | 6 | +0.76 pt | does not fire |

**Three readings out of four put a group below 8, and the one that does not is the one that
is not what entry 4 says** — and it produces **+2.02 pt**, below the hypothesis's own +5 pt
floor. **No reading supports the hypothesis.** The two look-ahead readings put the
difference at **−28.6 to −40.0 pt**, the **opposite sign** to the prediction; they are
inadmissible and are reported only so the definition's effect is visible rather than
hidden.

### The primary cell in full — 20 episodes, one on one side

**Group A — VXN retraced ≥ 20% from the within-episode maximum: 1 episode.**

| crossed | drawdown on the crossing day | VXN peak in `[prior high, crossing]` | VXN at crossing | retracement | 6m return | finished up |
|---|---|---|---|---|---|---|
| 2008-10-30 | **−39.75%** | 0.7916 | 0.6319 | **20.17%** | +7.06% | **yes** |

**Group B — retraced < 20%: 19 episodes, 16 non-overlapping.**

| crossed | drawdown | VXN peak | VXN at crossing | retracement | 6m return | up |
|---|---|---|---|---|---|---|
| 2004-03-22 | −11.09% | 0.2734 | 0.2715 | 0.69% | +3.67% | yes |
| 2005-03-29 | −10.09% | 0.2018 | 0.1799 | 10.85% | +7.62% | yes |
| 2006-05-23 | −10.63% | 0.2254 | 0.2218 | 1.60% | +15.17% | yes |
| 2007-11-12 | −11.45% | 0.3494 | 0.3494 | 0.00% | +0.96% | yes |
| 2010-05-07 | −10.11% | 0.4152 | 0.4152 | 0.00% | +18.92% | yes |
| 2011-08-08 | −15.16% | 0.4474 | 0.4474 | 0.00% | +23.51% | yes |
| 2012-05-18 | −10.90% | 0.2755 | 0.2755 | 0.00% | +5.42% | yes |
| 2012-11-08 | −10.03% | 0.2167 | 0.2014 | 7.06% | +16.72% | yes |
| 2015-08-21 | −10.16% | 0.3020 | 0.3020 | 0.00% | −0.26% | **no** |
| 2016-01-13 | −11.24% | 0.2833 | 0.2711 | 4.31% | +10.51% | yes |
| 2018-02-08 | −10.23% | 0.3389 | 0.3389 | 0.00% | +18.99% | yes |
| 2018-04-02 | −10.52% | 0.3019 | 0.2992 | 0.89% | +19.94% | yes |
| 2018-10-24 | −11.30% | 0.3025 | 0.3025 | 0.00% | +16.07% | yes |
| 2019-06-03 | −10.98% | 0.2525 | 0.2422 | 4.08% | +21.07% | yes |
| 2020-02-27 | −13.22% | 0.4100 | 0.4100 | 0.00% | +42.53% | yes |
| 2020-09-08 | −10.84% | 0.4209 | 0.4102 | 2.54% | +15.37% | yes |
| 2021-03-08 | −10.85% | 0.3616 | 0.3387 | 6.33% | +27.52% | yes |
| 2022-01-20 | −10.35% | 0.3061 | 0.3061 | 0.00% | **−16.25%** | **no** |
| 2023-10-26 | −10.78% | 0.2531 | 0.2485 | 1.82% | +26.41% | yes |

**Unclassifiable — a 6-month outcome exists, VXN does not: 6 episodes.** 1999-06-14
(+57.28%, up), 1999-08-04 (+68.70%, up), 2000-01-06 (+19.78%, up), 2000-01-28 (+1.02%, up),
2000-03-15 (−9.11%, **no**), 2000-04-03 (−15.73%, **no**).

**The retracement distribution over the 20 classifiable episodes (R1):** mean **3.02%**,
median **0.79%**, 75th percentile **4.14%**, maximum **20.17%**. **At or above 20%: 1. At or
above 10%: 2. Exactly zero — VXN's episode-window peak *is* the crossing day — 9 of 20
(45%).**

- Group A 6-month up rate **100.00%** (1 of 1), excess **+27.74 pt** over 72.26%.
- Group B 6-month up rate **89.47%** (17 of 19), excess **+17.21 pt** over the same 72.26%.
- **Difference +10.53 pt**, which nominally clears entry 4's +5 pt hypothesis clause.

### Why the split is 1 against 19 — the structural result, with its unconditional baseline

The crossing date is the first close 10% below a rolling 252-day high. That is, close
enough, the day the market is most frightened — so VXN's maximum over the fall into the
correction is at or very near the crossing day itself, and the retracement is near zero by
construction. Measured, with the unconditional rate beside each conditional one:

| conditional | value | its unconditional baseline |
|---|---|---|
| P(VXN ≥ 20% off its **trailing-252** peak \| a 10% crossing day) | **55.0%** (11/20) | P(… \| **any session**) = **89.9%** (n = 6,425) |
| P(VXN ≥ 20% off its **episode-window** peak \| a 10% crossing day) | **5.0%** (1/20) | — (no any-day analogue: an arbitrary session has no episode) |
| P(VXN's episode-window peak is the crossing day itself) | **45.0%** (9/20) | — |

**The conditional is far below the unconditional on the reading that has one.** VXN sits
≥ 20% off its trailing-year peak on nearly nine sessions in ten — it spikes and then stays
low for a year, which is the same `rank_in_window` property entry 3 reported — and on a
10%-crossing day that falls to 55%, or to 5% once the window is confined to the episode.
**"Fallen far" and "IV off its peak" are close to disjoint at the crossing date.** That is a
fact about the measurement date, not about the sample size, and it would not improve with
more history.

### Intervals, with the construction named — and the platform's refusals verbatim

All at **90%**, 2,000 draws, seed **20260917** (the module default).

| quantity | construction | result |
|---|---|---|
| group A 6m up rate | `bootstrap_trades(overlapping=True)` | **REFUSED**: *"1 usable values cannot support a bootstrap interval; at least 10 are needed. A number from fewer observations should be reported as the observations, not as a statistic with a range"* |
| group A 6m up rate | `bootstrap_trades(overlapping=False)` | **REFUSED**, same message |
| group B 6m up rate | `bootstrap_trades(overlapping=True)` | 0.8947 **[0.7895, 1.0000]**, with the module's *"too narrow where they overlap"* caveat |
| group B 6m up rate | `bootstrap_trades(overlapping=False)` | 0.8947 **[0.7895, 1.0000]** |
| **A − B difference** | paired resample, written in the run record (the platform has no two-sample bootstrap) | **+10.53 pt, 90% [+0.00, +21.05] → span 21.05 pt** |
| the unconditional rate | `block_bootstrap(block=126)` | 0.7226 **[0.6330, 0.7989]** → span 16.59 pt; **cancels in the difference**, bounds each excess |

**The difference's interval is degenerate and the run record proves it rather than
asserting it.** Across 2,000 draws the group-A side takes exactly **1** distinct value,
because resampling one episode with replacement returns that episode every time. **All
21.05 points of the span are group B's**; the one-episode side contributes none. The
platform's own floor would refuse the whole thing (1 < `bootstrap_min_observations` = 10),
and `events.py`'s doctrine says what to do instead — *"only 'this happened once in history,
and here it is'"* — which is why group A is reported above as the episode rather than as a
rate with a range.

### The one-episode sensitivity, which is the whole result

| what moves | the difference becomes |
|---|---|
| as observed | **+10.53 pt** |
| group A's single episode (2008-10-30) had finished **down** at 6 months | **−89.47 pt** |
| leave one group-B episode out (19 variants) | **+5.56 to +11.11 pt** |

One group-B episode is worth **5.26 pt** of its rate; **group A's single episode is worth
100 pt of its own.** `single_trade_share` (0.35) and `one_year_share` (0.70) from
`core/thresholds.py` are defined on P&L and have no arithmetic meaning for a probability,
but their analogue here is unambiguous: **one episode carries 100% of group A, and that
episode is one calendar year (2008).** Both cuts are breached by an order of magnitude, so
**this is a finding about an episode and not about a region of the state space** — which is
why it is in the verdict and not in a footnote.

**And that single episode is not a representative 10% correction entry.** Its crossing-day
drawdown is **−39.75%**, not ≈ −10%. `find_corrections` requires a **fresh reference high**
for a new episode, so the whole 2007-2009 bear market registers as **one** 10% episode,
dated **2008-10-30** against a 2007-11-06 high. The only episode in which VXN had retraced
20% is the one the episode rule compressed a fourteen-month collapse into. That is entry
1's episode set working exactly as documented, and it is not altered here — but it means
group A is a single, atypical observation.

### Baselines: random entry, same count, same horizon

50 draws. "pct" is the share of draws below the observed value.

| group | observed 6m up rate | unstratified random: median [5th, 95th] | pct | year-stratified random: median [5th, 95th] | pct |
|---|---|---|---|---|---|
| A (n=1) | 100.00% | 100.00% [0.00%, 100.00%] | 0.42 | 0.00% [0.00%, 100.00%] | 0.68 |
| B (n=19) | **89.47%** | 73.68% [57.89%, 87.11%] | **0.94** | **73.68% [63.16%, 89.47%]** | **0.90** |

**Group A's row is meaningless and is printed to show that it is.** A one-date draw of a
binary outcome has a two-point distribution; the percentile is a coin flip and no reading
should be taken from it.

**Group B's row is the one informative baseline in this finding**: 19 correction crossings
with VXN *not* off its peak still beat 90% of year-matched random draws of 19 dates. That
is entry 1's effect surviving inside the classifiable subsample — it is **not** evidence
for entry 4's hypothesis, which is about the **difference** between the groups and has only
one episode on one side.

### The pre-registered criterion, limb by limb

> **Abandon if** either group falls below **8 episodes** after the split, **or** the 90%
> interval on the difference spans more than **20 percentage points**. *"Expect the
> latter."*

**Limb 1 — the 8-episode floor. FIRES, decisively, on every range and both bases.** Group A
holds **1** episode on the primary reading (and 5 or 7 on the two inadmissible ones). 1 < 8.
No judgement is needed and no construction is in dispute: it is a count.

**Limb 2 — the 20-point span. FIRES on the only construction available, which is
degenerate.** Span **21.05 pt** > 20 pt. The legitimate interval is **refused by the
platform**. So limb 2 points the same way as limb 1 but adds nothing independent: an
interval on a one-episode group is not an interval.

**Entry 4 is abandoned, decided by limb 1.** The plan expected limb 2 and said so on
2026-11-06; what actually happened is worse than expected — the split is not *imprecise*,
it is *not a split*. **The hypothesis as written is not falsified and is not confirmed: it
is unevaluable at this measurement date**, and the point estimate that nominally clears its
+5 pt clause is one episode which could as easily have reversed the sign by 100 points.

## How much of tonight's evidence is one series looked at four ways

Entries 1, 2, 3 and 4 are **not four independent looks**, and the dependence is stronger
than entry 3 could see when it flagged it.

- **Entries 2 and 3 are two cuts of VXN's level** — entry 3 measured it:
  P(high vol | highest `iv_rank_252` quintile) = **0.544** against an unconditional
  **0.241**.
- **Entry 1's episode set is itself a high-VXN subsample.** On the 21 classifiable 10%
  crossing days, 1999-2024: P(entry 2's "high vol", VXN > 0.28 | a crossing day) =
  **0.571** against an unconditional **0.265**; P(low vol, VXN < 0.18 | a crossing day) =
  **0.048** against **0.332**; P(entry 3's top `iv_rank_252` quintile | a crossing day) =
  **0.619** against **0.200**; mean `iv_rank_252` on crossing days **0.661** against an
  unconditional **0.290**. **A 10% correction crossing is, in VXN terms, the same event
  entries 2 and 3 were stratifying on.**
- **Entry 4 then splits that set on a third transform of VXN** — its retracement from a
  within-episode peak.

So tonight's four entries are: **one underlying (QQQ), one volatility series (VXN), and one
price history, cut four ways.** Entry 2 cut VXN by absolute level, entry 3 by its rank in a
trailing year, entry 1 selected on QQQ's drawdown (which selects high VXN), and entry 4
split entry 1's selection by VXN's retracement.

**What a reader must carry.** The plan's § 6 declared **8** for entry 1, **3 plus a
two-half split** for entry 2, **1** for entry 3 and **1** for entry 4 — **13 or 14**
depending on whether the split counts, of which the 4 SPY trials could not be run, so
**9 or 10** were available. What the four findings actually recorded through
`research.tracker`: **4 + 28 + 14 + 17 = 63** (`Trials.total` each). The sum of the
**distinct configurations** each finding says it computed is **72** — 16 + 27 + 13 + 16 —
higher than 63 because entry 1 recorded thresholds rather than configurations and published
both numbers. And because `docs/requirements.md` 4.3 is `partial` — the count is recorded
and **nothing consumes it** — **the correction is entirely a person's to apply by hand.**
The honest summary is:

1. **The trial count is 63 recorded and 72 computed, not 13**, and all four findings publish
   both of their own numbers.
2. **They are not 63 independent tests.** They are nested and overlapping — entry 1's four
   thresholds are nested (all 7 of the 20% episodes sit inside a 15% episode, 24 of the 27
   10% episodes inside a 5% one), entries 2 and 3 share VXN, and entry 4 sits inside entry
   1. A naive Bonferroni over 63 or 72 is too harsh; treating them as 4 is far too generous. **No
   number here is the right divisor, and inventing one would be the method choice this
   session may not make.**
3. **The direction of the error is known even if its size is not.** Tonight produced three
   abandonments (entries 1, 3, 4) and one result whose verdict turned on a sample start the
   criterion never fixed (entry 2). **A search of this size over one price history
   and one volatility series yielding one surviving positive result is approximately what
   chance would produce**, and entry 2's own finding says its verdict cannot be settled.
   Nothing from tonight should be carried forward as an established effect.

## Caveats

- **The sample is too small for the claim, and that is the finding rather than a
  qualification.** Group A is **one episode**. There is no interval, no leave-one-out, no
  random-entry reading and no sensitivity analysis that means anything on it, and the
  platform refuses to compute three of those four. **What it would take:** the condition as
  written requires VXN to have fallen 20% from its episode peak *by the day QQQ first closes
  10% down*, which happened once in 20 classifiable episodes in 24 years. At that rate,
  reaching 8 episodes in group A needs roughly **160 correction episodes**, i.e. of the
  order of **two centuries** of QQQ-like history. **More data is not the answer; a different
  measurement date is**, and that is a different question.
- **The classifiable subsample is not random and it flatters both groups.** VXN starts
  2001-01-23, so the six 1999-2000 episodes — including two of entry 1's four 6-month
  losers — cannot be classified. Entry 1's +12.35 pt excess becomes **+17.74 pt** on the 20
  that remain, **before any split**. Every excess in the tables above sits on that inflated
  base. The *difference* between the groups is unaffected (one common baseline), but each
  group's excess is.
- **The measurement date is not implementable.** The condition and the entry are both at the
  crossing day's close, while `execution_lag_days` is 1 by default. No t+1 variant was
  computed, because group membership is fixed by VXN at t and limb 1 fires on the counts.
  A finding that had turned on the magnitude rather than the count would need that variant.
- **"Within-episode maximum" is ambiguous and the reading changes the sign.** R1 gives
  +10.53 pt on 1 episode; the two look-ahead readings give −40.00 pt on 5; the trailing-252
  reading gives +2.02 pt on 11. All four are reported and the primary one is forced by
  admissibility rather than chosen — but a criterion whose verdict could depend on a reading
  the entry did not fix is the same defect entry 1's limb 1 had, and **it is worth fixing in
  the plan before any similar entry is written.** Here it does not change the verdict: no
  reading supports the hypothesis.
- **One episode in group A is an artefact of the episode rule, not a typical correction
  entry.** 2008-10-30 enters at a **−39.75%** drawdown because `find_corrections` requires
  a fresh reference high, so the 2007-2009 bear market is one episode. Entry 1's episode
  definition was not altered, and it should not be — but the single observation that decides
  entry 4's direction is the least representative one in the set.
- **Nominal counts overstate the independent sample.** Group B is 19 episodes and **16**
  non-overlapping 6-month windows; entry 1's cell is 26 and 19. `study()`'s own
  `_sample_warning` is **silent** at the 10% threshold (it fires only when `effective ≤ 8`
  or `sample > 2 × effective`), and silence from the tool is not a statement about the
  sample — the same point entry 1 made.
- **The horizon mismatch is still there.** Horizons are trading days (`DAYS_PER_MONTH = 21`,
  so 6 months is 126 sessions) while `effective_sample` measures overlap in calendar days
  (`months × 30`). Already on record in
  `docs/specs/effective-sample-returns-its-subset.md`; no second spec was filed for it.
- **The two-sample difference interval was written outside the platform.** `intervals.py`
  offers one-sample bootstraps only, and entries 2, 3 and 4 have each now hand-rolled the
  paired version. Filed as `docs/specs/intervals-on-a-difference-of-two-samples.md`. It
  would **not** have changed this verdict — with n = 1 no construction helps — which is why
  it is a spec and not a blocker.
- **How many configurations were tried: 16**, all reported above, **none tried and
  dropped**, plus 4 interval constructions, 2 random-entry forms × 50 draws, a 19-fold
  leave-one-out, 2 look-ahead checks and the diagnostic below. The plan declared **1**.
  Neither count is consumed by anything automatic.
- **Not out of sample and cannot be made so.** QQQ has one price history and VXN has one.
  The 1999-2026 period has been looked at by this project before — `research-questions.md`
  § 2.1 is that look. The period hold-out (2026-11-08 onward) began today and holds nothing.
- **Not a trading result.** No option is priced anywhere, no exit, sizing or selection rule
  appears, and no engine ran.

### A diagnostic, with no criterion attached and no test behind it

Reported because it says what a future entry would have to measure, and **explicitly not a
result**: of the 27 primary-range episodes, **21 eventually reached a 20% VXN retracement
from the running episode peak**, at a **median of 5 trading days after the crossing date**
(range 0 to 16). The six that never did are the six with no VXN data.

So the condition entry 4 asks about is not rare in an episode — it is rare **on the crossing
day**, and typically arrives about a week later. **That is a different question**: it needs a
measurement date that moves with VXN, which means a different entry date, different forward
returns, and a different unconditional baseline (not "any day", but "any day a week after a
10% crossing"). **It is not tested here, has no pre-registered criterion, and nothing in
this finding's verdict rests on it.** Running it would have been choosing the test after
seeing that the pre-registered one could not be run.

## What it changes

**For plan entry 4: close it. Abandoned on limb 1, and the record should say it was the
count and not the interval.** The plan expected a wide interval; what it got was a group of
one. Nothing needs re-running to reach that conclusion, and this session did not revise the
criterion to avoid it. **The entry did its job**: it was written on 2026-11-06 saying it was
probably too small to conclude from, and it was.

**For the strategy: nothing.** No entry rule, no filter, no sizing and no exit should change
on the strength of this file. In particular **do not build an entry condition of the form
"after a 10% fall, once IV has come off its peak"** on anything here: at the crossing date
the condition held once in twenty episodes, and the one time it held, the outcome was a
single 2008 observation.

**For whoever writes the next entry in this family — three things this one learned that are
a person's to act on, not this session's:**

1. **The question is about a measurement date, not a filter.** "Fallen far, and IV off its
   peak" cannot be evaluated at the crossing date, because the crossing date is
   approximately the VXN peak (45% of the time it *is* the peak). A new entry would have to
   state its entry date as a function of both series, name its unconditional baseline for
   that date (which is **not** "any day"), and set its criterion on the resulting sample
   before looking. The diagnostic above says such an entry would have roughly 21 episodes to
   work with, not 10 — but they would be **delayed** entries, and the forward return from a
   later, higher price is a different quantity.
2. **A criterion that names a statistic must name its construction.** Entry 1's limb 1 was
   unsettleable for this reason; entry 4's limb 2 would have been, had limb 1 not already
   decided it. Entries 2 and 4 both hit the same class of defect.
3. **VXN's start date silently reshapes any entry that conditions on it inside entry 1's
   episodes.** Six episodes, the whole dot-com crash, and two of four losers vanish. Any
   future entry in this family should state its classifiable subsample and the excess on it
   **before** splitting, as the table in the Data section does.

**For `docs/research/plan.md` § 5 entry 4, for `doc-keeper` and the owner — two
corrections, neither of which this session may make:**

1. Entry 4's "smallest credible experiment" predicts *"splitting 21 in two gives something
   like 10 and 11"*. The actual split at the pre-registered measurement date is **1 and 19**,
   and the reason is structural. The prediction was right that the entry could not be
   answered and wrong about why — and the why is the useful part.
2. Entry 4's data line says *"QQQ OHLC and VXN. Both exist."* True, but **not over the same
   range**: VXN starts 2001-01-23 and six of entry 1's 27 10% episodes precede it. The entry
   should say that 6 of 27 episodes are unclassifiable before anyone relies on its sample
   arithmetic. This is the third entry running whose data line is optimistic about a series'
   start (entry 2's VIX3M by four and a half years, entry 3's VXN by six months, entry 4's
   by six episodes), which looks like a pattern worth one pass over the whole plan rather
   than three separate corrections.

**One platform gap, filed as a spec and not built:**
`docs/specs/intervals-on-a-difference-of-two-samples.md`. **No spec was written for the
measurement-date gap** the plan names, because the plan already ruled on it: conditioning a
measurement date on a second series is analysis, not a capability. **Nothing under
`backtest/`, `app/` or `tools/` was edited.**
