# Architecture · the plan, and where the code actually is

> The architecture is defined by **`Plan/backtest-system-architecture.pdf`** (38 pages, 2 Oct 2026).
> This file is not a second copy of it. It does one thing the PDF cannot: say, layer by
> layer, **what exists in the code, what is partly there, and what is absent** — so the
> gap is visible rather than assumed.
>
> Per-criterion status → **[requirements.md](requirements.md)**
> The questions to answer with it → **[research-questions.md](research-questions.md)**
> How changes are checked → **[conventions.md](conventions.md)**

---

## The five principles

From the plan. They apply to every layer, and each one is either held by a mechanism or
is not held at all — stated honestly below.

| | Principle | Held by |
|---|---|---|
| 1 | **One-way dependencies**: a layer depends only on interfaces below it | `tools/check_deps.py`, which fails the build. It found two real misplacements during this restructure |
| 2 | **Correctness by mechanism**: look-ahead, illegal trades and accounting errors are blocked by the foundation, not by the strategy author's discipline | partly. Look-ahead: the truncate-and-recompute property test plus decision-day signals with execution-day chains. Accounting: the ledger's identity check every day. Illegal trades: **nothing** — there is no MarketRule |
| 3 | **Same code for backtest and live** | not held. There is no live path, so nothing proves it |
| 4 | **Pessimistic by default** | held for fills (buy at ask, sell at bid, never the mid) and for marking (longs at the bid). Not held for capacity: there is no participation cap |
| 5 | **Reproducible**: config, code version and data version determine the result | partly. `result.manifest()` records the config, provenance and dependency versions, and the regression baseline catches drift. There is no `run_id` hash over (config, commit, data snapshot) |

## The three ways options differ

Also from the plan, and each shows up in the layers below:

- **The tradable set changes every day.** Contracts list and expire, so the universe is a
  chain, not a ticker list.
- **Price has more dimensions.** Spot, implied volatility and time all move it, so risk is
  measured in greeks.
- **Costs are dominated by the spread and by time decay**, not by commissions.

---

## The eight layers · what exists

Status is one of **built** (the layer does its job), **partial** (some of it works, with
what is missing named) and **absent**.

### Layer 0 · Foundation — partial

The plan's foundation is events, the component protocol, clock and data view, invariants,
config and logging. Fix it first and the layers above become swappable.

| Piece | Status | Where / what is missing |
|---|---|---|
| Event types (MarketEvent, QuoteEvent, SignalEvent, OrderEvent, FillEvent, RiskEvent, TimerEvent, ExpiryEvent…) | **absent** | **The largest structural gap.** Layers call each other directly. `core/run_log.py` is a *diagnostic* stream (quote missing, selection failed, exit deferred), not an inter-layer bus |
| Action vocabulary | **built** | `core/actions.py`. Nine actions; `BUILT` says which six the engine can execute and gives the reason for each of the other three, and `require_built` refuses them at the source |
| Component protocol (`on_event`, `get_state`, params schema) | **absent** | There is no event bus for `on_event` to receive from. The current hooks are bare callables: `entry=`, `select=`, `plan=`, `review=` |
| Variant registry by name and version | **absent** | Only chain sources are registered (`data/options/chain_registry.py`). Without the protocol there is nothing to register |
| Clock | **partial** | `core/clock.py`: trading calendar, early closes, expiry calendar. No session phases, and no single `now` — the engine passes the day around |
| DataView with an `available_at` cut | **partial** | No DataView object. The *property* is held by construction — a signal view is the decision day's and the chain is the execution day's — but there is no interface that enforces it, and raw data carries no `available_at` column |
| Invariants | **partial** | `accounting/ledger.py: check_invariant()` runs the accounting identity every day and aborts on a breach. The plan's other invariants (position equals cumulative fills, no open position in an expired contract) are not checked |
| Config | **partial** | `core/config.py` is frozen dataclasses with validation. Not YAML, no schema, no per-slot `{variant, version, params}` |
| Logging and traceability | **partial** | `core/run_log.py` plus the diagnostics panel. A fill cannot be traced back through fill → order → target → signal → component, because there are no orders or targets as events |

### Layer 1 · Data — built, for what it covers

