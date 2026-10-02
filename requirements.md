# The backtest platform · requirements and acceptance

> This file is **the anchor for holding me to account**. Opening it at any time should
> answer: where is the platform, what is still missing, and what do I believe exists that
> does not.
>
> The rule: **anything not built says so explicitly**, with no hedging. There are three
> statuses only — **done** (built and verified) / **partial** (exists but falls short, with
> what is short stated) / **missing** (absent, with whether it is a placeholder or still to
> do).

---

## Where it stands

```bash
python -m tools.status      # the counts, computed from this file
```

The counts are deliberately **not written here**. A hand-written total is exactly the kind
of fact that rots: it was right when written and nobody updates it, and then the document
contradicts itself while looking authoritative. `tools/status.py` counts the rows below, so
it cannot go stale.

What is stable enough to state in prose: the foundation (goal one, trustworthy) is the most
solid, and validation and falsification (goal four) are almost entirely empty — that is the
main theatre once the strategy is settled, and the interfaces have to be left in place now,
because adding them afterwards means rebuilding.

## The four goals

As a PM I use this platform for two things: **settling the strategy's details** (goals one
to three) and **validating it over the long run once it is settled** (goal four). Meeting
the bar means:

### Goal one · trustworthy
I believe every piece of logic and handling behind this platform, **and I can check the
important steps myself**.

### Goal two · usable
The research I want to do is easy to carry out on the platform. **What does not exist yet,
or is hard to build, is written into the plan as a placeholder** rather than faked.

### Goal three · useful
It supports long-term research into option strategies and ends in real trading — either
making money or giving me insight worth having. This is my fund's backtest platform, not a
toy.

### Goal four · able to validate a settled strategy over the long run
Once the strategy is settled this platform becomes a **falsification tool**: optimisation
finds plateaus rather than peaks, tests are deliberately constructed to overturn it, one
conclusion has to hold from mutually independent angles, and the statistics give a
direction rather than a verdict.

---

## Goal one's acceptance criteria · trustworthy

