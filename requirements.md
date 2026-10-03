# The backtest platform · requirements and acceptance

> This file is **the anchor for holding me to account**. Opening it at any time should
> answer: where is the platform, what is still missing, and what do I believe exists that
> does not.
>
> The architecture is **`Plan/backtest-system-architecture.pdf`**. This file is the only
> record of **what is built against it** — layer by layer, and criterion by criterion.
> There is no second copy of the architecture here; where the two would disagree, the
> document is right and this file is the gap.
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

## The eight layers · what exists

Status is one of **built** (the layer does its job), **partial** (some of it works, with
what is missing named) and **absent**.

### Layer 0 · Foundation — partial

The document's foundation is events, the component protocol, clock and data view, invariants,
config and logging. Fix it first and the layers above become swappable.

| Piece | Status | Where / what is missing |
|---|---|---|
| Event types (MarketEvent, QuoteEvent, SignalEvent, OrderEvent, FillEvent, RiskEvent, TimerEvent, ExpiryEvent…) | **absent** | **The largest structural gap.** Layers call each other directly. `core/run_log.py` is a *diagnostic* stream (quote missing, selection failed, exit deferred), not an inter-layer bus |
| Action vocabulary | **built** | `core/actions.py`. Nine actions; `BUILT` says which six the engine can execute and gives the reason for each of the other three, and `require_built` refuses them at the source |
| Component protocol (`on_event`, `get_state`, params schema) | **absent** | There is no event bus for `on_event` to receive from. The current hooks are bare callables: `entry=`, `select=`, `plan=`, `review=` |
| Variant registry by name and version | **absent** | Only chain sources are registered (`data/options/chain_registry.py`). Without the protocol there is nothing to register |
| Clock | **partial** | `core/clock.py`: trading calendar, early closes, expiry calendar. No session phases, and no single `now` — the engine passes the day around |
| DataView with an `available_at` cut | **partial** | No DataView object. The *property* is held by construction — a signal view is the decision day's and the chain is the execution day's — but there is no interface that enforces it, and raw data carries no `available_at` column |
| Invariants | **partial** | `accounting/ledger.py: check_invariant()` runs the accounting identity every day and aborts on a breach. The document's other invariants (position equals cumulative fills, no open position in an expired contract) are not checked |
| Config | **partial** | `core/config.py` is frozen dataclasses with validation. Not YAML, no schema, no per-slot `{variant, version, params}` |
| Logging and traceability | **partial** | `core/run_log.py` plus the diagnostics panel. A fill cannot be traced back through fill → order → target → signal → component, because there are no orders or targets as events |

### Layer 1 · Data — built, for what it covers

| Piece | Status | Where / what is missing |
|---|---|---|
| Ingest with retries | **built** | `data/ingest.py`. Raises rather than returning empty — a silently empty feed makes a backtest conclude there were no signals |
| Two price sets | **built** | `data/underlying.py`. Adjusted for signals, traded for strikes; the factor applies to open/high/low as well as the close |
| Validate | **built** | `data/underlying.py: validate_bars`, `data/options/chain.py: validate_chain`, and `data/options/adapters.py: check_file` with fatal and warning tiers |
| The pipeline, self-documenting and self-checking | **built** | `data/pipeline.py`: nine steps, each declaring its method, its reason, the alternatives and their costs, and where to change it — with a check that runs on the real data. **Not in the document**; keep it |
| Rates and dividends | **partial** | `data/market.py`: the ^IRX series and real ex-dividend events. A constant fallback rate is used when the series is absent, which matters at long tenors (see research-questions 7.3) |
| IV and greeks | **partial** | `data/options/pricing.py`: Black-Scholes plus an implied-vol solve that returns an explicit absence rather than a guess. No American model, so put prices are understated by 1.7% to 12.5% (research-questions 7.1) |
| Point-in-time alignment | **absent** | No `available_at` column, no as-of queries, no revision versioning |
| Universe | **absent** | One symbol per run, chosen by argument |
| Surface builder | **absent** | Moot on a synthetic chain whose IV is constant; needed with real data |
| Contract master with adjustment history | **absent** | `core/types.py: ContractId/ContractSpec` carry the static terms. OCC adjustments are not tracked |
| Store | **built** | `data/store.py`: a parquet cache whose key covers every dimension that changes the result |
| Wiring in your own data | **built** | `data/options/adapters.py`. **Not in the document** — the plan assumes data exists. Keep it |

