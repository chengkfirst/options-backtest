# What is the record behind `research-questions.md` § 2.1, and does the public `study()` reproduce it?

**Date:** 2026-11-07
**Answers:** `docs/research/plan.md` § 3, first bullet — "the first thing `research-analyst`
should do is write the existing result up as a finding so that what entry 1 varies is on the
record." **This is not plan entry 1.** Entry 1's test (four thresholds, two underlyings,
sub-periods) has not been run here, and its pre-registered abandonment criterion is not
evaluated below.
**Verdict:** The § 2.1 table reproduces **exactly** — all nine quoted cells, the 30-episode
count and the 21 non-overlapping observations — but only on an **unadjusted (traded) QQQ
close/high/low series ending 2026-09-15 to 2026-09-18**, which the standing document does not
say. On the range this session was asked to use (through 2026-11-06) and on the
dividend-adjusted series the module's own docstring prescribes for returns, the 6-month excess
is **+12.8 points** rather than +12.9 and the 12-month excess is **−0.9 points** rather than
+0.2 — the 12-month row flips sign. The 6-month excess's 90% bootstrap interval does not cross
zero on one construction and does cross zero at 95%; the number is real and the precision in
§ 2.1 is not.

---

## Data

- **Source:** `backtest.data.ingest.fetch_ohlcv("QQQ", ...)` (yfinance), through
  `backtest.data.store.ParquetCache`, built into bars with
  `backtest.data.underlying.build_bars`.
- **Symbol:** QQQ only.
- **Range requested:** 1999-03-10 to 2026-11-07. **Range actually available:** first bar
  **1999-03-10**, last bar **2026-11-06** — 6,960 sessions. QQQ's listing is 1999-03-10, so
  the "1999" in § 2.1's "QQQ 1999–2026" is the instrument's own start and not a choice.
- **Range the original claim names:** "QQQ 1999–2026". **They differ at the end.** The
  diagnosis below places the original's last bar in **mid-September 2026**, about seven weeks
  short of this session's. § 2.1 does not record an end date, which is the single reason the
  difference took a scan to find.
- **Option chain: none. Not used, synthetic or otherwise.** This is an `UNDERLYING`-scope
  event study on daily OHLC; `backtest/research/events.py`'s module docstring states it
  "never touches options". **So nothing here is affected by the synthetic chain's constant
  IV, and equally nothing here may be read as an option-side claim** — not which delta, not
  which tenor, not where a take-profit belongs. The finding is about QQQ's forward price only;
  the step from "QQQ is usually higher six months after a 10% correction" to "buying calls
  then makes money" is not taken anywhere in this file and is not supported by it.
- **Hold-out respected:** **no SPY run of any kind** (`docs/decisions/0001`, § 1). No data
  after 2026-11-07 — the last bar is 2026-11-06, so the period hold-out
  (`HoldOut(2026-11-08, 2099-12-31)`) was never reached and no `unlock=` was needed.
