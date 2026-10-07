"""The plan's **Details** column, clause by clause.

## The third level, and why there had to be one

Two inventories existed before this and both were too coarse.

``requirements_map.py`` holds the document's conformance table -- one row per section, a
summary. ``plan_components.py`` holds the document's component tables -- 75 named
components, which is what the first inventory missed and what let ``Clean`` sit on no
list at all.

Both are **name level**. Each of the document's component rows carries a *Details*
column, and that column is where the content is:

    Positions   What is held   Keyed by contract and grouped by trade_id, each leg
                               tagged main or hedge; quantity, multiplier, entry
                               premium, open date, holding days, **sellable quantity
                               under the settlement rule**

``Positions`` is built. "Sellable quantity under the settlement rule" is not. A name-level
inventory says the first and cannot say the second, so a layer could read 10/10 built --
accounting did -- with eight of its fifteen detail clauses missing.

## The needles here are stricter than anywhere else, and for a reason

The first pass at this probed the whole of ``backtest/`` for bare words and reported
**five false positives out of fifteen**: ``frozen`` matched ``@dataclass(frozen=True)``;
``margin`` matched a comment saying there is **no** margin model; ``split`` matched a
sentence about attribution; ``interest`` matched a docstring; ``append-only`` matched
``run_log.py`` rather than the ledger.

That is the same failure as the two audits before it, one level down: a loose test of a
claim about a system, in an audit written because the previous audit was wrong.

So a clause here names **where** it must be, not only what to look for. A word in
``research/`` does not make the ledger carry it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Clause:
    """One clause of one component's Details column."""

    component: str
    #: The clause, in the document's own words.
    says: str
    #: Where it would have to live. A clause is only checked against these files.
    owner: tuple[str, ...]
    #: What proves it. Empty when the clause is absent.
    needle: str = ""
    #: Why it is absent, and what it costs. Empty means it is there.
    absent: str = ""

    @property
    def built(self) -> bool:
        return not self.absent


#: Layer 5 · accounting, which read 10/10 at the name level.
ACCOUNTING: tuple[Clause, ...] = (
    Clause("Ledger", "fills, fees, exercise and expiry settlements",
           ("backtest/accounting/ledger.py",), needle="def apply_fill"),
    Clause("Ledger", "dividends to cash",
           ("backtest/accounting/ledger.py",), needle="def credit_dividend"),
    Clause("Ledger", "interest",
           ("backtest/accounting/ledger.py",), needle="def accrue_interest"),
    Clause("Ledger", "deposits and withdrawals",
           ("backtest/accounting/ledger.py",), needle="def deposit"),
    Clause("Ledger", "append-only",
           ("backtest/accounting/ledger.py",), needle="def verify_append_only"),
    Clause("Positions", "keyed by contract and grouped by trade_id",
           ("backtest/accounting/ledger.py",), needle="trade_id"),
    Clause("Positions", "quantity, multiplier, entry premium",
           ("backtest/accounting/ledger.py",), needle="cost_basis"),
    Clause("Positions", "open date and holding days",
           ("backtest/accounting/tradebook.py",), needle="holding_days"),
    Clause("Positions", "each leg tagged main or hedge",
           ("backtest/accounting/ledger.py",),
           absent="single-leg, so every position is the main leg. The **reader** exists "
                  "-- `portfolio/constraints.py` splits a trade log on an `is_hedge` "
                  "column and says it must be set at order time -- and nothing writes "
                  "it. It arrives with multi-leg, as `5.2.legs` does"),
    Clause("Positions", "sellable quantity under the settlement rule",
           ("backtest/accounting/ledger.py",), needle="def sellable"),
    Clause("Cash", "available",
           ("backtest/accounting/ledger.py",), needle="def cash"),
    Clause("Cash", "frozen by open orders",
           ("backtest/accounting/ledger.py",), needle="def freeze"),
    Clause("Cash", "margin borrowed",
           ("backtest/accounting/ledger.py",),
           absent="no margin model, stated rather than missing: the ledger refuses a "
                  "naked short outright because any margin number it produced would be "
                  "made up. A cash-secured put is sized at strike x shares, which is "
                  "the only short this system allows"),
    Clause("Valuation", "long options marked at bid, mid also reported",
           ("backtest/accounting/ledger.py",), needle="equity_liq"),
    Clause("Valuation", "untradable contracts at last valid price, flagged",
           ("backtest/accounting/ledger.py",), needle="stale_marks"),
    Clause("CorporateActions", "dividends to cash",
           ("backtest/accounting/ledger.py",), needle="def credit_dividend"),
    Clause("CorporateActions", "after tax",
           ("backtest/accounting/ledger.py",),
           absent="dividends are booked gross. A withholding rate is a fact about the "
                  "account's domicile rather than about the market, and inventing one "
                  "would put a number in the result that nobody chose"),
    Clause("CorporateActions", "splits adjust quantity and cost; contract adjustments "
                               "update multiplier and strike",
           ("backtest/accounting/ledger.py",),
           absent="needs the corporate-action feed `1.corporate` waits on. The "
                  "dual-price split handles the signal side; the position side -- a "
                  "split changing a held contract's quantity and strike -- has no "
                  "source"),
    Clause("Expiry", "exercise, cash settlement or lapse; removes the position",
           ("backtest/accounting/ledger.py",), needle="def settle_expiry"),
    Clause("PnL", "realised, unrealised, fees, dividends",
           ("backtest/research/metrics.py",), needle="def cost_breakdown"),
    Clause("PnL", "by the component that closed it",
           ("backtest/accounting/tradebook.py",), needle="component_id"),
    Clause("PnL", "by leg role (main or hedge)",
           ("backtest/research/metrics.py",),
           absent="same gap as the position's leg tag: single-leg, so there is one role"),
    Clause("GreeksAttribution", "delta, gamma, vega, theta and residual",
           ("backtest/accounting/attribution.py",), needle="residual"),
    Clause("Snapshots", "daily equity, cash, positions",
           ("backtest/accounting/ledger.py",), needle="class Snapshot"),
    Clause("Snapshots", "premium at risk",
           ("backtest/accounting/ledger.py",), needle="premium_at_risk"),
    Clause("Snapshots", "net greeks",
           ("backtest/engine/loop.py",), needle="aggregate_greeks"),
    Clause("Snapshots", "turnover",
           ("backtest/research/metrics.py",), needle="def turnover"),
    Clause("TradeBook", "MFE and MAE in premium and underlying terms",
           ("backtest/core/types.py",), needle="mfe_underlying"),
)

CLAUSES: tuple[Clause, ...] = ACCOUNTING