| # | The criterion | Status | How to check it yourself |
|---|---|---|---|
| 1.1 | No silent errors anywhere: absence is explicit and carries a reason, never None/NaN/0 | done | the `Maybe[T]` type; `if maybe:` raises outright |
| 1.2 | Errors are raised with context, never swallowed or downgraded | done | a selection function's error carries the date and the cause; `_run_id` returns 400 rather than falling back to 0 |
| 1.3 | When unsure, refuse to give a number rather than approximate | done | a stress grid returns NaN beyond the expansion's range rather than a wrong number; VaR refuses on too small a sample |
| 1.4 | No look-ahead, and a test holds it | done | the truncate-and-recompute property test; selection uses the **decision day's** signals with the **execution day's** chain |
| 1.5 | The two engines check each other | done | unconstrained, they agree row for row; verified under functional selection too |
| 1.6 | Results reproduce | done | `tools.baseline check`; the reproduction manifest in `result.manifest()` |
| 1.7 | The dependency direction is held by a tool, not a document | done | `tools/check_deps.py`; an unregistered module fails |
| 1.8 | Every step of data handling is inspectable and has a check | partial | `data/pipeline.py` has 9 steps, but the checks are weak → see P1 |
| 1.9 | The selection process is explainable | done | every selection returns its whole funnel; when nothing is selected it reports what the day's chain actually held |
| 1.10 | The system states the boundary on its own conclusions | done | synthetic data, too small a sample, too large an attribution residual, an MFE censored by the stop — each says so unprompted |
| 1.11 | Provenance is traceable all the way to each trade | done | the `source` column; `greeks_model_derived` marks derived against vendor-supplied |
| 1.12 | Data can be health-checked before being wired in | done | `check_file(path)`, with fatal and warning tiers |
| 1.13 | No-arbitrage checks on the chain (butterfly, calendar, put-call parity) | missing, to do → P1 | — |
| 1.14 | Point-in-time correctness (a vendor's later revisions do not contaminate a backtest) | missing, to do → P6 | — |

## Goal two's acceptance criteria · usable

| # | The criterion | Status | Note |
|---|---|---|---|
| 2.1 | **Each** of the seven components has a way in | partial | (2) and (5) are missing; see the table below |
| 2.2 | Every component takes either parameters (sweepable) or a function (judging by the conditions) | partial | (1), (2) and selection are done; exits and sizing are missing → P3 |
| 2.3 | The common analyses are present: classification, attribution, distributions, comparison | partial | attribution is on the greek dimensions only → P2 |
| 2.4 | Swapping the data source changes one argument, with nothing above it changed | done | `chain="file:/path"`; verified by running one strategy on two sources |
| 2.5 | The interface is clear, complete, well made and not shoddy | partial | → P5; the largest outstanding debt in the experience |
| 2.6 | Explanatory text falls into three kinds (hard boundary / this run's diagnostics / help), each in a fixed place | missing, to do → P5 | they are currently mixed together at the top |
| 2.7 | A feature that is not built says so on the interface | partial | the notice strip has some of it, not all |
| 2.8 | A research judgement can be recorded and read back later | partial | the backend has notes, the interface does not use them → P5 |

**Where the seven components stand**

| | Component | Status | What is missing |
|---|---|---|---|
| (1) | setup / regime | partial | rule-based classification exists but is not wired to the interface; statistical regimes (HMM) are a placeholder |
| (2) | entry | partial | a function can be written, but the interface's default is still a toy expression → P5 |
| (3) | take profit | partial | the MFE distribution and the level table exist; **the levels cannot be trusted** on a synthetic chain |
| (4) | stop loss | partial | MAE and censoring detection exist; as above |
| (5) | **headwind** | missing | **the largest hole of the seven**: reducing while held, and rolling, do not exist → P3 |
| (6) | positioning | partial | five methods can be compared, but the parameters are constants and cannot follow the conditions → P3 |
| (7) | hedge | partial | exposure limits, the drawdown brake, stress tests and the hedge-excluded control all exist; multi-leg is a placeholder |

## Goal three's acceptance criteria · useful

| # | The criterion | Status | Note |
|---|---|---|---|
| 3.1 | My own option data can be wired in | done | see 2.4; absent columns are derived and flagged |
| 3.2 | Option-side conclusions can be trusted (which delta, where to take profit) | missing, **blocked** | the synthetic chain's IV is constant, with no skew and no term structure. **The only fix is your real data** |
| 3.3 | The confidence in a conclusion can be judged | missing → **goal four, 4D** | see the section of its own below |
| 3.4 | Once settled, it can be validated and falsified repeatedly over the long run | missing → **goal four** | the platform's second use |
| 3.5 | Out-of-sample isolation / walk-forward | missing → 4.16 | — |
| 3.6 | Research accumulates, and the history can be traced | done | run records are immutable and strategies are versioned by a content hash |
| 3.7 | Selling strategies (cash-secured puts, spreads) | missing, placeholder | needs a margin model plus multi-leg; architectural |
| 3.8 | Live drift monitoring | missing, placeholder | your eventual goal, but it needs live trading first |

---

## Goal four's acceptance criteria · validating a settled strategy

> Once the strategy is settled, the platform turns from finding regularities to
> **repeatedly trying to overturn it**. The frame of mind is the opposite: use one wants to
> know why it works, use two wants to know when it does not.
>
> This group is almost entirely missing. **But the interfaces have to be left in place
> now** — every item needs the engine's cooperation, and adding them afterwards means
> rebuilding.

### 4A · optimisation: find the plateau, not the peak

| # | The criterion | Status | Why it is wanted |
|---|---|---|---|
| 4.1 | The parameter surface is visible: is this a region or a needle | missing → P4 | a conclusion that holds at delta 0.30 and at neither 0.25 nor 0.35 is noise, not a regularity |
| 4.2 | Neighbourhood robustness: how the ring around the optimum performs | missing → P4 | a live parameter never lands exactly on that point |
| 4.3 | A sweep counts its trials automatically and discloses them | partial, counted but unused → P4 | try a hundred sets and keep the best: how much of that best is luck |
| 4.4 | Refuse to output "the optimal parameters" | missing → P4 | the platform should not encourage that use; it owes a region and that region's stability |

### 4B · falsification: deliberately construct tests that could kill it

| # | The criterion | Status | What this one tries to kill |
|---|---|---|---|
| 4.5 | **A random-entry control**: can random entries at the same frequency do as well | missing → P4 | the signal does nothing and the return is the underlying's drift |
| 4.6 | **Shuffling the signal (a permutation test)**: keep the distribution, shuffle the timing | missing → P4 | the timing does nothing and the exposure was simply long enough |
| 4.7 | **A reverse test**: does doing the opposite also make money | missing → P4 | both directions making money means the return is beta, not the signal |
| 4.8 | **Cost sensitivity**: at what cost does the edge disappear | done | `cost_sensitivity()`; the anomaly scan warns above 30% of gross |
| 4.9 | **Execution-lag sensitivity**: what one more day's delay does | missing → P4 | a marginal edge does not survive real execution |
| 4.10 | **Selection perturbation**: the adjacent strike, the adjacent expiry | missing → P4 | holding on one contract only means it was fitted to the chain's noise |
| 4.11 | **Removing the head**: what is left without the best N trades | partial | `investor_view()` gives the top-5 share and the total without them; the systematic curve is not built |
| 4.12 | **Sub-period stability**: the sign holding year by year and regime by regime | partial | the anomaly scan reports "one year contributed X%"; `investor_view()` gives the share of profitable years and the worst year |

### 4C · cross-checking: mutually independent angles each holding

| # | The criterion | Status | Where the independence is |
|---|---|---|---|
| 4.13 | The two engines agreeing | done | two entirely different implementations |
| 4.14 | **Attribution agreeing with the story** | missing → P2/P4 | if you call it a gamma strategy, gamma should be the main source of profit. A mismatch means your understanding of it is wrong |
| 4.15 | **MFE/MAE agreeing with the exit levels** | partial, the tool exists but is not a check → P4 | a stop placed where the winners routinely go is killing your own winners |
| 4.16 | Out-of-sample isolation / walk-forward | missing → P4 | independent in time |
| 4.17 | Cross-underlying validation (it holds on QQQ; does it on SPY) | missing → P4 | independent in the underlying |
| 4.18 | **A consistency table**: every angle's conclusion side by side, with conflicts highlighted | missing → P4 | one angle holding does not count; what matters is whether several angles contradict each other |

### 4D · statistics: give a direction, not a verdict

| # | The criterion | Status | Note |
|---|---|---|---|
| 4.19 | **Effective sample size** (corrected for overlapping positions) | partial | event studies have `effective_sample()`; at the trade level the anomaly scan reports "only M of N do not overlap" |
| 4.20 | **Block bootstrap confidence intervals** (block length at least the typical holding period) | missing → P4 | an ordinary bootstrap treats overlapping samples as independent and the interval comes out too narrow |
| 4.21 | A concentration diagnostic (how much the P&L depends on a few trades) | partial, the distribution exists but is not a diagnostic → P4 | — |
| 4.22 | Multiple-testing disclosure (how many times it was tried) | partial → P4 | including **exploration by grouping** — stratifying on different dimensions is statistically the same as sweeping parameters |
| 4.23 | **Evidence graded strong / medium / weak** | missing → P4 | — |
| 4.24 | **Weak evidence is still shown, labelled as weak, with no binary gate** | missing → P4 | what you want is useful information plus a sense of how much to trust it, not to have it filtered away |

---

## An addition to goal three: what an investor asks

| # | The criterion | Status | Where |
|---|---|---|---|
| 3.9 | **Is this alpha, or leveraged beta** | done | `investor_view()`: beta against the underlying, annualised alpha, the buy-and-hold comparison |
| 3.10 | **Time underwater**: how long it spent there, and how long the deepest took to recover | done | as above. Max drawdown says how deep, not how long |
| 3.11 | Sortino / Calmar | done | Sharpe treats upside volatility as risk and understates a positively skewed strategy |
| 3.12 | Concentration: the top-N share, year-to-year consistency, the worst year | done | as above |
| 3.13 | Cost sensitivity (does it survive doubling) | done | `cost_sensitivity()` |
| 3.14 | **The anomaly scan**: what is out of the ordinary is picked out automatically | done | `anomalies()`, 8 rules, and nothing shown when nothing fires |
| 3.15 | Capacity: how much money this strategy holds | missing, placeholder | needs volume data, which the synthetic chain lacks |
| 3.16 | Turnover / share of time in the market | missing, to do | — |

## Additions at the mechanism level

| # | The capability | Status | Where |
|---|---|---|---|
| 5.1 | **The trade plan**: one function settling what to buy, how much, and how to exit | done | `engine/plan.py`. The seven components are coupled in a real trade, which the parameterised form cannot express |
| 5.2 | **The daily review**: dynamic exits, scaling in and out | done | `engine/review.py`. (3), (4) and (5) are one mechanism |
| 5.3 | **The daily position trajectory** | done | a hard prerequisite for researching (5), and it cannot be reconstructed afterwards |
| 5.4 | **Event studies**: what historically happened after a fall of X% | done | `research/events.py`. **It uses only the underlying's price, so its conclusions hold on a synthetic chain** |
| 5.5 | **The selection trade-off surface**: four moneyness x tenor tables | done | `research/tradeoff.py`, computed from the day's real chain |
| 5.6 | **IV level signals**: iv_rank / iv_pct / iv−RV | done | from a real volatility proxy (VXN/VIX), so they hold on a synthetic chain |
| 5.7 | Rolling | missing, placeholder | one logical trade would have to span several contracts, which `TradeRecord` cannot hold |

---

## The explicit placeholders (not built, and not soon)

These were not forgotten; they are **deliberately not built**, for the reason given. The
interface should say so too.

| The item | Why not yet |
|---|---|
| Multi-leg (spreads, iron condors) | architectural: `TradeRecord` holds one contract per row. Piling more onto single-leg means doing each of those things twice later |
| A margin model | the hard prerequisite for selling, and it only makes sense built together with multi-leg |
| Naked short calls | with no margin model any number given would be wrong — so it is refused rather than approximated |
| Fitting or interpolating an IV surface | pointless on a synthetic chain whose IV is constant. It waits for real data |
| Statistical regimes (HMM / Bayesian) | rule-based is sufficient and harder to overfit; with three or four complete cycles in ten years, a statistical method is dangerous here |
| Early exercise before expiry | assignment at expiry is modelled. Early exercise matters little to a buyer and a great deal to a seller — so it goes with selling |
| Delisting / survivorship bias | QQQ and SPY are unaffected. It waits for a different underlying |
| Point-in-time data versioning | it waits for your data, so the design can follow how your vendor actually revises |
| Live drift monitoring | it needs live trading first |

---

## The order of work

| | What | Which goal |
|---|---|---|
| **P1** | Complete the data-quality checks (arbitrage violations, stale quotes, crossed quotes) and make each pipeline step's check real | 1.8 / 1.13 |
| **P2** | The analysis layer: attribution on more than the greeks, classification views, distribution comparison | 2.3 |
| **P3** | Exits and sizing also become functions → **this is how (5) headwind gets built** | 2.2 / (5)(6) |
| **P4** | **The validation and falsification toolbox** (all 24 of goal four). The largest single piece; internally in this order:<br>  4B falsification (random control, reverse, perturbation, head removal) — first, because it saves the most time: one run says whether to continue<br>  4D statistics (effective sample, block bootstrap, evidence grading)<br>  4A optimisation (the parameter surface, neighbourhood robustness)<br>  4C the cross-checking table | goal four |
| **P5** | Rebuild the interface: one block per component, the three kinds of text separated, the research log wired up | 2.5 / 2.6 / 2.8 |
| **P6** | The IV / volatility signal family (IV rank, IV−RV, term structure, the volatility cone) | 2.1 |

---

## The constraints that never relax

| The constraint | Why |
|---|---|
| Two price sets: adjusted for signals, traded for lining up with options | the adjusted price is rewritten by every dividend, so judging a historical contract's moneyness off it shifts systematically |
| Two time conventions: calendar/365 for pricing, trading/252 for annualising volatility | forcing either into one makes one of them wrong |
| The synthetic chain may not be used to tune option-side parameters | what gets tuned is the optimum under that model |
| No look-ahead anywhere, held by a property test | the most fatal failure and the hardest to see by eye |
| Absence is always explicit | a delta read as 0 selects the wrong contract and the run finishes normally |
| Deterministic results plus a regression baseline | research rests on comparison, and an unstable baseline makes every comparison meaningless |
| The unit of statistics is the **logical trade**, not the position | counted by position, three rolls become four trades and both the win rate and the holding period are distorted |
| Pricing may not escape the data layer | only if the layers above read nothing but the chain's columns can the data source be swapped with no change |
