# Research questions

> This file records **the questions to answer with the platform**, not the features to build
> (those are in `requirements.md`).
>
> Each one says **whether it can be done now**. Most option-side questions are blocked on the
> same thing: the synthetic chain's IV is constant, with no skew and no term structure, so no
> conclusion that depends on any of the three holds.

---

## The status markings

| Marking | Meaning |
|---|---|
| **can do** | runnable now, and the conclusion can be trusted |
| **needs data** | needs a real option chain; the conclusion does not hold on a synthetic one |
| **needs tooling** | the platform lacks the capability; see `requirements.md` |

---

## 1. Choosing the tenor

### 1.1  Matching the tenor to the strength of the drift view  **can do (underlying side)**

At the same delta, the same holding period, and selling before expiry: short-dated options
have the higher mean return (more leverage, so they capture more of the drift), and
long-dated options the higher median and win rate (they survive longer).

**The question**: how strong is my view on QQQ's drift? A strong view takes a short tenor, a
weak view a long one, and no view means not trading.

**How**: run Monte Carlo under different drift assumptions and see at what drift the ordering
flips. The underlying's drift distribution can be estimated from real history and does not
depend on option data.

### 1.2  The term structure of the volatility risk premium  **needs data**

**This is the only place in the choice of tenor where a real edge might exist.** At constant
IV, changing tenor only slides along one expected-value line (high win rate and low payoff
against low win rate and high payoff); there is no free edge.

**The question**: is a long-dated option's IV cheaper relative to realised volatility over
the same period than a short-dated one's?

**How**: subtract each tenor's realised volatility from its IV and look at the term
structure. This needs a real chain.

### 1.3  Deep in-the-money LEAPS as leveraged long exposure  **needs data**

A two-year call 25% in the money: delta 0.87, down only 4.6% on a 5-point fall in IV, burning
0.038% a day, 3.1 times leverage. Time value is 29.8% of the premium, which is about 15% a
year to hold — **almost exactly QQQ's historical annual return**.

**The question**: after the time value and a drawdown three times as deep, is it better than
buy and hold?

**How**: `investor_view()`'s beta/alpha comparison plus time underwater. The synthetic chain
reaches 120 days at most, so **LEAPS cannot be run yet**; `max_dte` has to be raised first.

---

## 2. Entry

### 2.1  The edge window after a correction  **can do**

Already measured (QQQ 1999–2026, a 10% correction, 30 episodes):

| Horizon | P(up) | Unconditional baseline | Excess |
|---|---|---|---|
| 3 months | 75.9% | 68.3% | +7.6% |
| **6 months** | **85.7%** | 72.9% | **+12.9%** |
| 12 months | 78.6% | 78.4% | **+0.2%** |

**The question**: why is the edge concentrated at 6 months and gone by 12? Is that window
stable, or noise from 30 episodes?

**How**: other thresholds (5% / 15% / 20%), another underlying (SPY), and sub-periods, to see
whether it is consistent. Note that the 6-month window holds only 21 non-overlapping
observations.

### 2.2  The IV level as an entry condition  **partly can do**

`iv_rank_252` is computed from a real volatility proxy (VXN), **so this signal itself can be
trusted**.

**The question**: is performance systematically better entering at a low IV rank? How low is
low?

**How**: look at performance stratified by iv_rank. Note that exploring strata is
statistically the same as sweeping parameters, and the trials have to be counted.

### 2.3  The interaction between a correction and IV  **partly can do**

In a sharp fall IV is necessarily high — the moment you want to buy is the moment it is
dearest.

**The question**: is there a region where that tension resolves? "Fallen far enough **and** IV
already off its peak", say.

---

## 3. Getting out (3)(4)(5)

### 3.1  Where the stop belongs  **needs data**

The MAE distribution (with active exits off) gives: a -50% stop cuts 129 trades, of which
**14 would have been profitable**, losing 9,145 in profit to avoid 113,025 in losses; at -20%
it loses 41,825 in profit and the losses avoided are barely larger.