### Layer 2 · Signals — partial

| Piece | Status | Where / what is missing |
|---|---|---|
| Lifecycle | **partial** | `signals/lifecycle.py` defines the four states and the five settings. **The engine has no Armed state**: an entry condition becoming true produces an order in the same step, so Idle and Armed are collapsed. Four of the five settings are decided by accident rather than chosen — each one's gap is recorded and shown by `tools/status.py` |
| Slot 1 Setup / Regime | **partial** | `signals/setup/regime.py`: rule-based classification on volatility level, term structure and trend, with a discovery procedure. Not wired into the engine as a gate |
| Slot 2 Entry | **built** | `signals/entry/expression.py` (an allowlisted expression) or a callable |
| Contract selection | **built** | `signals/contracts.py`: a feasible region plus a ranking objective, with the whole funnel returned. Executability is filtered **before** the selector sees the chain, so picking freely on greeks is safe by construction |
| Slot 3 Take Profit / Slot 4 Stop Loss | **partial** | Static levels in `signals/strategy.py: ExitRule`; dynamic ones in `signals/review.py`. Not separate slots, so they cannot be swapped or ablated independently |
| Slot 5 Headwind | **absent** | The mechanism exists (a daily review can reduce or close), the slot does not. What signals mean "the thesis is failing" has to be researched out of the trajectory data |
| Slot 7 Hedge, per trade | **absent** | `HEDGE_ADD` is in the vocabulary and refused as unbuilt; a hedge leg needs a second leg under one `trade_id`, which `TradeRecord` cannot hold |
| Rolls | **absent** | `signals/review.py: Roll` raises with the reason: one logical trade would have to span several contracts |
| SignalCombiner | **absent** | Only one slot can act, so there is nothing to combine |

### Layer 3 · Portfolio and risk — partial

| Piece | Status | Where / what is missing |
|---|---|---|
| Slot 6 Positioning | **built** | `portfolio/positioning.py`: four methods. `risk_pct` divides by the stop distance, which is where "size = risk budget / stop distance" lands in code |
| Risk model, greeks aggregation | **built** | `portfolio/scenarios.py: aggregate_greeks`. A position whose greeks cannot be read is counted, never treated as 0 |
| Scenarios and stress | **built** | `portfolio/scenarios.py`: a spot × IV grid that **gives no number** beyond the expansion's range, plus historical replay and historical VaR/CVaR |
| Constraints | **partial** | `portfolio/constraints.py`: exposure limits and a drawdown brake. Not wired to the interface, and they only block new entries |
| Beta-weighted delta and vega to a common index | **absent** | SPY, QQQ and IWM behave largely as one position; without this, book risk is understated |
| Slot 7 Hedge, book-wide | **absent** | — |
| TargetPortfolioEvent | **absent** | There is no target-portfolio step; the engine turns an intent into a fill directly |

### Layer 4 · Execution simulation — thin

| Piece | Status | Where / what is missing |
|---|---|---|
| Fill price | **built** | `execution/costs.py`: buy at the ask, sell at the bid, with `adverse_fraction` interpolating to the mid. The document's λ > 1 (worse than the ask) is not expressible |
| Tradability | **built** | An absent quote or a spread over the limit means untradable. No estimated price is ever substituted |
| Costs | **partial** | Commission, a regulatory fee and slippage, broken out. No exchange, ORF, OCC or TAF fees; no margin interest |
| OrderGenerator, OrderType | **absent** | No orders as objects: an intent becomes a fill |
| MarketRule | **absent** | Trading hours, halts, tick sizes, position limits and borrow are not checked. **Principle 2 is not held here** |
| Participation cap | **absent** | Nothing caps size against volume, which the synthetic chain does not have anyway |
| ExerciseModel | **partial** | `accounting/ledger.py: settle_expiry` handles expiry, physical settlement and assignment. No early exercise, and the exercise-by-exception threshold is a parameter rather than the OCC rule |
| SimBroker | **absent** | Which is why principle 3 cannot be held |

