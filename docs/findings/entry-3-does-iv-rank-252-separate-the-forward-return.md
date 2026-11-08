# Does `iv_rank_252` separate QQQ's forward 21-session return?

**Date:** 2026-11-08
**Answers:** `docs/research/plan.md` § 5, **Entry 3** (hypothesis, test and abandonment
criterion all dated 2026-11-06, before any number existed)
**Verdict:** **No — and the gap cannot be told from noise.** On QQQ over
2001-07-23 to 2026-10-08 (6,341 sessions), the lowest `iv_rank_252` quintile's mean
21-session forward return is **+1.164%** and the highest quintile's is **+1.608%**, a gap of
**−0.444 percentage points** with a 90% interval of **[−1.804, +0.992]** that contains zero.
**Both limbs of the pre-registered criterion fire.** Limb 1: |gap| = 0.44 pt is far **under
the 1.5 pt floor** — a quarter of it, and the floor is outside the interval on the correct
side, so this is not a near miss. Limb 2: the gap **flips sign across the 2017-06-30 split**
(**−1.190 pt** in the first half, **+1.106 pt** in the second), with both halves' intervals
crossing zero. **Entry 3 is abandoned on its own terms.** Two further things travel with
that: the sign of such separation as exists is **the opposite of the hypothesis's** — the
*highest* IV-rank quintile has the higher forward return, in both cuts and in a
year-matched random control — and **that direction is not what entry 3 pre-registered and is
not established here**; and `iv_rank_252` is **not an independent dimension** from entry 2's
volatility level, since P(high vol | highest quintile) = **0.544** against an unconditional
**0.241**.

---

## Data

- **Source.** QQQ daily OHLC from `backtest.data.ingest.fetch_ohlcv` through
  `backtest.data.store.ParquetCache`, assembled by `backtest.data.underlying.build_bars`.
  VXN from `backtest.data.multi_asset.load_cross_market` (`^VXN`, same cache, same source,
  scaled by 0.01). `CrossMarket.unavailable` is **empty**: no series was missing and nothing
  was dropped.
- **Symbol: QQQ only. No SPY run of any kind.**
  `docs/decisions/0001-the-hold-out-part-b-and-the-option-data.md` § 1 reserves SPY until
  the final check, and that reservation is held by the document alone today (`HoldOut` takes
  dates, not symbols). Entry 3 asks for QQQ only, so nothing here is unevaluated for want of
  SPY.
- **Option chain: none.** Not the synthetic one, not any other. No option was priced
  anywhere in this finding and `run()` was never called. **What this finding therefore
  cannot claim:** nothing here supports any option-side conclusion — not which delta, not
  which expiry, not where a take-profit belongs, and above all **not that buying options
  when IV rank is low is better or worse than buying them when it is high.** That is the
  conflation entry 3 names as the single most likely way the plan gets misread, and it has
  its own section below.
- **No data after 2026-11-07.** The fetch range ends 2026-11-07, the last QQQ bar in it is
  2026-11-06, and `research.holdout.check(HoldOut(2026-11-08, 2099-12-31), start=1999-03-10,
  end=2026-11-06)` returns `{'touched': False, 'note': 'the run ends before the hold-out
  … begins'}`. **No `unlock=` was needed or passed.**

### The one data claim the entry makes, checked rather than assumed

Entry 3's data line says: *"VXN begins 2001, so the sample is 2001 onward."* Entry 2 found
the plan's equivalent claim about VIX3M to be false by four and a half years, so this one
was checked from the data first.

**VXN's claim is correct, and the practical start is still six months later than the
sentence implies.** The series the platform loads holds **6,487 observations beginning
2001-01-23** and ending 2026-11-06 — "2001" is right. But `iv_rank_252` is
`indicators.rank_in_window(v, 252)`, whose `min_periods` is `window // 2 = 126`, so **the
signal's first value is 2001-07-23**, and a *full* 252-session window is first available on
**2002-01-28**. Between those two dates the "252-day" rank is computed on a window of 126 to
251 sessions. So:

- the range the data permits for this question is **2001-07-23 → 2026-10-08** (the end is
  the forward-return censoring: a 21-session forward return exists only to 2026-10-08), and
  that is the range every headline figure below is computed on;
- **126 of the 6,341 sessions carry a rank from a short window.** They are kept, because
  excluding them is a range choice the entry did not pre-register; the variant that excludes
  them is reported as a robustness check and **changes nothing that matters** (see Result).

This is a much smaller discrepancy than entry 2's and it changes no verdict. It is recorded
because a reader who takes "the sample is 2001 onward" literally will expect ~6,470 sessions
and 2001-01-23, and will get 6,341 and 2001-07-23. **This session may not edit the plan and
did not.**

### Price basis