**The question**: is -40% to -50% the right region for this strategy?

**What blocks it**: this is a distribution of option prices, which cannot be trusted on a
synthetic chain.

### 3.2  A fixed ratio against a trailing stop  **needs data**

A fixed +100% take-profit is reached by only about 16% of trades; +30% by 49%. A trailing stop
(out after giving back a third of the gain) catches the class that ran up and came back.

**The question**: which is better for this strategy?

### 3.3  The **discovery procedure** for (5) headwind  **needs tooling and data**

**The core question: for the positions that ended badly, was there an observable warning
beforehand.**

How (the mechanism exists, the procedure does not):

1. Daily position trajectories are already being collected — a hard prerequisite that cannot
   be reconstructed afterwards
2. Split by final outcome and look at whether the two groups' state on holding day k separates
3. Of each candidate warning signal ask: how early it fires · its hit rate · its false-alarm
   rate · what acting on it would have done
4. **The trap**: a stop censors the data. Studying headwind requires first running a version
   with active exits off

---

## 4. The market regime (1)

### 4.1  The **discovery procedure** for the regime dimensions  **can do**

Do not pick the cut and then validate it — that is drawing the target around the arrow. The
right order:

1. Run a version with no regime condition at all
2. Stratify on each candidate dimension (VIX level, term structure, trend, credit spreads,
   realised volatility)
3. Ask three questions at once: **did it separate** (effect size) · **is each bucket big
   enough** · **does it hold on another sub-period**
4. **Write down how many dimensions were tried** — exploring strata is statistically the same
   as sweeping parameters

---

## 5. Falsification (goal four; 5 of 24 done)

**This group has the highest priority**: one run says whether the strategy is worth
researching further, before any statistical inference is finished.

### 5.1  A random-entry control  **can do**
Can random entries at the same frequency do as well? If so the signal does nothing and the
return is the underlying's drift. `research/baselines.py: random_entry`, reachable as
`r.baselines(draws=n)`: only the timing is randomised, the result is a percentile across
many draws rather than one, and the comparison is valid on a synthetic chain because both
sides are priced by it (criterion 4.5).

### 5.2  A reverse test  **needs tooling**
Does doing the opposite also make money? Both directions making money means the return is
beta, not the signal.

### 5.3  Shuffling the signal (a permutation test)  **needs tooling**
Keep the distribution, shuffle the timing. Does the timing actually do anything.

### 5.4  Removing the head  **partly can do**
What is left without the best N trades. `investor_view()` gives the top-5 share; the
systematic curve is not built.

### 5.5  Sensitivity to costs and to execution delay  **can do**
`cost_sensitivity()` exists, and so does the delay half: `result.delay_test(extra=(1, 2))`
reruns the strategy with the signal delayed, over `run(execution_lag_days=n)` (criteria 4.8
and 4.9).

### 5.6  Selection perturbation  **needs data**
The adjacent strike, the adjacent expiry — does it still hold? Holding on one contract only
means it was fitted to the chain's noise.

---

## 6. Research known to be invalid here

Recorded so that **no time is spent on it**.

| What one might do | Why it cannot be done now |
|---|---|
| Anything depending on skew | every strike on the synthetic chain has identical IV |
| Anything depending on the term structure | as above |
| Selecting by greek ratios | with no skew these are monotonic in the strike, so the ranking must land on the edge of the feasible region |
| Selling strategies | needs a margin model (not built) |
| Spreads / multi-leg | architectural: `TradeRecord` holds one contract per row |
| Capacity | the current size is three orders of magnitude below QQQ's liquidity, and volume data is absent |

---

## 7. The known limitations

**Not bugs — explicit approximations.** They are listed here so it is clear when they affect a
conclusion and when they can be ignored. They can be improved later; none of them is the
bottleneck now.

### 7.1  Greeks come from Black-Scholes, with no early exercise

