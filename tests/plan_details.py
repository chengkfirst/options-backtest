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

#: Layer 4 · execution. The clauses, not the module names -- `FillModel` exists and
#: "size capped by a participation rate of volume" does not.
EXECUTION: tuple[Clause, ...] = (
    Clause("OrderGenerator", "target minus current, to orders",
           ("backtest/portfolio/target.py",), needle="def orders("),
    Clause("OrderGenerator", "sell before buy so cash suffices",
           ("backtest/portfolio/target.py",), needle="o.quantity > 0"),
    Clause("OrderGenerator", "round to whole contracts",
           ("backtest/portfolio/positioning.py",), needle="int(budget // premium)"),
    Clause("OrderGenerator", "split large orders TWAP- or VWAP-style",
           ("backtest/portfolio/target.py",),
           absent="orders go out whole. Splitting needs a participation cap to split "
                  "against, which needs daily contract volume -- the same block as "
                  "`4.2.participation`. On a daily bar there is also nowhere to put the "
                  "slices: TWAP over one price is one order"),
    Clause("FillModel", "buys at ask, sells at bid",
           ("backtest/execution/costs.py",), needle="def fill_price"),
    Clause("FillModel", "limit orders fill only when the price trades through",
           ("backtest/execution/costs.py",),
           absent="every order is a market order. A limit needs intraday quotes to know "
                  "whether the price traded through it -- on a daily bar the only "
                  "honest answer is the close, and filling a limit at the close is a "
                  "market order with extra steps"),
    Clause("FillModel", "size capped by a participation rate of volume",
           ("backtest/execution/costs.py",),
           absent="needs daily contract volume, which the synthetic chain does not "
                  "produce and no real feed is wired yet. It is `4.2.participation`"),
    Clause("FillModel", "size capped by displayed size",
           ("backtest/execution/costs.py",),
           absent="needs bid and ask **sizes**, which the schema does not ask for and no "
                  "source here supplies. Adding the column without a source would be a "
                  "cap that never binds"),
    Clause("FillModel", "unfilled remainder carries over or cancels",
           ("backtest/engine/loop.py",), needle="EventKind.EXIT_DEFERRED"),
    Clause("SlippageModel", "a multiple of the half-spread",
           ("backtest/execution/costs.py",), needle="adverse_fraction"),
    Clause("SlippageModel", "wider near expiry",
           ("backtest/execution/costs.py",), needle="near_expiry_days"),
    Clause("SlippageModel", "wider for deep out-of-the-money strikes",
           ("backtest/execution/costs.py",), needle="deep_otm_delta"),
    Clause("SlippageModel", "square-root impact for the underlying",
           ("backtest/execution/costs.py",),
           absent="needs ADV and a k calibrated from live fills, which is "
                  "`4.1.impact`. It is also about trades in the **underlying**, and "
                  "this system trades options"),
    Clause("CostModel", "per-contract commission",
           ("backtest/core/config.py",), needle="commission_per_contract"),
    Clause("CostModel", "exchange, ORF and OCC clearing fees; SEC and TAF on sells",
           ("backtest/core/config.py",),
           absent="one combined `regulatory_fee_per_contract` rather than the five "
                  "named. The **total** is what reaches a result and the default of "
                  "0.05 is the right order of magnitude, so this is a reporting "
                  "difference: a researcher cannot see which fee grew when a venue "
                  "changes one. Splitting it is a day's work and needs the current "
                  "schedules, which are a fact about the venues rather than about this "
                  "system"),
    Clause("CostModel", "borrow fees on shorts",
           ("backtest/core/config.py",),
           absent="no short-share position is possible: the ledger refuses a naked "
                  "short, and a cash-secured put needs no borrow. Delivered shares from "
                  "a put exercise are **short** for one session, which is the one case "
                  "this would bite -- and a one-session borrow on 100 shares is cents"),
    Clause("CostModel", "margin interest",
           ("backtest/accounting/ledger.py",),
           absent="no margin model, stated rather than missing: the ledger refuses the "
                  "positions that would need one. Interest **on cash** is booked, which "
                  "is the other half of the same line"),
    Clause("ExerciseModel", "OCC exercise by exception at $0.01",
           ("backtest/accounting/ledger.py",), needle="auto_exercise_threshold"),
    Clause("ExerciseModel", "early exercise of American calls before ex-dividend",
           ("backtest/data/options/american.py",), needle="def value("),
    Clause("ExerciseModel", "cash or margin needed to take delivery",
           # Both engines, because both settle an expiry and the note has to be in
           # whichever one a reader is looking at.
           ("backtest/engine/loop.py", "backtest/engine/vectorized.py"),
           needle="to take delivery"),
    Clause("ExerciseModel", "physical for equity and ETF, cash for index",
           ("backtest/core/types.py",), needle="class SettlementStyle"),
    Clause("MarketRule", "spread and open-interest rejection",
           ("backtest/execution/costs.py",), needle="max_relative_spread"),
    Clause("MarketRule", "T+1 settlement",
           ("backtest/accounting/ledger.py",), needle="def sellable"),
    Clause("MarketRule", "trading hours and session phases",
           ("backtest/core/clock.py",),
           absent="the engine is daily and every bar is a session, so there are no "
                  "hours to be outside of. It is `0.3.phases` and needs intraday data"),
    Clause("MarketRule", "trading halts",
           ("backtest/execution/costs.py",),
           absent="needs a halt feed. A quote-less day is marked untradable by the "
                  "cleaning stage, which catches a halt's **effect** without knowing it "
                  "was a halt"),
    Clause("MarketRule", "Limit Up-Limit Down bands",
           ("backtest/execution/costs.py",),
           absent="an intraday band on the underlying, and this engine sees one close "
                  "per day. Needs the same data as the session phases"),
    Clause("MarketRule", "option tick sizes",
           ("backtest/execution/costs.py",),
           absent="fills are not rounded to a tick. The error is at most half a tick "
                  "per fill -- $0.005 on a penny-quoted contract against a λ of a full "
                  "half-spread -- so it is an order of magnitude below the cost model's "
                  "own dominant term, and it would be wrong to round a *model* price to "
                  "a tick as though it were a quote"),
    Clause("MarketRule", "position and exercise limits",
           ("backtest/execution/costs.py",),
           absent="**the OCC limit per underlying is a published external number I do "
                  "not have.** Building it would mean inventing the figure, which is "
                  "worse than not having it: a limit set to a made-up number binds at "
                  "the wrong size and reads as enforced"),
    Clause("MarketRule", "short-sale borrow availability",
           ("backtest/execution/costs.py",),
           absent="needs a borrow feed, and no short-share position is possible here "
                  "anyway except the one session after a put exercise"),
    Clause("MarketRule", "the pattern day trader rule",
           ("backtest/execution/costs.py",),
           absent="it applies to margin accounts below $25,000 and this is a "
                  "non-margin account. Also unreachable: the rule counts round trips "
                  "within one session, and the enforced execution lag makes those "
                  "impossible -- the same coincidence that hides the T+1 rule"),
)

CLAUSES: tuple[Clause, ...] = ACCOUNTING + EXECUTION
