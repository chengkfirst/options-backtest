# Is the 6-month edge window after a QQQ correction real, or 21 observations?

**Date:** 2026-11-08 (the runs were executed late on 2026-11-07 and written up
immediately after midnight; the guard record and the data snapshot are both 2026-11-07's)
**Answers:** `docs/research/plan.md` § 5, **Entry 1** (hypothesis, test and abandonment
criterion all dated 2026-11-06, before any number existed)
**Verdict:** **Away from the 10% threshold the 6-month excess cannot be told from noise** —
it is **+0.5 pt** at a 15% threshold (11 episodes) and **−0.8 pt** at 20% (7 episodes), on
samples where the platform **refuses to compute an interval at all**, and where one to three
episode flips would reverse which horizon is largest. On the pre-registered criterion entry 1
is therefore **abandoned**: limb 2 fires on all four price-basis × range combinations — the
6-month horizon is the largest of the four excesses in **1 of 4** thresholds on the
pre-registered range and **2 of 4** on the extended one, and the criterion required it to
survive in at least three. Limb 1 **cannot be settled as written**: the 10% threshold's
6-month excess is **+12.4 pt** with a 90% interval that excludes zero on two constructions and
contains it on a third, and the criterion names a level but not a construction. **The SPY half
of the test — 4 of its 8 declared trials — could not be run**, so the hypothesis as written
(which also claims another underlying) is only half evaluated.

---

## Data

- **Source:** `backtest.data.ingest.fetch_ohlcv("QQQ", ...)` (yfinance) through
  `backtest.data.store.ParquetCache`, built with `backtest.data.underlying.build_bars`.
- **Symbol: QQQ only.** **No SPY run of any kind** — `docs/decisions/0001-the-hold-out-part-b-and-the-option-data.md`
  § 1 reserves SPY until the final check, and that reservation is held by the document alone
  today (`HoldOut` takes dates, not symbols). Entry 1's test says "then the same four on SPY";
  that half was not run and is not abandoned — it is **unavailable**, which is the price of the
  hold-out and is recorded here rather than left to look like an omission.
- **Ranges.** Entry 1's test names **1999-03-10 to 2024-12-31**; that is the **primary** range
  here, because it is what was pre-registered. The **extended** range to the last available bar
  (**2026-11-06**) is reported beside it, because the previous finding
  (`correction-edge-window-2.1-restated.md`) is on that range and the two would otherwise not be
  comparable. **Both are reported for every threshold and every horizon; neither was chosen
  after the fact.** First bar available 1999-03-10 (QQQ's listing), last 2026-11-06, 6,960
  sessions.
- **No data after 2026-11-07.** The fetch range ends 2026-11-07 and the last bar in it is
  **2026-11-06**, so the period hold-out `HoldOut(2026-11-08, 2099-12-31)` was never reached
  and no `unlock=` was needed or passed. It is worth noting that 2026-11-08 is now the present:
  the hold-out has begun to accrue, and it holds nothing yet.
- **Option chain: none.** Not the synthetic one, not any other. This is an `UNDERLYING`-scope
  event study on daily OHLC; `backtest/research/events.py` states it "never touches options".
  **What this finding therefore cannot claim:** nothing here supports any option-side
  conclusion — not which delta to buy, not which expiry, not where a take-profit or a stop
  belongs, not whether entering after a correction is profitable in premium terms. The
  synthetic chain's IV is one constant per day and expiry with no skew and no term structure,
  so those questions are unanswerable on this platform today; they are not approximated here.
  The step from "QQQ was higher six months later" to "a call bought that day made money" is
  **not taken anywhere in this file and is not supported by it.**
- **Two price bases, and the choice was forced.** `UnderlyingBars` carries `adjusted()`
  ("corrected for splits and dividends, **for signals and returns**") and `traded()`. A forward
  return and a drawdown are returns, so **adjusted is primary** — prescribed by the module, not
  picked here. `traded` is reported beside it **because the previous finding showed the basis
  moves the answer and that `research-questions.md` § 2.1 fixed neither basis nor end date**.
  That is the reason both appear, and it was decided before this run produced a number: the
  conclusions below are identical on both bases, so no basis was selected.
- The extended-range frames are the full fetch sliced at 2024-12-31 for the primary range
  rather than a second fetch. Dividend adjustment after the cut applies one common
  multiplicative factor to every earlier date, so returns and drawdowns are unaffected.

## Method

No `run()` call: this question needs no engine, no strategy and no option price, and
`run()` cannot express it. The public calls are:

```python
from datetime import date
import numpy as np, pandas as pd
from backtest.data.ingest import fetch_ohlcv
from backtest.data.store import CacheKey, ParquetCache
from backtest.data.underlying import build_bars
from backtest.research.events import DAYS_PER_MONTH, effective_sample, study
from backtest.research.validation.intervals import block_bootstrap, bootstrap_trades
from backtest.research.tracker import count_trials

START, END = date(1999, 3, 10), date(2026, 11, 7)
cache = ParquetCache()
key = CacheKey("ohlcv", "QQQ", {"start": START, "end": END,
                                "source": "yfinance", "prices": "raw+adj"})
bars = build_bars("QQQ", cache.get_or_load(key, lambda: fetch_ohlcv("QQQ", START, END)))

# PRIMARY: adjusted OHLC, pre-registered range.
frame = pd.DataFrame({"close": bars.adjusted("close"),
                      "high":  bars.adjusted("high"),
                      "low":   bars.adjusted("low")}).loc[:pd.Timestamp(2024, 12, 31)]

for threshold in (0.05, 0.10, 0.15, 0.20):            # the four pre-registered thresholds
    s = study(frame, threshold, horizons_months=(1, 3, 6, 12))
    print(s["table"], s["excluded_by_data_end"], s["intraday"], s["basis"])

# SECONDARY: swap bars.adjusted for bars.traded, and/or drop the .loc slice. Nothing else.
```

**Real OHLC, not a close series.** Every run returned `intraday = True` and `basis` =
*"events detected on the close; further falls and the deepest drawdown measured on the
intraday low"*, which is the confirmation the plan asked for.

**Everything else is left at the module's default** — `reference_window=252`,
`reset_fraction=0.5`, `DAYS_PER_MONTH=21` (so "6 months" is 126 sessions). **No parameter,
window, threshold or definition was moved at any point.** The four thresholds are the plan's;
the four horizons are the plan's; the two bases and two ranges are reported in full rather than
chosen.

**Guard state:** `backtest.core.run_id.guards_were_green()` →
`{'state': 'green', 'when': '2026-11-07T23:44:15', 'note': 'the guards passed on this exact
code'}`. **Green.** Code version `c4f003a+dirty`. The record matches by version string and the
only uncommitted files are under `docs/` and `research/` — **nothing under `backtest/`,
`app/` or `tools/` was touched by this session**, so the green record covers the code that
produced these numbers.

**Trials recorded.** Through `backtest.research.tracker.count_trials`, idea tag
**`"correction entry: the 6-month window after a QQQ drawdown (plan entry 1)"`**, one record
per threshold with a `run_id` hashed from the study configuration:

| threshold | `run_id` |
|---|---|
| 0.05 | `20facf51a89b16aa` |
| 0.10 | `a6455b8dbc804943` |
| 0.15 | `6a226f141648ab8a` |
| 0.20 | `8d3dd7fa62dceb3a` |

`Trials.total` = **4** (4 distinct configurations, 0 groupings, 0 reruns). The plan declared
**8** for entry 1 (4 thresholds × 2 underlyings); **4 were recorded because the SPY four could
not be run.** Two things must travel with that number: `docs/requirements.md` 4.3 is `partial`
— "counted but unused" — so **the Deflated Sharpe does not consume this count and a person has
to carry it into the reading by hand**; and 4 is the count of *thresholds*, while **16 study
configurations were actually computed** (4 thresholds × 2 price bases × 2 ranges), all of them
reported below. Whether a price basis or a date range is a trial is a method judgement this
session may not make, so the raw count is given instead of a chosen one.

**Local run record:** `research/2026-11-07-task-1c/` (`entry1.py` + `entry1.out`,
`fragility.py` + `fragility.out`; not version controlled).

## Baselines

The unconditional baseline is not an add-on here: `study()` computes it itself
(`events.baseline` — "enter on any day at random, same horizons") and the **`excess` column is
conditional minus unconditional**. Every conditional figure below is printed beside the
unconditional rate for the same series, the same range and the same horizon. **For an event
study the unconditional any-day rate *is* the random-entry control** — same count of draws is
not the question, the question is whether the condition adds anything to entering on an
arbitrary day — and it is built into the tool rather than bolted on. `research/baselines.py`'s
random-entry comparison does not apply: there is no strategy, no entry rule and no P&L to
compare.

Three further baselines, because conditional-minus-unconditional is not the only thing that can
be wrong:

1. **The unconditional rate's own sampling error.** 6,370 overlapping 6-month windows are not
   6,370 observations. A block bootstrap of the daily up-indicator in blocks of 126 days
   (≥ the horizon) puts the unconditional 6-month rate at **72.3%, 90% interval
   [63.3%, 79.9%]** on the primary range — a span of **16.6 points**, about as wide as the
   excess being measured.
2. **Episodes, not days**, and **non-overlapping episodes** — `study()`'s `sample` and
   `effective` columns, reported for every cell below.
3. **The ranking baseline for limb 2:** how many episodes would have to flip to change which
   horizon is largest. Reported per threshold, because that is what limb 2 actually turns on.

## Result

### Primary: adjusted OHLC, 1999-03-10 → 2024-12-31 (the pre-registered range)

`excluded_by_data_end` is per threshold, keyed by horizon, and is quoted in each header.

**threshold 0.05 — 49 episodes, `excluded_by_data_end = {1: 0, 3: 0, 6: 1, 12: 2}`**

| horizon | sample | effective | up | baseline up | excess |
|---|---|---|---|---|---|
| 1m | 49 | 46 | 69.39% | 62.83% | **+6.56 pt** |
| 3m | 49 | 33 | 75.51% | 68.49% | **+7.02 pt** |
| 6m | 48 | 25 | 79.17% | 72.26% | **+6.91 pt** |
| 12m | 47 | 16 | 74.47% | 77.98% | **−3.51 pt** |

**threshold 0.10 — 27 episodes, `excluded_by_data_end = {1: 0, 3: 0, 6: 1, 12: 1}`**

| horizon | sample | effective | up | baseline up | excess |
|---|---|---|---|---|---|
| 1m | 27 | 25 | 62.96% | 62.83% | **+0.14 pt** |
| 3m | 27 | 22 | 74.07% | 68.49% | **+5.58 pt** |
| **6m** | **26** | **19** | **84.62%** | **72.26%** | **+12.35 pt** |
| 12m | 26 | 14 | 76.92% | 77.98% | **−1.06 pt** |

**threshold 0.15 — 11 episodes, `excluded_by_data_end = {1: 0, 3: 0, 6: 0, 12: 0}`**

| horizon | sample | effective | up | baseline up | excess |
|---|---|---|---|---|---|
| 1m | 11 | 11 | 81.82% | 62.83% | **+18.99 pt** |
| 3m | 11 | 11 | 72.73% | 68.49% | **+4.24 pt** |
| 6m | 11 | 10 | 72.73% | 72.26% | **+0.47 pt** |
| 12m | 11 | 10 | 63.64% | 77.98% | **−14.34 pt** |

**threshold 0.20 — 7 episodes, `excluded_by_data_end = {1: 0, 3: 0, 6: 0, 12: 0}`**

| horizon | sample | effective | up | baseline up | excess |
|---|---|---|---|---|---|
| 1m | 7 | 7 | 57.14% | 62.83% | **−5.68 pt** |
| 3m | 7 | 7 | 71.43% | 68.49% | **+2.94 pt** |
| 6m | 7 | 7 | 71.43% | 72.26% | **−0.83 pt** |
| 12m | 7 | 5 | 57.14% | 77.98% | **−20.84 pt** |

`study()`'s own `note` column fires at the 0.20 threshold ("effective sample 7: read the
direction, not the decimals"; 5 at 12 months) and at 0.05's 12-month row ("47 episodes
nominally but only 16 non-overlapping"). It is **silent** at the 0.10 and 0.15 thresholds,
which should not be read as reassurance — see the caveats.

### The traded (unadjusted) series, same range

| threshold | horizon | sample | effective | up | baseline up | excess |
|---|---|---|---|---|---|---|
| 0.05 (50 ep., censored {1:1,3:1,6:2,12:3}) | 1m | 49 | 46 | 69.39% | 62.32% | +7.07 pt |
| | 3m | 49 | 33 | 75.51% | 68.01% | +7.50 pt |
| | 6m | 48 | 27 | 77.08% | 71.79% | +5.29 pt |
| | 12m | 47 | 16 | 76.60% | 76.94% | −0.34 pt |
| 0.10 (27 ep., censored {1:0,3:0,6:1,12:1}) | 1m | 27 | 25 | 62.96% | 62.32% | +0.65 pt |
| | 3m | 27 | 22 | 74.07% | 68.01% | +6.07 pt |
| | **6m** | **26** | **19** | **84.62%** | **71.79%** | **+12.83 pt** |
| | 12m | 26 | 14 | 76.92% | 76.94% | −0.01 pt |
| 0.15 (11 ep., censored all 0) | 1m | 11 | 11 | 81.82% | 62.32% | +19.50 pt |
| | 3m | 11 | 11 | 72.73% | 68.01% | +4.72 pt |
| | 6m | 11 | 10 | 72.73% | 71.79% | +0.94 pt |
| | 12m | 11 | 10 | 63.64% | 76.94% | −13.30 pt |
| 0.20 (7 ep., censored all 0) | 1m | 7 | 7 | 42.86% | 62.32% | −19.46 pt |
| | 3m | 7 | 7 | 71.43% | 68.01% | +3.42 pt |
| | 6m | 7 | 7 | 71.43% | 71.79% | −0.36 pt |
| | 12m | 7 | 5 | 57.14% | 76.94% | −19.80 pt |

### The extended range, 1999-03-10 → 2026-11-06, both bases

| basis | threshold | horizon | sample | effective | up | baseline up | excess |
|---|---|---|---|---|---|---|---|
| adjusted | 0.05 (54 ep., censored {1:1,3:1,6:2,12:3}) | 1m | 53 | 50 | 67.92% | 62.80% | +5.12 pt |
| | | 3m | 53 | 36 | 75.47% | 68.80% | +6.67 pt |
| | | 6m | 52 | 27 | 80.77% | 73.44% | +7.33 pt |
| | | 12m | 51 | 17 | 76.47% | 79.46% | −2.99 pt |
| adjusted | 0.10 (31 ep., censored {1:1,3:1,6:2,12:3}) | 1m | 30 | 28 | 63.33% | 62.80% | +0.53 pt |
| | | 3m | 30 | 25 | 76.67% | 68.80% | +7.87 pt |
| | | **6m** | **29** | **22** | **86.21%** | **73.44%** | **+12.77 pt** |
| | | 12m | 28 | 15 | 78.57% | 79.46% | −0.89 pt |
| adjusted | 0.15 (12 ep., censored all 0) | 1m | 12 | 12 | 83.33% | 62.80% | +20.53 pt |
| | | 3m | 12 | 12 | 75.00% | 68.80% | +6.20 pt |
| | | 6m | 12 | 11 | 75.00% | 73.44% | +1.56 pt |
| | | 12m | 12 | 11 | 66.67% | 79.46% | −12.79 pt |
| adjusted | 0.20 (8 ep., censored all 0) | 1m | 8 | 8 | 62.50% | 62.80% | −0.30 pt |
| | | 3m | 8 | 8 | 75.00% | 68.80% | +6.20 pt |
| | | 6m | 8 | 8 | 75.00% | 73.44% | +1.56 pt |
| | | 12m | 8 | 6 | 62.50% | 79.46% | −16.96 pt |
| traded | 0.05 (54 ep., censored {1:1,3:1,6:2,12:3}) | 1m | 53 | 50 | 67.92% | 62.31% | +5.61 pt |
| | | 3m | 53 | 36 | 73.58% | 68.33% | +5.25 pt |
| | | 6m | 52 | 29 | 78.85% | 72.96% | +5.89 pt |
| | | 12m | 51 | 17 | 78.43% | 78.49% | −0.06 pt |
| traded | 0.10 (31 ep., censored {1:1,3:1,6:2,12:3}) | 1m | 30 | 28 | 63.33% | 62.31% | +1.02 pt |
| | | 3m | 30 | 25 | 76.67% | 68.33% | +8.33 pt |
| | | **6m** | **29** | **22** | **86.21%** | **72.96%** | **+13.25 pt** |
| | | 12m | 28 | 15 | 78.57% | 78.49% | +0.08 pt |
| traded | 0.15 (12 ep., censored all 0) | 1m | 12 | 12 | 83.33% | 62.31% | +21.02 pt |
| | | 3m | 12 | 12 | 75.00% | 68.33% | +6.67 pt |
| | | 6m | 12 | 11 | 75.00% | 72.96% | +2.04 pt |
| | | 12m | 12 | 11 | 66.67% | 78.49% | −11.82 pt |
| traded | 0.20 (8 ep., censored all 0) | 1m | 8 | 8 | 50.00% | 62.31% | −12.31 pt |
| | | 3m | 8 | 8 | 75.00% | 68.33% | +6.67 pt |
| | | 6m | 8 | 8 | 75.00% | 72.96% | +2.04 pt |
| | | 12m | 8 | 6 | 62.50% | 78.49% | −15.99 pt |

### Intervals on the 6-month excess, 10% threshold, by construction

All at **90%** (the level the criterion names), 2,000 draws, seed 20260917 (the module
default). **The construction is stated beside every interval, because the previous task found
that they do not agree and this one confirms it.** `block_bootstrap` could not be applied to
the episode-level statistic — it resamples a **daily series** in contiguous blocks and refuses
fewer than three whole blocks (`block_bootstrap_min_blocks` = 3), so 19 or 22 episode outcomes
cannot be blocked at 126 days. **This is the same deviation the previous task reported and
handled the same way:** `bootstrap_trades` on the episode side with its overlap caveat stated,
`block_bootstrap` on the unconditional side where the data genuinely is a daily series.

| range / basis | construction | estimate | 90% interval | contains zero |
|---|---|---|---|---|
| **primary** (adjusted, to 2024-12-31) | **A** · `bootstrap_trades(overlapping=True)` on all 26 episodes, baseline held fixed | **+12.35 pt** | **[+0.82, +23.89]** | **no** |
| | **B** · `bootstrap_trades(overlapping=False)` on the 19 non-overlapping, baseline fixed | **+17.21 pt** | **[+6.69, +27.74]** | **no** |
| | **C** · `block_bootstrap(block=126)` on the unconditional daily up-indicator | 72.26% | [63.30, 79.89] | n/a (a rate) |
| | **D(A)** · A with the baseline's own interval admitted, worst corners | **+12.35 pt** | **[−6.81, +32.86]** | **yes** |
| | **D(B)** · B with the baseline's own interval admitted, worst corners | **+17.21 pt** | **[−0.94, +36.70]** | **yes** |
| primary (traded) | A | +12.83 pt | [+1.29, +24.36] | no |
| | B | +17.68 pt | [+7.16, +28.21] | no |
| | D(A) | +12.83 pt | [−6.35, +33.32] | yes |
| | D(B) | +17.68 pt | [−0.48, +37.16] | yes |
| extended (adjusted) | A | +12.77 pt | [+2.42, +23.11] | no |
| | B | +17.47 pt | [+8.38, +26.56] | no |
| | D(A) | +12.77 pt | [−4.34, +31.36] | yes |
| | D(B) | +17.47 pt | [+1.62, +34.81] | **no** |
| extended (traded) | A | +13.25 pt | [+2.90, +23.59] | no |
| | B | +17.95 pt | [+8.86, +27.04] | no |
| | D(A) | +13.25 pt | [−3.86, +31.91] | yes |
| | D(B) | +17.95 pt | [+2.10, +35.35] | **no** |

At **95%** on the primary range, construction A becomes **[−3.03, +23.89]** and contains zero;
B stays positive at **[+1.42, +27.74]**. The criterion names 90%, so the 95% figures are
context and not the test.

**The other three thresholds' 6-month excess intervals, at 90%, adjusted, primary range:**

| threshold | 6m sample / effective | construction A | construction B |
|---|---|---|---|
| 0.05 | 48 / 25 | +6.91 pt [−3.51, +15.24] — **contains zero** | +7.74 pt [−4.26, +19.74] — **contains zero** |
| 0.15 | 11 / 10 | +0.47 pt [−26.81, +18.65] — **contains zero** | +7.74 pt [−12.26, +27.74] — **contains zero** |
| 0.20 | 7 / 7 | **refused by the platform** | **refused by the platform** |

The refusal is verbatim: *"7 usable values cannot support a bootstrap interval; at least 10 are
needed. A number from fewer observations should be reported as the observations, not as a
statistic with a range"* (`bootstrap_min_observations` = 10). So **at the 20% threshold the
honest output is the seven episodes themselves**:

| crossed | 6m return | finished up | deepest drawdown after |
|---|---|---|---|
| 2000-04-12 | −16.24% | no | −21.47% |
| 2008-02-05 | +5.33% | yes | −6.00% |
| 2008-10-30 | +7.06% | yes | −23.72% |
| 2018-12-21 | +26.18% | yes | −2.51% |
| 2020-03-12 | +54.18% | yes | −6.79% |
| 2022-03-14 | −7.40% | no | −15.26% |
| 2022-12-27 | +38.43% | yes | −1.46% |

(The extended range adds **2025-04-04, +44.11%, up**.)

### The pre-registered criterion, limb by limb

> **Abandon if** the 6-month excess's bootstrap 90% interval contains zero **on the 10%
> threshold**, **or** the 6-month horizon fails to be the largest excess in **two or more** of
> the four threshold runs.

**Limb 1 — the interval. Cannot be settled as written.** The criterion names the statistic, the
threshold and the level, but **not which bootstrap construction**, and the four do not agree:

| construction | primary (adjusted) | primary (traded) | extended (adjusted) | extended (traded) |
|---|---|---|---|---|
| A · all episodes, baseline fixed | does **not** fire | does not fire | does not fire | does not fire |
| B · non-overlapping, baseline fixed | does **not** fire | does not fire | does not fire | does not fire |
| D(A) · both sides resampled | **FIRES** | **FIRES** | **FIRES** | **FIRES** |
| D(B) · both sides resampled | **FIRES** | **FIRES** | does not fire | does not fire |

This is the ambiguity the previous finding flagged before this run existed, and it is still
unresolved. **This session does not pick a construction and does not revise the criterion.**
Settling which construction limb 1 means is a new plan entry with its own date, and it is a
person's call. What can be said without choosing: **the excess is positive on every
construction and every basis, and its sign survives only while the unconditional baseline is
treated as known. It is not known** — construction C gives it a 16.6-point interval of its own.

**Limb 2 — the ranking. FIRES, on all four basis × range combinations.** The 6-month horizon
had to be the largest excess in at least 3 of the 4 threshold runs:

| range / basis | 0.05 | 0.10 | 0.15 | 0.20 | 6m largest in | limb 2 |
|---|---|---|---|---|---|---|
| primary, adjusted | 3m (+7.02 vs 6m +6.91) | **6m** | 1m (+18.99 vs 6m +0.47) | 3m (+2.94 vs 6m −0.83) | **1 of 4** | **FIRES** |
| primary, traded | 3m (+7.50 vs 6m +5.29) | **6m** | 1m (+19.50 vs 6m +0.94) | 3m (+3.42 vs 6m −0.36) | **1 of 4** | **FIRES** |
| extended, adjusted | **6m** | **6m** | 1m (+20.53 vs 6m +1.56) | 3m (+6.20 vs 6m +1.56) | **2 of 4** | **FIRES** |
| extended, traded | **6m** | **6m** | 1m (+21.02 vs 6m +2.04) | 3m (+6.67 vs 6m +2.04) | **2 of 4** | **FIRES** |

**So the pre-registered criterion is met and entry 1 is abandoned.** The hypothesis's first
clause (6-month excess at the 10% threshold at least +5 points) **is** satisfied on the point
estimate — +12.35 pt primary, +12.77 pt extended — though every interval above also spans
values below +5. Its second clause, that the 6-month peak is not an artefact of the 10%
threshold, **fails**: at 15% and 20% the 6-month excess is +0.47 pt and −0.83 pt, which is to
say nothing at all, while the 1-month row at 15% is +19 pt and the 12-month row is −14 pt.
**The 6-month concentration is a property of the 10% threshold and does not generalise across
threshold depth.**

**What limb 2 actually rests on, which matters as much as the verdict.** The cells that make it
fire are the smallest in the study, and the ranking inside them turns on one to three episodes:

| threshold (primary, adjusted) | 6m n | one episode is worth | gap to the leading horizon | episode flips needed to make 6m the largest |
|---|---|---|---|---|
| 0.05 | 48 | 2.1 pt | 0.1 pt | **1** |
| 0.10 | 26 | 3.8 pt | 0.0 pt (6m already leads) | 0 |
| 0.15 | 11 | 9.1 pt | 18.5 pt | **3** |
| 0.20 | 7 | 14.3 pt | 3.8 pt | **1** |

And **the four threshold runs are not four independent trials**: all 7 of the 20% episodes fall
inside an open 15% episode, 8 of the 11 15% episodes fall inside an open 10% episode, and 24 of
the 27 10% episodes fall inside an open 5% episode. They are four nested views of largely the
same history, so "2 or more of 4" is not two or more independent failures.

### Concentration

The P&L cuts in `core/thresholds.py` (`single_trade_share` 0.35, `one_year_share` 0.70) are
defined on P&L and do not apply to a proportion. Their analogue, at the 10% threshold, primary
range, 6-month horizon (26 episodes):

- **One episode is worth 3.8 points.** The excess is +12.35 pt, so **about three episodes carry
  the whole result**; at 15% one episode is 9.1 points and at 20% it is 14.3.
- **The four 6-month losers are 2000-03-15, 2000-04-03, 2015-08-21 and 2022-01-20.** Two of the
  four are three weeks apart in the same crash.
- **Arrivals are clustered.** Of the 27 episodes at 10%, **6 fall in 1999-2000**; 2001, 2002,
  2003, 2009, 2013, 2014 and 2017 contribute none.
- **Leave-one-era-out, 6-month excess (primary range):** drop 1999-2000 → **+17.7 pt** (n=20);
  drop 2008 → **+11.7 pt** (n=25); drop 2020 → **+11.1 pt** (n=24). No single era carries the
  sign at the 10% threshold, which is the one robustness check here that comes out well.

## Caveats

- **The sample is too small for the claim the criterion is being asked to decide, and that is
  the first caveat rather than the last.** Limb 2 is a ranking of four excesses computed on 7
  to 49 nested episodes; at the two thresholds that make it fire, the platform itself refuses
  an interval (n=7) or returns one spanning 45 points (n=11). **"The 6-month horizon is not the
  largest at a 20% threshold" is not a finding about the market; it is a statement about seven
  episodes**, five of which fall in 2008, 2020 and 2022. The verdict above is what the
  pre-registered criterion says on the numbers obtained. It is **not** evidence that the
  6-month window is absent at deeper thresholds — it is evidence that this data cannot resolve
  the question at deeper thresholds, which is a different statement and is why it is in the
  verdict and not only here.
- **Half the pre-registered test could not be run.** Entry 1 declared 4 thresholds × 2
  underlyings. SPY is reserved (`docs/decisions/0001` § 1), so 4 of the 8 trials do not exist
  and the hypothesis's "another underlying" clause is **unevaluated** — not failed, not
  confirmed. `research-questions.md` 9.5 wants the effect to hold on each underlying
  separately; that check is unavailable until the final evaluation, by design.
- **The sub-period half named in plan § 3 was not run.** § 3 says entry 1 attacks whether the
  6-month concentration survives "other thresholds, another underlying and **sub-periods**",
  but entry 1's *test* section prescribes no sub-period split and sets no criterion for one.
  Running one would have been choosing a test that was not pre-registered. The two ranges
  reported here are not a sub-period split — both start in 1999. The leave-one-era-out figures
  are descriptive and have no criterion attached. **A sub-period test needs a new plan entry
  with its own date.**
- **This is a robustness check and cannot be made into an out-of-sample test.** The 1999-2026
  period has already been looked at for this exact question — `research-questions.md` § 2.1
  *is* that look. The honest caveat entry 1 itself requires is carried here verbatim in spirit:
  this cannot be made out of sample, and QQQ has one price history which has been seen.
- **`study()`'s own sample warning is silent at the 0.10 and 0.15 thresholds** because
  `_sample_warning` fires only when `effective ≤ 8` or `sample > 2 × effective`. At 11 episodes
  and 10 effective, the 15% threshold trips neither and still cannot support the ranking it is
  being used to decide. **Silence from the tool is not a statement about the sample.**
- **The 12-month row's sign is not determined by the data** at any threshold: −1.06 pt
  (adjusted) against −0.01 pt (traded) at 10% on 14 non-overlapping observations. "Gone by 12
  months" is supported on every basis and threshold; the decimal is not.
- **End-of-range censoring differs by horizon, so the horizons are not computed on the same
  episodes.** Primary range at 10%: `{1: 0, 3: 0, 6: 1, 12: 1}`; extended at 10%:
  `{1: 1, 3: 1, 6: 2, 12: 3}`. Comparing excesses across horizons — which is exactly what limb
  2 does — compares slightly different samples. At 15% and 20% nothing is censored on the
  primary range, so limb 2's two deciding cells are at least internally consistent.
- **Horizons are trading days, not calendar months** (`DAYS_PER_MONTH = 21`, so 6 months is 126
  sessions), while `effective_sample` measures overlap in **calendar** days (`months × 30`).
  The mismatch is the platform's and is already on record in
  `docs/specs/effective-sample-returns-its-subset.md`.
- **The non-overlapping subset was re-implemented outside the platform** to compute
  construction B, because `effective_sample` returns a count and not the subset. The
  re-implementation is asserted equal to the platform's count on every cell it was used for.
  This is the gap already specified in `docs/specs/effective-sample-returns-its-subset.md`; no
  new spec is needed and none is written.
- **How many configurations were tried.** Sixteen `study()` calls: 4 thresholds × 2 price bases
  × 2 date ranges, **all of them reported above**, plus interval constructions A/B/C/D at two
  levels on the 10% threshold and at one level on the others. Nothing was tried and dropped.
  The four thresholds, the four horizons and the 90% level are the plan's; the two bases were
  forced by the previous finding; the two ranges are the plan's own range and the range the
  previous finding used. **4 trials were recorded** under one idea tag, which is the count of
  thresholds — the figure a person must reconcile with the sixteen configurations when reading
  any of these numbers as a discovery.
- **Not a trading result.** No option is priced anywhere in this finding. An 86% chance of
  being higher in six months says nothing about what a call bought on the crossing day would
  have returned, because that depends on the volatility surface, which the synthetic chain does
  not have.

## What it changes

**For plan entry 1: it is abandoned by its own criterion, and the record should say which limb
and on what sample.** Limb 2 fires on all four basis × range combinations. Nothing here needs
re-running to reach that conclusion, and this session does not revise the criterion to avoid
it. Two things follow that are a person's to decide, not this session's:

1. **Limb 1 is unsettleable as written** and should be closed rather than left open — the four
   constructions disagree and the criterion names none of them. Whichever is chosen, it is a
   **new entry with its own date**, and entry 1 stays on the record saying what was expected.
2. **Limb 2's verdict was decided by cells of 7 and 11 nested episodes**, where the platform
   refuses an interval and one to three episode flips reverse the ranking. Whether a ranking
   criterion evaluated on such cells is the right test is exactly the judgement the role rules
   say to hand back. **If the answer is no, that is a new entry too** — and the finding that
   the 10% threshold's 6-month excess is the only cell in the study with a sample worth a
   statistic should be carried into it.

**For `research-questions.md` § 2.1 — one correction beyond the three the previous finding
already asked for**, for `doc-keeper` or the owner: § 2.1's result is specific to a **10%**
threshold, and this is now measured. At 15% the 6-month excess is +0.5 pt and at 20% it is
−0.8 pt; the striking figure at those depths is the **1-month** excess (+19 to +21 pt at 15%),
on 11 to 12 episodes. Any statement of the form "after a correction, six months is the window"
should carry "at a 10% threshold" or it is not supported.

**For the strategy: nothing.** The one cell with a sample worth quoting is QQQ, 10% threshold,
6-month horizon: 26 episodes, 19 non-overlapping, excess +12.35 pt, 90% interval
[+0.82, +23.89] with the baseline held fixed and containing zero once the baseline's own
uncertainty is admitted on three of four constructions. That rests on about three episodes'
worth of margin, in one index, over a history that has already been looked at, with no
out-of-sample stage available today. **No position, sizing, entry or exit decision should
change on the strength of this file**, and in particular the plan's wider claim — that the
6-month window survives threshold depth — has now been tested and does not.

**No new platform gap was found.** `bootstrap_trades`' refusal below 10 observations and
`block_bootstrap`'s refusal below three blocks are the platform working correctly, and the one
genuine gap (`effective_sample` returning a count and not its subset) is already specified.
Nothing under `backtest/`, `app/` or `tools/` was edited, and no spec was written.