| Piece | Status | Where / what is missing |
|---|---|---|
| Ingest with retries | **built** | `data/ingest.py`. Raises rather than returning empty — a silently empty feed makes a backtest conclude there were no signals |
| Two price sets | **built** | `data/underlying.py`. Adjusted for signals, traded for strikes; the factor applies to open/high/low as well as the close |
| Validate | **built** | `data/underlying.py: validate_bars`, `data/options/chain.py: validate_chain`, and `data/options/adapters.py: check_file` with fatal and warning tiers |
| The pipeline, self-documenting and self-checking | **built** | `data/pipeline.py`: nine steps, each declaring its method, its reason, the alternatives and their costs, and where to change it — with a check that runs on the real data. **Not in the plan**; keep it |
| Rates and dividends | **partial** | `data/market.py`: the ^IRX series and real ex-dividend events. A constant fallback rate is used when the series is absent, which matters at long tenors (see research-questions 7.3) |
| IV and greeks | **partial** | `data/options/pricing.py`: Black-Scholes plus an implied-vol solve that returns an explicit absence rather than a guess. No American model, so put prices are understated by 1.7% to 12.5% (research-questions 7.1) |
| Point-in-time alignment | **absent** | No `available_at` column, no as-of queries, no revision versioning |
| Universe | **absent** | One symbol per run, chosen by argument |
| Surface builder | **absent** | Moot on a synthetic chain whose IV is constant; needed with real data |
| Contract master with adjustment history | **absent** | `core/types.py: ContractId/ContractSpec` carry the static terms. OCC adjustments are not tracked |
| Store | **built** | `data/store.py`: a parquet cache whose key covers every dimension that changes the result |
| Wiring in your own data | **built** | `data/options/adapters.py`. **Not in the plan** — the plan assumes data exists. Keep it |

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
| Fill price | **built** | `execution/costs.py`: buy at the ask, sell at the bid, with `adverse_fraction` interpolating to the mid. The plan's λ > 1 (worse than the ask) is not expressible |
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
| Vectorized screening | **built** | `engine/vectorized.py`. The plan limits it to underlying-level screening; here it also computes option P&L, and **the two engines are required to produce row-identical trade logs when unconstrained** — a cross-check the plan does not have. Keep it |
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
| Investor metrics | **built** | `research/investor.py`: beta and alpha against the underlying, time underwater, Sortino and Calmar, concentration. **Not in the plan**; it answers "is this alpha or leveraged beta". Keep it |
| MFE / MAE | **built** | `research/excursion.py`, with **censoring detection** — using a distribution shaped by the current stop to set the stop is circular, and it says so |
| Event studies | **built** | `research/events.py`: episodes rather than days, the unconditional baseline beside every conditional probability, effective sample size, end-of-range censoring, and closes for detection with intraday lows for pain. **Only one line in the plan**; this is a full implementation with the statistical traps handled. Keep it |
| Trade-off surface | **built** | `research/tradeoff.py`: the moneyness × tenor surface from the day's real chain. **Not in the plan**. Keep it |
| Anomaly scan | **built** | `research/anomaly.py`: eight rules that stay silent when nothing fires. **Not in the plan**. Keep it |
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
| REST API, JobQueue, ResultStore | **absent** | `app/server.py` is a local service with three endpoints, not the plan's REST API |
| LiveAdapter | **absent** | — |
| Auth | **absent** | Single user |

---

## The seven slots and the lifecycle

Defined once in code, not in prose:

- **`backtest/core/slots.py`** — the seven slots: what each decides in a trade, what it
  outputs, its status, what is missing, and for a discovered slot how to find it. The
  interface's form is generated from this, and a guard checks every field is a real
  `run()` parameter.
- **`backtest/signals/lifecycle.py`** — the four states, which slots run in each, and the
  five lifecycle settings with what enforces each one today.
- **`backtest/core/actions.py`** — the nine actions, which six are executable, and why
  each of the other three is not.

`python -m tools.status` prints all three, computed. There is no second copy to go stale.

## One architectural tension, stated plainly

The plan requires that **slots never call each other**: every influence passes through a
shared trade context and the action vocabulary. That is what makes per-slot ablation and
variant swapping possible, which is the whole of section 6.3.

