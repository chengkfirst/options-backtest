# Does any regime dimension separate QQQ's forward 21-session return?

**Date:** 2026-11-08
**Answers:** `docs/research/plan.md` § 5, **Entry 2** (hypothesis, test and abandonment
criterion all dated 2026-11-06, before any number existed)
**Verdict:** **One dimension of three separates the forward return well beyond noise, and
entry 2's criterion still cannot be settled — because the answer is decided by a sample
start the criterion never fixed, and that start rests on a false statement about the data.**
The **volatility term structure** (VIX3M/VIX below 1) separates clearly on **2011 onward**:
the inverted bucket's mean 21-session forward return is **+4.37%** against **+1.33%** normal,
a gap of **+3.04 percentage points**, 90% interval **[+1.30, +4.80]**, same sign in both
halves with neither half's interval crossing zero, stable from +2.59 to +3.44 under
leave-one-year-out, and above all 50 draws of a **year-stratified** random-entry control of
the same size. But **the plan's reason for starting at 2011 is wrong**: it says "VIX3M has no
data before 2011", and the series this platform actually loads **begins 2006-07-17**. On that
longer range the same gap is **+1.25 pt** with a 90% interval of **[−0.84, +3.09]** that
contains zero — **below the pre-registered 1.5 pt floor** — because in the removed stretch
(2006-07-17 to 2010-12-31) the sign **reverses**: inverted **+0.44%** against normal
**+1.28%**. The other two dimensions do not survive: **volatility level** clears the floor
only on 2011 onward (+1.75 pt) and is **+0.46 pt with a sign that reverses across the split**
on its own full range; **trend** clears the floor on no range at all and reverses across the
split on 2011 onward. Two further things travel with every number below: **299 "inverted"
sessions are 54 non-overlapping 21-session windows**, so the pre-registered "200 sessions"
floor is met on a count that overstates the independent sample about five-fold; and **the
three dimensions are not three independent trials** — 130 of those 299 sessions carry all
three labels at once.

---

## Data

- **Source.** `backtest.data.ingest.fetch_ohlcv("QQQ", ...)` (yfinance) through
  `backtest.data.store.ParquetCache`, built with `backtest.data.underlying.build_bars`; the
  cross-market panel from `backtest.data.multi_asset.load_cross_market` (same cache, same
  source). No series was unavailable: `CrossMarket.unavailable` is empty and
  `RegimeSet.unavailable` is empty, so **no dimension was dropped**.
- **Symbol: QQQ only.** **No SPY run of any kind.**
  `docs/decisions/0001-the-hold-out-part-b-and-the-option-data.md` § 1 reserves SPY until
  the final check, and that reservation is held by the document alone today (`HoldOut` takes
  dates, not symbols). Entry 2 asks for QQQ only, so nothing here is unevaluated for want of
  SPY — unlike entry 1.
- **Ranges.** First QQQ bar **1999-03-10**, last **2026-11-06**, 6,960 sessions. A forward
  21-session return exists up to **2026-10-08**, so every table below ends there (the last 21
  sessions are the data-end censoring of this statistic, and they are excluded rather than
  partially filled).
- **No data after 2026-11-07.** The fetch range ends 2026-11-07 and the last bar in it is
  2026-11-06, so the period hold-out `HoldOut(2026-11-08, 2099-12-31)` was never reached and
  no `unlock=` was needed or passed.
- **Option chain: none.** Not the synthetic one, not any other. No option was priced anywhere
  in this finding, and `run()` was never called. **What this finding therefore cannot claim:**
  nothing here supports any option-side conclusion — not which delta to buy, not which expiry,
  not where a take-profit or a stop belongs, not whether entering on an inverted term structure
  is profitable in premium terms. The synthetic chain's IV is one constant per day and expiry
  with no skew and **no term structure of its own**, which makes the irony worth stating
  plainly: this finding is about the *market's* volatility term structure, measured from the
  VIX3M/VIX index ratio, and the platform's option chain has no term structure at all. The
  step from "QQQ rose 4.4% over the next 21 sessions" to "a call bought that day made money"
  is **not taken anywhere in this file and is not supported by it.**

### The one data constraint the entry names, checked rather than assumed

The entry says: *"VIX3M has **no data before 2011** (the series' own caveat), so the
term-structure dimension's sample is 2011 onward and must be reported on that range, not
silently on the full one."* The caveat it refers to is in
`backtest/data/multi_asset.py`'s `SERIES["vix3m"]`: *"its ratio to VIX is the term structure;
**no data before 2011**"*.

**Both are wrong, and the error is load-bearing.** Fetched over 1998-02-04 to 2026-11-07,
`^VIX3M` returns **5,111 observations whose first date is 2006-07-17**. The derived
`term_structure` column has the same 5,111 observations and the same first date. The first
date at which `regime.classify` assigns the dimension a bucket is therefore **2006-07-17**,
not 2011-01-03 — confirmed from the platform's own `RegimeSet.frame`, not from the fetch.

So the 2011 start is not forced by data availability. It is a four-and-a-half-year choice
that nothing in the data requires, and § 5 of the plan presents it as forced. **That choice
decides entry 2's verdict** (see Result). The two statements above are `doc-keeper`'s and the
owner's to correct; this session may not edit either file and did not.