- **Two price series, and the choice matters.** `UnderlyingBars` carries both
  (`backtest/data/underlying.py`): `adjusted()` ("corrected for splits and dividends, **for
  signals and returns**") and `traded()` ("the prices that actually traded, for lining up with
  option strikes"). A forward return and a drawdown are returns, so **adjusted is the series
  the module prescribes** and is this finding's primary basis. Both are reported, because the
  difference is what reproduces § 2.1.

## Method

No `run()` call: this question needs no engine, no strategy and no option price. The public
calls are `backtest.research.events.study` and
`backtest.research.validation.intervals.{bootstrap_trades, block_bootstrap}`.

```python
from datetime import date
import numpy as np, pandas as pd
from backtest.data.ingest import fetch_ohlcv
from backtest.data.store import CacheKey, ParquetCache
from backtest.data.underlying import build_bars
from backtest.research.events import DAYS_PER_MONTH, effective_sample, study
from backtest.research.validation.intervals import block_bootstrap, bootstrap_trades

START, END = date(1999, 3, 10), date(2026, 11, 7)
cache = ParquetCache()
key = CacheKey("ohlcv", "QQQ", {"start": START, "end": END,
                                "source": "yfinance", "prices": "raw+adj"})
bars = build_bars("QQQ", cache.get_or_load(key, lambda: fetch_ohlcv("QQQ", START, END)))

# PRIMARY basis: adjusted OHLC, because a forward return is a return.
frame = pd.DataFrame({"close": bars.adjusted("close"),
                      "high":  bars.adjusted("high"),
                      "low":   bars.adjusted("low")})
s = study(frame, 0.10, horizons_months=(1, 3, 6, 12))   # reference_window=252 (default)

# SECONDARY basis: swap bars.adjusted for bars.traded. Nothing else changes.
```

**Everything in `study()` is left at its default.** `threshold=0.10` and
`horizons_months=(1, 3, 6, 12)` are § 2.1's own values; `reference_window=252` and
`reset_fraction=0.5` are the module's. **No parameter, window or definition was moved to
improve agreement with § 2.1** — the two things that were varied are the price basis (a
documented either/or, both reported) and the data end date (a diagnostic scan, reported as
such, with the full-range result as the headline).

`study()` reports `basis` = *"events detected on the close; further falls and the deepest
drawdown measured on the intraday low"* and `intraday` = `True`, confirming real high/low
bars were supplied rather than a close series. Running the same close-only series for
contrast leaves `up`, `baseline up` and `excess` **identical** and only degrades the
"fell another X%" and deepest-drawdown columns — which matters for how § 2.1 is read: the
close/OHLC distinction the task flags does **not** touch the P(up) table at all. It touches
the path columns, and those are where "could you have held on" lives.

**Guard state:** `backtest.core.run_id.guards_were_green()` →
`{'state': 'green', 'when': '2026-11-07T23:44:15', 'note': 'the guards passed on this exact
code'}`. **Green**, recorded for this exact code. Code version `c4f003a+dirty` — the `+dirty`
is `docs/decisions/0001-...md` and the files of this run, both untracked and neither under
`backtest/`.

**Local run record:** `research/2026-11-07-task-1b/` (four scripts with their stdout; not
version controlled).

## Baselines

The unconditional baseline is not optional here and is not an add-on: `study()` computes it
itself (`events.baseline`, "enter on any day at random, same horizons") and the `excess`
column **is** conditional minus unconditional. Every conditional figure below is quoted with
the unconditional rate it is measured against, on the same series and the same range.

Three further baselines, because the conditional-minus-unconditional pair is not the only
thing that can be wrong:

1. **The unconditional rate's own sampling error.** 6,834 overlapping 6-month windows are not
   6,834 observations — 27.5 years hold about 54 non-overlapping ones. A block bootstrap of the
   daily up-indicator in blocks of 126 days (≥ the horizon) puts the unconditional 6-month rate
   at **73.4%, 90% interval [65.2%, 80.2%]** — a span of 15 points. **The thing the excess is
   measured against is itself uncertain by about as much as the excess**, and § 2.1 quotes it
   to one decimal place.
2. **Episodes, not days.** The platform's unit is the episode (one stretch from one prior
   high), so the sample is 29 at 6 months and not the 1,900-odd days QQQ spent more than 10%
   down. Counting days is the error this module exists to prevent.
3. **Non-overlapping episodes.** `study()`'s `effective` column is the greedy count of
   non-overlapping windows. Reported beside `sample` for every horizon below.

A random-entry control in the `research/baselines.py` sense does not apply: there is no
strategy, no entry rule and no P&L. The unconditional any-day baseline **is** the random-entry
control for an event study, and it is built into the tool.

## Result

### The headline: full range, adjusted OHLC (the primary basis)

QQQ 1999-03-10 → 2026-11-06, threshold 0.10, **31 episodes found**.

| Horizon | sample | effective | P(up) | unconditional P(up) | excess | median return | median deepest drawdown | excluded_by_data_end |
|---|---|---|---|---|---|---|---|---|
| 1m | 30 | 28 | 63.3% | 62.8% | **+0.5 pt** | +4.2% | −3.3% | 1 |
| 3m | 30 | 25 | 76.7% | 68.8% | **+7.9 pt** | +10.3% | −4.3% | 1 |
| **6m** | **29** | **22** | **86.2%** | **73.4%** | **+12.8 pt** | +16.7% | −5.7% | **2** |
| 12m | 28 | 15 | 78.6% | 79.5% | **−0.9 pt** | +20.9% | −7.4% | 3 |

### The same run on the traded (unadjusted) series

| Horizon | sample | effective | P(up) | unconditional P(up) | excess | excluded_by_data_end |
|---|---|---|---|---|---|---|
| 1m | 30 | 28 | 63.3% | 62.3% | +1.0 pt | 1 |
| 3m | 30 | 25 | 76.7% | 68.3% | +8.3 pt | 1 |
| **6m** | **29** | **22** | **86.2%** | **73.0%** | **+13.2 pt** | **2** |
| 12m | 28 | 15 | 78.6% | 78.5% | +0.1 pt | 3 |