### Layer 5 · Engine and accounting — partial

| Piece | Status | Where / what is missing |
|---|---|---|
| Event loop with a fixed order | **partial** | `engine/loop.py` has a fixed eight-step daily order with a reason recorded for each step's position. It is a hand-written loop, not a priority queue over events |
| Vectorized screening | **built** | `engine/vectorized.py`. The document limits it to underlying-level screening; here it also computes option P&L, and **the two engines are required to produce row-identical trade logs when unconstrained** — a cross-check the document does not have. Keep it |
| Scheduler, Runner, BatchRunner | **partial** | `engine/strategy.py` is the assembled config the runner would build; `research/sweep.py` runs many configs. No resume from a state snapshot |
| Ledger, positions, cash, valuation, expiry, invariants | **built** | `accounting/ledger.py`, including two equity curves (mid and liquidation) because an option spread makes them differ by percentage points of max drawdown |
| Greeks attribution | **built** | `accounting/attribution.py`. The residual is a diagnostic, not a waste bin: past a threshold the attribution says it cannot be trusted |
| TradeBook | **partial** | `accounting/tradebook.py` builds the trade log both engines share. No per-trade record of every action and the component that issued it |
| Shadow tracking after exit | **absent** | Which is why counterfactual exits cannot be computed |
| Corporate actions in the ledger | **absent** | — |

### Layer 6 · Research and reports — partial

| Piece | Status | Where / what is missing |
|---|---|---|
| Metrics | **built** | `research/metrics.py` (trade level) and `api/sdk.py: equity_metrics` (book level) |
| Investor metrics | **built** | `research/investor.py`: beta and alpha against the underlying, time underwater, Sortino and Calmar, concentration. **Not in the document**; it answers "is this alpha or leveraged beta". Keep it |
| MFE / MAE | **built** | `research/excursion.py`, with **censoring detection** — using a distribution shaped by the current stop to set the stop is circular, and it says so |
| Event studies | **built** | `research/events.py`: episodes rather than days, the unconditional baseline beside every conditional probability, effective sample size, end-of-range censoring, and closes for detection with intraday lows for pain. **Only one line in the document**; this is a full implementation with the statistical traps handled. Keep it |
| Trade-off surface | **built** | `research/tradeoff.py`: the moneyness × tenor surface from the day's real chain. **Not in the document**. Keep it |
| Anomaly scan | **built** | `research/anomaly.py`: eight rules that stay silent when nothing fires. **Not in the document**. Keep it |
| Parameter sweep | **partial** | `research/sweep.py` records the trial count. No heatmap, and the count is recorded but not used |
| Sizing comparison | **built** | `research/positioning.py` |
| ExperimentTracker | **absent** | Runs are stored (`app/store.py`: immutable runs, strategies versioned by content hash) but there is no tracker keyed by a `run_id` hash, and no count of trials across an idea |
| Validation: walk-forward, purged k-fold, bootstrap, hold-out | **absent** | **This is the whole of use two.** Nothing here yet |
| Baselines: random entry, delta-equivalent underlying | **absent** | The highest-priority gap: one run says whether an idea is worth researching further |
| Ablation, variant swap, counterfactual exits | **absent** | Each needs the component protocol first, and counterfactual exits need shadow tracking |

### Layer 7 · Interfaces — partial

| Piece | Status | Where / what is missing |
|---|---|---|
| Python SDK | **built** | `api/sdk.py`: one `run()` through the whole chain |
| Local web UI | **built** | `app/`: a form generated from the slot definitions, run history with a pinned baseline, charts, the anomaly scan and the diagnostics panel. Front and back are pinned together by a declared `CONTRACT` and a guard |
| CLI | **partial** | `run_backtest.py` is an editable script, not a CLI |
| REST API, JobQueue, ResultStore | **absent** | `app/server.py` is a local service with three endpoints, not the document's REST API |
| LiveAdapter | **absent** | — |
| Auth | **absent** | Single user |

