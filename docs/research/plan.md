# The research plan

> **DRAFT — for discussion.** Written 2026-11-06 by `research-lead` running
> **non-interactively**. The one thing this role depends on — a person arguing back — has
> not happened. Section 2 separates the decisions that are mine and need challenging from
> the ones the data forces, which are not really decisions.

**Status:** no entry has been run. Nothing here has a result, and the criteria below are
therefore pre-registered in the strict sense: they were written before any number existed.

---

## 1 · Why this order

The order is **not** the one `research-questions.md` asks for, and the reason is a single
fact: **there is no settled strategy to falsify.** Section 5 of that file says its
falsification group "has the highest priority", and it is right that one random-entry run
decides whether an idea deserves further work — but a random-entry control needs a strategy
to control, and the only configuration in this repository (`run_backtest.py`, exported to
`out/baseline/`) is a demonstration of the interface: `sma_20 > sma_50`, delta 0.30, 30
days, out at +100% / −50% / 10 days. Nobody chose it as a thesis. Falsifying it would
produce a true statement about a strategy no one intends to trade, and would spend the
project's first pre-registered test on it. So the plan is ordered the way the architecture
document's own protocol orders research (`research-questions.md` section 9, item 3):
**Setup and Entry first, judged on the forward returns of the underlying with no exits
applied — an event study.** Entries 1 to 4 are of that kind. They need no strategy, no
exits, no option prices and no new code; they are the questions whose answers constrain
every strategy that might later be written, and they are the only group the synthetic chain
cannot corrupt, because they never touch it. Entries 5 to 9 are the falsification battery
and are **gated on a person naming the strategy**. Entries 10 to 15 need a capability that
does not exist and so lead to a spec rather than an experiment. Entries 16 to 20 are
blocked on real option data and are written out in full anyway, because the cheapest time to
fix the test's criteria is while no result can be seen.

---

## 2 · What in this draft is mine, and what is forced

**Forced by the data or the code — not decisions, and arguing with them is arguing with a
fact:**

- Every option-side question is blocked (section 4). The synthetic chain's IV is one number
  per day per expiry, shared by every strike (`backtest/data/options/synthetic/generator.py`
  writes `"iv": vol` for the whole strike grid). This is not a precision problem.
- The permutation test, the reverse test, the selection-perturbation test and the Headwind
  slot do not exist. `grep -rni "permutation\|reverse_test\|shuffle" backtest/` returns
  nothing; `backtest/research/ablation.py: NOT_YET` names the gaps. Their next step is a
  spec, not a run.
- There is no Monte Carlo anywhere in `backtest/`. `research-questions.md` 1.1 is marked
  "can do" and prescribes "run Monte Carlo under different drift assumptions". It cannot be
  done. See entry 15.
- `max_dte` is a field (`SyntheticChainConfig.max_dte = 120`), so raising it for LEAPS is
  one line. That does **not** unblock entry 17, and the ease of it is the trap.

**Mine, and the ones I most want argued with, in order of how much hangs on them:**

1. **Demoting the falsification group below the event studies.** This overrides the standing
   list's own stated priority. My reason is in section 1. The counter-argument is real: a
   random-entry control on the demo baseline is cheap, and if even the demo cannot beat
   random that is information about the *instrument* — buying 30-day 0.30-delta calls on a
   trend signal — and not only about the demo. If you take that view, move entry 5 to the
   front and say so here.
2. **The hold-out.** I propose reserving an **underlying** rather than a period: SPY is
   untouched by any run in this repository, while 2015–2024 has already been run
   (`out/baseline/`) and 1999–2026 has already been studied (the table quoted in 2.1). The
   longest period never looked at is 2025-01-01 to 2026-11-06 — about 23 months, which can
   reject gross failure and nothing finer. Reserving SPY buys independence in the underlying
   instead of in time, and `research-questions.md` 9.5 wants the effect to hold on each
   underlying separately anyway, so reserving SPY costs that check. Both options are bad.
   Pick one; do not leave it open, because the value of a hold-out is destroyed by choosing
   it later.
3. **Promoting the underlying-terms half of the stop question (entry 8) ahead of the rest of
   the exit research.** `research-questions.md` 3.1 marks the whole of it "needs data". Half
   of it does not: `ExcursionStudy.underlying_summary()` exists and is real data. I think
   that half is the more useful half. Section 3's first bullet is why that is a correction to
   a document rather than a reordering by convenience.
4. **Fixing the minimum trade count at 40 non-overlapping logical trades** before any
   strategy-level conclusion is drawn (entries 5 to 9). This is a group-4 choice
   (`research-questions.md` section 8) and so must be set before the research and not after.
   40 is my number and it is defensible rather than derived. Change it now if you disagree.

**What is absent and cannot be substituted:** `docs/feedback/` holds only its README. The
human has written no feedback, so **this plan has no evidence about which question actually
matters in use** — the one input nothing in the repository can derive. `docs/ideas/backlog.md`
is empty (`/explore` has not run) and `docs/findings/` holds only its README. Every ordering
judgement below therefore rests on the documents and the code, which is the weakest basis
this role has, and is the reason the draft is a draft.

---

## 3 · Five things said plainly, before the entries

**That question is already answered — and the answer is not in `docs/findings/`.**
`research-questions.md` 2.1 carries a complete result: QQQ 1999–2026, a 10% correction, 30
episodes, P(up) at 6 months 85.7% against a 72.9% unconditional baseline, excess +12.9%.
That is a finding, it is quoted in a standing-questions file, and `docs/findings/` is empty.
It therefore has no recorded data range beyond one line, no configuration, no guard state,
no trial count and no caveats section. **Entry 1 is not a re-run of it**; re-running it with
no new data or method would produce a second number, not a confirmation. Entry 1 attacks the
part that is open — whether the 6-month concentration survives other thresholds, another
underlying and sub-periods — and the first thing `research-analyst` should do is write the
existing result up as a finding so that what entry 1 varies is on the record.

**That result is likely noise.** Two specific cases, both pre-emptive:

- The 6-month window in 2.1 rests on **21 non-overlapping observations** (the file says so).
  At that sample an excess of +12.9 points has a confidence interval wide enough to contain
  zero on plausible assumptions, and the "gone by 12 months" part (+0.2 points) is the
  comparison most likely to be a sampling artefact. Entry 1 is therefore specified to report
  intervals and effective sample size, and its abandonment criterion is set on the interval,
  not the point.
- The demo baseline's 1,767 trades over 2015–2024 are **heavily overlapping** by
  construction: the entry `sma_20 > sma_50` is true on most days and
  `Lifecycle.reentry_policy` defaults to `immediate`, so the strategy re-enters continuously.
  Treating 1,767 as a sample size would be wrong by an order of magnitude. Every
  strategy-level entry below requires `r.anomalies()`' overlap count and the
  `single_trade_share` (0.35) and `one_year_share` (0.70) cuts from
  `backtest/core/thresholds.py` reported beside the headline number.

**We are optimising a realism assumption.** `research-questions.md` 5.5 is listed as one
question; it is two, and only one of them is research. `r.cost_sensitivity()` and
`r.delay_test()` are sensitivity tests of group-1 assumptions (λ is
`CostConfig.adverse_fraction`, default 1.0; the lag is `execution_lag_days`, default 1) and
they may never be tuned to improve a result. Worse, on the synthetic chain the spread they
scale is the fixture's own (`spread_pct` 0.02, `spread_floor` 0.05) — so **the cost level at
which an edge disappears is a property of the fixture**, and only the *shape* of the decay
is informative. Entry 6 is written as a sensitivity test with that stated, and its
abandonment criterion is about collapse, not about an optimum.

**That is extending the platform, not using it.** Six of the standing questions are
development decisions wearing a research question's clothes: 5.2 (reverse test), 5.3
(permutation test), 5.6 (selection perturbation), 3.3 (the Headwind slot), the systematic
drop-the-head curve in 5.4, and 1.1 (which needs a path simulator that does not exist).
Entries 10 to 15 say what each would cost in capability terms and stop there. **No spec is
written tonight** — a spec commits a person to build something, and that person has not been
in the conversation.

**Three rows of the documents contradict the code, and the code wins.** Found by reading it,
not by impression. These matter because `requirements.md` is the place where "can the
platform support it" is supposed to be answered, and in these three places it answers wrong:

| Document says | The code says | Consequence for this plan |
|---|---|---|
| `requirements.md` Layer 2: "Slot 1 Setup / Regime … **Not wired into the engine as a gate**"; `ablation.py: NOT_YET["setup"]` repeats it | `run(setup=...)` exists and `backtest/engine/loop.py:886-919` arms or disarms the candidate on it, with a `component_id="setup"` record | Entry 2 is **runnable now** by two runs differing only in `setup=`. `r.ablate(["setup"])` still refuses, so the comparison is run by hand. Event-driven engine only — the trade-table engine has no `setup` |
| `requirements.md` Layer 6: MFE/MAE "**Premium terms only**" | `ExcursionStudy.underlying_summary()` and `premium_per_underlying_point()` exist (`backtest/research/excursion.py:96,120`); conformance row 5.2 of the same file says "both terms (1.38)" | Entry 8 exists. The underlying-terms half is the half that survives a synthetic chain, and the file that is supposed to say what is built says it is missing |
| `requirements.md` Layer 5: "Shadow tracking after exit — **absent** — which is why counterfactual exits cannot be computed"; `research-questions.md` 10.3 lists both as "the next things to build" | `run(shadow_days=...)` exists, `TradeBook.shadow_frame` holds it, and `r.counterfactual(...)` reads it (`backtest/api/sdk.py:440,1169`) | The exit research is **not blocked on tooling**. It is blocked on option data alone, which is a different and harder blocker. Entry 19 says so |