**The shape § 2.1 describes survives both bases:** the excess is near zero at 1 month, rises
to a maximum at 6 months, and is gone at 12. The sign of the 12-month excess, however, is a
coin flip between the two price series (−0.9 pt adjusted, +0.1 pt traded) — so "gone by 12
months" is supported and "+0.2%" is not a number with a sign.

### Against § 2.1, cell by cell

| Horizon | § 2.1 P(up) | reproduced (adjusted, full range) | § 2.1 baseline | reproduced | § 2.1 excess | reproduced |
|---|---|---|---|---|---|---|
| 3m | 75.9% | 76.7% | 68.3% | 68.8% | +7.6% | +7.9 pt |
| **6m** | **85.7%** | **86.2%** | **72.9%** | **73.4%** | **+12.9%** | **+12.8 pt** |
| 12m | 78.6% | 78.6% | 78.4% | 79.5% | +0.2% | **−0.9 pt** |

Episodes: § 2.1 says **30**; the full range finds **31**. 6-month non-overlapping
observations: § 2.1 says **21**; the full range gives **22**.

### What differs, named

**Both differences are the data end date, and the price basis.** The § 2.1 table was first
committed on **2026-09-26** (`86a52ff`, in Chinese, before the English rename; it moved to
`docs/` later at `5969320` unchanged) — so "1999–2026" means *to late September 2026*, not to
November. A scan of every session from 2026-08-01 to 2026-11-06 on both price bases finds
**exactly one window that reproduces all nine quoted cells to the quoted precision, plus the
30-episode count, plus the 21 non-overlapping observations**:

> **traded (unadjusted) OHLC, last bar 2026-09-15, -16, -17 or -18.**

| Horizon | sample | effective | P(up) | baseline | excess | § 2.1 |
|---|---|---|---|---|---|---|
| 3m | 29 | 24 | 75.86% | 68.26% | +7.60 pt | 75.9 / 68.3 / +7.6 ✓ |
| **6m** | **28** | **21** | **85.71%** | **72.86%** | **+12.85 pt** | 85.7 / 72.9 / +12.9 ✓ |
| 12m | 28 | 15 | 78.57% | 78.38% | +0.20 pt | 78.6 / 78.4 / +0.2 ✓ |

`excluded_by_data_end` there is `{1: 0, 3: 1, 6: 2, 12: 2}`. The adjusted series at the same
end date gives 75.9 / 85.7 / 78.6 for P(up) — the conditional column is basis-independent —
but baselines of 68.7 / 73.4 / 79.4, so the 12-month excess is **−0.8 pt** and § 2.1's +0.2%
does not appear on any adjusted-series end date tested.

So § 2.1 is **not wrong; it is under-specified in two places that each move a cell**:

1. **No end date.** Seven more weeks add one episode (2026-11-02, a fresh 10% crossing) and
   un-censor one more at 6 months (2026-03-27). That is why 28 became 29 and 21 became 22, and
   why 85.71% became 86.21%. Nothing changed about history; the window moved.
2. **No price basis.** The quoted *baselines* (68.3 / 72.9 / 78.4) are the **traded** series'
   to the digit and are 0.5 to 1.0 points away from the adjusted series'. The 12-month row is
   the one where that gap decides the sign.

The third candidate explanation — a different horizon definition, a different reference
window, or a conditional probability computed over days rather than episodes — is **ruled
out**: the conditional column matched to four decimals on the defaults, which it could not
have done had any of those differed.

### An interval on the 6-month excess

Constructed four ways, because the construction is most of the answer. All at **90%**, 2,000
draws, seed 20260917 (the module default), full range, adjusted basis.

| What | estimate | 90% interval | crosses zero | method |
|---|---|---|---|---|
| P(up at 6m \| 10% correction), all 29 episodes | 86.2% | [75.9%, 96.6%] | no | `bootstrap_trades(overlapping=True)` — **module's own caveat: too narrow where episodes overlap** |
| P(up at 6m \| 10% correction), 22 non-overlapping | 90.9% | [81.8%, 100%] | no | `bootstrap_trades(overlapping=False)` |
| Unconditional P(up at 6m) | 73.4% | [65.2%, 80.2%] | no | `block_bootstrap(block=126)`, blocks ≥ the horizon |
| **6m excess, all 29 episodes, baseline held fixed** | **+12.8 pt** | **[+2.4, +23.1]** | **no** | `bootstrap_trades(overlapping=True)` on `up − 0.7344` |
| **6m excess, 22 non-overlapping, baseline held fixed** | **+17.5 pt** | **[+8.4, +26.6]** | **no** | `bootstrap_trades(overlapping=False)` on `up − 0.7344` |
| **6m excess, both sides resampled (conservative)** | **+17.5 pt** | **[+1.6, +34.8]** | **no** | episode interval minus the baseline's, worst corners |