The measured early-exercise premium (QQQ parameters: a 0.6% dividend yield, a 4% rate, 22% IV):

| | 90 days | 1 year | 2 years |
|---|---|---|---|
| **call** | −0.04% | −0.03% | −0.03% |
| **put (at the money)** | +1.7% | **+4.9%** | **+8.8%** |
| put (20% in the money) | — | — | **+12.5%** |

**When it does not matter**

- **A buyer's call strategy** (the current line): the error is 0.03% and can be ignored
  outright. With a 0.6% dividend, early exercise is worth almost nothing.
- **A LEAP call**: a longer tenor does not make this error larger — the table is still 0.03%
  at 730 days.

**When it does matter**

- **Any strategy that sells puts**: Black-Scholes understates by 2% to 12%, worse the longer
  and the deeper in the money. A binomial tree is required before selling.
- A deep in-the-money call before an ex-dividend date: the system flags it in
  `quality_flags` but does not reprice it.

### 7.2  The precision of the greeks is not the bottleneck

The greeks **take no part in the P&L** — that comes from real bid/ask. They are used only for
selection, attribution and risk measurement.

The measured sensitivity of selection: a target delta from 0.31 to 0.39 (a span of ±0.04, far
larger than any plausible model error) moves net P&L from −81,114 to −69,801. **The direction,
the magnitude and the conclusion are unchanged**; adjacent steps differ by about 4%.

So **do not spend money or time on the precision of the greeks**. What would change a
conclusion is whether IV has skew (a structural problem, not a precision one) · real bid/ask ·
American pricing for selling.

### 7.3  The rate is a constant fallback and the dividend is estimated from history

**This is where long tenors are genuinely sensitive.** Measured on an at-the-money call:

| Tenor | Rate wrong by 1% | Dividend yield wrong by 0.5% | Change in delta (rate wrong by 1%) |
|---|---|---|---|
| 30 days | +1.6% | −0.8% | +0.005 |
| 1 year | +4.9% | −2.9% | +0.017 |
| **2 years** | **+6.4%** | **−4.1%** | **+0.023** |

**When it does not matter**: an ordinary 21–45 day position, where 1.6% is swamped by the
spread.

**When it does matter**: **LEAPS**. At two years, a rate wrong by one percentage point is
6.4% — and a LEAP's time value is itself about 30% of the premium, so this error is a fifth
of it. A real rate curve should be wired in before researching LEAPS.

### 7.4  What to improve (low priority, with explicit triggers)

None of these is a bottleneck now; do not go after them. **Each one's trigger is stated** —
at that point it stops being optional.

| What | Its effect now | When it becomes required |
|---|---|---|
| A binomial tree (American pricing) | a buyer's call error is 0.03%, negligible | **when selling puts** |
| A real rate curve | 30 days differs by 1.6%, swamped by the spread | **when researching LEAPS** (two years differs by 6.4%) |
| Dividend forecasting (currently from history) | the same magnitude | as above, or for deep in-the-money calls (early exercise before an ex-date) |
| Handling settlement at expiry | none — we are a buyer, and `dte_floor` means nothing is held to exercise | **when researching holding to expiry** |

### 7.5  The synthetic chain has no skew and no term structure

This is not a precision problem, it is a **structural absence**; see section 6. It is the only
hard blocker at present.

---

## 8. The four groups of choice, and which may be optimised

> From [docs/architecture.md](docs/architecture.md), "Research and Trade-off Register". The
> full register is there; what matters here is the **discipline**, because it decides
> which of the questions above may be answered by searching for the best value.

