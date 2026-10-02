# The options backtest platform · the whole picture

> The acceptance criteria, each item's status, and the placeholder list are in
> **[requirements.md](requirements.md)**. This file holds two things only: what the
> system looks like, and how things flow through it. The two documents do not overlap,
> so they cannot contradict each other.

## What the system is for

**Two uses, one foundation, and they want different things.**

### Use one · settling the details (now)

Working out the details of the seven strategy components:
`1 setup/regime` · `2 entry` · `3 take profit` · `4 stop loss` · `5 headwind` ·
`6 positioning` · `7 hedge`

This stage wants **exploration** — grouping, attribution, distributions, controls — to
see where the regularities are.

### Use two · long-term validation of a settled strategy (later, but it has to be designed for now)

Once the strategy is settled, the platform becomes a **falsification tool**: not finding
again why it works, but **repeatedly trying to show that it does not**.

This stage wants four things, in the opposite frame of mind from use one:

| | What it wants | The frame of mind |
|---|---|---|
| **Optimise** | which *region* of parameters is stable, not which point is highest | find the **plateau**, not the peak |
| **Falsify** | deliberately construct tests that could kill the strategy | try to overturn it, not confirm it |
| **Cross-check** | one conclusion holding from **mutually independent** angles | one angle holding does not count |
| **Statistics** | a **direction** and a confidence for the judgement, not a verdict | weak evidence is shown, and labelled as weak |

**Why it has to be designed for now**: what falsification needs — random controls, reverse
tests, sub-periods, perturbations, cost sensitivity — all turns out to require engine
changes if the interfaces are not left in the foundation.

## How the data flows

```
   your data ──┐
               ├──▶ the ChainSource protocol ──▶ ┌──────────────────────┐
   synthetic ──┘   (a swap changes one argument)  │  the foundation       │
   (a test fixture)                               │  underlying · pricing │
                                                  │  calendar · signals   │
                                                  │  selection · costs    │
                                                  └──────────┬───────────┘
                              ┌────────────────────┬─────────┘
                              ▼                    ▼
                    ┌──────────────────┐   ┌────────────────────┐
                    │  trade table      │   │  event driven       │
                    │  independent,     │   │  day by day,        │
                    │  seconds          │   │  minutes            │
                    │  "is there edge"  │   │  "can it be run"    │
                    │  count is a BOUND │   │  bound by cash and  │
                    │  components 1-4   │   │  concurrency        │
                    │                   │   │  components 5-7     │
                    └─────────┬─────────┘   └─────────┬──────────┘
                              └────────────┬──────────┘
                                           ▼
                                ┌──────────────────────┐
                                │  one trade log        │ <- the two engines must agree
                                │  with each trade's    │    row for row (unconstrained)
                                │  greeks, IV and every │
                                │  signal at entry and  │
                                │  at exit              │
                                └──────────┬───────────┘
                                           ▼
                                ┌──────────────────────┐
                                │  the research layer   │
                                │  distributions ·      │
                                │  attribution · strata │
                                │  risk · scenarios ·   │
                                │  sizing comparison    │
                                └──────────────────────┘
```

## The three seams

There are exactly three replaceable points in the system; everything else is an
implementation detail. Knowing these three is knowing what can be changed without
breaking something elsewhere.

| The seam | How to swap it | Does anything above change |
|---|---|---|
| **The data source** | `chain="file:/path"`, or any object implementing `ChainSource` | nothing |
| **Each part of the strategy** | fill in parameters (sweepable) or write a function (judges by the conditions) | nothing |
| **The engine** | `engine="trade_table"` / `"event_driven"` | nothing, but they can do different things |

## Where the synthetic chain sits

**It is a test fixture, not a data source.** Its only use is getting the whole pipeline to
run when there is no real data.

- Its IV is a constant, with no skew and no term structure
- So **conclusions about the underlying (when to enter) can be trusted, and option-side
  conclusions (which delta, where to take profit) cannot**
- That boundary is **determined automatically** from the `source` column and flagged in
  the report; it cannot be turned off by hand
- Pricing lives in `backtest/pricing.py`, not inside the synthetic chain — the synthetic
  chain uses it to make prices, and the file source uses it to fill in greeks a vendor
  omits. Remove the synthetic chain and pricing is still there

## Wiring in your own data

Three steps, with a tool for each:

```python
from backtest.data.adapters import describe_schema, check_file

describe_schema()          # 1. which columns, what each means, what absence costs
check_file("your/path")    # 2. a health check **before** any backtest, fatal vs warning
```

```python
run(..., chain="file:your/path")                      # 3. when the names are standard
run(..., chain={"kind": "file", "path": "your/path",  #    when they are not
                "column_map": {"trade_date": "asof"}})
```

Absent optional columns are filled in automatically (IV solved from the mid, greeks
derived with Black-Scholes) **and flagged** — a vendor's delta and a derived delta are not
the same thing, and the flag travels into the trade log.
