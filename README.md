# The options backtest platform

A tool for researching US equity option strategies (QQQ / SPY, mid-frequency, days to
months). Two uses:

1. **Settling the strategy's details** — how each of the seven components should be set
2. **Validating it over the long run once settled** — optimisation, falsification,
   cross-checking

> Where it stands and what is missing → **[requirements.md](requirements.md)**
> (every criterion with its status)
> The architecture → **`Plan/backtest-system-architecture.pdf`**
> What is built against it, layer by layer → **[requirements.md](requirements.md)**
> Which questions the platform is meant to answer → **[research-questions.md](research-questions.md)**

---

## Install

Python 3.11+.

```bash
pip install -e ".[dev]"
```

## Use one: the web interface (start here)

```bash
python app/server.py
```

Open http://localhost:8765. It listens on localhost only.

The left panel is laid out in four layers, **in the order things happen in a trade**,
rather than as seven equal parameter boxes:

| Layer | What it covers |
|---|---|
| **before deciding** | (1) regime — should this be on today, and at what size |
| **one trade** | (2) timing · (2b) what to buy · (3)(4) the exit levels · (6) how much |
| **while holding** | (5) headwind — what to do when the thesis is partly broken |
| **portfolio** | (7) hedge — how to strip off the risk you do not want |

## Use two: Python / Jupyter

```python
from backtest import run

r = run(symbol="QQQ", start="2020-01-01", end="2024-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,      # which contract
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "take_profit": 0.8, "stop_loss": 0.4, "dte_floor": 7},
        engine="event_driven",
        sizing={"method": "risk_pct", "risk_pct": 0.02})

r.summary()          # a one-page conclusion
r.trades             # the trade log (each trade's greeks, IV, and every signal at entry and exit)
r.equity_metrics()   # return, drawdown, Sharpe, capital used
r.spread()           # what the fills cost, as a share of the premium they were paid on
r.vol_premium()      # IV at entry against the volatility the underlying actually realised
```

## Before the first run: check the data

```python
# skip (loads data)
from backtest import check_data

print(check_data(symbol="QQQ", start="2020-01-01", end="2024-12-31").text())
```

Nine checks, run against the data a backtest would load and **without running one**: the
source and whether it is synthetic, the raw/adjusted price split, the trading calendar,
the quality flags, rates, dividends, the volatility proxy, the expiry calendar, and the
chain's columns. Each step prints what it measured, why it is checked, and the file to
change it in.

It reports rather than refuses — `report.failed` and `report.warned` are there for a
script that has to decide. Point it at your own file with the same `chain=` argument
`run()` takes, and a configuration that checks out here is the one that will run.

---

# The trade plan: one function settles one trade

A strategy can be filled in component by component, or written as one function. The reason
the latter exists is that the components constrain each other, and the hardest constraint
is:

```
position size = risk budget / stop distance
```

Fix two of the three and the third is fixed. Parameter mode makes the stop and the size two
independent inputs, so they can contradict each other while nothing raises.

```python
from backtest import run
from backtest.signals.plan import Plan

def plan(ctx):
    rsi = ctx.signal("rsi_14", default=50.0)
    if rsi > 45:
        return None                              # (2) timing: not oversold enough today

    deep = rsi < 35
    c = ctx.calls[(ctx.calls.spread < 0.10) & ctx.calls.delta.notna()]
    c = c[c.dte.between(45, 75)] if deep else c[c.dte.between(21, 45)]
    if c.empty:
        return None

    return Plan(
        contract    = c.iloc[(c.delta - (0.50 if deep else 0.30)).abs().argmin()],  # (2b)
        risk_pct    = 0.030 if deep else 0.015,   # (6) how much
        stop_loss   = 0.60  if deep else 0.35,    # (4) where the thesis failed
        take_profit = 1.00  if deep else 0.60,    # (3) where it is paid
        max_hold    = 40    if deep else 15,
        tag         = "deep" if deep else "shallow",
    )

r = run(symbol="QQQ", start="2022-01-01", end="2023-12-31",
        plan=plan, select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                           "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7}, engine="event_driven")

r.trades.groupby("tag").agg(trades=("net_pnl", "size"), mean_contracts=("quantity", "mean"))
```

`tag` is stored with every trade. **The classification reflects the judgement actually made
at order time**, not one imposed afterwards from the outcome — the latter lets the analysis
conclude whatever it set out to.

### What is in `ctx`

```
ctx.chain / ctx.calls / ctx.puts   the day's selectable contracts (a DataFrame, one per row)
ctx.equity  ctx.cash  ctx.positions  ctx.drawdown    portfolio state
ctx.spot  ctx.day
ctx.signal("name", default=...)    read a signal; absent with no default **raises**, and it
                                   never quietly returns 0
```