| Group | What it holds | How it may be treated |
|---|---|---|
| **1 · realism assumptions** | λ (where between mid and ask a buy fills), decision latency, participation cap, marking at bid or mid, stale-quote tolerance, cleaning rules, excluding OCC-adjusted contracts, American against European IV, trading against calendar days, dividend and rate sources, surface smoothing, sticky-strike against sticky-delta, the greeks attribution method | **Sensitivity tests only, never optimised.** Set conservatively. Picking the value that maximises backtest returns optimises an illusion |
| **2 · engineering trade-offs** | data granularity, check_frequency, event-driven against vectorized, the underlying universe, the shadow-tracking window | Chosen once, for cost and realism. Not a research result |
| **3 · strategy choices** | the snapshot time, contract delta and days to expiry, the structure, the Take Profit and Stop Loss approaches, headwind sensitivity, the hedge approach, rolling, positioning, the regime model, the lifecycle settings, book limits | **May be optimised**, but only under the protocol in section 9 |
| **4 · research-method trade-offs** | the walk-forward window, the hold-out length, pooling across underlyings, the number of variants tested, the optimisation objective, the minimum trade count and bootstrap block length | Chosen before the research, not after seeing results |

**The five that move results most**, and so come first: λ, the decision latency and the
marking method from group 1; the contract choice and the Take Profit approach from group 3.

This is why section 7.4's items have triggers rather than priorities: a binomial tree and a
real rate curve are group 1, so they are never tuned - they are either accurate enough for
the question at hand or they are not.

## 9. The research protocol

> Also from the architecture document, 6.4. Seven slots with variants and parameters, plus
> the lifecycle settings, make a very large search space, and with few trades the main risk
> is fitting history rather than finding an edge.

1. **Lock a final hold-out period at the start** and use it once, at the end.
2. **Count every trial** across all slots and settings; the Deflated Sharpe uses the total.
3. **Research in stages, each on walk-forward folds**: Setup and Entry first, judged by the
   forward returns of the underlying and of the selected options **with no exits applied**
   (an event study); then Take Profit and Stop Loss; then Headwind; then Positioning; then
   Hedge. Finish with interaction tests and a joint re-check.
4. **Report confidence intervals, not point estimates**: bootstrap by trade and block
   bootstrap by period. Set a minimum trade count before any conclusion is drawn.
5. **Pool across underlyings** to gain sample size, but require the effect to hold on each
   underlying separately.
6. **A counterfactual exit result is in-sample by construction**; anything it suggests must
   be confirmed out of sample before adoption.
7. **Compute return and risk from the book's daily mark-to-market**, not from per-trade
   returns, so overlapping trades are counted correctly.

Of these seven, **none is enforced by a tool yet** — see requirements.md 1.28 and 2.9 to
2.15. Until they are, the protocol rests on a person, and saying so is part of it.

---

## 10. Future topics · the systems still to build

> From the architecture document. These are **systems**, not questions — each one is work
> before it is research. They are here rather than in `requirements.md` because each was
> blocked on something concrete, and the trigger is what decides when it stops being
> future.
>
> **10.2 and 10.3 are no longer future.** Every row in both is built; each now says where
> it is and what is left of it. They are kept rather than deleted because the trigger each
> one was waiting on is the useful half of the record — but 10.1, 10.4 and 10.5 are the
> only parts of section 10 still describing work.
>
> The rule from section 8 applies: anything in group 1 (a realism assumption) is built
> conservatively and only tested for sensitivity. None of these is ever tuned for return.

### 10.1  Blocked on data, not on effort

| Topic | What it unlocks | Blocked on |
|---|---|---|
| **Intraday snapshots and a decision latency** | The document's timing rule: the signal at snapshot t, the fill at t plus a latency. Today's daily close with T+1 execution holds the principle at one-day resolution and cannot express "signal 15:45, fill 15:50" | intraday option quotes. Until then every reaction time is a day, and `check_frequency` is daily by necessity rather than by choice |
| **An IV surface** | Skew and term structure as features, so Setup and contract selection can read them | a real chain. Moot on a synthetic one whose IV is constant across every strike |
| **Point-in-time alignment** | An `available_at` on every record, as-of queries, versioned revisions. The DataView enforces the cut it can see; it cannot enforce one the data does not carry | knowing how the vendor revises, which needs the vendor |
| **A market-rule layer** | Rejecting orders that break trading hours, halts, tick sizes, position limits or borrow. **Principle 2 is not held in Layer 4 without it** | halt and tick-size history |
| **Capacity** | How much money the strategy holds: a participation cap and return decay as capital grows | option volume, which the synthetic chain does not have |
| **Earnings and event exposure** | Splitting results by whether a trade was held through an event. A long option through earnings behaves very differently | a point-in-time earnings calendar |