At **95%** the first of those excess intervals — all 29 episodes, baseline fixed — becomes
**[−1.0, +23.1] and crosses zero.**

Three things about this that have to be said together rather than chosen between:

- **`block_bootstrap` could not be applied to the statistic the task named.** The instruction
  was "bootstrap the 6-month excess **by episode** with `block_bootstrap`, block length ≥ the
  horizon." `block_bootstrap`'s contract is a **daily series** resampled in contiguous blocks
  of days, and it refuses fewer than three whole blocks (`block_bootstrap_min_blocks` = 3). An
  episode-level proportion over 22 observations is not a daily series, and the 22 outcomes
  cannot be blocked at 126. The module's own docstring assigns this case to the other function:
  "the trade bootstrap is for a per-trade statistic such as the win rate, with its limitation
  stated." So `block_bootstrap` was used where it is the right tool — the **unconditional**
  side, which genuinely is a daily series — and `bootstrap_trades` on the episode side, with its
  limitation stated. **This is a deviation from the instruction, reported rather than worked
  around.**
- **Holding the baseline fixed understates the width.** The unconditional rate is not known; it
  has a 15-point interval of its own. The "both sides resampled" row is the conservative
  combination (no correlation assumed, worst corners taken) and spans **33 points**. The honest
  summary of the 6-month excess is *"positive, probably somewhere between a couple of points and
  thirty"*, which is a statement § 2.1's "+12.9%" cannot be read as.
- **The non-overlapping subset gives a *larger* excess, not a smaller one** (+17.5 vs +12.8 pt).
  Dropping overlapping episodes removes the 1999–2000 cluster's losers. That is worth knowing
  before anyone assumes the effective-sample correction can only shrink the result.

### Concentration

The P&L cuts in `core/thresholds.py` (`single_trade_share` 0.35, `one_year_share` 0.70) are
defined on P&L and do not apply to a proportion. Their analogue does:

- **One episode is worth 3.4 points** of the 6-month P(up) — flipping any single one of the 29
  moves it by 1/29. The excess is +12.8 points, so **four episodes carry the whole result.**
- **The four 6-month losers are 2000-03-15, 2000-04-03, 2015-08-21 and 2022-01-20.** Two of the
  four are three weeks apart in the same crash.
- **Episode arrivals are heavily clustered.** Of 31 episodes, **6 fall in 1999–2000** and **3 in
  2026**; 2001, 2002, 2003, 2009, 2013, 2014 and 2017 contribute none. The sample is not 29
  draws from 27 years; it is a handful of clusters.
- **Leave-one-era-out, 6-month excess:** drop 1999–2000 → +17.9 pt (n=23); drop 2008 → +12.3 pt
  (n=28); drop 2020 → +11.7 pt (n=27). **No single era carries the sign**, which is the one
  robustness check here that comes out well.

## Caveats

- **This is a restatement and a robustness check. It is not, and cannot be made into, an
  out-of-sample test.** The 1999–2026 period has already been looked at for exactly this
  question — that is what § 2.1 *is* — so re-running it on the same history with seven weeks
  added cannot confirm it. The seven weeks are the only genuinely new data, they contain one
  episode, and one episode cannot confirm or refute anything. **There is no way to repair this
  for QQQ:** QQQ has one price history and it has been seen. Per `docs/decisions/0001`, the
  out-of-sample stages available are SPY (reserved until the final check) and everything after
  2026-11-07 (empty today).
- **The platform's own sample warning is silent here, and should not be read as reassurance.**
  `study()`'s `note` column is empty for every horizon, because `_sample_warning` fires only
  when `effective ≤ 8` or `sample > 2 × effective`, and 29/22 trips neither. The "21
  non-overlapping observations" caution in § 2.1 was added by a person. **At 22 effective
  observations an excess of 12.8 points has an interval wide enough to cross zero at 95% on one
  construction**, which the tool does not say and this finding does.
- **The 12-month row's sign is not determined by the data.** It is −0.9 pt adjusted and +0.1 pt
  traded, on 28 episodes with **only 15 non-overlapping**. Fifteen is close to the module's own
  "read the direction, not the decimals" band. § 2.1's "+0.2%" is inside the noise, and the
  interesting claim — that the edge is *gone* by 12 months — is supported by both bases while
  the number is supported by neither.