`signals/plan.py` does the opposite. One function decides the contract, the size and the
exit levels together, because in a real trade they are one judgement — a deep drawdown
means buying longer dated, betting bigger, widening the stop and raising the target, all
at once.

Both are answers to the same problem. The plan's is more decomposed and researchable;
`Plan` is more direct and is what exists and is tested. Under the plan, `Plan` becomes one
*variant style* rather than the main abstraction — reachable once the trade context exists.
It is kept as it is until then, because deleting a working, tested capability to make room
for an unbuilt one would leave neither.

## The directory map

The code tree follows the plan. Where a directory in the plan does not exist here, the
reason is above rather than an empty folder.

```
backtest/
  core/         maybe.py errors.py types.py config.py clock.py
                actions.py  run_log.py  slots.py
                                        [absent: events.py component.py registry.py
                                                 dataview.py invariants.py]
  data/         ingest.py validate (in underlying.py / options/chain.py) store.py
                market.py multi_asset.py pipeline.py underlying.py
                                        [absent: pit.py universe.py features/]
    options/    chain.py chain_registry.py adapters.py pricing.py synthetic/
                                        [absent: contracts.py quotes.py rates.py
                                                 surface.py]
  signals/      lifecycle.py contracts.py registry.py indicators.py strategy.py
                review.py plan.py
    setup/      regime.py                           (slot 1)
    entry/      expression.py                       (slot 2)
                                        [absent: take_profit/ stop_loss/ headwind/
                                                 hedge/ rolls/ universe/ filters/
                                                 combiner.py]
  portfolio/    positioning.py (slot 6)  scenarios.py  constraints.py
                                        [absent: rebalance.py optimizers/
                                                 risk_models/ risk_rules/ hedges/]
  execution/    costs.py
                                        [absent: orders.py market_rules/ fill_models/
                                                 slippage/ exercise.py sim_broker.py]
  engine/       loop.py vectorized.py strategy.py
                                        [absent: scheduler.py runner.py batch.py]
  accounting/   ledger.py tradebook.py attribution.py
                                        [absent: positions.py cash.py valuation.py
                                                 corp_actions.py expiry.py pnl.py
                                                 snapshots.py — all inside ledger.py]
  research/     metrics.py sweep.py positioning.py excursion.py events.py
                investor.py tradeoff.py anomaly.py
                                        [absent: tracker.py validation/ baselines.py
                                                 ablation.py counterfactual.py reports/]
  api/          sdk.py                   [absent: cli.py rest/ jobs/]
app/            server.py store.py static/        (the plan's Layer 7 web UI)
tools/          the guards and the regression baseline
                                        [absent: live/ — no live path]
```

## Build order · where we are

The plan's seven stages. Credibility and option execution come before research speed,
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

## What this system has that the plan does not mention

Listed so it does not get deleted in the name of matching the plan.

| | Why it is worth keeping |
|---|---|
| `Maybe[T]` (`core/maybe.py`) | The plan says "flag failures instead of guessing" and gives no mechanism. This is the mechanism: `__bool__` raises, so absence cannot be read as falsy, and an architecture test bans bare `float \| None` across module boundaries |
| The guard suite (`tools/`) | The plan's 0.4 lists risks and mechanisms; this is eight executable guards that fail a commit, each answering to one shape of silent failure that actually happened here |
| The two-engine cross-check | The plan uses the vectorized engine only to screen. Here both engines must produce row-identical trade logs when unconstrained — two entirely different implementations checking each other |
| `data/pipeline.py` | Each of nine steps declares its method, its reason, the alternatives and their costs, and where to change it, with a live check |
| `data/options/adapters.py` | The on-ramp for your own data: a schema, a health check with fatal and warning tiers, and column mapping |
| `research/anomaly.py` | Eight rules that stay silent when nothing fires |
| `research/events.py` | A full event study with the statistical traps handled |
| `research/tradeoff.py` | The moneyness × tenor surface from the day's real chain |
| `research/investor.py` | Is this alpha, or leveraged beta |
| The synthetic chain as a bounded fixture | Its conclusions are limited automatically from the `source` column, with no manual switch |
| `signals/plan.py` | See the tension above |