Each row of the chain carries: `iv delta gamma vega theta` · `bid ask mid spread` · `dte`
(calendar days) · `sessions_left` (trading days) · `moneyness` (positive always means out of
the money) · `is_call` / `is_put`.

**Anything unexecutable on T+1 is already removed from the chain**, so picking freely on the
greeks cannot land on something that will not open.

---

# The daily review: dynamic exits, scaling in and out

A fixed take-profit or stop is settled at entry. A trailing stop, exiting because the thesis
broke, and scaling in all have to be recomputed daily, which is what the `review` hook is
for.

```python
from backtest import run
from backtest.signals.review import Add, Close, Reduce

def review(ctx, pos):
    # (3) a trailing stop: out after giving back a third of the peak gain
    if pos.mfe_so_far > 0.4 and pos.ratio < pos.mfe_so_far * 0.67:
        return Close("gave back a third of the gain", kind="take_profit")

    # (4) the thesis failed — usually earlier than a fixed stop
    if ctx.signal("close") < ctx.signal("sma_50"):
        return Close("below the 50-day; the reason for being long is gone", kind="stop_loss")

    # (5) the middle ground: not all or nothing
    if pos.days_held > 10 and pos.ratio < 0:
        return Reduce(0.5, "ten days and nothing has happened; cut half")

    return None      # keep holding

r = run(symbol="QQQ", start="2023-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", review=review)
print(len(r.trades), "trades")
```

`pos` carries `mfe_so_far` / `mae_so_far` (**as of today**, never looked back on), `ratio`,
`days_held`, `dte`, `iv`, `delta` and `tag`.

Two boundaries:

- **The static rules remain a safety net.** The time-based exits (`hold_days` /
  `dte_floor`) may not be circumvented by a review, or one review function written wrong
  quietly drags a position into expiry.
- **A reduction produces no new trade record.** Counted by position, three rolls become four
  trades and both the win rate and the holding period are distorted.

---

# Cleaning rules

```python
# skip (needs your own data file)
r = run(**config, chain={
    "kind": "file", "path": "your/data.parquet",
    "clean": ["crossed_quote", "below_intrinsic", "no_quote",
              {"wide_spread": 0.20}],
})
print(r.raw.chain.cleaning_report.text())
```

```python
# skip (needs your own data file)
r = run(**config, chain={"kind": "file", "path": "your/data.parquet",
                          "iv_model": "american"})   # default: "european"
```

**Which model solves the IV is your choice**, and the default is European because of a
measurement rather than an argument: one American solve is a bisection over a 200-step
binomial tree, about **13 ms**, so 200,000 rows is 43 minutes. Screening does not help —
the put case applies whenever rates are positive, which is every put.

The bias is one-sided and stated rather than hidden: an American option is worth at least
its European counterpart, so a European solve returns an IV that is too **low** wherever
early exercise has value — and that is largest on exactly the contracts you look at
hardest, a deep in-the-money put or a call before a dividend. Every solved row carries
`iv_european` or `iv_american` in `quality_flags`, so a result never has to be asked
which produced it.

**Supply an `iv` column if your vendor has one.** Neither model then runs: it is faster,
and the vendor's own solve is also where a bad quote became an absent IV rather than a
wrong one.

**No cleaning rule is on by default**, and that is deliberate. A platform-chosen cleaning rule is
the platform deciding which market conditions your strategy is tested through — and the
register lists cleaning as a group-1 choice: **test its sensitivity, never optimise it.**
Cleaner data can remove stressed periods and bias the result toward calm markets, so the
reading that counts is the same run with the rules off, compared over the stressed
stretches rather than over the whole range.

Two things a rule can do, and the difference matters:

- **mark untradable** — the row stays with its prices and no order fills against it. The
  default for a wide spread or a missing quote, which is the document's own instruction:
  never forward-fill a quote-less contract, because a halted contract at yesterday's
  price is the most flattering thing a backtest can do with missing data.
- **drop** — the row leaves. Only for a row that is not a price at all: a crossed quote,
  or an ask below intrinsic value.

Nothing writes a price. A cleaned price is a price somebody made up, and the one thing
worse than bad data is bad data that looks fine.

# Wiring in your own data

**The synthetic option chain is a test fixture, not a data source.** Its IV is constant,
with no skew and no term structure, so **option-side conclusions (which delta, where to take
profit) do not hold on it**. Conclusions about the underlying (when to enter) can be
trusted.

Three steps, with a tool for each:

```python
from backtest.data.adapters import check_file, describe_schema

print(describe_schema())    # 1. which columns, what each means, what absence costs
# check_file("your/path")   # 2. a health check before any run: fatal / warning  # skip
```

```python
# skip (this needs your own data file)
run(..., chain="file:your/path")                       # 3a. standard column names
run(..., chain={"kind": "file", "path": "your/path",   # 3b. names that do not line up
                "column_map": {"trade_date": "asof"}})
```

`.parquet` / `.csv` / a directory of either is accepted. Absent optional columns are filled
in automatically (IV solved from the mid, greeks derived with Black-Scholes) **and flagged** —
a vendor's delta and a derived delta are not the same thing.

After a swap, the "option-side cannot be trusted" warning **disappears on its own**: it is
determined from the `source` column, not by a manual switch. Nothing above (selection,
engine, research) changes.

---

# The two engines

| | trade table | event driven |
|---|---|---|
| How it runs | each trade independently, vectorised | day by day, with portfolio state |
| Speed | seconds | minutes |
| What it answers | **is there edge** in this idea | **can this strategy be run** |
| Trade count | an **upper bound** (unconstrained by capital) | what could actually be done |
| Equity curve / drawdown / Sharpe | no | yes |
| Plans / daily review / non-fixed sizing | **refused**, with the reason | supported |

On the same configuration and **with no capital constraint the two engines must produce
row-identical trade logs** — the basis for checking each against the other, held by a test.

---

# Reading the results

```python
from backtest import run

r = run(symbol="QQQ", start="2023-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 10, "dte_floor": 5}, engine="event_driven")

r.stats()            # count, win rate, payoff ratio, mean holding
r.distribution()     # quantiles, tails, skew and kurtosis
r.by_exit_reason()   # grouped by how it ended
r.costs()            # commission / regulatory / slippage, before and after
r.attribution()      # P&L attribution: delta / gamma / vega / theta plus the residual
r.greeks()           # daily portfolio greeks
r.stress()           # the stress grid (**no number beyond the expansion's range**)
r.scenarios()        # historical scenario replay
r.excursion()        # the MFE / MAE distribution, with **censoring detection**
r.equity_metrics()   # return, drawdown, Sharpe, capital used
r.events             # run events: selection failures, untradable, deferrals, dropped candidates
r.turnover()         # premium traded over mean equity, and annualised
r.why()              # why each fill happened: order -> target -> signal -> component
r.manifest()         # the run_id, the commit, the data version, the config, provenance
```

`r.why()` is the one worth knowing about. Every fill is followed back to the slot that
asked for it, through the order it came from and the target that produced the order, with
the values each step saw — and **it prints the gaps**. A chain that stops early because a
link was never set reads exactly like a complete one, so each trace says which hops it
found. A run where no fill can be explained produces the same trade table as one where
every fill can. Pass a `trade_id` for one trade instead of all of them.


## Stopping and continuing a run

```python
# skip (two runs plus a continuation)
first = run(**config, until="2022-12-30")     # stops there, with a checkpoint
whole = run(**config, resume_from=first.checkpoint)
```

A resumed run is **row-identical** to a continuous one — that is the correctness
criterion and the test that holds it. The useful case is not an interrupted run: it is
taking the book as it stood at a date and continuing it under a different rule, which
otherwise needs two full runs that differ in every trade before the change as well as
after it.

```python
# skip (one backtest per configuration)
from backtest import run_batch

batch = run_batch({"wide": {**config, "exit": {"stop_loss": 0.6}},
                   "tight": {**config, "exit": {"stop_loss": 0.3}}})
print(batch.describe())
```

`batch.trials` counts the configurations that **failed** as well as the ones that ran. A
batch of forty is forty trials whether or not thirty-nine are mentioned again, and that
is the number the deflated Sharpe needs.

## The account, and the strategy

The ledger separates four things that all move the equity, because folding any of them
into the strategy's P&L would credit the strategy with something it did not earn:

| | |
|---|---|
| realised and unrealised | the strategy's own P&L |
| **interest** | earned on the cash balance at the risk-free rate |
| **dividends** | received on shares, which only arrive from physical delivery |
| external flows | deposits and withdrawals |

The figure worth knowing: over three years on $100,000 at 4.5%, **interest is around
9,300 and the fixture strategy's net P&L is −473**. Twenty times. Folded in, a losing
strategy reads as a profitable one — which is why the accounting identity carries each
as its own term, and why it refused the first session after interest was booked.

## How much of this is the cost assumption

```python
# skip (three full backtests)
print(r.realism_test().text(synthetic=True))
```