`UnderlyingBars` carries `adjusted()` ("corrected for splits and dividends, **for signals
and returns**") and `traded()`. A forward return is a return, so **adjusted is primary** —
prescribed by the module, not picked here — and **`traded` is reported beside it on every
cell**, following entry 2. **No conclusion differs between the bases**: the headline gap is
−0.444 pt adjusted and −0.450 pt traded, and no limb of the criterion moves. The signal
itself is computed from VXN on both, so the bucket boundaries do not change with the basis;
only the forward return does.

## Method

**No `run()` call, and that is not a workaround.** Entry 3, like entry 2, is an event study
of the underlying: no strategy, no engine, no option price. `run()` cannot express "the mean
forward 21-session return of the underlying by signal quintile", and nothing here reaches
inside an engine — every number comes from `data.ingest`, `data.store`, `data.underlying`,
`data.multi_asset`, `signals.registry.SignalSet`, `signals.setup.regime`,
`research.validation.intervals`, `research.baselines`, `research.holdout` and
`research.tracker`, all public.

```python
from datetime import date
import numpy as np, pandas as pd
from backtest.data.ingest import fetch_ohlcv
from backtest.data.multi_asset import load_cross_market
from backtest.data.store import CacheKey, ParquetCache
from backtest.data.underlying import build_bars
from backtest.research.validation.intervals import block_bootstrap
from backtest.signals.registry import SignalSet
from backtest.signals.setup.regime import Dimension, classify, stratify

START, END, HORIZON = date(1999, 3, 10), date(2026, 11, 7), 21
SPLIT = pd.Timestamp("2017-06-30")                 # entry 2's split, which entry 3 cites
LABELS = ("Q1 lowest", "Q2", "Q3", "Q4", "Q5 highest")

cache = ParquetCache()
key = CacheKey("ohlcv", "QQQ", {"start": START, "end": END,
                                "source": "yfinance", "prices": "raw+adj"})
bars = build_bars("QQQ", cache.get_or_load(key, lambda: fetch_ohlcv("QQQ", START, END)))
cm = load_cross_market(START, END, cache=cache)              # ^VXN, already / 100
signals = SignalSet.from_bars(bars, vol_proxy=cm.frame["vxn"])   # the registry's own path
rank = signals.frame["iv_rank_252"]                          # = rank_in_window(VXN, 252)

close = bars.adjusted("close")                               # PRIMARY basis
fwd = (close.shift(-HORIZON) / close - 1.0) * 100.0          # percent
idx = pd.DatetimeIndex(sorted(rank.dropna().index.intersection(fwd.dropna().index)))

edges = tuple(float(x) for x in rank.reindex(idx).quantile([0.2, 0.4, 0.6, 0.8]))
dim = Dimension(name="iv_rank_252 quintile", source="iv_rank_252", thresholds=edges,
                labels=LABELS, rationale="equal-count quintiles, declared in advance")
regimes = classify(pd.DataFrame({"iv_rank_252": rank}), None, (dim,))
frame = pd.DataFrame({"entry_date": idx, "net_pnl": fwd.reindex(idx).to_numpy()})
print(stratify(frame, regimes, by="iv_rank_252 quintile", min_trades=200))
# then the same with thresholds=(0.2, 0.4, 0.6, 0.8); then the halves at SPLIT;
# then bars.traded for the return. Nothing else was varied.
```

**One trial, and five quintiles is one trial.** The split into five is the entry's own, fixed
on 2026-11-06. **Deciles and terciles were not computed**, not reported and not looked at —
the entry says trying all three and reporting the best-looking would be three trials, so the
others were never run. (If a different cut is worth asking about, that is a proposal for a
new plan entry with its own date and a person's decision; it is not in this file, and no
number here comes from one.)

**The one ambiguity in "quintiled", declared before any result was seen.** "Quintiled" can
mean equal-count buckets or fixed bands of width 0.2 on the 0–1 rank scale, and entry 3 does
not say which. Both were declared in the script's header **before the first number printed**
and **both are reported in full** below; neither was selected after the fact. They answer
slightly different questions and each has one defect the other does not:

| cut | edges | what it costs |
|---|---|---|
| **equal-count quintiles** (primary) | 0.0965 / 0.1905 / 0.2995 / 0.4661, from the sample's own quantiles | the **edges are set using the whole sample**, including the future — a mild look-ahead in the boundary, not in the signal |
| **fixed 0.2 bands** (secondary) | 0.2 / 0.4 / 0.6 / 0.8, a priori | **no look-ahead at all**, at the cost of wildly unequal counts (2,655 against 244), because `rank_in_window` sees only the ends of the range so one spike depresses the rank for a year and the distribution is skewed low (median **0.242**) |

**Both cuts fail limb 1.** They are reported as a pair, not as a choice.

**Thresholds, horizon, split and range: none was moved at any point.** The quintile count,
the 21-session horizon, the 1.5 pt floor and the 2017-06-30 split are the entry's. The range
is the one the data permits, and **no alternative range was computed** — which is the move
that decided entry 2's verdict, so it is worth saying that entry 3 needed none: there is only
one VXN start and one censoring end, and the hypothesis fails on the whole of it.

**Two robustness variants, neither a tuning knob.**

1. **The implementable timing.** The forward return above runs from the close of the day the
   signal is known, which no strategy could trade (`execution_lag_days` is 1 by default).
   Recomputed from **t+1 to t+22**: gap **−0.464 pt**, halves **−1.166 / +1.006**. **No limb
   moves** — the sign flip survives the shift, which matters because limb 2 is a sign.
2. **Full window only.** Dropping the 126 sessions whose rank window held fewer than 252
   VXN observations, edges unchanged: Q1 **+1.2649%** (n=1,257), Q5 **+1.2644%** (n=1,238),
   gap **+0.0005 pt**. The gap does not merely stay under the floor; it vanishes.

**Look-ahead, checked rather than cited.** `iv_rank_252` was recomputed on VXN and QQQ
truncated at 2015-06-30 and compared with the full-series values on the 3,506 shared
sessions: **largest absolute difference 0.000e+00**. The regime labels on session *t* use
VXN through *t*'s close; the forward return runs from *t*'s close forward.

**The platform's own arithmetic, asserted against this script's.** Every bucket's count and
mean was computed independently and asserted equal to `stratify`'s on **all 5 cells of both
cuts, to 1e-12**; the script fails rather than reports if they ever disagree. `min_trades=200`
was passed so the `sample` column reads against entry 2's 200-session floor rather than the
15-trade default — that changes a label and no number. (Entry 3 imposes **no** bucket floor
of its own; the smallest bucket anywhere here is 244 sessions.)

**Guard state:** `backtest.core.run_id.guards_were_green()` →
`{'state': 'green', 'when': '2026-11-07T23:44:15', 'note': 'the guards passed on this exact
code'}`. **Green.** Code version `c4f003a+dirty`; the record matches by version string and
the only uncommitted files are under `docs/` and `research/` — **nothing under `backtest/`,
`app/` or `tools/` was touched by this session** — so the green record covers the code that
produced these numbers. `python -m tools.check` was **not** run: no code changed, and it
rewrites a shared guard artefact.

**Trials recorded** through `backtest.research.tracker.count_trials`, one idea tag:
`"iv_rank_252 quintiles vs QQQ forward 21-session return (plan entry 3)"`.

- **As the plan declared it (entry 3: 1).** `Trials.total = 1`, one distinct configuration,
  `run_id` **`366b61ed067ed4b5`**.
- **As it was actually computed.** `Trials.total = 14` — **13 distinct `(cut, range, price
  basis, timing)` configurations plus 1 grouping** (the two-half split). All 13 ids are in
  `research/2026-11-08-task-1c-entry3/trials.out`, each printed beside its configuration,
  and **all 13 are distinct** (the record asserts it).
- **The range is inside the `run_id` payload**, with the price basis and the timing, which is
  the fix for the undercount entry 2's analyst hit: `settings = {"range": …,
  "price_basis": …, "timing": …, "split": "2017-06-30"}`. Checked rather than assumed — the
  same cut on the full range and on the first half produce **2 distinct ids**, and the 13
  configurations produce **13**. A second thing the check showed, worth passing on: an earlier
  pass of the same record used the key `"variant"` where the final one uses `"timing"`, and
  **every id changed**. A `run_id` is comparable only within one payload schema, so the schema
  belongs beside the ids — it is above.
- `docs/requirements.md` 4.3 is `partial` ("counted but unused"), so **nothing consumes
  either count automatically and a person must carry it into the reading**. Both are published
  rather than a chosen one, because whether a price basis or a timing variant is a *trial* is
  a method judgement this session may not make. No Deflated Sharpe is computed: there is no
  Sharpe here, only a mean return.

**Local run record:** `research/2026-11-08-task-1c-entry3/` — `probe.py`, `entry3.py`,
`entry3b.py`, `entry3c.py`, `trials.py`, each with its `.out`. Not version controlled.

## Baselines

Five, because a bucket mean on its own says nothing.

1. **The unconditional mean, beside every conditional one.** Every table carries
   `uncond mean` — the mean forward 21-session return of **every** session in the range,
   **+1.191%** adjusted (+1.133% traded), n=6,341 — and `uncond pos %` (**64.01%**) beside
   every bucket's positive share. `conventions.md`'s hard line, in the table rather than in a
   sentence.
2. **The unconditional mean's own interval.** `block_bootstrap(block=21)` on the full daily
   forward-return series: **+1.191%, 90% [+0.746, +1.696]** — a span of **0.95 pt**, which is
   twice the gap being measured. That comparison is the reason this baseline exists: the
   quantity entry 3 asks about is smaller than the noise on the baseline it is measured
   against.
3. **Random entry, same count, same horizon, unstratified.** `research.baselines.random_entry`
   with a runner returning the mean forward return of the drawn dates; **50 draws** (the
   registry default `random_entry_draws`, not reduced); `n_entries` = the bucket's own session
   count. This is "the structure without the signal" in the only form available with no
   strategy: same number of entry dates, same holding period, dates drawn at random.
4. **Random entry, year-stratified** — the same call with `like=` the bucket's own dates, so
   the draw's **calendar-year distribution matches the bucket's**. This is the baseline that
   matters here, and it is the one that says something the headline number does not.
5. **Leave-one-year-out on the gap**, every year 2001–2026 one at a time — the analogue of
   `one_year_share` (0.70), which is defined on P&L and does not apply to a mean return.

A sixth, specific to a gap: **a null for the gap itself**, 50 paired draws of two date sets
of the same two sizes (unstratified, and year-matched to the two end buckets), differenced.

`delta_equivalent` does not apply: there is no option position. `r.baselines()` does not
apply: there is no run. Buy-and-hold does not apply: there is no strategy to compare to it,
and the unconditional mean is its event-study equivalent.

## Result

All figures **adjusted basis**, forward **21 sessions**, in **percent**. "effective" is the
largest subset of non-overlapping 21-session windows in that bucket (greedy, in time order).
`h1` is 2001-07-23 → **2017-06-30**; `h2` is **2017-07-03** → 2026-10-08.

### Primary cut — equal-count quintiles (edges 0.0965 / 0.1905 / 0.2995 / 0.4661)

| bucket | rank range | sessions | effective | mean adj | mean traded | positive % | h1 n | h1 mean | h2 n | h2 mean |
|---|---|---|---|---|---|---|---|---|---|---|
| **Q1 lowest** | 0.000–0.096 | **1268** | 110 | **+1.164** | +1.108 | 64.59 | 881 | +0.620 | 387 | +2.402 |
| Q2 | 0.097–0.190 | 1267 | 153 | +0.762 | +0.699 | 63.38 | 856 | +0.359 | 411 | +1.599 |
| Q3 | 0.190–0.299 | 1269 | 171 | +1.053 | +0.993 | 66.59 | 737 | +0.421 | 532 | +1.928 |
| Q4 | 0.300–0.466 | 1268 | 152 | +1.368 | +1.311 | 65.69 | 767 | +1.373 | 501 | +1.360 |
| **Q5 highest** | 0.466–1.000 | **1269** | 110 | **+1.608** | +1.557 | 59.81 | 770 | +1.811 | 499 | +1.296 |
| *unconditional* | all | *6341* | *302* | ***+1.191*** | *+1.133* | *64.01* | *4011* | *+0.901* | *2330* | *+1.691* |

Every positive share with its unconditional rate, since a conditional rate without one is not
reportable: Q1 **64.59%** against **64.01%**, Q2 63.38%, Q3 66.59%, Q4 65.69%, Q5 **59.81%**
against **64.01%**. **The mean and the win rate point opposite ways at the top end**: the
highest-IV-rank quintile has the *highest* mean and the *lowest* positive share, which says
whatever is there lives in the size of the up moves rather than their frequency — the same
pattern entry 2 found on its frightened buckets, and for the same sessions.

### Secondary cut — fixed 0.2 bands (no look-ahead in the edges)

| bucket | rank range | sessions | effective | mean adj | mean traded | positive % | h1 n | h1 mean | h2 n | h2 mean |
|---|---|---|---|---|---|---|---|---|---|---|
| **Q1 lowest** | 0.0–0.2 | **2655** | 180 | **+0.954** | +0.894 | 63.92 | 1802 | +0.445 | 853 | +2.031 |
| Q2 | 0.2–0.4 | 2026 | 192 | +1.176 | +1.115 | 66.19 | 1206 | +0.899 | 820 | +1.584 |
| Q3 | 0.4–0.6 | 936 | 125 | +1.354 | +1.305 | 62.50 | 592 | +1.597 | 344 | +0.935 |
| Q4 | 0.6–0.8 | 480 | 68 | +1.935 | +1.881 | 60.00 | 271 | +2.230 | 209 | +1.552 |
| **Q5 highest** | 0.8–1.0 | **244** | 37 | **+1.808** | +1.754 | 60.66 | 140 | +1.264 | 104 | +2.540 |

On this cut the five means are **nearly monotone increasing in IV rank** (+0.95, +1.18,
+1.35, +1.94, +1.81) — again the opposite ordering to the hypothesis's. The counts are the
price of the a priori edges: the top band holds **244 sessions and 37 non-overlapping
windows**, against 2,655 and 180 in the bottom one.

### The gap, with its interval and its construction named

**Construction C** is the only one that answers the question asked, so it is the one quoted:
`block_bootstrap(block=21)` over the **calendar session positions** of the range, with **both
bucket means recomputed inside every draw**, so the pair stays paired and the 21-session
overlap between neighbouring forward returns is kept inside a block. **2,000 draws, 90%**
(the level entry 1 used; entry 3 names none), **seed 20260917** (the module default). **No
draw was degenerate: `nan_draws = 0` everywhere.**

| cut | basis | gap = Q1 − Q5 | 90% interval (construction C) | contains zero | ≥ 1.5 pt |
|---|---|---|---|---|---|
| **equal-count quintiles** | **adjusted** | **−0.444 pt** | **[−1.804, +0.992]** | **yes** | **no** |
| equal-count quintiles | traded | −0.450 pt | [−1.817, +0.988] | yes | no |
| fixed 0.2 bands | adjusted | −0.854 pt | [−3.146, +1.666] | yes | no |
| fixed 0.2 bands | traded | −0.860 pt | [−3.142, +1.655] | yes | no |

Two other constructions, on the two end buckets' **means**, reported as a pair rather than a
choice because their disagreement is the information. **A** =
`bootstrap_trades(statistic=mean, overlapping=True)`, which the module itself labels
*"resampled as if independent; where they overlap in time this interval is too narrow"*;
**B** = `block_bootstrap(block=21)` on the bucket's own returns in chronological order, whose
blocks straddle the calendar gaps where the bucket changed, which is why C is quoted for the
gap:

| cut | bucket | mean | A (too narrow) | B (blocks in bucket order) |
|---|---|---|---|---|
| equal-count | Q1 | +1.164 | [+0.945, +1.379] | [+0.551, +1.905] |
| equal-count | Q5 | +1.608 | [+1.253, +1.972] | [+0.342, +2.664] |
| fixed bands | Q1 | +0.954 | [+0.796, +1.112] | [+0.485, +1.567] |
| fixed bands | Q5 | +1.808 | [+0.906, +2.717] | [−1.056, +3.827] — crosses zero |

**No interval was refused anywhere in this finding.** `bootstrap_min_observations` is **10**
and `block_bootstrap_min_blocks` is **3**, so the smallest bucket here (244 sessions, needing
63 for a block of 21) clears both comfortably **on the nominal count**. Whether it clears
them on the *effective* count (37) is a different question and is in the caveats. The one
place the platform's floors would have refused is a cell nobody asked for: a half of the
fixed-band top band holds 104 sessions, still above 63, so not even that refuses.

### The two halves

| cut | full gap | first half | second half | sign flips |
|---|---|---|---|---|
| **equal-count quintiles** | **−0.444 [−1.804, +0.992]** | **−1.190 [−3.041, +0.618]** (881 / 770) | **+1.106 [−1.031, +3.168]** (387 / 499) | **YES** |
| fixed 0.2 bands | −0.854 [−3.146, +1.666] | −0.820 [−4.486, +2.740] (1802 / 140) | −0.510 [−3.344, +2.206] (853 / 104) | no |

**Every one of these six intervals crosses zero.** On the primary cut the gap reverses sign
across the split and neither half establishes a sign; on the secondary cut it keeps its sign
and still establishes nothing. Both halves of both cuts are consistent with no gap at all.

### Baselines: random entry at the same count and horizon

50 draws, adjusted, equal-count quintiles. "pct" is the share of draws below the observed
value.

| bucket | observed | unstratified random: median [5th, 95th] | pct | **year-stratified random: median [5th, 95th]** | pct |
|---|---|---|---|---|---|
| Q1 lowest (n=1268) | +1.164 | +1.198 [+0.973, +1.425] | 0.34 | **+1.946 [+1.813, +2.146]** | **0.00** |
| Q2 (n=1267) | +0.762 | +1.210 [+0.978, +1.412] | 0.00 | +1.518 [+1.321, +1.719] | 0.00 |
| Q3 (n=1269) | +1.053 | +1.222 [+1.021, +1.416] | 0.12 | +1.469 [+1.301, +1.694] | 0.00 |
| Q4 (n=1268) | +1.368 | +1.198 [+0.973, +1.425] | 0.88 | +1.110 [+0.897, +1.360] | 0.96 |
| Q5 highest (n=1269) | +1.608 | +1.222 [+1.021, +1.416] | 0.98 | **−0.174 [−0.409, +0.004]** | **1.00** |

And the null for the gap itself, 50 paired draws: **unstratified** median **−0.025 pt**
[−0.309, +0.305]; **year-stratified** median **+2.113 pt** [+1.808, +2.351]. The observed
−0.444 pt is below all 50 draws of both.

**What the year-stratified column says, and what it does not.** It matches each draw's
calendar-year distribution to the bucket's, so it removes the "those were good years"
explanation — and here it removes it in the direction *against* the hypothesis. Low-IV-rank
days fall in good years (a random day of Q1's own years earns +1.95%) and earn **less** than
those years' average; high-IV-rank days fall in bad years (a random day of Q5's own years
earns **−0.17%**) and earn **more**. So the **raw** gap is near zero because two effects
cancel: the composition of years pushes it one way by about +2.1 pt and the within-year
relationship pushes it the other way by about −2.6 pt.

**This does not rescue anything, and it is not a second test.** Entry 3's hypothesis is that
the **lowest** quintile's mean exceeds the highest by 1.5 pt, on the plain unconditional
means — that is what was pre-registered, that is what was measured, and it fails. The
year-stratified figure is a baseline, it points the **opposite** way to the hypothesis, and a
year-matched excess is a different statistic with a different null. **If the within-year
version is worth asking, it is a new plan entry with its own date and a person's decision.**
No number in this finding's verdict comes from it.

**One honest limitation of the random-entry baseline here, which entry 2 did not face.**
Entry 2's buckets were 299 sessions out of 3,965; these are 1,268 out of 6,341. A draw of a
fifth of the sample from the same sample reuses most of the data every time, so the spread
across draws is a subsample spread under a finite-population correction, **not** a sampling
distribution for the bucket mean — which is why the unstratified column looks ten times more
precise than construction C's interval on the same quantity. **Construction C is the
interval to read**; the random-entry percentiles are read as "is the bucket different from an
arbitrary fifth of the same history", which is the question the baseline is for.

### Leave-one-year-out on the gap

Equal-count quintiles: the gap ranges from **−1.161 pt** (dropping 2008) to **+0.103 pt**
(dropping 2002); fixed bands, **−2.613 pt** (dropping 2008) to **−0.411 pt** (dropping 2011).
**No single year's removal brings either cut to the 1.5 pt floor** — on the primary cut the
most any one year is worth is 0.72 pt, and that year is 2008. The failure is therefore not
an artefact of one episode, and neither is it rescued by removing one: `one_year_share`
(0.70) and `single_trade_share` (0.35) are defined on P&L and have no meaning for a mean
return, and this scan is their analogue.

### `iv_rank_252` is not a fourth dimension — it is mostly entry 2's first one

Same 6,341 sessions, `iv_rank_252` quintile against entry 2's volatility level (VXN at
0.18 / 0.28). Every conditional with its unconditional baseline:

| conditional | value | its unconditional baseline |
|---|---|---|
| P(Q5 highest \| high vol) | **0.452** | P(Q5 highest) = **0.200** |
| P(Q1 lowest \| low vol) | **0.272** | P(Q1 lowest) = **0.200** |
| P(Q5 highest \| low vol) | **0.047** | P(Q5 highest) = **0.200** |
| P(high vol \| Q5 highest) | **0.544** | P(high vol) = **0.241** |
| P(high vol \| Q1 lowest) | **0.121** | P(high vol) = **0.241** |
| P(low vol \| Q5 highest) | **0.075** | P(low vol) = **0.317** |

690 of the 1,269 highest-quintile sessions are entry 2's "high vol", and only 95 are "low
vol". **Entries 2 and 3 are two cuts of one measurement — the level of VXN — one in absolute
terms and one relative to its own trailing year.** The multiple-comparison correction a
person applies by hand should not treat tonight's entries 2 and 3 as two independent looks;
and the fact that the absolute cut separated something on 2011 onward while the relative cut
separates nothing on 2001 onward is a statement about *which* transform of VXN matters, not
two independent results.

### The pre-registered criterion, limb by limb

> **Abandon if** the lowest-vs-highest quintile gap is **under 1.5%**, **or** if it **flips
> sign** across the halves.

**Limb 1 — the gap floor. FIRES, on every configuration computed.**

| configuration | gap | ≥ 1.5 pt |
|---|---|---|
| equal-count quintiles, adjusted, t→t+21 | **−0.444** | no |
| equal-count quintiles, traded | −0.450 | no |
| equal-count quintiles, t+1→t+22 | −0.464 | no |
| equal-count quintiles, full 252-window only | **+0.0005** | no |
| fixed 0.2 bands, adjusted | −0.854 | no |
| fixed 0.2 bands, traded | −0.860 | no |
| fixed 0.2 bands, full 252-window only | −0.346 | no |

The largest magnitude anywhere is **0.854 pt**, on the cut with a 244-session top bucket, and
its interval is **[−3.146, +1.666]**. Nothing is close to 1.5 pt, and the floor sits outside
the primary interval on the far side.

**Limb 2 — the sign across the 2017-06-30 split. FIRES on the primary cut.** −1.190 pt in
the first half, **+1.106 pt** in the second. Unlike the one reversal entry 2 had to argue
about (−0.047 pt, four hundredths from zero), **this one is not a last-digit artefact**: it
is 2.3 pt of swing, it survives the lag-1 timing (−1.166 → +1.006), and its two halves hold
881/770 and 387/499 sessions. It does **not** fire on the secondary cut, where the sign holds
— but limb 1 has already fired there, and the entry's criterion is a disjunction.

**So, limb by limb, on the numbers obtained: limb 1 fires everywhere and limb 2 fires on the
primary cut. Entry 3 is abandoned, and unlike entry 2 it is abandoned without a single
judgement call** — no range was left open by the criterion (there is one VXN start and one
censoring end), no reading of the words is in dispute, and the two cuts that "quintiled"
could mean agree. **The hypothesis as written is dead and the data says so clearly.**

## The conflation this finding must not become

**This is the underlying-side question only.** It asks what QQQ's own price did over the
following 21 sessions, conditioned on where VXN sat in its trailing-year range. It does not
ask, and cannot answer, what an option bought on those days would have been worth.

**The option-P&L version is plan entry 20, and it is blocked.** The reason is not cost or
time: the synthetic chain prices options off **the same VXN series this signal is computed
from**, so a Black-Scholes price on a low-VXN day is cheap *by construction* and a positive
answer to "is buying at low IV rank better" is **guaranteed by arithmetic** before any market
behaviour enters. A number would come out, it would look reasonable, and it would be a
property of the fixture.

**So if any figure in this file is later quoted as "buying when IV is low is better", that is
the conflation entry 3 names, and it is wrong twice over** — once because this file contains
no option P&L at all, and once because what little direction is visible here points the
**other** way (the highest-IV-rank quintile has the higher underlying forward return).

**What this finding does not license,** stated plainly:

- **Not** an entry filter on `iv_rank_252`, in either direction. It found no separation.
- **Not** "high IV rank is a buy signal". The year-stratified control points that way; the
  pre-registered test does not test it, and the raw gap's interval contains zero.
- **Not** any statement about which delta, which expiry, where to take profit, or anything
  about the volatility surface or term structure. No option was priced.
- **Not** an argument that IV rank is useless for *option selection*. A signal that does not
  predict the underlying's direction can still be the right thing to condition premium
  spending on — that is entry 20's question, it needs real option data, and nothing here
  bears on it either way.

## Caveats

- **The counts are inflated and the intervals are not.** Q1 and Q5 hold 1,268 and 1,269
  sessions but only **110 non-overlapping 21-session windows each** — an **11.5×**
  overstatement of the independent sample, worse than entry 2's 5.5× because the buckets are
  larger. Entry 3 sets no count floor, so nothing was met on an inflated number here; but
  every count in the tables should be read beside its `effective` column. Construction C
  keeps the overlap inside a block, and on a bucket mean the block construction B is about
  **three times wider** than the trade construction A (Q1: 1.35 pt against 0.43 pt), so the
  quoted intervals are honest about the overlap even though the counts are not.
- **A null result is not proof of absence, and the sample bounds what it rules out.** The
  primary interval is [−1.804, +0.992]. It excludes the pre-registered +1.5 pt comfortably,
  so **the hypothesis as written is ruled out**; it does **not** rule out a gap of, say,
  −1.5 pt in the opposite direction. "No effect of the size entry 3 predicted" is the claim
  this sample supports. Ruling out a gap of ±0.5 pt would need a materially longer history or
  a less overlapped statistic, and neither exists for VXN.
- **One cut's edges use the whole sample.** The equal-count quintile boundaries are the
  sample's own quantiles, which a real-time user would not have known. The fixed-band cut has
  no such defect and fails limb 1 too, which is why both are reported — but the primary
  figure carries that look-ahead in its *boundary*, and it is the kind that usually flatters
  a result rather than killing one.
- **The first 126 of 6,341 sessions carry a short-window rank** (`min_periods = 252 // 2`),
  so "`iv_rank_252`" is a 126-to-251-session rank until 2002-01-28. Excluding them takes the
  gap to **+0.0005 pt**, so this does not change the verdict; it is here because the signal's
  name says 252 and for six months it is not.