---

---

## One architectural tension, stated plainly

The architecture document requires that **slots never call each other**: every influence passes through a
shared trade context and the action vocabulary. That is what makes per-slot ablation and
variant swapping possible, which is the whole of section 6.3.

`signals/plan.py` does the opposite. One function decides the contract, the size and the
exit levels together, because in a real trade they are one judgement — a deep drawdown
means buying longer dated, betting bigger, widening the stop and raising the target, all
at once.

Both are answers to the same problem. The document's is more decomposed and researchable;
`Plan` is more direct and is what exists and is tested. Under the document, `Plan` becomes one
*variant style* rather than the main abstraction — reachable once the trade context exists.
It is kept as it is until then, because deleting a working, tested capability to make room
for an unbuilt one would leave neither.

---

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

## Goal one, continued: the criteria the architecture document adds

> These come from `Plan/backtest-system-architecture.pdf` and were not in this list
> before. Most are structural: they are what makes the seven slots separately
> researchable, which is the whole of use two. The layer tables above say where each one stands in
> the code.

| # | The criterion | Status | Note |
|---|---|---|---|
| 1.15 | **Layers communicate only through events** (MarketEvent, SignalEvent, OrderEvent, FillEvent, RiskEvent…) | missing | **The largest structural gap.** Layers call each other directly today. Until this lands, no slot can be a component that receives events, so ablation and variant swapping cannot be built |
| 1.16 | **One component protocol** — `on_event`, `get_state`, a parameter schema — and a variant registry by name and version | missing | The parameter schema is what drives both config validation and the generated web form; `core/slots.py` does the second half already |
| 1.17 | **One shared action vocabulary**, with the unbuilt actions refused at the source rather than mapped to the nearest thing that works | done | `core/actions.py`. Six of nine executable; the other three each state why not |
| 1.18 | **The trade lifecycle is explicit**: candidate scope against trade scope, four states, and the five lifecycle settings chosen rather than implied | partial | `signals/lifecycle.py` defines it. The engine has no Armed state, and four of the five settings are currently decided by accident — each gap is recorded and shown by `tools/status.py` |
| 1.19 | **A DataView that cuts at `available_at`**, raising on an out-of-range access | partial | The property is held by construction (decision-day signals, execution-day chain); the interface does not exist and raw data carries no `available_at` |
| 1.20 | **Signal and fill never share a quote**: the signal at snapshot t, the fill at t plus a decision latency | partial | Held at daily resolution (T decides, T+1 fills). The plan's intraday form needs intraday option quotes |
| 1.21 | **Illegal orders are rejected**: trading hours, halts, tick sizes, position limits, borrow | missing | Nothing checks any of these, so principle 2 ("correctness by mechanism") is not held in Layer 4 |
| 1.22 | **A `run_id` hash over config, code commit, data snapshot and run settings** | partial | `result.manifest()` records all four; they are not hashed into one identifier |
| 1.23 | **The same strategy code runs in backtest, paper and live**, with only the feed and the broker swapped | missing | There is no SimBroker interface, so nothing proves it |
| 1.24 | **The fill-price multiple λ can exceed 1** (worse than the ask), so the strategy can be tested at λ = 1.5 | missing | `adverse_fraction` interpolates between mid and the ask and stops at 1 |
| 1.25 | **Beta-weighted delta and vega to a common index** | missing | SPY, QQQ, IWM and their largest holdings behave largely as one position; without this, book risk is understated |
| 1.26 | **Shadow tracking after a trade closes** | missing | Without prices after the exit, counterfactual exit research is guesswork — which blocks 4.15 |
| 1.27 | **A point-in-time earnings and event calendar**, readable by Setup, Entry and Headwind | missing | A long option held through an earnings event behaves very differently; results should be split by event exposure |
| 1.28 | **Deflated Sharpe over the total trial count**, across every slot and setting | missing | `research/sweep.py` records trials per sweep; nothing aggregates them across an idea |