λ — how far from the mid an order actually fills — is the assumption results move most on
and the least observable one in the model: the fill a strategy would have got is the one
fact a backtest cannot have. The default is 1.0, the far side of the quote, and the run
at 0.5 is a **sensitivity**, not a result.

The tool refuses a sweep that leaves 1.0 out, and names no best fraction. A sweep of 0.3,
0.5 and 0.7 is a search for a figure that makes the strategy work, and it is what somebody
rescuing a result would write.

## What the book may have at risk at once

```python
# skip (a full backtest)
r = run(**config, premium_budget={"max_premium_pct": 0.10,          # of equity, total
                                   "max_premium_per_expiry_pct": 0.04,
                                   "min_cash_pct": 0.20})
```

For a long-options book the maximum loss on a position is the premium paid, so premium at
risk is the budget with a floor under it — and the only limit here whose breach is a
**certainty** rather than a scenario: an option held to expiry out of the money loses all
of its premium with probability one. Delta and vega limits answer a different question,
which is how the book moves on the next dollar.

**The per-expiry limit is not implied by the total.** Ten percent of equity over six
expiries loses at most a sixth of it on any one Friday; the same ten percent on one expiry
is all of it at once, and a total limit passes both identically.

There is no default. How much of equity a strategy may have at risk is the single most
consequential number in a long-options book, and a platform that picks it has made the
strategy's main decision for you.

## Before refining anything, run the baselines

```python
# skip (each random draw is a full backtest)
from backtest.research.baselines import report

r = run(symbol="QQQ", start="2022-01-01", end="2023-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "take_profit": 0.8, "stop_loss": 0.4, "dte_floor": 7},
        engine="event_driven", sizing={"method": "risk_pct", "risk_pct": 0.02})

print(report(r.baselines(draws=50), synthetic=r.provenance()["all_synthetic"]))
```

Two comparisons, and they ask different questions:

- **a delta-equivalent position in the underlying** — if shares with the same entry
  exposure beat the option, the option added nothing, and the work spent on selection and
  exit levels was spent on a worse instrument
- **random entries with the same structure** — the same contract rules, sizing and exits,
  on dates drawn at random. The result is a **percentile in a distribution**, never a
  single draw: a strategy at the 60th percentile of random has found nothing

Run these first. Tuning a take-profit level on a strategy that cannot beat random entries
is work on a number that does not matter.

## What is each slot worth

```python
# skip (one extra backtest per slot)
from backtest.research.ablation import report

print(report(r.ablate()))
```

Each slot switched off in turn, the rest fixed. A slot whose contribution is small is a
parameter you are tuning for nothing.

It **refuses** the stop-loss ablation under `risk_pct` sizing unless you accept the joint
reading, because removing the stop also changes the size — `size = risk budget / stop
distance`. Two variables moved, so the difference cannot be attributed to one. Ablate with
fixed sizing for a clean reading.

The **distribution** of per-trade P&L matters far more than the mean. An option buyer has a
low win rate, positive skew and fat tails: 30% winners and profitable, or 60% winners and
losing, are both normal. When the median and the mean have opposite signs the report says so
explicitly — that means a few extremes drive the average, which does not generalise.

---

# Three boundaries worth knowing

**One: the synthetic chain cannot tune option-side parameters.** See "Wiring in your own
data" above.

**Two: the trade-table engine has no equity curve.** Its trade count is an upper bound, not
what could be done. For drawdown and Sharpe use `engine="event_driven"`.

**Three: the execution lag is one trading day.** The intent forms at T's close and the fill
happens at T+1's close, for entries and exits alike. Using T's close for both the decision
and the fill is the commonest source of look-ahead, and it is excluded here
architecturally. A side effect: a stop can be followed by a rebound the next day, so a few
trades labelled "stop loss" end up profitable — that is a real cost, not a bug.

---

# Development

```bash
python -m tools.check               # the guards
python -m tools.check --fast        # skipping the slow ones
python -m tools.status              # the project's state (computed from the definitions)
python -m tools.baseline update     # update the regression baseline (prints the difference)
```

| Guard | What it checks |
|---|---|
| test suite | behavioural regression; the truncate-and-recompute property test covers look-ahead |
| dependencies | module layers; pricing may not escape the data layer; an unregistered module fails |
| shipped | files that exist locally and not in git |
| language | anything in the project that is not English |
| baseline | whether the results changed unintentionally |
| components | the definitions being self-consistent; a form field must be a `run()` parameter |
| doc examples | the python blocks in this README actually run |

The checking rules and the bar for a new guard are in [conventions.md](conventions.md).

## Layout