- **`rank_in_window` sees only the two ends of the range.** One VXN spike depresses the rank
  for up to a year, which is why the distribution is skewed low (median 0.242) and why the
  fixed-band top bucket holds 244 sessions out of 6,341. `iv_pct_252` is the percentile
  version and is **not** reported here: entry 3 names `iv_rank_252` and swapping the signal
  would be a second trial on a different question. That is a candidate for a new plan entry,
  not a result in this file.
- **How many configurations were tried: 13**, all of them reported above or in the run record,
  **none tried and dropped**, plus 3 interval constructions, 2 random-entry forms × 50 draws,
  a 50-draw paired gap null, and a leave-one-year-out scan of 26 years. The plan declared
  **1**. Neither count is consumed by anything automatic.
- **Deciles and terciles were never computed**, so the "best of three cuts" inflation entry 3
  warns about cannot be present. What *is* present is two readings of "quintiled", both
  declared in advance and both reported.
- **`stratify` names a session a trade.** Used as entries 2 and 3 prescribe — on the
  underlying's forward return rather than a strategy's P&L — it takes a column called
  `net_pnl`, reports a column called `trades` whose unit is a session, a `total` that is the
  sum of returns and is meaningless and is quoted nowhere, and a `sample` verdict from a
  `min_trades` argument. The numbers are right (asserted to 1e-12 against an independent
  computation on all 10 cells); the **labels** are not. The spec is already filed from entry
  2: `docs/specs/stratify-reports-its-unit-and-its-effective-sample.md`.