## Goal two, continued: the research tools the architecture document adds

| # | The criterion | Status | Note |
|---|---|---|---|
| 2.9 | **Ablation**: run with one slot switched off or filled by a neutral variant | missing | Needs 1.16 |
| 2.10 | **Variant swap**: fill one slot differently, everything else fixed, as a pair of runs differing in one slot alone | missing | Needs 1.16. This is what makes the strategy's change history auditable |
| 2.11 | **Counterfactual exits**: replay each closed trade under an alternative exit | missing | Needs 1.26. In-sample by construction, so anything it suggests must be confirmed out of sample |
| 2.12 | **Attribution by component**: split P&L by the slot that opened, sized, reduced, hedged or closed each part | missing | Needs the action envelope to carry `component_id`, which `core/actions.py` now defines |
| 2.13 | **Interaction tests**: vary two slots jointly and compare against the sum of the single-slot effects | missing | A large gap means the two have to be researched together |
| 2.14 | **An ExperimentTracker**: every run findable, comparable and reproducible, with the trial count per idea | missing | `app/store.py` stores runs immutably and versions strategies by content hash — the half that exists |
| 2.15 | **Baselines**: a delta-equivalent position in the underlying, and random entries with the same structure, delta and days to expiry | missing | The highest-priority gap in this group: one run says whether the option adds anything over the underlying, and whether the timing beats the volatility risk premium |

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

## Build order · where we are

The document's seven stages. Credibility and option execution come before research speed,
because running faster on an engine you cannot trust produces conclusions you cannot use.

| | Stage | State |
|---|---|---|
| 1 | Minimal loop | **done** |
| 2 | Credibility: point-in-time data, market rules, slippage and impact, invariants, golden regression | **partial** — invariants and a regression baseline are in; point-in-time data and market rules are not |
| 3 | Options data and execution | **partial** — IV, greeks and expiry are in; a contract master, a surface, American pricing and the exercise rules are not |
| 4 | Strategy components and research | **partial** — the lifecycle and the action vocabulary are defined, the slots are not separable, and the research tools that need them are absent |
| 5 | Portfolio: premium and greeks budgets, scenario limits, rolls | **partial** — budgets and scenarios exist and are not wired into the engine |
| 6 | Platform: REST API, job queue, web UI | **partial** — the local web UI exists |
| 7 | Live | **absent** |

**The one thing that unblocks the most**: Layer 0's event bus and component protocol. Until
a slot is a registered component receiving events, ablation, variant swapping and
attribution by component — the whole of use two — cannot be built.

## The order of work here

> The stages above are the document's. These are the next pieces in this repository,
> which is the same thing seen from the code rather than from the architecture.


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

---

## What this system has that the architecture document does not mention

Listed so it does not get deleted in the name of matching the document.

| | Why it is worth keeping |
|---|---|
| `Maybe[T]` (`core/maybe.py`) | The document says "flag failures instead of guessing" and gives no mechanism. This is the mechanism: `__bool__` raises, so absence cannot be read as falsy, and an architecture test bans bare `float \| None` across module boundaries |
| The guard suite (`tools/`) | Section 0.4 lists risks and mechanisms; this is eight executable guards that fail a commit, each answering to one shape of silent failure that actually happened here |
| The two-engine cross-check | The document uses the vectorized engine only to screen. Here both engines must produce row-identical trade logs when unconstrained — two entirely different implementations checking each other |
| `data/pipeline.py` | Each of nine steps declares its method, its reason, the alternatives and their costs, and where to change it, with a live check |
| `data/options/adapters.py` | The on-ramp for your own data: a schema, a health check with fatal and warning tiers, and column mapping |
| `research/anomaly.py` | Eight rules that stay silent when nothing fires |
| `research/events.py` | A full event study with the statistical traps handled |
| `research/tradeoff.py` | The moneyness × tenor surface from the day's real chain |
| `research/investor.py` | Is this alpha, or leveraged beta |
| The synthetic chain as a bounded fixture | Its conclusions are limited automatically from the `source` column, with no manual switch |
| `signals/plan.py` | See the tension above |