The tree follows the architecture document's eight layers, and `tools/check_deps.py` fails
the build on a dependency that points upward. Square brackets mark a directory the
document specifies that **does not exist here yet**; `requirements.md` says why for each
one.

```
backtest/
  core/         maybe.py errors.py types.py config.py clock.py actions.py
                run_log.py slots.py events.py component.py dataview.py
                invariants.py run_id.py
                                        [absent: registry.py — the variant registry
                                                 lives in component.py]
  data/         ingest.py validate (in underlying.py / options/chain.py) store.py
                market.py multi_asset.py pipeline.py underlying.py
                                        [absent: pit.py universe.py features/]
    options/    chain.py chain_registry.py adapters.py pricing.py synthetic/
                                        [absent: contracts.py quotes.py rates.py
                                                 surface.py]
  signals/      lifecycle.py contracts.py registry.py indicators.py strategy.py
                review.py plan.py exit_context.py
                take_profit/ stop_loss/                    (slots 4 and 5)
                                        [absent: hedge/ rolls/ universe/ filters/
                                                 combiner.py]
    setup/      regime.py                                   (slot 1)
    entry/      expression.py                               (slot 2)
  portfolio/    positioning.py (slot 6)  scenarios.py  constraints.py
                risk_rules/                                 (slot 7, the headwind)
                                        [absent: rebalance.py optimizers/
                                                 risk_models/ hedges/]
  execution/    costs.py
                                        [absent: orders.py market_rules/ fill_models/
                                                 slippage/ exercise.py sim_broker.py]
  engine/       loop.py vectorized.py strategy.py runner.py
                                        [absent: scheduler.py — batch.py is
                                                 run_batch() in runner.py]
  accounting/   ledger.py tradebook.py attribution.py
                                        [absent: positions.py cash.py valuation.py
                                                 corp_actions.py expiry.py pnl.py
                                                 snapshots.py — all inside ledger.py]
  research/     metrics.py sweep.py positioning.py excursion.py events.py
                investor.py tradeoff.py anomaly.py tracker.py validation/
                baselines.py ablation.py counterfactual.py delay.py realism.py
                holdout.py boundary.py forms.py
                                        [absent: reports/]
  api/          sdk.py config_file.py    [absent: cli.py rest/ jobs/]
app/            server.py store.py static/        (the plan's Layer 7 web UI)
tools/          the guards and the regression baseline
                                        [absent: live/ — no live path]
```

Two of the brackets moved rather than shrank. `take_profit/` and `stop_loss/` sat under
`entry/`, where the indentation reads as "missing from entry" and they are siblings of it;
`risk_rules/` was listed absent from `portfolio/` after it was built. A bracket whose
indentation does not say which directory it belongs to cannot be checked, which is half of
why `tools/check_docs.py` exists.

The shape of the modules that do exist:

```
core/       maybe.py     values that may be absent — no bare None / NaN / 0
            dataview.py  every read cut at as_of; reading the future raises
            invariants.py the five that hold on every day of every run
            run_id.py    the hash of config, commit, data and settings
            types.py     ContractId, TradeRecord, exit reasons and their precedence
            actions.py   the nine-action vocabulary, and which six are executable
            slots.py     the seven slots: what each decides in a trade (the UI's source)
data/       underlying.py  two price sets: adjusted for signals, traded for strikes
            pipeline.py    nine steps, each with its reason, alternatives and a check
  options/  adapters.py    wire in your own data: schema, health check, column mapping
            pricing.py     a market model, not part of the synthetic chain
signals/    lifecycle.py   four states, and the five settings still open
            contracts.py   selection: a feasible region plus a ranking objective
            plan.py        one function decides contract, size and exits together
            review.py      the daily review: dynamic exits, scaling in and out
portfolio/  positioning.py slot 6 · size = risk budget / stop distance
            scenarios.py   greeks aggregation, the stress grid, VaR, historical replay
execution/  costs.py       buy at the ask, sell at the bid, never the mid
engine/     loop.py        day by day        vectorized.py  screening
accounting/ ledger.py      the accounting identity checked every day
            attribution.py the greeks split, with the residual as a diagnostic
research/   baselines.py   is it the signal, the structure, or the drift
            ablation.py    what each slot is worth, with the coupling refused
            excursion.py   MFE / MAE, with censoring detection
            events.py      event studies, with the unconditional baseline
            tradeoff.py    the moneyness × tenor surface from the day's real chain
            anomaly.py     eight rules that stay silent when nothing fires
```

`research/` is your local research record (run history, trade logs, notes) and is **not
version controlled** — it is your asset rather than code, and a SQLite binary cannot be
merged. To back it up, copy that directory on its own.
