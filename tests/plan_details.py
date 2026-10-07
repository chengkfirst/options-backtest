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
           ("backtest/accounting/ledger.py",),
           absent="**the ledger does not book a dividend at all.** The guard found this "
                  "-- I had it down as built because the word appears in a dozen files, "
                  "and every one of them is pricing: the dividend yield goes into "
                  "Black-Scholes, into the tree, into the early-exercise test. None of "
                  "it is a cash movement.\n\n"
                  "For an option holder that is correct -- an option pays no dividend. "
                  "It stops being correct for **shares**, and this system now takes "
                  "physical delivery. The reason nothing has gone wrong is narrow: "
                  "delivered shares are sold on the next session, so a dividend is "
                  "missed only when the ex-date falls on that single overnight. It "
                  "becomes a real omission the moment shares are held for any longer, "
                  "which is what a hedge in the underlying would do"),
    Clause("Ledger", "interest",
           ("backtest/accounting/ledger.py",),
           absent="no interest is booked -- not on idle cash and not on margin. For a "
                  "long-options book held days to weeks the cash balance earns the "
                  "risk-free rate, which over three years at 4.5% on $100,000 is a "
                  "figure comparable to the strategy's own P&L. **This flatters nothing "
                  "and understates nothing; it simply omits a return the account would "
                  "have earned**, so a result here is a strategy's P&L rather than an "
                  "account's"),
    Clause("Ledger", "deposits and withdrawals",
           ("backtest/accounting/ledger.py",),
           absent="capital is fixed at the start. The invariant for daily P&L already "
                  "takes an `external_flows` term and it is always zero, so the shape "
                  "is there and nothing supplies it. It matters for validating a live "
                  "account against a backtest, which is use two"),
    Clause("Ledger", "append-only",
           ("backtest/accounting/ledger.py",),
           absent="entries are appended and nothing enforces it -- no method refuses a "
                  "rewrite, and `_records` is an ordinary list. The ledger is not in "
                  "fact mutated anywhere, so this is a missing **guarantee** rather "
                  "than a bug: a future path that edited a booked entry would not be "
                  "stopped"),
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
           ("backtest/accounting/ledger.py",),
           absent="**absent, and it is the clause a name-level inventory cannot see.** "
                  "US equity options settle T+1, so a contract bought today cannot be "
                  "sold today -- and with a one-day execution lag enforced, no position "
                  "in this system is ever sold on the day it was bought, which is why "
                  "nothing has gone wrong. The gap bites the moment the lag is "
                  "configurable or an intraday snapshot is added: a same-session "
                  "round-trip would fill, and in an account it would be refused"),
    Clause("Cash", "available",
           ("backtest/accounting/ledger.py",), needle="def cash"),
    Clause("Cash", "frozen by open orders",
           ("backtest/accounting/ledger.py",),
           absent="cash is not reserved when an order is queued. The engine checks "
                  "capital at the entry decision and the fill is a day later, so two "
                  "entries queued on the same day are each checked against the full "
                  "balance and both can fill. `enforce_capital` then refuses the second "
                  "**at the fill**, which is the right outcome by the wrong route: the "
                  "trade is reported as refused rather than never placed, and a real "
                  "account would not have accepted the order"),
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
           ("backtest/accounting/ledger.py",),
           absent="same gap as the ledger's own clause above: the dividend reaches "
                  "pricing and never reaches cash. Listed twice because the document "
                  "lists it twice -- once as something the ledger records and once as "
                  "something the corporate-action module applies -- and both are absent"),
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
           ("backtest/accounting/ledger.py",),
           absent="**the document's core budget measure for a long book**, and the "
                  "snapshot does not carry it. For a long-options book the maximum loss "
                  "on a position is the premium paid, so premium at risk is the one "
                  "exposure number that is exactly knowable -- and `deployed` is in the "
                  "snapshot, which is the capital in positions at cost. The two are the "
                  "same figure for a long book; what is missing is the name and the "
                  "guarantee, not the arithmetic"),
    Clause("Snapshots", "net greeks",
           ("backtest/engine/loop.py",), needle="aggregate_greeks"),
    Clause("Snapshots", "turnover",
           ("backtest/research/metrics.py",),
           absent="not computed anywhere. It is one of the document's trade metrics and "
                  "one of its capacity measures, and for a strategy holding days to "
                  "weeks it is the figure that says how much of the return the costs "
                  "are eating -- which `cost_breakdown` answers directly, so this is a "
                  "second view of something already answered rather than a missing fact"),
    Clause("TradeBook", "MFE and MAE in premium and underlying terms",
           ("backtest/core/types.py",), needle="mfe_underlying"),
)

CLAUSES: tuple[Clause, ...] = ACCOUNTING