- **End-of-range censoring is part of the number and moves it.** Full range:
  `excluded_by_data_end = {1: 1, 3: 1, 6: 2, 12: 3}` of 31 episodes. The 12-month horizon
  discards three, including the 2026-03-27 and 2026-07-29 episodes, so the most recent 15
  months of history contribute to the 1- and 3-month rows and not to the 12-month row. The
  horizons are **not** computed on the same episodes, and comparing excesses across horizons —
  which is the whole of § 2.1 — compares slightly different samples.
- **One episode in the sample is three sessions old.** 2026-11-02 crossed −10.3% and is
  censored at every horizon but contributes to the 31-episode count. Any re-run next week will
  produce different numbers for an uninteresting reason.
- **Horizons are trading days, not calendar months.** `DAYS_PER_MONTH = 21`, so "6 months" is
  126 sessions. `effective_sample`, by contrast, measures overlap in **calendar** days
  (`months × 30`). The two conventions are not identical and the mismatch is the platform's,
  not this run's.
- **How many configurations were tried.** Two price bases × one threshold × one
  `reference_window` × 4 horizons = **the single pre-specified configuration, run twice**. The
  end-date scan touched 68 sessions × 2 bases and was a **forensic search for the original run's
  range**, not a search for a result: the full-range number was computed and written down first,
  the scan was declared before it ran, and no § 2.1 cell was adjusted toward agreement. It is
  still a search and it is still reported here, because a search's size is what a deflated figure
  corrects for.
- **No new episodes were added, removed or reclassified by hand.** Everything in the tables comes
  from `study()`'s return value; the only arithmetic done outside the platform is the greedy
  non-overlapping subset (needed for the interval — see the spec below) which was verified to
  reproduce `effective_sample`'s count exactly.
- **Not a trading result.** No option is priced anywhere in this finding, and the step from
  P(up) to P&L is not taken. An 86% chance of being higher in six months says nothing about
  what a call bought on the crossing day would have returned, because that depends on the
  volatility surface — which the synthetic chain does not have.

## What it changes

**For § 2.1 as a quotable result — three specific corrections, all for `doc-keeper` or the
owner, none for this session to make:**

1. **The table needs its end date and its price basis.** As written it is reproducible only by
   accident. Suggested: *"QQQ traded (unadjusted) OHLC, 1999-03-10 to 2026-09-18, threshold
   0.10, `events.study` defaults."*
2. **The 12-month row's "+0.2%" should not carry a sign.** On the adjusted series it is −0.9
   pt. The defensible statement is "no measurable excess at 12 months".
3. **The 6-month "+12.9%" should carry an interval.** [+2.4, +23.1] points at 90% with the
   baseline held fixed; wider once the baseline's own 15-point interval is admitted; crossing
   zero at 95%. A one-decimal point estimate on 22 non-overlapping observations invites a
   precision the sample does not have.

**For plan entry 1 — one thing it now has that it did not, and one thing to add:**

- The entry-1 baseline is on the record: **adjusted OHLC, full range, 10% threshold, 6-month
  excess +12.8 pt, 90% interval [+2.4, +23.1] with the baseline fixed.** Entry 1's abandonment
  criterion ("the 6-month excess's bootstrap 90% interval containing zero **on the 10%
  threshold**") would **not** fire on this construction and **would** fire at 95% on the all-29
  construction. **That ambiguity is in the criterion, not in the data, and it is worth settling
  before entry 1 runs** — the criterion names a level and a bootstrap but not which of the four
  constructions above, and they do not all agree. Settling it now, before entry 1's result
  exists, is free; settling it afterwards is choosing the test after seeing the result.
- Entry 1 should fix a **price basis** in its configuration, not only a range. It currently
  says "QQQ **OHLC** bars" without saying adjusted or traded, and this finding shows that
  choice moves the baselines by up to 1.0 point — enough to flip the 12-month sign.

**For the strategy: nothing yet, and that is the honest answer.** The shape § 2.1 claims —
excess rising to a 6-month maximum and gone by 12 — survived both price bases, the full range
and leave-one-era-out, so it is not an artefact of any of those. It has **not** survived
another threshold, another underlying or a sub-period split, because none of those was run
here; that is entry 1. Until it has, "enter after a 10% correction and hold six months" rests
on 22 non-overlapping observations of one index over one history that has already been looked
at. **No position, sizing or exit decision should change on the strength of this file.**

**One platform gap found, written up and not built:**
`docs/specs/effective-sample-returns-its-subset.md` — `events.effective_sample` returns a
*count* of non-overlapping episodes but not *which ones*, so an interval on the effective
sample cannot be produced through the public API without re-implementing the greedy rule
outside the platform. This finding did re-implement it (verified against the count) and should
not have had to.