For completeness the other two dimensions' first labelled dates, from the same frame: VXN
begins **2001-01-23** (volatility level), and the 200-day average of the adjusted close is
first available **1999-12-21** (trend). The module does not silently drop the earlier
sessions — it labels them `NaN`, which `RegimeSet.on` renders `"unknown"`: 768 unknown
sessions for volatility level, **2,165** for the term structure, 488 for trend, in a 7,283-row
frame. That is the safeguard which makes a range omission visible rather than silent, and it
is why the ranges below are explicit.

### Price basis

`UnderlyingBars` carries `adjusted()` ("corrected for splits and dividends, **for signals and
returns**") and `traded()`. A forward return is a return, so **adjusted is primary** —
prescribed by the module, not picked here. **`traded` is reported beside it for every cell**,
because the two earlier findings of 2026-11-07/08 showed the basis can move a sign and that
the standing documents fix no basis. **No conclusion below differs between the two bases**;
the term-structure gap is +3.043 pt adjusted and +3.044 pt traded on the primary range, so no
basis was selected.

One asymmetry has to be stated: **the trend dimension's own classification is adjusted-only
and cannot be put on a traded basis through the public API.** `regime.classify` computes
`trend_200` from `bars.adjusted("close")` internally with no argument for it, so in the
"traded" rows only the forward return changes basis, not the bucket boundary. That is the
platform's choice and the right one for a trend measure; it is recorded so the traded column
is not read as a full basis swap for that one dimension.

## Method

**No `run()` call, and that is not a workaround.** This question needs no engine, no strategy,
no regime *condition* and no option price — it is an event study of the underlying, which is
what entry 2 prescribes ("No strategy and no regime condition at all"). `run()` cannot express
it, and nothing here reaches inside an engine: every number comes from
`data.multi_asset.load_cross_market`, `signals.setup.regime.classify`,
`signals.setup.regime.stratify`, `research.validation.intervals`, `research.baselines` and
`research.events`, all public.

```python
from datetime import date
import numpy as np, pandas as pd
from backtest.data.ingest import fetch_ohlcv
from backtest.data.multi_asset import load_cross_market
from backtest.data.store import CacheKey, ParquetCache
from backtest.data.underlying import build_bars
from backtest.research.baselines import random_entry
from backtest.research.validation.intervals import block_bootstrap, bootstrap_trades
from backtest.signals.setup.regime import DEFAULT_DIMENSIONS, classify, stratify

START, END, HORIZON = date(1999, 3, 10), date(2026, 11, 7), 21
cache = ParquetCache()
key = CacheKey("ohlcv", "QQQ", {"start": START, "end": END,
                                "source": "yfinance", "prices": "raw+adj"})
bars = build_bars("QQQ", cache.get_or_load(key, lambda: fetch_ohlcv("QQQ", START, END)))
panel = load_cross_market(START, END, cache=cache).frame
regimes = classify(panel, bars, DEFAULT_DIMENSIONS)          # the three declared dimensions

close = bars.adjusted("close")                               # PRIMARY basis
fwd = (close.shift(-HORIZON) / close - 1.0).dropna()         # forward 21-session return

for dim in DEFAULT_DIMENSIONS:                               # 3 dimensions = 3 trials
    lo = regimes.frame[dim.name].dropna().index[0]           # the dimension's own first label
    idx = fwd.index.intersection(regimes.frame.index)
    idx = idx[idx >= lo]                                     # or >= 2011-01-01, declared below
    frame = pd.DataFrame({"entry_date": idx, "net_pnl": fwd.reindex(idx).to_numpy()})
    print(stratify(frame, regimes, by=dim.name, min_trades=200))   # 200 = the entry's floor
    # then the same two calls on idx <= 2017-06-30 and idx > 2017-06-30
# SECONDARY: swap bars.adjusted for bars.traded, and/or shift the slice. Nothing else.
```

**The thresholds are the module's, read from it and not retyped.** `DEFAULT_DIMENSIONS`
gives volatility level on `vxn` at **(0.18, 0.28)** → `low vol / normal / high vol`; term
structure on `term_structure` (VIX3M/VIX) at **(1.0,)** → `inverted / normal`; trend on
`trend_200` at **(0.0,)** → `below the 200-day / above the 200-day`. They match the plan's
statement of them exactly — **no discrepancy to report** — and each carries its own
`rationale` saying it was not fitted. **No threshold, bucket edge, horizon, split date or
window was moved at any point in this session.**

**`stratify` on sessions rather than trades, which the entry prescribes and the module does
not name.** `stratify` requires columns `entry_date` and `net_pnl` and returns columns
`trades`, `win rate`, `mean per trade`, `total` and `worst trade`. Here the unit is a
**session** and the quantity is a **forward 21-session return**, so `trades` is a session
count, `mean per trade` is the mean forward return, and `total` is meaningless and is not
quoted anywhere below. `min_trades=200` was passed so the module's `sample` label reads
against the entry's pre-registered 200-session floor rather than its 15-trade default; that
changes a label and no number. Every bucket's count and mean was also computed independently
and **asserted equal to the platform's to within 1e-12 on all 24 cells** — the script fails
rather than reports if they ever disagree.

**Declared ranges, and why there are three.** The entry fixes the gap floor (1.5 pt), the
bucket floor (200 sessions), the horizon (21 sessions) and the split date (2017-06-30), and
**fixes no sample start or end.** Three ranges are therefore reported in full for every
dimension, and the order they were computed in is on the record because it matters:

| name | range | where it comes from | computed |
|---|---|---|---|
| **own availability** | each dimension's own first labelled date → 2026-10-08 | the range the data permits | first |
| **2011-2024** | 2011-01-03 → 2024-12-31 | the entry's *smallest credible experiment* line ("the volatility-level dimension alone, on QQQ, 2011–2024"); also the range for which 2017-06-30 is almost exactly the midpoint | first |
| **2011 onward** | 2011-01-03 → 2026-10-08 | the entry's *data* line, read literally: "the term-structure dimension's sample is 2011 onward" — which names no end | **second, after the first two had been seen** |

The third range was added after the first two produced numbers. It is reported because the
entry's own data note names it and running it completes the pre-registered test rather than
shopping for a result; it is flagged here, in the method, rather than in a footnote, because
a range added mid-session is exactly the move this role is supposed to declare. It is the
range on which the hypothesis passes most clearly — so the reader is entitled to know it was
not among the first two, and to weigh that.

A fourth, **2006-07-17 → 2010-12-31**, is the complement of the 2011 cut. It is reported for
the same reason a leave-one-era-out is: it says what the removed stretch contains, and it was
computed only after the discrepancy about VIX3M's start was found in the data.

**One robustness variant, not a tuning knob.** The forward return runs from the close of the
classification day, which no strategy could trade — `execution_lag_days` is 1 by default. The
whole set was therefore recomputed on the forward return from **t+1 to t+22** on the adjusted
basis for the "own availability" and "2011-2024" ranges. **It changes no conclusion** and is
quoted only where it changes a *sign on a gap already indistinguishable from zero*, which it
does once and which is itself the point (see the trend row).

**Guard state:** `backtest.core.run_id.guards_were_green()` →
`{'state': 'green', 'when': '2026-11-07T23:44:15', 'note': 'the guards passed on this exact
code'}`. **Green.** Code version `c4f003a+dirty`. The record matches by version string, and
the only uncommitted files are under `docs/` and `research/` — **nothing under `backtest/`,
`app/` or `tools/` was touched by this session** — so the green record covers the code that
produced these numbers. `python -m tools.check` was **not** run: no code changed, and it
rewrites a shared guard artefact.

**Trials recorded** through `backtest.research.tracker.count_trials`, one idea tag:
`"regime dimensions vs QQQ forward 21-session return (plan entry 2)"`.

- **As the plan declared it:** three dimensions, plus the two-half split as a grouping.
  `Trials.total = 4` (3 distinct + 1 grouping). `run_id`s `a3f55d4c5d3aff37`,
  `ed14a5b2924c3d5c`, `1f41c742bc46d835`.
- **As it was actually computed:** **`Trials.total = 28`** — 27 distinct `(dimension, range,
  price basis, execution lag)` configurations plus the one grouping. The 27 are listed in
  `research/2026-11-08-task-1c-entry2/trials.out` with their ids.
- **And a mistake of mine that belongs in the record because it is the exact failure
  `docs/findings/README.md` names.** The first pass built the trial records with the
  dimension, the horizon and the split in the `run_id` payload and **left the range out**. The
  two range variants of each dimension therefore hashed identically and the tracker reported
  *"3 distinct configurations; 3 reruns not counted again"* — "two configurations sharing one
  fingerprint make the search look smaller than it was". Since the **range turned out to be
  the field that decides this entry's verdict**, it was the worst possible one to omit. The
  record was rebuilt with range, basis and lag inside the payload, which is where the 27 comes
  from. Both counts are published; `docs/requirements.md` 4.3 is `partial` ("counted but
  unused"), so **the Deflated Sharpe does not consume either number and a person must carry
  it into the reading by hand**. Whether a range or a price basis is a *trial* is a method
  judgement this session may not make, so both the declared count and the raw one are given
  rather than a chosen one.

**Local run record:** `research/2026-11-08-task-1c-entry2/` — `probe.py`, `entry2.py`,
`entry2b.py`, `entry2c.py`, `entry2d.py`, `trials.py`, `halves_iv.py`, `uncond_iv.py`, each
with its `.out`. Not version controlled.

## Baselines

Five, because a bucket mean on its own says nothing.

1. **The unconditional mean, beside every conditional one.** Every table below carries
   `uncond_mean_pct` — the mean forward 21-session return of **every** session in the same
   range — and `uncond_positive_share_pct` beside every bucket's positive share. This is
   `conventions.md`'s hard line and it is in the tables rather than in a sentence.
2. **The unconditional mean's own interval.** `block_bootstrap(block=21)` on the full daily
   forward-return series of the range. On 2011 onward it is **+1.560%, 90% interval
   [+1.066, +2.068]** — a span of **1.00 pt**, which is of the same order as two of the three
   gaps being measured, and that comparison is the reason this baseline exists. The span is
   1.00 pt on 2011 onward, 1.02 pt on 2011-2024, 0.97 pt on 2006-07-17 onward and 1.01 pt on
   2001-01-23 onward — **the sample length barely narrows it**, because the binding constraint
   is the 20-session overlap between neighbouring forward returns and not the session count.
3. **Random entry, same count, same horizon, unstratified.** `research.baselines.random_entry`
   with a runner that returns the mean forward return of the drawn dates; 50 draws (the
   registry default `random_entry_draws`, not reduced); `n_entries` = the bucket's own session
   count. **This is the "structure without the signal" control** in the only form it can take
   when there is no strategy: the same number of entry dates, the same holding period, dates
   drawn at random instead of by the rule.
4. **Random entry, year-stratified** — the same call with `like=` the bucket's own dates, so
   `random_entry_dates` matches the draw's **calendar-year distribution** to the bucket's. This
   is the baseline that matters most here and the one that could have killed the result:
   regime buckets cluster heavily in time (183 of 626 "high vol" sessions are in 2020), so an
   unstratified draw compares market environments rather than timing. The module's docstring
   makes exactly that point, and this is the form it prescribes.
5. **Leave-one-year-out on the gap**, every year in the range, one at a time — the analogue of
   `one_year_share` (0.70), which is defined on P&L and does not apply to a mean return.

`delta_equivalent` does not apply: there is no option position. `r.baselines()` does not apply:
there is no run.

## Result

All figures are **adjusted basis**, forward 21 sessions, in percent. "effective" is the
largest subset of non-overlapping 21-session windows in that bucket (greedy, in time order).
`h1` is 1999-12-21/2001-01-23/2006-07-17/2011-01-03 → **2017-06-30**; `h2` is **2017-07-03** →
the range end.

### Per dimension, per bucket: count, effective, mean, and each half

| dimension | range | bucket | sessions | effective | mean | h1 n | h1 mean | h2 n | h2 mean | uncond mean |
|---|---|---|---|---|---|---|---|---|---|---|
| volatility level | own (2001-01-23→2026-10-08) | low vol | 2013 | 130 | **+0.867** | 1503 | +0.644 | 510 | +1.524 | +1.067 |
| | | normal | 2802 | 192 | **+1.324** | 1499 | +1.364 | 1303 | +1.278 | +1.067 |
| | | high vol | 1651 | 115 | **+0.874** | 1134 | **−0.049** | 517 | **+2.898** | +1.067 |
| volatility level | 2011 onward | low vol | 1515 | 98 | **+1.078** | 1005 | +0.851 | 510 | +1.524 | +1.560 |
| | | normal | 1824 | 130 | **+1.525** | 521 | +2.142 | 1303 | +1.278 | +1.560 |
| | | high vol | 626 | 52 | **+2.826** | 109 | +2.487 | 517 | +2.898 | +1.560 |
| volatility level | 2011-2024 | low vol | 1499 | 95 | **+1.075** | 1005 | +0.851 | 494 | +1.531 | +1.541 |
| | | normal | 1450 | 109 | **+1.658** | 521 | +2.142 | 929 | +1.387 | +1.541 |
| | | high vol | 573 | 44 | **+2.464** | 109 | +2.487 | 464 | +2.458 | +1.541 |
| term structure | own (2006-07-17→2026-10-08) | inverted | 553 | 80 | **+2.567** | 369 | +1.472 | 184 | +4.763 | +1.456 |
| | | normal | 4537 | 233 | **+1.321** | 2391 | +1.224 | 2146 | +1.428 | +1.456 |
| term structure | **2011 onward** | **inverted** | **299** | **54** | **+4.373** | 115 | +3.749 | 184 | +4.763 | +1.560 |
| | | **normal** | **3666** | **184** | **+1.330** | 1520 | +1.192 | 2146 | +1.428 | +1.560 |
| term structure | 2011-2024 | inverted | 260 | 48 | **+4.100** | 115 | +3.749 | 145 | +4.378 | +1.541 |
| | | normal | 3262 | 164 | **+1.337** | 1520 | +1.192 | 1742 | +1.464 | +1.541 |
| trend | own (1999-12-21→2026-10-08) | below the 200-day | 1754 | 107 | **+0.241** | 1349 | −0.183 | 405 | +1.653 | +0.929 |
| | | above the 200-day | 4986 | 259 | **+1.171** | 3061 | +0.838 | 1925 | +1.700 | +0.929 |
| trend | 2011 onward | below the 200-day | 600 | 41 | **+2.419** | 195 | +4.012 | 405 | +1.653 | +1.560 |
| | | above the 200-day | 3365 | 173 | **+1.406** | 1440 | +1.014 | 1925 | +1.700 | +1.560 |
| trend | 2011-2024 | below the 200-day | 543 | 37 | **+1.956** | 195 | +4.012 | 348 | +0.803 | +1.541 |
| | | above the 200-day | 2979 | 154 | **+1.465** | 1440 | +1.014 | 1539 | +1.888 | +1.541 |

Positive shares, every one with its unconditional rate for the same range, since a
conditional rate without one is not reportable here: 2011 onward — inverted **74.58%**
against an unconditional **67.59%**; normal 67.02% against 67.59%; high vol 67.25%; low vol
68.32%; below the 200-day 65.83%; above the 200-day 67.91%. On the own-availability ranges —
high vol **58.69%** against an unconditional 63.39%, below the 200-day **55.19%** against
62.46%. **The win rate and the mean do not point the same way for volatility level and trend
on the long range**: the frightened buckets are *less often* up and, on the long range,
*not* higher on average; on 2011 onward they are higher on average without being much more
often up, which says the gap lives in the size of the up moves and not in their frequency.

### The traded basis, same cells

Every gap on the traded basis, for comparison with the adjusted figures in the next table:
volatility level — own **+0.476**, 2011 onward **+1.778**, 2011-2024 **+1.416**; term
structure — own **+1.252**, 2011 onward **+3.044**, 2011-2024 **+2.765**; trend — own
**+0.910**, 2011 onward **+0.998**, 2011-2024 **+0.473**. **No limb of the criterion changes
on either basis**, and the full per-bucket traded table is in
`research/2026-11-08-task-1c-entry2/entry2d.out`.

### The best-worst gap, with its interval and its construction named

**Construction C** is the only one that can answer the question asked, so it is the one
quoted: `block_bootstrap(block=21)` over the **calendar session positions** of the range, with
**both bucket means recomputed inside every draw**, so the pair stays paired and the 21-session
overlap between neighbouring forward returns is kept inside a block. 2,000 draws, 90% (the
level entry 1 used; the entry names none), seed 20260917 (the module default). No draw was
degenerate: `nan_draws = 0` everywhere.

| dimension | range | gap (best − worst) | 90% interval, construction C | contains zero | both buckets ≥ 200 |
|---|---|---|---|---|---|
| volatility level | own | +0.457 (normal − low vol) | [−0.210, +1.133] | **yes** | yes (2802 / 2013) |
| volatility level | 2011 onward | +1.749 (high vol − low vol) | [+0.227, +3.307] | no | yes (626 / 1515) |
| volatility level | 2011-2024 | +1.389 (high vol − low vol) | [−0.259, +2.977] | **yes** | yes (573 / 1499) |
| **term structure** | own | **+1.247** (inverted − normal) | **[−0.838, +3.090]** | **yes** | yes (553 / 4537) |
| **term structure** | **2011 onward** | **+3.043** | **[+1.303, +4.799]** | **no** | yes (299 / 3666) |
| **term structure** | 2011-2024 | **+2.763** | **[+1.038, +4.426]** | no | yes (260 / 3262) |
| trend | own | +0.930 (above − below) | [−0.543, +2.422] | **yes** | yes (4986 / 1754) |
| trend | 2011 onward | +1.013 (below − above) | [−0.825, +2.950] | **yes** | yes (600 / 3365) |
| trend | 2011-2024 | +0.490 (below − above) | [−1.334, +2.421] | **yes** | yes (543 / 2979) |

Two other constructions were computed on every **bucket mean** and are in the run record.
They are reported as a pair rather than a choice, because they disagree by design and the
disagreement is the information: **A** = `bootstrap_trades(statistic=mean, overlapping=True)`,
which the module itself labels *"resampled as if independent; where they overlap in time this
interval is **too narrow**"* — for the 2011-onward inverted bucket it gives **+4.373%
[+3.41, +4.77]**; **B** = `block_bootstrap(block=21)` on the bucket's own returns in
chronological order, which gives **+4.373% [+2.50, +6.15]**, roughly four times as wide. B's
blocks are contiguous in *bucket-membership* order and therefore straddle the calendar gaps
where the regime changed, which is why C — resampling the calendar — is the construction
quoted for the gap. **No interval was refused anywhere in this finding**:
`bootstrap_min_observations` is 10 and `block_bootstrap_min_blocks` is 3, so the smallest
bucket here (260 sessions, 48 non-overlapping) clears both floors comfortably on the nominal
count. Whether it clears them on the *effective* count is a different question and is in the
caveats.

### The half-by-half gap, for the dimensions that reach the floor

| dimension (2011 onward) | full gap | first half | second half | sign reverses |
|---|---|---|---|---|
| term structure (inverted − normal) | +3.043 [+1.303, +4.799] | **+2.557 [+1.248, +3.958]** (115 / 1520) | **+3.335 [+0.602, +5.760]** (184 / 2146) | **no**, and neither half's interval crosses zero |
| volatility level (high vol − low vol) | +1.749 [+0.227, +3.307] | +1.635 [−0.133, +3.466] (109 / 1005) | +1.374 [−0.643, +3.451] (517 / 510) | no, but **both halves' intervals cross zero** |
| trend (below − above) | +1.013 [−0.825, +2.950] | +2.998 [+1.737, +4.331] (195 / 1440) | **−0.047** [−2.719, +2.462] (405 / 1925) | **yes** |

The term-structure halves are the only cells in this finding where a gap keeps its sign
*and* both halves' intervals exclude zero. Note what the counts cost: the inverted bucket's
halves hold **115 and 184 sessions**, both **below the 200-session floor** the entry imposes
on the full-period buckets. The entry sets no floor on the halves; this is stated so the
half-period figures are not read as carrying the same weight as the full-period one.

### What the 2011 cut removes — and the reason it decides the answer

2006-07-17 → 2010-12-31, 1,125 sessions, unconditional mean **+1.092%**:

| dimension | bucket | sessions | mean | 90% interval (construction B, `block_bootstrap(21)`) |
|---|---|---|---|---|
| term structure | **inverted** | 254 | **+0.442** | [−3.169, +3.487] — crosses zero |
| | normal | 871 | +1.281 | [+0.004, +2.295] |
| volatility level | high vol | 340 | +0.990 | [−1.992, +3.184] — crosses zero |
| | normal | 582 | +1.255 | [−0.372, +2.641] — crosses zero |
| | low vol | 203 | +0.794 | [−0.317, +1.959] — crosses zero |
| trend | below the 200-day | 372 | +0.902 | [−2.633, +3.341] — crosses zero |
| | above the 200-day | 753 | +1.185 | [−0.018, +2.198] — crosses zero |

In the removed stretch the inverted bucket is the **worst** bucket, not the best: the gap
runs **−0.839 pt** against the direction it runs on 2011 onward. Episode by episode (82
inverted episodes across 2006-07-17 onward, defining an episode as a run of inverted sessions
allowing gaps of up to five sessions), the **episode-level** mean of the per-episode mean
forward return is **+1.508%, 90% interval [−0.426, +3.334] on 23 pre-2011 episodes — crossing
zero** — against **+3.775%, [+2.676, +4.803] on 59 episodes from 2011 onward**. The single
heaviest pre-2011 episode is **2008-09-09 → 2008-12-24: 69 consecutive inverted sessions,
mean forward 21-session return −7.39%, worst −30.16%**. One episode, 69 sessions, and it is
most of what separates the two eras.

### Baselines: random entry at the same count and horizon

50 draws, adjusted, 2011 onward. "percentile" is the share of draws below the observed value.

| dimension | bucket | observed | unstratified random: median [5th, 95th] | pct | year-stratified random: median [5th, 95th] | pct |
|---|---|---|---|---|---|---|
| term structure | **inverted** (n=299) | **+4.373** | +1.660 [+1.105, +2.076] | **1.00** | **+1.351 [+0.792, +1.768]** | **1.00** |
| | normal (n=3666) | +1.330 | +1.561 [+1.515, +1.591] | 0.00 | +1.580 [+1.533, +1.617] | 0.00 |
| volatility level | high vol (n=626) | +2.826 | +1.514 [+1.290, +1.850] | 1.00 | +0.882 [+0.626, +1.200] | 1.00 |
| | low vol (n=1515) | +1.078 | +1.563 [+1.370, +1.753] | 0.00 | +1.780 [+1.666, +1.875] | 0.00 |
| trend | below the 200-day (n=600) | +2.419 | +1.510 [+1.182, +1.860] | 1.00 | +0.089 [−0.121, +0.339] | 1.00 |
| | above the 200-day (n=3365) | +1.406 | +1.558 [+1.500, +1.618] | 0.00 | +1.812 [+1.782, +1.849] | 0.00 |

The year-stratified column is the one that matters: it matches the draw's calendar-year
distribution to the bucket's, so it removes the "those were good years" explanation. On it,
the inverted bucket's +4.37% sits above all 50 draws of 299 same-year sessions whose median
is +1.35%. **The separation is not an artefact of which years the inverted days fall in.** It
is also not an artefact of a few extreme years: leave-one-year-out on the 2011-onward
term-structure gap ranges **+2.586 (dropping 2020) to +3.439 (dropping 2018)**, and on
2011-2024 **+2.145 to +3.170** — **no year's removal brings it below the 1.5 pt floor.** By
contrast the volatility-level gap on 2011-2024 falls to **+0.169 pt** when 2020 is dropped,
and the trend gap on 2011-onward falls to **+0.574** when 2011 is dropped and rises to
**+3.469** when 2022 is dropped.

Fifty draws resolve a percentile to 2 points, which is the registry's default and was not
changed; "1.00" means all fifty, not "significant at any particular level".

### The pre-registered criterion, limb by limb

> **Abandon if** no dimension produces a gap above **1.5%** with both buckets above **200
> sessions**; **or** any gap that does appear **reverses sign** across the 2017-06-30 split.

**Limb 1 — the gap floor. It fires or does not fire depending on the sample start, which the
criterion did not fix.**

| range | volatility level | term structure | trend | dimensions clearing 1.5 pt with both buckets ≥ 200 | limb 1 |
|---|---|---|---|---|---|
| own availability | +0.457 | +1.247 | +0.930 | **none** | **FIRES** |
| 2011-2024 | +1.389 | **+2.763** | +0.490 | 1 of 3 | does not fire |
| 2011 onward | **+1.749** | **+3.043** | +1.013 | 2 of 3 | does not fire |

Identical on the traded basis and on the lag-1 variant. **The entry's own data note names
"2011 onward", so on the range the entry prescribes, limb 1 does not fire.** On the range the
data permits, it does. The note's stated reason for 2011 is false (see Data), and the four
years it removes are the four in which the term-structure gap runs the other way.

**Limb 2 — the sign across the split. It has two readings and the criterion names neither.**

- **Reading (i), the words as written — "any gap that does appear".** Then every one of the
  three dimensions' best-worst gaps counts, and on **every** range at least one reverses:
  volatility level reverses on its own range (+0.720 → −0.246); trend reverses on 2011 onward
  (+2.998 → **−0.047**) and on 2011-2024 (+2.998 → **−1.085**) and on its own range **under
  the lag-1 variant** (+1.065 → −0.037). Under this reading **limb 2
  FIRES on all three ranges and entry 2 is abandoned regardless of the term-structure
  result.**
- **Reading (ii), "any gap that does appear" = any gap that qualified under limb 1.** Then the
  qualifying gaps are term structure (2011 onward, 2011-2024) and volatility level (2011
  onward), and **none of them reverses**. Under this reading limb 2 does not fire on the 2011+
  ranges, and the hypothesis survives.

**This session does not pick a reading and does not revise the criterion.** It will say one
thing about reading (i) and no more. The reversals that make it fire are, by range:
**−0.246 pt** (volatility level, own range), **−0.047 pt** (trend, 2011 onward) and
**−1.085 pt** (trend, 2011-2024). The middle one is the one that fires on **the range the
entry prescribes**, and its sign is set by a one-day shift in the execution assumption
(−0.047 at lag 0, and the lag-1 variant moves the own-range trend gap's second half from
+0.047 to −0.037 for the same reason), on an interval ±2.5 pt wide. A limb that fires on the
sign of a number four hundredths of a point from zero is reading the last digit of noise —
but the 2011-2024 reversal at −1.085 pt is **not** of that kind, so reading (i) is not
disposed of by this observation. Which reading limb 2 means is an argument about the
criterion, and that makes it a **new plan entry with its own date and a person's decision**,
not something to be settled here.

**So, limb by limb, on the numbers obtained:** limb 1 fires on the range the data permits and
does not fire on the range the entry prescribes; limb 2 fires on the broad reading of its own
words and does not fire on the narrow one. **Entry 2 cannot be settled as written.** What can
be said without choosing either: **the volatility term structure separates QQQ's forward
21-session return on 2011 onward by about 3 percentage points, and that separation can be told
from noise on every construction, in both halves, under leave-one-year-out and against a
year-matched random control. The other two dimensions cannot be told from noise on any range
that includes the data the platform actually has.**

### Three trials, and they are not three independent ones

The entry insists this be recorded: **stratifying on three dimensions is three trials, exactly
as sweeping three parameters would be.** It is recorded — and the stronger statement is that
they are **three views of one condition**, so the multiple-comparison correction a person
applies by hand should not treat them as three independent looks. On 2011 onward (3,965
sessions):

| conditional | value | its unconditional baseline |
|---|---|---|
| P(inverted \| high vol) | **0.294** (n=626) | P(inverted) = **0.075** |
| P(inverted \| below the 200-day) | **0.255** (n=600) | P(inverted) = **0.075** |
| P(high vol \| inverted) | **0.615** (n=299) | P(high vol) = **0.158** |
| P(below the 200-day \| inverted) | **0.512** (n=299) | P(below the 200-day) = **0.151** |

**130 of the 299 inverted sessions carry all three labels at once**, and 979 sessions carry at
least one. On the longer 2006-onward range the dependence is stronger still (P(high vol |
inverted) = 0.653 against 0.190). Only **5 of 1,515** low-vol sessions are ever inverted. All
three dimensions are measuring one thing — *the market has just fallen and is frightened* —
cut three ways, and on 2011 onward all three point the same direction: the frightened bucket
has the **higher** forward return.

## Caveats

- **299 sessions are not 299 observations, and the pre-registered floor is stated in the
  inflated unit.** The inverted bucket on 2011 onward holds 299 sessions but only **54
  non-overlapping 21-session windows** — the entry's "at least 200 sessions" floor is met on a
  count that overstates the independent sample by about **5.5×**. Every bucket in this finding
  has the same problem to a different degree (the table above gives `effective` for all 24).
  Construction C's block bootstrap keeps the overlap inside a block and is the reason its
  intervals are two to four times wider than construction A's; the intervals quoted above are
  therefore honest about the overlap even though the *counts* are not. **Had the entry's floor
  been written in non-overlapping windows rather than sessions, the term-structure bucket would
  fail it at 54.** This is the single largest caveat in the file and it is first for that
  reason.
- **The verdict turns on a four-and-a-half-year sample boundary that the criterion left open
  and the plan justified with a false fact.** Every other parameter of entry 2 was
  pre-registered and none was moved. The start date was not, and it is worth 1.8 percentage
  points of the term-structure gap and the whole of limb 1. A criterion that fixes the
  threshold and not the sample is not fully pre-registered, and that is the lesson here rather
  than anything about volatility.
- **The 2011-onward range was the third computed, not the first.** It is the range on which
  the hypothesis passes most clearly. It comes from the entry's own data note read literally,
  it is reported with the other two rather than instead of them, and the sequence is in the
  Method — but a reader should discount it by exactly the amount that sequence deserves.
- **The separation is a statement about 2011-2026 and reverses before it.** The inverted
  bucket's edge is +3.8% per episode after 2011 and +1.5% [−0.4, +3.3] before it, and the
  single 69-session inverted episode of autumn 2008 has a mean forward return of **−7.39%**.
  If "the regime where the front month is dearer than the three-month" is supposed to name a
  class of market that includes 2008, then this result does not hold on that class. If it names
  the post-2010 era's particular pattern of short sharp drawdowns bought back quickly, it does
  — and then it is an **episode-dependent** finding, not a structural one. That is a judgement
  about what the dimension means, and it is the owner's.
- **One era carries much of it, and the P&L concentration cuts do not apply.**
  `single_trade_share` (0.35) and `one_year_share` (0.70) are defined on P&L and have no
  meaning for a mean return; the analogue is the leave-one-year-out above. The term-structure
  gap survives every single-year removal, which is the one robustness check here that comes out
  unambiguously well. The volatility-level gap does not: dropping 2020 takes it from +1.389 to
  **+0.169** on 2011-2024, so **that dimension's result is about 2020 and 2022**, which hold
  183 and 193 of its 626 high-vol sessions.
- **How many configurations were tried.** **27 distinct stratification configurations** — 3
  dimensions × {3 ranges × 2 price bases, plus 2 ranges × the lag-1 variant, plus the
  2006-2010 complement} — **all of them reported above or in the run record, none tried and
  dropped**, plus 3 interval constructions, 2 random-entry forms × 50 draws, and a
  leave-one-year-out scan of 14 to 28 years per cell. The plan declared **3 trials plus the
  split**. Both numbers are published; neither is consumed by anything automatic.
- **`stratify` names a session a trade, and nothing in its output says otherwise.** Used as the
  entry prescribes — on the underlying's forward return rather than a strategy's P&L — it takes
  a column called `net_pnl`, reports a column called `trades` whose unit is a session, a `total`
  that is the sum of returns and is meaningless, and a `sample` verdict from a `min_trades`
  argument. The numbers are right (asserted equal to an independent computation on all 24
  cells); the **labels** are not, and a table lifted out of this run record would read as a
  trade study. A short spec is filed:
  `docs/specs/stratify-reports-its-unit-and-its-effective-sample.md`.
- **No look-ahead, stated because it is the easiest thing to get wrong here.** The regime label
  on session *t* uses the VXN, VIX3M/VIX and 200-day-average values **through t's close**; the
  forward return runs from *t*'s close to *t+21*'s close. `classify` forward-fills the panel,
  which carries past values forward and never future ones. The return therefore starts at the
  close of the day the signal is known, which **no strategy could trade** — the lag-1 variant
  (t+1 → t+22) is the implementable version and changes no conclusion, moving the
  term-structure gap from +1.247 to +1.370 on the own range.
- **This is not out of sample and cannot be made so.** QQQ has one price history; VXN and
  VIX3M have one each. No part of this is a hold-out. The period hold-out
  (2026-11-08 onward) holds nothing yet — it began today.
- **Not a trading result.** No option is priced anywhere in this finding, and no exit, sizing
  or selection rule appears in it.

## What it changes

**For plan entry 2: it cannot be closed on these numbers, and three things are a person's to
decide.** Stated as the three, rather than resolved here:

1. **The sample start.** Entry 2's data note says VIX3M begins in 2011 and it begins
   **2006-07-17**. Whether the term-structure dimension's range is 2011 onward (where the gap
   is +3.04 pt and clears the floor) or 2006-07-17 onward (where it is +1.25 pt and does not)
   is now an open, consequential choice that was never meant to be one. **It is a new plan
   entry with its own date**, and entry 2 stays on the record saying what was expected.
   Whichever is chosen, choosing it *after* seeing both numbers makes it a worse pre-registration
   than entry 2 was, and that cost should be acknowledged rather than absorbed.
2. **Limb 2's wording.** "Any gap that does appear" has a broad reading under which entry 2 is
   abandoned on every range and a narrow one under which it survives on two. The reversal that
   fires it on the entry's own prescribed range is **−0.047 pt**, a sign set by a one-day shift
   in the execution assumption; the one on 2011-2024 is **−1.085 pt** and is not.
3. **Whether a bucket floor stated in sessions means anything** when 299 sessions are 54
   non-overlapping windows. Every future stratification entry in the plan inherits this.

**For `docs/research/plan.md` § 5 entry 2 and `backtest/data/multi_asset.py`'s `SERIES`, for
`doc-keeper` and the owner:** both say VIX3M has no data before 2011. The platform loads 5,111
observations from **2006-07-17**. The plan's sentence is the more serious of the two, because it
converted a choice into a constraint inside a pre-registration. This session may not edit either
file and did not.

**For a strategy: nothing yet, and one thing it specifically does not license.** A dimension
separating the **underlying's** forward return is not the same as a regime filter being worth
something to a strategy, and nothing here was run through an engine. Three specific
non-conclusions:

- **This does not say a regime filter helps.** `run(setup=...)` is wired as the Armed gate, so
  the follow-up is two runs differing in one argument — and it needs a strategy, which Part B's
  gate says must be named by a person. Until then there is no P&L here to filter.
- **If anything, it points the opposite way to how a regime filter is usually written.** On
  2011 onward all three dimensions put the **higher** forward return in the frightened bucket:
  high vol +2.83% against low vol +1.08%, inverted +4.37% against normal +1.33%, below the
  200-day +2.42% against above +1.41%. A setup gate that disarmed in high volatility, on an
  inverted term structure or below the 200-day average would have been gating away the best
  21-session windows of that period. On the **longer** range it reverses for volatility level
  and trend, so the usual direction is right for 2001-2010 and wrong for 2011-2026. **The
  honest reading is that the sign of a regime filter's value is era-dependent**, which is a
  worse problem for a regime filter than either sign would be.
- **`research-questions.md` 10.5's condition for considering a statistical regime (HMM) has
  not been met.** That condition is performance separating on something **no threshold
  captures**. Here a threshold did capture something, on one of three dimensions, on one of
  three ranges — which is not that condition and is a far easier finding than it requires.
  Nothing in this file is an argument for fitting states.

**One platform gap, filed as a spec and not built:**
`docs/specs/stratify-reports-its-unit-and-its-effective-sample.md`. Nothing under `backtest/`,
`app/` or `tools/` was edited.