- **The non-overlapping subset was re-implemented, again.** `research.events.effective_sample`
  takes `Episode` objects and a calendar-month span, so a session-and-horizon version does not
  exist in the package and the greedy loop was written in the run record for the third finding
  running. Both relevant specs are already filed
  (`docs/specs/effective-sample-returns-its-subset.md` and the `stratify` one); **no third
  spec was filed for the same gap.**
- **This is not out of sample and cannot be made so.** QQQ has one price history and VXN has
  one. The period hold-out (2026-11-08 onward) began today and holds nothing yet. The
  2001-2026 period has been looked at by this project before.
- **Not a trading result.** No option is priced anywhere, no exit, sizing or selection rule
  appears, and no engine ran.

## What it changes

**For plan entry 3: close it. The hypothesis failed, and nothing is a person's judgement
this time.** Both limbs fire, on both readings of the only ambiguous word in the entry, on
both price bases, under the implementable timing, and after removing any single year. Entry 3
stays on the record saying what was expected on 2026-11-06; this finding is the result, and
it is a clean negative. **That is the entry working as designed** — the criterion was written
to be able to fail, and it did.

**For `iv_rank_252` as an underlying-side entry filter: do not build one.** In either
direction. There is no separation at the pre-registered size, and the strongest thing that
can be said is the opposite of what was predicted and is not established.