`research-questions.md` 5.1 ("a random-entry control — **needs tooling**") and 5.5 ("what one
more day's delay does is not built") are stale in the same direction: `research/baselines.py`
and `research/delay.py` both exist. **The standing list understates the platform in five
places and overstates it in one** (1.1's Monte Carlo). `doc-keeper` should be asked to
reconcile them; I may not edit either file.

---

## 4 · The constraint that governs the list: what the synthetic chain cannot answer

The synthetic chain is a **test fixture**. Its generator says so
(`backtest/data/options/synthetic/generator.py`, first paragraph) and `SyntheticChainConfig`
says "no volatility skew, no term structure". Concretely: one IV per `(day, expiry)`, taken
from the VXN proxy, applied to every strike in the grid; a spread that is a flat 2% of
theoretical with a $0.05 floor regardless of moneyness or liquidity; `volume` and
`open_interest` written as `UNAVAILABLE`; and greeks flagged `greeks_model_derived = True`,
so selecting by delta on it is circular. `backtest/research/boundary.py` already labels every
research output `UNDERLYING`, `OPTION` or `MIXED` for exactly this reason, and `MIXED` is the
worst case rather than a middle one.

One consequence deserves naming because it looks like an exception and is not: the chain's IV
**does** vary over time, since it tracks VXN. So a question like "is performance better when
IV is low" returns a clean, plausible number — and it is a tautology of Black-Scholes, which
prices the option lower when the vol input is lower. The underlying's forward return
conditioned on `iv_rank_252` is real; the option P&L conditioned on it is arithmetic. Entries
3 and 4 take the first half; entry 20 holds the second.

### 4.1 · Blocked on real option data — named individually

| # | The question (`research-questions.md` §) | Why the fixture cannot answer it |
|---|---|---|
| 16 | **1.2 · the term structure of the volatility risk premium** | Requires IV to differ by expiry beyond what one vol input implies. The fixture has one IV per expiry drawn from the same proxy, so its term structure is flat by construction and the premium it reports is zero plus pricing noise |
| 17 | **1.3 · deep-ITM LEAPS as leveraged long exposure** | Two compounding blockers: strikes reach only ±20% of spot (`strike_range`) so a 25%-ITM call is outside the grid, and the rate is a constant fallback — `research-questions.md` 7.3 measures a 1% rate error as **6.4%** of a two-year premium, a fifth of its time value. Raising `max_dte` to 730 makes it *run*, which is the trap |
| 18 | **3.1 · where the stop belongs (premium terms)** | The MAE distribution in premium terms is a distribution of option prices. Its shape comes from the fixture's constant-IV path, not the market's. The cited "a −50% stop cuts 129 trades, 14 of them profitable" is a fixture statistic |
| 19 | **3.2 · a fixed take-profit ratio against a trailing stop** | Same cause. Note that the *tooling* is built (shadow tracking plus `r.counterfactual()`), which makes this the most tempting entry in the file to run anyway. The result would be labelled `OPTION` scope and in-sample, and would still be a statement about Black-Scholes |
| 20 | **2.2 / 2.3 · the IV-level entry condition, in its option-P&L form** | The tautology above. The underlying-side half is entries 3 and 4 |
| 12 | **5.6 · selection perturbation (the adjacent strike, the adjacent expiry)** | Doubly blocked: not built (`requirements.md` 4.10 missing, `ablation.py: NOT_YET["select"]`), and on a chain where adjacent strikes share one IV the perturbation is a smooth slide along one Black-Scholes curve, so it cannot fail |

### 4.2 · What real data would unblock them

**Fields.** From `backtest.data.options.adapters.describe_schema()` — not from my memory of
what option data looks like.

*Required, or the loader refuses the file:* `asof` (the quote date),
`symbol`, `expiry`, `strike`, `right` (C/P), `bid`, `ask`, `underlying_price` (**the traded
price, not the adjusted one**, measured at the same moment as the quote).

*Optional, with the stated consequence of absence:* `available_at` (absent → assumed equal to
`asof`, and the run records that it was assumed — get this if the vendor revises or
backfills, because it is the only route to the point-in-time alignment `requirements.md`
marks absent); `volume` and `open_interest` (absent → the liquidity filters are **skipped and
flagged**, which matters for entries 12 and 19 and for ever saying anything about capacity);
`iv` (absent → solved from the bid/ask mid, which is acceptable); `delta`/`gamma`/`vega`/
`theta` (absent → derived from IV by Black-Scholes and flagged — acceptable, and arguably
preferable to a vendor's undocumented model); `last` (no effect, fills come from bid/ask).

So the irreducible ask is **eight columns**, and the two worth paying extra for are
`open_interest` and `available_at`.

**Frequency.** Daily end-of-day is enough for every blocked entry above. The binding
requirement is not frequency but **simultaneity**: the chain snapshot and
`underlying_price` must come from the same timestamp window, which is `requirements.md`'s
unenforced data rule 3. A vendor whose quote is a 16:00 composite and whose spot is a 16:15
print will make every moneyness slightly wrong in a way nothing here can detect. Intraday
quotes are needed only for the latency question (`research-questions.md` 10.1) and no entry
in this plan depends on them.

**History, and the cheapest usable sample — which differs by question, and that is the
useful part:**

- **Entry 16 (the VRP term structure) is by far the cheapest, and is not a chain at all.**
  It needs **one row per `(asof, expiry)`**: the IV of the strike nearest ATM, for every
  listed expiry, daily. No strike grid, no greeks, no bid/ask. Ten years of that for QQQ is
  of the order of tens of thousands of rows, and it answers the one place in the choice of
  tenor where `research-questions.md` 1.2 says an edge might actually exist. **If only one
  dataset can be bought, buy this one.** It is also the only blocked entry whose answer is
  not strategy-dependent.
- **Entries 18, 19 and 12 (the exit and selection research)** need the strike grid, but a
  narrow one: calls only, strikes within **±25% of spot**, expiries out to **120 days**,
  daily EOD. Not a delta band — a delta band would make entry 12 impossible, since the
  adjacent strike is the thing being varied. The history must span more than one volatility
  regime or the MAE distribution describes one market: **2018-01-01 to 2019-06-30**
  (a vol-spike year), **2020-01-01 to 2021-06-30** (a crash and a melt-up) and
  **2022-01-01 to 2023-06-30** (a bear market) — 4.5 years in three stretches rather than ten
  continuous years. Three stretches is the cheapest honest sample: continuity buys nothing
  here because the unit of analysis is the trade, while regime coverage is what the
  conclusion depends on.
- **Entry 17 (LEAPS)** is a superset and the most expensive: strikes to **±35% of spot** (a
  25%-ITM call must be inside the grid with room to move) and expiries to **730 days**, plus
  a real **rate curve** wired in before any conclusion, per `research-questions.md` 7.3. This
  one should wait.
- **The sample floor, in trades rather than in days:** 40 non-overlapping logical trades per
  cell being compared (section 2, point 4). An exit study comparing a −40% stop against −50%
  is comparing two cells, so the stretch must be long enough to yield 40 each after the
  overlap correction, not 40 in total.

### 4.3 · What is answerable tonight, with no new data and no new code

Entries 1 to 4 — all four of the Setup-and-Entry questions, in their underlying-side form.
They use `backtest/research/events.py` (event studies, which never touch an option),
`backtest/signals/setup/regime.py` (three rule-based dimensions, thresholds declared
unfitted), `signals/registry.py: iv_rank_252` (computed from the real VXN series, so the
signal itself is trustworthy) and `backtest/data/multi_asset.py` (VIX, VIX3M, VXN, the 3m and
10y Treasury, the dollar, HYG and LQD). Entries 5 to 9 become answerable the moment a person
names a strategy, and need no data either.

---

## 5 · The entries

Each has the six parts. **Any part marked "not ready" is not to be filled in by guessing** —
an entry that says "not ready, missing X" is doing its job.

### Part A · answerable now; needs no strategy and no option prices

---

#### Entry 1 · Is the 6-month edge window after a correction real, or 21 observations?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 2.1 · **Scope:** `UNDERLYING`

- **Hypothesis (falsifiable).** The excess probability of QQQ finishing up 6 months after a
  10% correction, over the unconditional 6-month baseline, is **at least +5 percentage
  points**, and its 6-month peak is not an artefact of the 10% threshold: at 5%, 15% and 20%
  thresholds the 6-month excess remains the largest of the four horizons tested
  (1/3/6/12 months) in at least **three of the four** threshold runs.
- **The test.** `backtest.research.events.study(bars, threshold, horizons_months=(1,3,6,12))`
  for `threshold` in `(0.05, 0.10, 0.15, 0.20)`, on QQQ **OHLC** bars (not a close series —
  `study()` returns `basis` saying the deepest drawdown is understated on closes alone),
  1999-03-10 to 2024-12-31. Then the same four on SPY. Report `sample`, `effective`,
  `excess`, and `excluded_by_data_end` for every row. Bootstrap the 6-month excess by
  episode (`research/validation/intervals.py`, block length ≥ the horizon).
  **Four thresholds × two underlyings = 8 trials**, declared here and recorded via
  `research/tracker.py`.
- **Data required.** QQQ and SPY daily OHLC. **Exists** — `data/underlying.py`, cached.
  Nothing else.
- **Platform support.** **Yes, fully.** `research/events.py` already handles the traps this
  question walks into: episodes rather than days (one correction is one stretch from one
  prior high, so August and September 2024 are one episode), the unconditional baseline beside
  every conditional figure, `effective_sample()`, end-of-range censoring, and detection on
  closes with intraday lows for pain.
- **Smallest credible experiment.** The 10% and 20% thresholds on QQQ only — two runs, under
  a minute. If the 6-month peak does not survive 20%, the rest is not worth running.
- **What makes us abandon it.** The 6-month excess's bootstrap 90% interval containing zero
  **on the 10% threshold**, or the 6-month horizon failing to be the largest excess in two or
  more of the four threshold runs. Either means the window was chosen by looking at the
  answer. Note the honest caveat that must be carried: the 1999–2026 period has already been
  looked at for this question, so this is a robustness check and not an out-of-sample test,
  and it cannot be made into one.

---

#### Entry 2 · Does any regime dimension separate the underlying's forward return?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 4.1 · **Scope:** `UNDERLYING`

- **Hypothesis.** On at least one of the three declared dimensions, the **best** and
  **worst** buckets' mean 21-session forward return on QQQ differ by at least **1.5% in
  absolute terms**, with each bucket holding at least 200 sessions, and the sign of the gap
  holding in both halves of the period split at 2017-06-30.
- **The test.** No strategy and no regime condition at all. Build the cross-market panel
  (`data/multi_asset.py`), classify with
  `signals/setup/regime.classify(panel, bars, DEFAULT_DIMENSIONS)` — volatility level
  (VXN at 0.18/0.28), term structure (VIX3M/VIX at 1.0), trend (above/below the 200-day —
  the three thresholds are declared unfitted in the module, which is what makes this a
  discovery procedure rather than drawing the target around the arrow). Then `stratify` the
  **forward 21-session return of the underlying**, not a strategy's P&L. Report effect size,
  bucket count and the two-half split together, as the module's procedure requires.
  **Three dimensions = 3 trials**, plus the two-half split, declared here.
- **Data required.** QQQ OHLC, plus `vxn`, `vix`, `vix3m` from `data/multi_asset.py`. All
  exist. **One real constraint:** VIX3M has **no data before 2011** (the series' own caveat),
  so the term-structure dimension's sample is 2011 onward and must be reported on that range,
  not silently on the full one.
- **Platform support.** **Yes.** And more than `requirements.md` claims: `run(setup=...)` is
  wired into the event-driven engine as the Armed gate
  (`backtest/engine/loop.py:886-919`), so the follow-up — what a regime filter is worth to a
  strategy — is two runs differing in one argument. `r.ablate(["setup"])` refuses with a
  stale reason; use the two runs.
- **Smallest credible experiment.** The volatility-level dimension alone, on QQQ, 2011–2024.
  One dimension, three buckets, one number each.
- **What makes us abandon it.** No dimension producing a gap above 1.5% with both buckets
  above 200 sessions; **or** any gap that does appear reversing sign across the 2017-06-30
  split. Then a rule-based regime has not been shown to separate anything, and
  `research-questions.md` 10.5's condition for considering a statistical regime (HMM) has
  **not** been met either — that condition is performance separating on something no
  threshold captures, which is a different and much harder finding than this test produces.
  Record the dimension count: stratifying on three dimensions is three trials, exactly as
  sweeping three parameters would be.

---

#### Entry 3 · Does `iv_rank_252` separate the underlying's forward return?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 2.2, underlying half ·
**Scope:** `UNDERLYING`

- **Hypothesis.** The underlying's mean 21-session forward return in the **lowest** quintile
  of `iv_rank_252` differs from the **highest** quintile by at least **1.5%**, and the
  direction is stable across the two halves of the period.
- **The test.** `signals/registry.py: iv_rank_252` on QQQ (VXN through
  `indicators.rank_in_window(v, 252)`), quintiled; the forward 21-session return of the
  underlying by quintile, each with its count; the two-half split as in entry 2. Five
  quintiles is **one trial** if declared as a quintile split in advance, which it is here —
  reporting the best-looking cut after trying deciles, terciles and quintiles would be three.
- **Data required.** QQQ OHLC and the VXN series. Both exist. VXN begins 2001, so the sample
  is 2001 onward.
- **Platform support.** Yes, for the underlying-side form.
- **Smallest credible experiment.** It is already the smallest: two numbers and two counts.
- **What makes us abandon it.** A gap under 1.5%, or one that flips sign across the halves.
  **And the thing this entry must not become:** the same question asked of option P&L. That
  version is entry 20 and is blocked, because the fixture prices options off the same VXN
  series the signal is built from, so a positive answer is guaranteed by arithmetic. If a
  result from this entry is later quoted as "buying when IV is low is better", that is the
  conflation, and it is the single most likely way this plan gets misread.

---

#### Entry 4 · Is there a region where "fallen far, and IV off its peak" resolves the tension?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 2.3, underlying half ·
**Scope:** `UNDERLYING`

- **Hypothesis.** Among 10% correction episodes, those where VXN has already fallen at least
  **20% from its episode peak** at the entry date show a 6-month forward excess (over the
  unconditional baseline) at least **5 percentage points higher** than episodes entered at
  the VXN peak.
- **The test.** Take the episodes from entry 1's 10% run
  (`events.find_corrections(bars, 0.10)`), split each by whether VXN at the measurement date
  has retraced 20% from its within-episode maximum, and compare the two groups' 6-month
  outcomes against the same unconditional baseline. Report both groups' episode counts and
  effective sample sizes.
- **Data required.** QQQ OHLC and VXN. Both exist.
- **Platform support.** **Partial, and the gap is honest.** `research/events.py` measures
  outcomes from an episode's crossing date; conditioning the measurement date on a second
  series is not a parameter it takes, so this is a short analysis script over
  `study()["episodes"]` and `study()["outcomes"]` rather than a platform call. That is
  analysis, not a new capability — no spec.
- **Smallest credible experiment.** It is already minimal, and it is **probably too small to
  conclude from**, which is the point of saying so now: entry 1 reports roughly 30 episodes
  at the 10% threshold and 21 non-overlapping at 6 months. Splitting 21 in two gives
  something like 10 and 11. **A difference between two groups of ten episodes is not
  evidence**; at best this entry produces a direction and a confidence interval wide enough
  to be useless, and the right output is a finding that says so. It is in the plan because
  `research-questions.md` asks it and because the cost is minutes — not because it can be
  answered.
- **What makes us abandon it.** Either group falling below **8 episodes** after the split, or
  the 90% interval on the difference spanning more than 20 percentage points. Expect the
  latter.

---

### Part B · answerable now, but gated on a person naming the strategy

> **These five cannot start until a person writes the strategy down.** Not the demo in
> `run_backtest.py`. The gate is deliberate: pre-registering a test against a configuration
> nobody intends to trade burns the one thing pre-registration provides. The honest way to
> run them against the demo is to label the whole group a **method test** — proving the
> battery works — and to record that no conclusion about a strategy was drawn. If that is
> what you want, say so here and I will record it as your call.

---

#### Entry 5 · Can random entries at the same frequency do as well?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 5.1 · **Scope:** `UNDERLYING`
(both sides priced by the same chain, so the comparison is honest even on the fixture)

- **Hypothesis.** The strategy's total P&L sits **above the 90th percentile** of 50
  random-entry draws at the same entry frequency, the same contract rules and the same exits.
- **The test.** `r.baselines(draws=50)` on the named strategy. `research/baselines.py` reports
  a percentile across draws and **refuses to offer a single draw**, which is correct: one
  draw beating or losing to the strategy is a coin flip reported as a finding. Read
  `Comparison.valid_on_synthetic` — it is `True` for `random_entry` and `False` for
  `delta_equivalent`, and on the fixture the second's **direction** may be read but not its
  size.
- **Data required.** None beyond the strategy's own run.
- **Platform support.** **Yes — built.** `requirements.md` 4.5 is `done`.
  `research-questions.md` 5.1 says "needs tooling" and is stale.
- **Smallest credible experiment.** `draws=50` is the registry default
  (`random_entry_draws`, an engineering choice, not tunable). Twenty draws would resolve a
  90th percentile poorly; do not reduce it to save time.
- **What makes us abandon the strategy.** The strategy at or below the **60th percentile** of
  random. Then the timing does nothing and the return is drift plus structure, and no amount
  of work on exits or selection changes that. This is the cheapest kill in the plan and it is
  why `research-questions.md` puts the group first — a point I am overriding on sequencing
  grounds only, not on merit.

---

#### Entry 6 · Does the result survive realistic costs and one more day of delay?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 5.5 · **Scope:** `OPTION` for the
cost level, `MIXED` for the shape

- **Hypothesis.** Total P&L stays positive at **2x** the modelled cost, and the P&L at
  `execution_lag_days = 2` retains at least **70%** of the P&L at lag 1.
- **The test.** `r.cost_sensitivity(multiples=(1.0, 2.0, 3.0))` and
  `r.delay_test(extra=(1, 2))`. Both are **sensitivity tests of group-1 realism assumptions**
  and neither value may be moved to improve a result (`research-questions.md` section 8).
  Report the anomaly scan's cost warning (it fires above 30% of gross).
- **Data required.** None. **But the cost level is the fixture's**: the synthetic spread is a
  flat 2% of theoretical with a $0.05 floor and does not widen with moneyness or stress, so
  real spreads on a 0.30-delta 30-day QQQ call are the thing this test is pretending to know
  and does not. **Only the shape of the decay is informative on the fixture**; the breakeven
  multiple is not a number to quote.
- **Platform support.** **Yes — both built.** `requirements.md` 4.8 and 4.9 are `done`.
  `research-questions.md` 5.5's "what one more day's delay does is not built" is stale:
  `research/delay.py` exists and `run(execution_lag_days=n)` is exposed.
- **Smallest credible experiment.** The 2x cost run and the lag-2 run. Two runs.
- **What makes us abandon it.** P&L turning negative at 2x cost, **or** the lag-2 run
  retaining under 50%. The second is the more interesting failure: the default is already a
  full session between signal and fill, so as `research/delay.py` puts it, a steep decline
  here is more surprising than it would be in an intraday system. Read it as a question about
  whether the strategy is implementable at all before reading it as one about the edge.

---

#### Entry 7 · What is left without the best N trades?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 5.4 · **Scope:** inherits the run's

- **Hypothesis.** Removing the best **5** trades leaves total P&L positive, and no single
  trade accounts for more than **35%** of total P&L (`single_trade_share`), and no single
  year for more than **70%** (`one_year_share`).
- **The test.** `r.investor_view()` gives the top-5 share and the total without them;
  `r.anomalies()` fires the one-year concentration rule. Both cuts come from
  `core/thresholds.py` and are group-4 method choices already fixed — I am not choosing them
  here, which is the point of their living in a registry.
- **Data required.** None.
- **Platform support.** **Partial, and that is enough for this hypothesis.**
  `requirements.md` 4.11 is `partial`: the top-5 figure exists, the **systematic
  drop-the-best-N curve does not**. The hypothesis above is written against what exists. The
  curve is entry 14 and needs a spec.
- **Smallest credible experiment.** One call on an existing result. Seconds.
- **What makes us abandon it.** P&L turning negative without the top 5, or either threshold
  breached. Then the strategy is an envelope around a handful of episodes, and the sample for
  every other entry in Part B is effectively those episodes rather than the trade count.

---

#### Entry 8 · Is the stop placed where the winners routinely go — in underlying terms?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 3.1, the half that is not blocked;
`requirements.md` 4.15 · **Scope:** `UNDERLYING`

- **Hypothesis.** The median adverse excursion of **winning** trades, measured in the
  **underlying's** terms, is smaller than the underlying move the premium stop implies — so
  the stop does not fire on the typical winner's worst moment. Falsifiably: the underlying
  move equivalent to the configured premium stop is at least **1.5x** the winners' median
  adverse underlying excursion.
- **The test.** `r.excursion()`, then `underlying_summary()` for the underlying-terms
  distributions and `premium_per_underlying_point()` for the leverage — how many percent of
  premium one percent of the underlying was worth, adversely. The module states the case
  this exists for: a −40% premium stop on a position levered 25x fires on a **1.6%** move in
  the underlying, which is noise. Also read `ExcursionStudy`'s **censoring detection**: if
  the run had a stop, the MAE distribution is cut off at exactly the level you are trying to
  choose, and using it to choose is circular. **So this entry requires a companion run with
  `ExitRule(hold_days=N)` only** — `take_profit=None`, `stop_loss=None`, which the dataclass
  explicitly permits as "a real configuration rather than a stand-in" and which requires at
  least one time exit.
- **Data required.** None beyond the strategy's run, plus the no-active-exits companion run.
  **This is the entry that shows why the plan does not quietly reorder itself**: the premium
  half of `research-questions.md` 3.1 stays blocked at entry 18, and nothing here is a proxy
  for it. The underlying-terms half is a **different question** — is the stop set in a
  quantity that is signal or noise — and it is answerable because the underlying's path is
  real data on any chain.
- **Platform support.** **Yes**, contradicting `requirements.md`'s Layer 6 row (see section
  3). The censoring detection and the underlying-terms pair both exist in
  `research/excursion.py`.
- **Smallest credible experiment.** The no-active-exits run plus one `excursion()` call.
- **What makes us abandon it.** The premium stop's underlying equivalent coming in **below**
  the winners' median adverse excursion — the stop is killing winners, and the exit
  configuration is wrong independently of anything the premium distribution could say. Or the
  censored share exceeding `censored_share_warning` (0.30) even on the companion run, which
  would mean the companion run was not actually exit-free and the entry is **not ready**.

---

#### Entry 9 · Does it hold on SPY?

**Dated:** 2026-11-06 · **From:** `requirements.md` 4.17 · **Scope:** inherits the run's

- **Hypothesis.** The same strategy on SPY produces the same **sign** of total P&L and a
  Sharpe within **0.5** of QQQ's.
- **The test.** The identical configuration with `symbol="SPY"`. The vol proxy switches
  automatically (`VOL_PROXY_BY_SYMBOL`: SPY to `^VIX`), which is itself a difference to
  record — the signal is not literally the same series.
- **Data required.** SPY OHLC and the VIX series. Both reachable. **Decision needed first:**
  if SPY is the hold-out (section 2, point 2), **this entry cannot be run until the end**,
  and that is the cost of that choice. Stated here rather than discovered later.
- **Platform support.** Partial by design: one underlying per run, so this is two runs
  compared by hand. `requirements.md` 6.4 records that **pooling across underlyings does not
  exist**, so `research-questions.md` 9.5's "pool to gain sample size, but require the effect
  to hold on each underlying separately" can only be done in its second half.
- **Smallest credible experiment.** One run.
- **What makes us abandon it.** Opposite signs. Then the result is specific to QQQ in a way
  nothing in the hypothesis explains, and the most likely explanation is the sample.

---

### Part C · needs a capability that does not exist — the next step is a spec

> **No spec is written tonight.** A spec commits a person to build, and the person has not
> been in this conversation. Each entry below says what is missing and what becomes
> answerable, which is what a spec would open with.

| # | The question | What is missing, in code terms | What becomes answerable | Not ready because |
|---|---|---|---|---|
| 10 | **5.3 · permutation test** — shuffle the signal's timing, keep its distribution | Nothing exists: `requirements.md` 4.6 is `missing`; no `shuffle`/`permutation` anywhere in `backtest/`. Needs a signal-level permutation that preserves the entry count and the exposure, and a null distribution over many shuffles | Whether the **timing** does anything, as opposed to the exposure simply having been long enough. Distinct from entry 5: random entry randomises *when*, a permutation randomises *the signal's alignment to the price path* | The hypothesis and the abandonment criterion depend on how the null is constructed (per-episode block shuffle or free permutation), which is a design choice the spec must settle first. **Writing a threshold now would be inventing one** |
| 11 | **5.2 · reverse test** — does doing the opposite also make money | `requirements.md` 4.7 `missing`. Needs the entry condition negated **and** the right flipped, and a decision on which of the two "opposite" means | Whether the return is beta rather than signal. Both directions making money is the clearest possible kill | Same reason: "the opposite" is ambiguous in a way that changes the answer, and the spec has to choose. Partially overlaps entry 5, and the spec should say whether it adds anything beyond it |
| 12 | **5.6 · selection perturbation** | `requirements.md` 4.10 `missing`; `ablation.py: NOT_YET["select"]` says the nearest thing is not built. Needs the adjacent strike and adjacent expiry re-run with everything else fixed | Whether the result was fitted to one contract's quote noise | **Doubly blocked** — also needs real data (section 4.1). Build-then-wait would be the wrong order: on the fixture, adjacent strikes share one IV, so the test cannot fail and building it first would produce a passing test that means nothing |
| 13 | **3.3 · the headwind discovery procedure** | Slot 5 does not exist (`requirements.md` Layer 2 `absent`, `ablation.py: NOT_YET["headwind"]`). The **prerequisites do** exist: daily trajectories (`paths_frame`), shadow tracking, and exit-free runs | For positions that ended badly, whether there was an observable warning beforehand — and what acting on it would have done | The measurement half is partly runnable now (split trajectories by outcome, test separation on holding day k), but **acting** on a warning cannot be simulated without the slot. The underlying-side features of a trajectory are legitimate on the fixture; the premium-side ones are not, and the spec must say which half it is building |
| 14 | **5.4 · the systematic drop-the-best-N curve** | `requirements.md` 4.11 `partial`: top-5 exists, the curve does not. The smallest capability in this table | Whether P&L degrades smoothly or falls off a cliff at a particular N — a shape the top-5 number cannot show | Not blocking: entry 7 answers the question that matters with what exists. Listed so it is not mistaken for missing analysis |
| 15 | **1.1 · matching the tenor to the strength of the drift view** | **No Monte Carlo exists** — no path simulator, no drift-parameterised resampling, nothing. The standing list marks this "can do" and prescribes exactly that method | At what assumed drift the short-tenor/long-tenor ordering flips, which is a decision rule for choosing tenor from the strength of a view | **The standing list is wrong about this one.** A simulator is a new capability, and one with a trap: the ordering it reports would come from the simulator's own return distribution, so the conclusion is "under GBM with drift mu" and not "in this market". A block bootstrap of real QQQ returns would be the honest form, and that is a design decision for a spec |

---

### Part D · blocked on real option data

> Criteria fixed now, while no result can be seen — which is the only moment they can be
> fixed honestly. Each entry runs the day the data in section 4.2 exists, with no
> renegotiation.

---

#### Entry 16 · Is long-dated IV cheaper relative to realised volatility than short-dated?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 1.2 · **Scope:** `OPTION`

- **Hypothesis.** The mean difference (ATM IV at entry minus realised volatility of the
  underlying over the matching forward window) is at least **2 volatility points larger** for
  a 365-day tenor than for a 30-day tenor, and the sign of the gap holds in each calendar
  year of the sample.
- **The test.** For each `asof`, take ATM IV at each listed expiry; pair it with the
  underlying's realised volatility from `asof` to that expiry; average by tenor bucket
  (21-45, 46-120, 121-365, 366-730 days); report per year.
- **Data required.** **The cheapest ask in this plan, and not a chain:** one row per
  `(asof, expiry)` — the nearest-ATM strike's `iv`, daily, QQQ, ten years. If only one dataset
  is bought, buy this.
- **Platform support.** The realised-volatility half exists. The IV-by-tenor half has no home:
  `requirements.md` marks the **surface builder absent** and 6.1 records that "IV at entry vs
  realised volatility over the holding period does not exist" as a missing options metric.
  So this needs a small amount of code **and** data — but the code is an analysis over two
  series, not a platform capability, so it is not a spec.
- **Smallest credible experiment.** Two tenor buckets (30 and 365 days) over three years.
- **What makes us abandon it.** A gap under 2 points, or a sign that flips in more than one
  calendar year. Then `research-questions.md`'s claim that 1.2 is "the only place in the
  choice of tenor where a real edge might exist" is falsified, and the whole of section 1
  becomes a trade-off to be chosen rather than an edge to be captured.

---

#### Entry 17 · Are deep-ITM LEAPS better than buy-and-hold after time value?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 1.3 · **Scope:** `OPTION`

- **Hypothesis.** A rolled 2-year 25%-ITM QQQ call delivers **positive alpha** against QQQ
  buy-and-hold after the time value paid, **and** its time underwater is no more than
  **1.5x** QQQ's.
- **The test.** `r.investor_view()`'s beta/alpha comparison and time underwater, against QQQ
  buy-and-hold, on a rolling LEAPS position.
- **Data required.** The most expensive ask: strikes to ±35% of spot, expiries to 730 days,
  daily. **Plus a real rate curve**, which is a prerequisite and not a refinement —
  `research-questions.md` 7.3 measures a 1% rate error as 6.4% of a two-year premium, and the
  LEAP's time value is itself about 30% of the premium, so the error is a fifth of the
  quantity being measured. `data/market.py` currently falls back to a constant
  (`fallback_risk_free_rate`, 0.02).
- **Platform support.** `investor_view()` exists. **Three things must change first** and the
  first one is deceptively easy: `SyntheticChainConfig.max_dte` is 120 and `strike_range` is
  0.20, so raising `max_dte` to 730 makes a LEAPS backtest **run** on the fixture and produce
  a plausible, worthless number — a property of Black-Scholes at a constant rate and a
  constant IV. The second is the rate curve (a group-1 realism assumption, so built
  conservatively and never tuned). The third is the data.
- **Smallest credible experiment.** Five non-overlapping two-year holding periods.
- **What makes us abandon it.** Alpha at or below zero, or time underwater above 1.5x. Note
  what `research-questions.md` 1.3 already implies: the time value is about 15% a year, which
  is "almost exactly QQQ's historical annual return" — so the prior should be that this fails,
  and a positive result on five periods deserves more suspicion than a negative one.
- **Priority.** Last. Most expensive data, a prerequisite build, and an unfavourable prior.

---

#### Entry 18 · Is −40% to −50% the right region for the stop? (premium terms)

**Dated:** 2026-11-06 · **From:** `research-questions.md` 3.1 · **Scope:** `OPTION`

- **Hypothesis.** Across stops from −20% to −70% in 10-point steps, total P&L forms a
  **plateau** rather than a spike: at least **75%** of the neighbours of the best level sit
  within **20%** of it (`plateau_share` and `plateau_tolerance` in `core/thresholds.py` —
  method-group values already fixed, not chosen by me here).
- **The test.** The MAE distribution from an **exit-free** run (or the censored share is above
  `censored_share_warning` and the study says so), then `r.counterfactual(take_profits=...,
  stop_losses=...)` over the grid. Six levels is **6 trials**. Every counterfactual result is
  **in-sample by construction** (`research-questions.md` 9.6) and must be confirmed out of
  sample before anything is adopted.
- **Data required.** Real chain: the narrow grid in section 4.2 — calls, ±25% of spot, to 120
  days, across the three regime stretches. 40 non-overlapping trades per cell being compared.
- **Platform support.** **Fully built, and that is the danger.** Shadow tracking
  (`run(shadow_days=...)`, event-driven only) and `r.counterfactual()` both exist —
  `requirements.md`'s Layer 5 "shadow tracking absent" row is stale. So this entry will run
  today, produce a grid, and `boundary.py` will label it `OPTION`. **It must not be run on the
  fixture for a conclusion.** The cited figures in `research-questions.md` 3.1 (129 trades
  cut, 14 of them profitable, 9,145 of profit against 113,025 of losses) are fixture
  statistics and should not be carried into a decision.
- **Smallest credible experiment.** Three levels (−30, −50, −70) on one regime stretch.
- **What makes us abandon it.** No plateau — the best level's neighbours falling outside the
  `slope_share` floor (0.40) too. Then the stop level is noise in this sample and the honest
  output is a region with its width, not a number.

---

#### Entry 19 · A fixed take-profit ratio, or a trailing stop?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 3.2 · **Scope:** `OPTION`

- **Hypothesis.** A trailing stop (out after giving back one third of the peak gain) beats
  the better of a fixed +30% and a fixed +100% target by at least **15% of total P&L**, on
  the same trades.
- **The test.** **Not the counterfactual grid** — `research/counterfactual.py: sweep_levels`
  takes static `take_profits` and `stop_losses` and cannot express a path-dependent exit. A
  trailing stop is stateful (`stop_loss/trailing` carries a high-water mark), so this is
  `r.compare_forms("stop_loss", [...])` over the registered forms — `premium`, `underlying`,
  `ratchet`, `trailing` — which runs each as a full run rather than a replay. Event-driven
  engine only. Being full runs rather than replays, these are **not** in-sample by
  construction the way entry 18 is, which makes this the stronger of the two exit entries.
- **Data required.** The same narrow real grid as entry 18, and the same 40-trade floor per
  arm. `research-questions.md`'s cited hit rates (+100% reached by about 16% of trades, +30%
  by 49%) are fixture figures.
- **Platform support.** **Yes, and better than expected.** A `trailing` form is registered
  (`backtest/signals/stop_loss/__init__.py:168`, `fraction` = how much of the best premium may
  be given back), alongside `ratchet`, `premium` and `underlying`. `requirements.md` 6.3's
  "only two slots have more than one form" counts the slots correctly and undersells the
  stop slot, which has four. So the comparison this question asks for is one call.
- **Smallest credible experiment.** One regime stretch; `premium` at 0.5 against `trailing`
  at 0.33. Two forms, one call.
- **What makes us abandon it.** A margin under 15%. The two exits catch different trade
  classes, so a small margin means the choice does not matter and the slot should be left at
  whichever is simpler to run live.

---

#### Entry 20 · Is option performance better entering at a low IV rank?

**Dated:** 2026-11-06 · **From:** `research-questions.md` 2.2 / 2.3, option-P&L form ·
**Scope:** `OPTION`

- **Hypothesis.** Strategy P&L per trade in the lowest `iv_rank_252` quintile exceeds the
  highest quintile by at least **25%**, on real option prices.
- **The test.** The strategy's trades stratified by `iv_rank_252` at entry. Five quintiles,
  declared in advance as one trial.
- **Data required.** A real chain. **This entry exists mainly to be refused**, and the reason
  is the sharpest instance of the whole constraint: the fixture prices every option off the
  **same VXN series** that `iv_rank_252` is computed from, so Black-Scholes necessarily
  returns a cheaper option when the rank is low, and a confirming result is guaranteed by
  arithmetic. It will look like the cleanest finding in the project.
- **Platform support.** Yes, mechanically. Which is exactly why the refusal has to be written
  down rather than left to be remembered.
- **Smallest credible experiment.** None on the fixture. Zero.
- **What makes us abandon it.** On real data: a gap under 25%, or a gap that does not survive
  controlling for the regime (entry 2's buckets) — because low IV and a rising trend coincide,
  and the IV rank may be a proxy for the trend rather than a cost effect.

---

## 6 · The pre-registration record

Fixed on **2026-11-06**, before any entry was run:

- **Criteria.** Every hypothesis and abandonment criterion above. A criterion that turns out
  to be the wrong test becomes a **new entry with its own date**, and the old one stays on
  the record saying what was expected.
- **Trials declared.** Entry 1: 8. Entry 2: 3 plus a two-half split. Entry 3: 1. Entry 4: 1.
  Entry 18: 6. Entry 19: 3. Entry 20: 1. Recorded through `research/tracker.py`; the Deflated
  Sharpe uses the total, and `requirements.md` 4.3 notes the count is recorded but **not yet
  used**, so for now a person must carry it into the reading.
- **Minimum trade count.** 40 non-overlapping logical trades per compared cell. The unit is
  the **logical trade**, not the position.
- **Hold-out.** **NOT FIXED — and this is the one gap that makes the plan incomplete.**
  Section 2, point 2 gives the two options and why both are bad. `research/holdout.py` is
  built and will refuse a run that reaches into a declared hold-out unless `unlock=` is passed
  with a reason, which is recorded. It deliberately **does not pick the period**, because
  that is a group-4 choice. Until a person picks it, entries 5 to 9 and 16 to 20 have no
  out-of-sample stage, and every one of their results is in-sample.

---

## 7 · Where this stops and hands back

Per the role's rules, four things stop this plan rather than being worked around:

1. **The hold-out is the human's.** Section 6. Not fixing it is not an oversight.
2. **No strategy exists to falsify.** Part B's gate. The demo in `run_backtest.py` is an
   interface demonstration; the only alternative to a person writing one is relabelling
   entries 5 to 9 as method tests, which must be recorded as a decision rather than slid in.
3. **Six questions need a capability, and the specs are not written.** Part C. Each needs a
   person to approve the build, and two of them (10 and 11) need a design decision about how
   the null is constructed before a hypothesis can even be stated.
4. **The order rests on a judgement that is not mine to make.** Whether the term-structure
   question (entry 16, by far the cheapest data) matters more than the exit research (entries
   18 and 19, by far the most data) is a commercial and personal judgement about what this
   platform is for. I have put 16 first inside Part D on cost grounds alone. If the exits are
   what you actually want to know, the data ask changes completely and so does the order.

**And one thing that is nobody's judgement:** `docs/feedback/` is empty, so nothing here is
grounded in what happened when the platform was used. The first feedback entry is likely to
reorder this plan more than any argument in it.