### 10.2  Was blocked on Layer 0, not on data — and Layer 0 landed

| Topic | What it unlocks | Where it is, and what is left |
|---|---|---|
| **The event bus** | Layers communicating only by events, and a fill traceable back through fill → order → signal → component | **built** — `core/events.py: EventBus`, fifteen named types, the document's order within a timestamp in `PHASE`, and `result.why()` printing the chain for every fill (criteria 0.6, 1.15). What is left is the word *only*: the engine publishes the chain and still acts inline, so turning the loop into handlers is the remaining work, and it is what 7.2 needs |
| **The component protocol and a variant registry** | A slot as a registered component with a parameter schema and a version, so a run differs from its baseline in one slot alone and the change history of the strategy is auditable | **built** — `core/component.py`: named, versioned, parameter-schema'd forms registered per slot, bound after validation, carried into the run fingerprint as `slot/name@version`. **No `on_event`**: `decide` is what the engine calls, and when the loop becomes handlers that is what `on_event` will dispatch to |
| **Variant swap** | The second of the document's research tools. Swapping one slot for a different registered variant | **built** — `research/forms.py`, as `r.compare_forms(slot, forms)`, which requires the forms to compare rather than defaulting them (criterion 1.40). Twelve shipped forms across **four of the seven slots** — stop loss (4), take profit (2), headwind (2), positioning (4) — so setup, entry and hedge are the three still with one form each |
| **Attribution by component** | Which slot opened, sized, reduced or closed each part of a trade | **built** — the engine fills `component_id` (`engine/loop.py`, e.g. `component_id="setup"` and `component_id="review"`), and `EventDrivenResult.actions_frame` is one row per fill with the slot that issued it (`accounting/tradebook.py:136-175`), followable per trade with `trace()` |
| **A shared trade context** | Slots influencing each other through a per-trade record instead of not at all, which is what makes the seven genuinely separate | **built** — `core/trade_context.py: TradeContext`, assembled in one place for both engines (`accounting/tradebook.py: trade_context`), with each slot writing to its own namespace |

### 10.3  Was blocked on nothing — and all six are now built

| Topic | What it unlocks | Where it is |
|---|---|---|
| **Shadow tracking** | Keeping a closed trade's contract quoted for a window afterwards, which is what counterfactual exits need. Without prices after the exit, stop and target research is guesswork | `EventDrivenResult.shadow` / `shadow_frame` (`accounting/tradebook.py:135, 191-199`), for `shadow_days` sessions after the exit (criterion 1.26) |
| **Counterfactual exits** | Replaying each closed trade under an alternative exit, to measure money saved or given up. In-sample by construction, so anything it suggests must be confirmed out of sample | `research/counterfactual.py`, as `r.counterfactual(take_profits=…, stop_losses=…)`. A trade whose alternative never fires is **censored**, never assumed to have closed (criterion 2.11) |
| **Validation: walk-forward, purged k-fold, block bootstrap** | Section 9's protocol enforced rather than remembered. A locked hold-out, confidence intervals instead of point estimates, a minimum trade count | `research/validation/`: `splits.py` (walk-forward, purged k-fold with an embargo), `intervals.py` (trade and block bootstrap, block length required rather than defaulted), `deflated.py`; and the hold-out locked by `research/holdout.py`, which raises on an overlap unless `unlock="<why>"` is recorded (criterion 1.53) |
| **An ExperimentTracker** | Trials counted across an idea, which the Deflated Sharpe needs | `research/tracker.py`: `count_trials` keyed by `run_id` so a rerun is not a new trial, a record without one **raises**, and `deflate()` hands the count to the Deflated Sharpe. Which runs belong to one idea stays a judgement and is not guessed |
| **Interaction tests** | Varying two slots jointly and comparing against the sum of the single-slot effects. A large gap means the two have to be researched together | `research/ablation.py: interaction`, with `additive_share` from `core/thresholds.py` as the cut above which the two have to be researched together |
| **A parameter surface** | Whether a result sits on a plateau or a spike | `research/sweep.py: surface` and `plateau`, with `plateau_share`, `slope_share` and `plateau_tolerance` in `core/thresholds.py`. Nothing **plots** it — the interface shows no heatmap |

