"""The plan's own component inventory, as data.

## Why this file exists, and what it is not

``tests/requirements_map.py`` transcribes the architecture document's
**"Conformance with the architecture document"** table: one row per document section,
82 requirements. That table is a **summary**.

The document also names its components directly, in tables of its own: the component
subtypes per layer (section 0.2), the Layer 1 submodules, and one table per layer from 2
to 7. **Seventy-five named components**, and they were never transcribed.

The consequence was a real accounting failure. Layer 1 had six requirements in the map --
the four hard rules, bad-data rejection, corporate actions -- and the document's own
submodule table names fourteen. So ``Clean``, ``Adjust``, ``SurfaceBuilder`` and
``Universe`` were never on any list, and "65 of 82 built" was answering a narrower
question than the one it was asked.

This file is the other inventory. It does not replace the map: a requirement is a
**behaviour** that can be falsified, and a component is a **thing that either exists or
does not**. Both questions are worth asking and neither answers the other.

## How presence is decided

By a needle in the source, checked by ``tests/test_plan_components.py``. A needle has to
be **specific**: the first version of this audit probed ``Clean`` with the word
``untradable``, which appears in the order-rejection path and has nothing to do with a
cleaning stage -- four of the eighteen findings were false positives from loose needles,
in an audit written **because** the previous audit was wrong. So each needle here names a
definition, not a word.

``absent`` carries the reason, in the same spirit as the requirements map: an absence with
a reason is an answer, and one without is a question nobody has looked at.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Component:
    """One component the document names, and where it is."""

    name: str
    #: Which of the document's tables it comes from.
    layer: str
    #: What the document says it does, in one line.
    does: str
    #: A string whose presence in the source proves the component exists. Specific
    #: enough that a word used elsewhere cannot satisfy it -- see the module docstring.
    needle: str = ""
    #: Why it does not exist. Empty means it does.
    absent: str = ""

    @property
    def built(self) -> bool:
        return not self.absent


COMPONENTS: tuple[Component, ...] = (
    # ── Layer 1 · Data ────────────────────────────────────────────────────
    Component("Loader", "data", "pull raw data from sources",
              needle="def fetch_ohlcv"),
    Component("Validator", "data", "find problems, never fix them silently",
              needle="def check_frame"),
    Component("Adjuster", "data",
              "store raw prices plus adjustment factors; signals use adjusted, fills "
              "use raw; OCC contract adjustments applied by date",
              absent="the **dual-price split is built** (`data/underlying.py`: adjusted "
                     "for signals, traded for strikes), which is the half that matters "
                     "for a single underlying with no corporate actions in range. What "
                     "is absent is the adjustment *stage*: factors stored beside raw "
                     "prices, and OCC contract adjustments applied by date. Both need "
                     "the corporate-action feed `1.corporate` waits on"),
    Component("FeatureCalculator", "data", "derived data, obeying available_at",
              needle="class SignalSet"),
    Component("RegimeClassifier", "data", "regime features, fitted only on data before t",
              needle="class RegimeSet"),
    Component("IVCalculator", "data", "solve IV from the mid with a matching model",
              needle="def implied_vol"),
    Component("GreeksCalculator", "data", "delta, gamma, vega, theta from one model",
              needle="def greeks("),
    Component("SurfaceBuilder", "data",
              "IV surface per underlying: a delta x tenor grid, smoothing, arbitrage "
              "checks, and derived features (ATM IV, skew, term structure, IV rank)",
              absent="**nothing fits a surface.** On the synthetic chain there is one IV "
                     "per session shared by every strike, so a surface over it would be "
                     "a constant and the smoothing would have nothing to smooth. It "
                     "becomes buildable the moment real option data is wired in, and it "
                     "is what the scenario grid needs for sticky-delta -- see "
                     "`portfolio/scenario_rule.py`, which can only do sticky strike "
                     "because a single vega cannot express a smile that moves"),
    Component("ContractMaster", "data",
              "static terms of every contract: listing and delisting dates, multiplier, "
              "exercise style, full adjustment history",
              absent="contract terms are carried on each chain row rather than held in a "
                     "master. For one underlying from one source that is the same "
                     "information; what it cannot answer is 'was this contract listed "
                     "on that date', which is `0.4.survivorship` and needs a "
                     "point-in-time universe"),
    Component("Quotes", "data", "option bid, ask and the underlying at one timestamp",
              needle="def series("),
    Component("RatesAndDividends", "data", "the rate curve and the dividend schedule",
              needle="def risk_free_rate"),
    Component("Clean", "data",
              "apply explicit rules; every rule configurable and logged; suspended or "
              "quote-less contracts marked untradable, never forward-filled",
              needle="class Cleaning"),
    Component("PITalignment", "data", "every record carries event_time and available_at",
              needle="available_at"),
    Component("Universe", "data",
              "the tradable set per day: underlyings by listing age and liquidity, the "
              "listed chain filtered by DTE, moneyness, spread and open interest",
              absent="the **option half is built** (`signals/contracts.py`: a feasible "
                     "region on DTE, moneyness, spread and open interest). The "
                     "underlying half is a list of one, so selecting among underlyings "
                     "has nothing to select from -- it arrives with multiple "
                     "underlyings, as `2.2.styles` and `6.4.pooling` do"),
    Component("Store", "data", "columnar files partitioned by date, with snapshot ids",
              needle="class ParquetCache"),
    Component("Access", "data", "the DataView for the layers above",
              needle="class DataView"),

    # ── Layer 2 · Signals ─────────────────────────────────────────────────
    Component("UniverseSelector", "signals", "pick candidate underlyings",
              absent="one underlying at a time, so there is nothing to pick between"),
    Component("Filter", "signals", "drop what should not trade now",
              needle="skipped_filters"),
    Component("SetupRule", "signals", "slot 1: mark a candidate ready or not",
              needle="candidate.arm"),
    Component("EntryRule", "signals", "slot 2: fire the entry while the setup is valid",
              needle="class EntryRule"),
    Component("ContractSelector", "signals", "slot 2: turn the entry into contracts",
              needle="def select_contract"),
    Component("StructureBuilder", "signals", "slot 2: define the legs",
              absent="single-leg only. Multi-leg needs a margin model for any short leg "
                     "and a per-leg P&L split, which is `5.2.legs`"),
    Component("TakeProfitRule", "signals", "slot 3: manage gains",
              needle='slot="take_profit"'),
    Component("StopLossRule", "signals", "slot 4: manage losses",
              needle='slot="stop_loss"'),
    Component("HeadwindRule", "signals", "slot 5: respond to adverse factors",
              needle='slot="headwind"'),
    Component("PositionHedgeRule", "signals", "slot 7: protective legs on one trade",
              absent="needs multi-leg and, for an index-future or short-share hedge, "
                     "instruments Layers 4 and 5 do not support. The document names "
                     "this limit itself"),
    Component("RollRule", "signals", "replace legs with a later expiry or new strike",
              absent="one logical trade would have to span several contracts, which "
                     "`TradeRecord` does not express -- it is keyed to one contract"),
    Component("SignalCombiner", "signals", "merge actions issued in the same period",
              needle="class TargetPortfolio"),

    # ── Layer 3 · Portfolio and risk ──────────────────────────────────────
    Component("RebalanceRule", "portfolio", "decide whether to act this period",
              absent="the engine acts every session, which is `check_frequency` fixed at "
                     "daily. A rebalance rule is the setting made configurable, and the "
                     "register lists it as an engineering choice that must equal live "
                     "trading"),
    Component("Sizer", "portfolio", "slot 6: turn OPEN and ADD into quantities",
              needle="class Sizing"),
    Component("Optimizer", "portfolio", "best weights under constraints (optional)",
              absent="the document marks it optional. With one underlying and one leg "
                     "there are no weights to optimise"),
    Component("RiskModel", "portfolio",
              "risk estimates: greeks by underlying and expiry bucket, plus "
              "beta-weighted delta and vega to a common index",
              absent="**greeks aggregation is built** (`portfolio/scenarios.py`). "
                     "Beta-weighting to a common index is not, and it needs several "
                     "underlyings and their betas -- the document's reason for wanting "
                     "it is that SPY, QQQ and IWM behave largely as one position, which "
                     "is a statement about holding more than one"),
    Component("Constraint", "portfolio", "hard limits on premium, greeks, cash",
              needle="class ExposureLimits"),
    Component("RiskRule", "portfolio", "slot 5 book-wide: monitor and intervene",
              needle="class RiskDecision"),
    Component("ScenarioRule", "portfolio", "stress the book; block trades over a limit",
              needle="class ScenarioLimit"),
    Component("PortfolioHedgeRule", "portfolio", "slot 7 book-wide: protect the book",
              absent="same instruments gap as the per-trade hedge, plus hedge legs under "
                     "a book-level trade_id, which `TradeRecord` does not express"),

    # ── Layer 4 · Execution ───────────────────────────────────────────────
    Component("OrderGenerator", "execution", "target minus current, sells before buys",
              needle="def orders("),
    Component("OrderType", "execution",
              "market, limit, stop, auction; DAY or GTC",
              absent="every order is a market order filled at bid or ask on the "
                     "execution day. A limit order needs intraday quotes to know whether "
                     "the price traded through it, which is the data block behind "
                     "`0.3.phases`"),
    Component("MarketRule", "execution", "reject impossible orders",
              needle="def check_tradable"),
    Component("FillModel", "execution", "fill price and quantity",
              needle="def fill_price"),
    Component("SlippageModel", "execution", "price beyond the quote",
              needle="slippage_per_contract"),
    Component("CostModel", "execution", "commission and the regulatory fees",
              needle="def order_fees"),
    Component("ExerciseModel", "execution", "expiry, exercise by exception, early exercise",
              needle="def settle_expiry"),
    Component("SimBroker", "execution",
              "one interface -- submit, cancel, on_snapshot -- that the live broker also "
              "implements",
              absent="the engine fills directly. This is the same gap as `0.1.only`: "
                     "until the loop acts through handlers there is no seam to put a "
                     "broker behind, and `7.2.live` is defined as swapping what sits "
                     "there"),

    # ── Layer 5 · Engine and accounting ───────────────────────────────────
    Component("EventLoop", "engine", "dispatch by (timestamp, phase, sequence)",
              needle="class EventBus"),
    Component("Scheduler", "engine",
              "emit timed events: start of day, snapshot, rebalance, expiry",
              absent="the loop walks the calendar's sessions directly. A scheduler is "
                     "what makes `check_frequency` and a rebalance calendar "
                     "configurable, and both are fixed at daily"),
    Component("Runner", "engine", "one run, with state snapshots for resume",
              needle="class Checkpoint"),
    Component("BatchRunner", "engine", "run many configs",
              needle="def run_batch"),
    Component("VectorizedEngine", "engine", "fast screening",
              needle="class TradeTableEngine"),
    Component("Ledger", "accounting", "record every money movement",
              needle="class Ledger"),
    Component("Positions", "accounting", "what is held, by contract and trade_id",
              needle="class Position"),
    Component("Cash", "accounting", "available, frozen, borrowed",
              needle="def cash"),
    Component("Valuation", "accounting", "end-of-day mark, at bid and at mid",
              needle="equity_liq"),
    Component("CorporateActions", "accounting", "dividends to cash, splits adjust",
              absent="**I had this built and every one of its detail clauses is "
                     "absent**, which the cross-check between the two inventories "
                     "caught: a component that exists in name only. The needle was "
                     "`def dividends_between`, which is `data/market.py` reading a "
                     "dividend **for pricing** -- it goes into Black-Scholes, into the "
                     "tree, into the early-exercise test, and never into cash.\n\n"
                     "So the accounting module named here does not exist: no dividend "
                     "is booked, no split adjusts a held position. For an option holder "
                     "the first is correct; it stops being correct for the shares "
                     "physical delivery now produces, and the split side needs the feed "
                     "`1.corporate` waits on"),
    Component("Expiry", "accounting", "settle expiring contracts",
              needle="def settle_expiry"),
    Component("PnL", "accounting", "realised, unrealised, fees, by leg and component",
              needle="def cost_breakdown"),
    Component("GreeksAttribution", "accounting", "split daily P&L into the greeks",
              needle="def attribute("),
    Component("Snapshots", "accounting", "daily equity, premium at risk, net greeks",
              needle="class Snapshot"),
    Component("TradeBook", "accounting", "one record per trade_id with every action",
              needle="def actions_frame"),

    # ── Layer 6 · Research ────────────────────────────────────────────────
    Component("ExperimentTracker", "research", "every run findable; count the trials",
              needle="def count_trials"),
    Component("ParamSweep", "research", "grid, random or Bayesian search",
              needle="def sweep("),
    Component("Validation", "research", "walk-forward, purged k-fold, bootstrap",
              needle="def walk_forward"),
    Component("Baselines", "research", "separate skill from structure",
              needle="def random_entry"),
    Component("Metrics", "research", "return, risk, trade and option metrics",
              needle="def trade_stats"),
    Component("Attribution", "research", "where returns come from",
              needle="def attribute_by"),
    Component("Report", "research",
              "a visual report: equity vs benchmark, drawdowns, a monthly heatmap, "
              "rolling Sharpe, net greeks, HTML or notebook export",
              absent="**the web interface is the only view.** There is no HTML or "
                     "notebook export, so a result cannot be handed to somebody who "
                     "does not run the service. This is `6.1.report`"),
    Component("Compare", "research", "runs side by side",
              needle="def compare_forms"),

    # ── Layer 7 · Interfaces ──────────────────────────────────────────────
    Component("PythonSDK", "interfaces", "run, sweep, report from a notebook",
              needle="def run("),
    Component("CLI", "interfaces", "run, sweep, report, data update from a shell",
              absent="**absent, and I had it down as built.** The guard caught it: the "
                     "needle was `argparse`, which matches `tools/check.py` and "
                     "`tools/baseline.py` -- the guards' own entry points, not a "
                     "researcher's. `backtest/api/` holds `sdk.py` and "
                     "`config_file.py` and no `cli.py`. A run is started from Python or "
                     "from the web interface; there is no shell entry point"),
    Component("RESTAPI", "interfaces", "programmatic access",
              needle="def do_GET"),
    Component("JobQueue", "interfaces", "run backtests asynchronously",
              absent="runs are synchronous. This is `7.1.api`'s other half and it is "
                     "deployment rather than research: a queue changes who can start a "
                     "run and when, not what a run means"),
    Component("ResultStore", "interfaces", "keep results",
              needle="def run_records"),
    Component("WebUI", "interfaces", "forms from parameter schemas, run list, reports",
              needle="def do_GET"),
    Component("LiveAdapter", "interfaces", "real-time feeds and a live broker",
              absent="needs a feed and a broker, and `SimBroker` before either -- there "
                     "is no seam to put them behind"),
    Component("Auth", "interfaces", "users, roles, an audit log",
              absent="one person on one machine. The document lists it for team use"),
)

#: Lookup by name.
BY_NAME = {c.name: c for c in COMPONENTS}