**For the plan's remaining stratification entries, one thing worth carrying:** entries 2 and
3 are now known to be **two cuts of VXN's level**, with P(high vol | highest quintile) =
0.544 against 0.241. A plan that counts them as two independent looks at "does a regime
dimension separate the forward return" is overcounting the evidence and undercounting the
search. Entry 18 and 19's exit work is on different data; **entry 4 is not** — it conditions
on VXN's retracement from an episode peak, which is a third cut of the same series, and its
reading should say so.

**For `docs/research/plan.md` § 5 entry 3, for `doc-keeper` and the owner:** "VXN begins
2001, so the sample is 2001 onward" is **correct about VXN** (2001-01-23) and **six months
optimistic about the signal**: `iv_rank_252` first has a value on **2001-07-23** and a full
252-session window on **2002-01-28**, because `rank_in_window`'s `min_periods` is
`window // 2`. Unlike entry 2's VIX3M claim this costs nothing — the verdict is the same on
every version of the range — but the same sentence pattern produced a four-and-a-half-year
error one entry earlier, so it is reported rather than absorbed. **This session may not edit
the plan and did not.**

**One platform gap, filed as a spec and not built:**
`docs/specs/the-iv-level-signals-describe-themselves.md`. None of the five signals in
`registry.VOL_SIGNALS` — `iv`, `iv_rank_252`, `iv_pct_252`, `iv_rank_63`, `iv_rv_spread` —
has an entry in `SIGNAL_META`, so `describe_signals(("iv_rank_252",))` returns
`family="custom"`, `measures="custom (no description registered)"`, `unit="—"`,
`window=None`, `caveat=""` for the signal this entry is built on. The caveat that actually
decides how to read it (*"IV rank sees only the two ends of the range, so one extreme spike
depresses it for a long time"*) is in `indicators.py`'s docstring and does not travel. The
module's own docstring says a signal missing from `SIGNAL_META` makes `describe()` *"raise
outright"*; it does not raise — it labels. **Nothing under `backtest/`, `app/` or `tools/`
was edited.**