### 10.4  Open decisions · not work, but choices

> The document ends on six of these, and none can be settled by the platform. Each one
> changes what gets built, so leaving them open is a cost that compounds.

| Decision | Why it cannot wait indefinitely |
|---|---|
| **Historical quote granularity** | Daily close against intraday snapshots decides how realistic everything downstream can be. With daily data only, fills have to be modelled more pessimistically and the bias stated |
| **The signal snapshot time and the fill latency** | Must match how orders will actually be placed, so it is a trading decision before it is a modelling one |
| **`check_frequency` for the Open-state slots** | How fast a stop can react, in backtest and live alike. It must be identical in both |
| **The hold-out period and the minimum trade count** | Defines when a research result is allowed to count. Choosing it **after** seeing results is the commonest way to fool yourself, which is why section 9 puts it first |
| **Hedge instruments** | Shares, index futures (ES, MES), options, or a mix. It decides what Layers 4 and 5 must support, so building the Hedge slot before deciding would build the wrong one |
| **The underlying universe** | Index ETFs only, or single stocks too. Single stocks add earnings handling, far more contracts, wider spreads and borrow constraints — a different data bill and a different Layer 1 |

### 10.5  Not now, and what would change that

> **These are not decisions this document gets to make.** An earlier version of this
> section was headed "deliberately never", which was wrong twice over: nobody had decided
> any of it was permanent, and one entry contradicted the platform's stated purpose
> outright — the goal in `requirements.md` is to end in real trading, so a live path is
> the destination rather than something ruled out.
>
> What follows is therefore the same shape as the rest of section 10: not built, with the
> condition that would make it the next thing to build. Deciding any of them is yours.

| | Why not now | What would change it |
|---|---|---|
| **A live adapter, paper trading, reconciliation** | The strategy is not settled, so there is nothing to trade yet, and principle 3 ("the same code in backtest and live") cannot be demonstrated without a SimBroker interface to implement | **The strategy settling.** This is the destination, not an exclusion. The work it needs first is the SimBroker interface in Layer 4, which also makes the backtest's execution honest |
| **Multi-leg (spreads, iron condors) and a margin model** | Architectural: `TradeRecord` holds one contract per row, so a spread cannot be one trade. The margin model is the hard prerequisite for any selling and only makes sense built with it | **Wanting to sell premium, or to cap cost with a spread.** Both land on the same architectural change, so they are one piece of work rather than two |
| **Statistical regimes (HMM, Bayesian)** | Three or four complete cycles in ten years: the states are hard to tell from noise and have no names, so there is no way to explain why a day counted as high volatility. That is a reason to prefer rules **now**, not a verdict on the method | **A rule-based regime proving insufficient** — specifically, finding that performance separates on something no threshold on VIX, term structure or trend captures. Then the extra fitting risk is worth carrying |
| **A REST API, a job queue, auth** | One researcher. The SDK and the local web app cover what one person does, and a queue with no second user is machinery maintaining itself | **A second person using it**, or sweeps long enough that they have to run unattended |

## 11. Maintenance

This list is **maintained by hand** and cannot be computed the way `tools/status.py` is.

When one is finished, change its status and say where the conclusion lives; when a new question
appears, add it. **Do not let this become a wish list that only grows** — delete what will not
be done, or move it to section 6 with the reason.
