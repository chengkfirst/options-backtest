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

#: Layer 1 · data, which the plan calls the layer where "most of a backtest's credibility
#: is decided". Its pipeline is ten stages and its Details column is the longest in the
#: document, so this is the layer where a name-level count misleads most.
#:
#: **Half of what is absent here is one missing feed.** Corporate actions, index
#: constituents, halts, earnings dates, OCC contract adjustments and listing dates are six
#: separate clauses and one purchase; they are listed separately anyway, because "we have
#: no corporate-action feed" is a different statement from "six details are missing" and
#: only the second one says what a result cannot be trusted for.
DATA: tuple[Clause, ...] = (
    # --- Loader (the document's Ingest row) ---
    Clause("Loader", "underlying bars",
           ("backtest/data/ingest.py",), needle="def fetch_ohlcv"),
    Clause("Loader", "option quotes",
           ("backtest/data/options/chain.py",), needle="def snapshot"),
    Clause("Loader", "contract terms",
           ("backtest/core/types.py",), needle="CHAIN_COLUMNS"),
    Clause("Loader", "rates",
           ("backtest/data/market.py",), needle="def risk_free_rate"),
    Clause("Loader", "dividends",
           ("backtest/data/ingest.py",), needle="def fetch_dividends"),
    Clause("Loader", "calendars",
           ("backtest/data/market.py",), needle="def sessions"),
    Clause("Loader", "retries",
           ("backtest/data/ingest.py",), needle="def _with_retry"),
    Clause("Loader", "corporate actions",
           ("backtest/data/ingest.py",),
           absent="**no corporate-action feed.** Splits and special dividends reach the "
                  "adjusted price series through the vendor's own adjustment, so a "
                  "signal is right; what cannot be answered is *when* the action "
                  "happened, which is what an option strike adjustment needs"),
    Clause("Loader", "index constituents",
           ("backtest/data/ingest.py",),
           absent="needs a constituents feed with join and leave dates. Without it the "
                  "universe cannot be 'the index as it stood then', which is the "
                  "survivorship question `0.4.survivorship` records"),
    Clause("Loader", "trading halts",
           ("backtest/data/ingest.py",),
           absent="needs a halt feed. The cleaning stage marks a quote-less day "
                  "untradable, which catches a halt's **effect** without knowing it was "
                  "a halt -- and cannot catch a halt that still had quotes"),
    Clause("Loader", "earnings and event calendars",
           ("backtest/data/ingest.py",),
           absent="needs an events feed. Its absence is not neutral for options: the "
                  "IV collapse after an earnings print is one of the largest single-day "
                  "moves an option position sees, and nothing here can avoid or seek it"),
    Clause("Loader", "incremental updates",
           ("backtest/data/store.py",),
           absent="every fetch is a whole range; the store caches by fingerprint so a "
                  "repeat is free, but extending a series by one session re-downloads "
                  "it. That is a cost at the vendor, not a correctness problem -- it "
                  "becomes one only when a range is too large to fetch at all"),
    Clause("Loader", "raw data is append-only",
           ("backtest/data/store.py",),
           absent="the cache is keyed by a fingerprint of the request, so a new request "
                  "writes a new file and nothing is overwritten in practice. **Nothing "
                  "verifies it**, which is the difference between a property and a "
                  "habit -- `ledger.verify_append_only` is what this would look like"),

    # --- Quotes ---
    Clause("Quotes", "option bid and ask at a fixed snapshot",
           ("backtest/core/types.py",), needle='"bid",'),
    Clause("Quotes", "volume and open interest",
           ("backtest/core/types.py",), needle='"open_interest",'),
    Clause("Quotes", "the underlying quote at the same timestamp",
           ("backtest/core/types.py",), needle='"underlying_price",'),
    Clause("Quotes", "bid and ask sizes",
           ("backtest/core/types.py",),
           absent="there is no size column. **This is the one absence here that changes "
                  "a number**: without depth, the execution layer caps a fill by volume "
                  "and open interest instead of by what was actually on the bid, so a "
                  "size the book could not have absorbed fills at the quote"),
    Clause("Quotes", "settlement price",
           ("backtest/core/types.py",),
           absent="not carried. It matters for a margin account's daily mark and for "
                  "expiry settlement on cash-settled index options; this account is "
                  "non-margin and the underlying is an ETF, so the mark uses the mid "
                  "and expiry uses the underlying's close"),

    # --- RatesAndDividends ---
    Clause("RatesAndDividends", "discrete dividend schedule with ex-dates",
           ("backtest/data/market.py",), needle="def dividends_between"),
    Clause("RatesAndDividends", "the dividend input the American price needs",
           ("backtest/data/market.py",), needle="def dividend_yield"),
    Clause("RatesAndDividends", "a Treasury or SOFR curve by tenor",
           ("backtest/data/market.py",),
           absent="one rate per session, not a curve. Every option here is priced at "
                  "the same short rate regardless of its expiry; over a one-year tenor "
                  "with a sloped curve that is a real pricing error, and it is "
                  "systematic rather than noise"),
    Clause("RatesAndDividends", "implied forwards from put-call parity",
           ("backtest/data/options/pricing.py",),
           absent="nothing solves parity for the forward. The forward is instead built "
                  "from the spot, the rate and the dividend yield -- which is the same "
                  "number *if those three are right*, and the point of the parity "
                  "solve is that it does not depend on them being right"),

    # --- Validator ---
    Clause("Validator", "a quality report per update",
           ("backtest/data/pipeline.py",), needle="class PipelineReport"),
    Clause("Validator", "duplicate timestamps",
           ("backtest/data/options/chain.py",), needle="duplicate primary keys"),
    Clause("Validator", "OHLC contradictions",
           ("backtest/data/underlying.py",), needle="def validate_bars"),
    Clause("Validator", "gaps",
           ("backtest/data/pipeline.py",), needle="def _check_calendar"),
    Clause("Validator", "crossed or locked markets",
           ("backtest/data/options/clean.py",), needle="def crossed_quote"),
    Clause("Validator", "very wide spreads",
           ("backtest/data/options/clean.py",), needle="def wide_spread"),
    Clause("Validator", "prices below intrinsic value",
           ("backtest/data/options/clean.py",), needle="def below_intrinsic"),
    Clause("Validator", "stale quotes",
           ("backtest/data/options/adapters.py",), needle="stale"),
    Clause("Validator", "zero volume",
           ("backtest/data/options/clean.py",), needle="def no_quote"),
    # Built as a **measurement plus one factual claim**, not as a threshold: the
    # largest session move is always reported with its date, and only a ratio near a
    # common split (1/2, 1/3, 2/3, 1/4) fails -- because "this is a 2:1 split the
    # vendor did not apply" is a different statement from "this move is large", and a
    # size threshold would also delete March 2020.
    Clause("Validator", "price jumps",
           ("backtest/data/pipeline.py",), needle="largest_move_pct"),

    # --- Clean ---
    Clause("Clean", "every rule configurable",
           ("backtest/data/options/clean.py",), needle="class Cleaning"),
    Clause("Clean", "every rule logged",
           ("backtest/data/options/clean.py",), needle="class CleaningReport"),
    Clause("Clean", "quote-less contracts are marked untradable",
           ("backtest/data/options/clean.py",), needle="mark_untradable"),
    Clause("Clean", "never forward-filled",
           ("backtest/data/options/clean.py",), needle="Forward-filling"),

    # --- PITalignment ---
    Clause("PITalignment", "every record carries available_at",
           ("backtest/core/types.py",), needle='"available_at",'),
    Clause("PITalignment", "as-of queries",
           ("backtest/data/options/adapters.py",), needle="known_by"),
    Clause("PITalignment", "an option and the underlying used with it share a timestamp",
           ("backtest/data/pipeline.py",), needle="def _check_dual_price"),
    Clause("PITalignment", "every record carries event_time",
           ("backtest/core/types.py",),
           absent="a row carries the session it describes and the date it could be "
                  "known, which on a daily engine is the whole of the distinction. "
                  "A separate event_time is what an intraday or a revised record needs, "
                  "and both are `0.3.phases`"),
    Clause("PITalignment", "revisions versioned",
           ("backtest/data/store.py",),
           absent="a second delivery of the same range replaces the first in the cache. "
                  "`available_at` is the column a revision would be recorded in, so the "
                  "shape is ready and the storage is not; nothing in this project "
                  "revises, since the sources deliver a session once"),
    Clause("PITalignment", "financials effective on announcement date",
           ("backtest/data/ingest.py",),
           absent="no fundamentals are loaded at all, so there is nothing to date. This "
                  "is a whole-asset-class absence rather than a missing field: the "
                  "signals here are price, volatility and the chain"),

    # --- IVCalculator and GreeksCalculator ---
    Clause("IVCalculator", "solve IV from the mid price",
           ("backtest/data/options/adapters.py",), needle="implied_vol"),
    Clause("IVCalculator", "using the synchronized underlying, rate and dividend",
           ("backtest/data/options/adapters.py",), needle="self.market.dividend_yield"),
    Clause("IVCalculator", "a European or American model matching the contract style",
           ("backtest/data/options/adapters.py",), needle="iv_model"),
    Clause("IVCalculator", "one time convention everywhere",
           ("backtest/core/config.py",), needle="pricing_basis"),
    Clause("IVCalculator", "flag failures instead of guessing",
           ("backtest/data/options/adapters.py",), needle="iv_unsolved"),
    Clause("GreeksCalculator", "compute delta, gamma, vega and theta",
           ("backtest/data/options/pricing.py",), needle="def greeks"),
    Clause("GreeksCalculator", "say when a greek was derived rather than supplied",
           ("backtest/core/types.py",), needle='"greeks_model_derived",'),

    # --- Universe ---
    Clause("Universe", "the listed chain at t filtered by DTE, moneyness, spread "
                       "and open interest",
           ("backtest/signals/contracts.py",), needle="class SelectionRule"),
    Clause("Universe", "expired and delisted contracts are kept in history",
           ("backtest/data/options/chain.py",), needle="already_expired"),
    Clause("Universe", "underlyings by listing age, liquidity and exclusions",
           ("backtest/signals/contracts.py",),
           absent="the underlying is a list of one, so there is nothing to select "
                  "among. It is the same gap as the constituents feed seen from the "
                  "other side, and it arrives with multiple underlyings"),

    # --- Adjuster ---
    Clause("Adjuster", "signals use adjusted prices, fills and costs use raw prices",
           ("backtest/data/underlying.py",), needle="def adjusted"),
    Clause("Adjuster", "store raw prices plus adjustment factors",
           ("backtest/data/underlying.py",),
           absent="both price series are stored, which carries the same information for "
                  "a signal but not the factor itself -- and the factor is what an "
                  "option strike adjustment is applied with. Needs the "
                  "corporate-action feed"),
    Clause("Adjuster", "OCC contract adjustments applied by date",
           ("backtest/data/options/adapters.py",),
           absent="a split rewrites every strike and the multiplier on an existing "
                  "contract, and nothing here applies that. **The silent version of "
                  "this is the dangerous one**: an unadjusted strike through a split "
                  "makes a position look deep out of the money and the backtest closes "
                  "it for nothing"),
    Clause("Adjuster", "non-standard deliverables flagged and excluded by default",
           ("backtest/data/options/clean.py",),
           absent="a merger or spin-off leaves a contract deliverable that is no longer "
                  "100 shares of one name, and the pricing model here cannot price it. "
                  "Excluding them needs the adjustment history to know which they are"),

    # --- SurfaceBuilder ---
    Clause("SurfaceBuilder", "an IV rank feature",
           ("backtest/signals/registry.py",), needle="iv_rank_252"),
    Clause("SurfaceBuilder", "a delta x tenor grid per underlying",
           ("backtest/data/options/chain.py",),
           absent="**nothing fits a surface.** On the synthetic chain there is one IV "
                  "per session shared by every strike, so the grid would be a constant "
                  "surface and every derived feature a constant. It becomes buildable "
                  "the moment a real chain is wired in"),
    Clause("SurfaceBuilder", "smoothing and arbitrage checks",
           ("backtest/data/options/chain.py",),
           absent="there is no surface to smooth, and the arbitrage checks a surface "
                  "needs (monotone in strike, convex, no calendar arbitrage) have "
                  "nothing to check on a flat one. The pieces that do exist are "
                  "`below_intrinsic` and `crossed_quote`, which are the same idea "
                  "applied one row at a time"),
    Clause("SurfaceBuilder", "ATM IV, skew and term structure features",
           ("backtest/signals/registry.py",),
           absent="all three read the surface across strikes or expiries, so they wait "
                  "on it. The IV signals that exist read a **proxy index** (VIX for "
                  "SPY, VXN for QQQ) rather than the chain's own surface -- real data, "
                  "and the reason IV rank is built while skew is not"),

    # --- FeatureCalculator ---
    Clause("FeatureCalculator", "features obey available_at like raw data",
           ("backtest/core/dataview.py",), needle="class DataView"),
    Clause("FeatureCalculator", "anything fitted is fitted only on data before t",
           ("backtest/signals/registry.py",), needle="rank_in_window"),
    Clause("FeatureCalculator", "a cache keyed by parameter hash",
           ("backtest/data/store.py",),
           absent="the data store hashes a request, and signals are recomputed each "
                  "run rather than cached. On this size of problem the recompute is "
                  "seconds; it becomes a real cost when a sweep fits the same feature "
                  "a thousand times"),
    Clause("FeatureCalculator", "a dependency graph with incremental updates",
           ("backtest/signals/registry.py",),
           absent="each signal is an independent function of the bars, so there is no "
                  "graph to walk -- a flat registry *is* the right shape for a flat "
                  "dependency structure. It stops being right when one feature takes "
                  "another as input"),
)


#: Layer 2 · signals, whose Details column is mostly the words **"to be defined"**.
#:
#: That is the document's own state, not a gap in the transcription: the seven
#: components' forms are the thing the platform exists to help settle, so the clauses
#: here are the ones that *are* specified -- the action envelope, the lifecycle, the two
#: interface styles, and the boundary between a per-trade decision and a book-wide one.
SIGNALS: tuple[Clause, ...] = (
    Clause("SignalCombiner", "a shared action vocabulary",
           ("backtest/core/actions.py",), needle="HEDGE_REMOVE"),
    Clause("SignalCombiner", "an action carries the trade it belongs to",
           ("backtest/core/actions.py",), needle="trade_id: int"),
    Clause("SignalCombiner", "an action carries the component and variant "
                             "that produced it",
           ("backtest/core/actions.py",), needle="variant: str"),
    Clause("SignalCombiner", "an action carries its reason and the values used",
           ("backtest/core/actions.py",), needle="values: Mapping"),
    Clause("SignalCombiner", "the share of the trade affected, for REDUCE",
           ("backtest/core/actions.py",), needle="fraction: float | None"),
    # **Built, after its recorded reason turned out to be wrong.** It said there was
    # "nowhere to record a stop that moves during the trade"; the stateful component
    # protocol is exactly that place and `stop_loss/trailing` had been using it all
    # along. What was actually missing is a stop move that is *logged and attributable*
    # rather than private to one component -- "up 30%, stop to breakeven" could be
    # written, and a reader could not see that it had happened.
    Clause("SignalCombiner", "SET_STOP moves a stop, logged with the slot that asked",
           ("backtest/engine/loop.py",), needle="def _stop_setter"),
    Clause("StopLossRule", "a stop that moves during the trade",
           ("backtest/signals/stop_loss/__init__.py",), needle="def _ratchet"),
    Clause("SignalCombiner", "HOLD is an explicit no-change, logged with its reason",
           ("backtest/core/actions.py",), needle="HOLD"),
    Clause("SignalCombiner", "an optional score passed on to Positioning",
           ("backtest/core/actions.py",),
           absent="no strength field. Nothing downstream would read it: every sizing "
                  "method here takes the account and the stop, not a score, so adding "
                  "the field without a method that consumes it would be a value that "
                  "looks acted on and is not"),
    Clause("SetupRule", "each component runs only in its lifecycle state",
           ("backtest/signals/lifecycle.py",), needle="class Lifecycle"),
    Clause("StopLossRule", "exits may read entry premium, holding days, current "
                           "greeks, MFE and MAE so far",
           ("backtest/engine/loop.py",), needle='"mfe_so_far"'),
    Clause("StopLossRule", "exits never place orders",
           ("backtest/core/trade_context.py",), needle="class SlotWriter"),
    Clause("EntryRule", "a time-series rule on the underlying, then a contract "
                        "selector that queries the chain",
           ("backtest/signals/contracts.py",), needle="def select_contract"),
    Clause("Filter", "data-quality gates",
           ("backtest/data/options/chain.py",), needle="def validate_chain"),
    Clause("EntryRule", "the cross-sectional style scores all candidates each period",
           ("backtest/core/dataview.py",),
           absent="`cross_section` raises rather than returning: it needs more than one "
                  "underlying per run. Both styles are meant to implement one Component "
                  "protocol, and only the time-series half can be exercised -- so the "
                  "protocol's generality is **unproven**, not merely unused"),

    # --- Store and Access, which the document lists in this layer's table ---
    Clause("Store", "columnar files",
           ("backtest/data/store.py",), needle="parquet"),
    Clause("Access", "a DataView for the layers above",
           ("backtest/core/dataview.py",), needle="class DataView"),
    Clause("Access", "history, chain and contract",
           ("backtest/core/dataview.py",), needle="def contract"),
    Clause("Access", "a hot-data cache",
           ("backtest/data/store.py",), needle="def get_or_load"),
    Clause("Access", "a run binds to one data snapshot id",
           ("backtest/api/sdk.py",), needle="data_version"),
    Clause("Store", "partitioned by date and underlying",
           ("backtest/data/store.py",),
           absent="the cache is one file per request fingerprint, so a second range of "
                  "the same symbol is a second whole file rather than a partition read. "
                  "It is a shape that stops scaling at the point where one symbol's "
                  "history no longer fits in memory"),
    Clause("Store", "a metadata database for datasets, versions and snapshot ids",
           ("backtest/data/store.py",),
           absent="there is no catalogue. The run fingerprint records *which* data a "
                  "result came from, which is the half that keeps a result honest; what "
                  "is missing is the half that lets you ask what data exists"),
    Clause("Access", "cross_section, panel and surface",
           ("backtest/core/dataview.py",),
           absent="all three raise `NotImplementedError` with the reason, which is the "
                  "right failure -- each needs something absent (several underlyings, "
                  "the feature graph, the surface). Returning an empty frame instead "
                  "would make a strategy that reads one look like it found nothing"),
)


#: Layer 3 · portfolio and risk. The layer whose order of work the document fixes:
#: rebalance check -> sizing -> optimisation -> constraints -> portfolio risk -> target.
PORTFOLIO: tuple[Clause, ...] = (
    Clause("Sizer", "turn OPEN and ADD actions into contract quantities",
           ("backtest/portfolio/positioning.py",), needle="def contracts_for"),
    Clause("Constraint", "total premium outstanding",
           ("backtest/portfolio/constraints.py",), needle="max_premium_pct"),
    Clause("Constraint", "premium per expiry",
           ("backtest/portfolio/constraints.py",),
           needle="max_premium_per_expiry_pct"),
    Clause("Constraint", "minimum cash",
           ("backtest/portfolio/constraints.py",), needle="min_cash_pct"),
    Clause("Constraint", "greeks limits",
           ("backtest/portfolio/constraints.py",), needle="max_vega_pct"),
    Clause("Constraint", "contract position limits",
           ("backtest/portfolio/constraints.py",), needle="max_positions"),
    Clause("Constraint", "premium at risk is a core budget measure",
           ("backtest/accounting/ledger.py",), needle="premium_at_risk"),
    Clause("Constraint", "premium per underlying",
           ("backtest/portfolio/constraints.py",),
           absent="a run holds one underlying, so the per-underlying limit and the total "
                  "are the same number and the grouping cannot be tested. The budget "
                  "groups by expiry, which is the axis that **does** have more than one "
                  "value here"),
    Clause("RiskRule", "the four auditable risk actions",
           ("backtest/portfolio/risk_actions.py",), needle="NO_NEW_POSITIONS"),
    Clause("RiskRule", "a drawdown breaker",
           ("backtest/portfolio/risk_rules/__init__.py",), needle="def _drawdown"),
    Clause("RiskRule", "a daily loss limit",
           ("backtest/portfolio/risk_rules/__init__.py",), needle="def _daily_loss"),
    Clause("ScenarioRule", "revalue on a grid of spot moves, IV moves and time",
           ("backtest/portfolio/scenarios.py",), needle="def stress_grid"),
    Clause("ScenarioRule", "an explicit sticky-strike or sticky-delta assumption",
           ("backtest/portfolio/scenario_rule.py",), needle="sticky"),
    Clause("ScenarioRule", "block trades that breach a scenario loss limit",
           ("backtest/portfolio/scenario_rule.py",), needle="class ScenarioLimit"),
    Clause("RiskModel", "greeks aggregation",
           ("backtest/portfolio/scenarios.py",), needle="def aggregate_greeks"),
    Clause("RiskModel", "greeks by underlying and expiry bucket",
           ("backtest/portfolio/scenarios.py",),
           absent="the aggregate is one book-wide total. Bucketing by expiry is the one "
                  "that would say something here -- a book whose vega sits entirely on "
                  "one expiry is a different book from one spreading it, and the total "
                  "cannot tell them apart. The premium budget now groups by expiry; the "
                  "greeks do not"),
    Clause("RiskModel", "beta-weighted delta and vega to a common index",
           ("backtest/portfolio/scenarios.py",),
           absent="needs several underlyings and their betas. The document's reason for "
                  "wanting it -- that SPY, QQQ and IWM behave largely as one position -- "
                  "is a statement about holding more than one"),
    Clause("RiskModel", "covariance and factor models for the underlyings",
           ("backtest/portfolio/scenarios.py",),
           absent="a covariance matrix of one asset is its variance, and a factor model "
                  "of one asset is that asset. Both become real with a second "
                  "underlying and neither can be written against one"),
    Clause("TargetPortfolio", "the target quantity for each contract",
           ("backtest/portfolio/target.py",), needle="_targets"),
    Clause("TargetPortfolio", "the reason for every adjustment",
           ("backtest/portfolio/target.py",), needle="_reasons"),
    Clause("TargetPortfolio", "execution turns the gap between target and current "
                              "into orders",
           ("backtest/portfolio/target.py",), needle="class Order"),
    Clause("RebalanceRule", "decide whether to act this period",
           ("backtest/portfolio/target.py",),
           absent="every session is a decision point, which is the `calendar` form with "
                  "a period of one day -- the degenerate case rather than the rule. "
                  "A drift threshold and a signal-driven trigger need a target weight "
                  "to drift **from**, and this book has no weights"),
    Clause("Optimizer", "best weights under constraints",
           ("backtest/portfolio/positioning.py",),
           absent="the document marks it optional, and it is the one component here that "
                  "would be wrong to build early: mean-variance on option payoffs needs "
                  "a return distribution for a position whose payoff is not symmetric, "
                  "and the obvious implementation -- treat the premium as the "
                  "asset -- would be confidently wrong"),
    Clause("PortfolioHedgeRule", "hedge legs tagged with their own trade_id",
           ("backtest/core/types.py",),
           absent="`TradeRecord` has one trade_id per trade and a `tag`, so a hedge leg "
                  "can be marked as one but not given a trade of its own. Book-level "
                  "hedging also needs an instrument this chain does not carry"),
)


#: Layer 5 · the engine half. (Accounting's clauses are in ``ACCOUNTING`` above.)
ENGINE: tuple[Clause, ...] = (
    Clause("EventLoop", "a queue ordered by timestamp and processing phase so order "
                        "is deterministic",
           ("backtest/core/events.py",), needle="PHASE: Mapping"),
    Clause("EventLoop", "the invariant checks run at every end of day",
           ("backtest/engine/loop.py",), needle="check_invariants("),
    Clause("Runner", "load config, bind the data snapshot, build components, run, "
                     "save results",
           ("backtest/api/sdk.py",), needle="def run("),
    Clause("Runner", "state snapshots for resume",
           ("backtest/engine/runner.py",), needle="class Checkpoint"),
    Clause("BatchRunner", "run many configurations",
           ("backtest/engine/runner.py",), needle="def run_batch"),
    Clause("VectorizedEngine", "matrix operations with simplified fills, for "
                               "underlying-level screening",
           ("backtest/engine/vectorized.py",), needle="def run"),
    Clause("EventLoop", "a sequence number as the third sort key",
           ("backtest/core/events.py",),
           absent="events are ordered by timestamp and phase, and within a phase by "
                  "publication order -- which is a sequence number in effect, kept by "
                  "the list rather than written on the event. It becomes a real gap "
                  "when a persisted stream has to be re-sorted after loading, since the "
                  "order would then depend on the storage"),
    Clause("Scheduler", "start of day, snapshot time, rebalance days, end of day, "
                        "expiry days, from the calendars",
           ("backtest/core/clock.py",),
           absent="the loop walks sessions and does each phase inline, so the schedule "
                  "is the code's shape rather than a stream of timed events. Equivalent "
                  "on a daily engine; a separate scheduler is what intraday needs, and "
                  "it is `0.3.phases`"),
    Clause("BatchRunner", "in parallel, with an independent seed per task",
           ("backtest/engine/runner.py",),
           absent="the batch runs in sequence. **The seed half matters more than the "
                  "parallel half**: nothing here seeds per task, so two synthetic runs "
                  "in one batch share the generator's seed and a sweep over them is "
                  "less independent than its trial count claims"),
)


#: Layer 6 · research and reports.
RESEARCH: tuple[Clause, ...] = (
    Clause("ExperimentTracker", "run_id, config, commit, data snapshot, summary",
           ("backtest/api/sdk.py",), needle="def manifest"),
    Clause("ExperimentTracker", "counts how many parameter sets were tried for one idea",
           ("backtest/research/tracker.py",), needle="def count_trials"),
    Clause("ParamSweep", "explore parameters in bulk",
           ("backtest/research/sweep.py",), needle="def sweep"),
    Clause("ParamSweep", "whether results sit on a stable plateau or an isolated spike",
           ("backtest/research/sweep.py",), needle="def plateau"),
    # The document's IS/OOS split, implemented as the locked hold-out: the same
    # mechanism, with the "used once, at the end" rule enforced rather than advised.
    Clause("Validation", "in-sample and out-of-sample split",
           ("backtest/research/holdout.py",), needle="class HoldOut"),
    Clause("Validation", "walk-forward, rolling or expanding",
           ("backtest/research/validation/splits.py",), needle="walk_forward"),
    Clause("Validation", "purged k-fold",
           ("backtest/research/validation/splits.py",), needle="purged"),
    Clause("Validation", "bootstrap of trades",
           ("backtest/research/validation/intervals.py",), needle="def bootstrap"),
    Clause("Validation", "block bootstrap by period",
           ("backtest/research/validation/intervals.py",), needle="block"),
    Clause("Validation", "one-bar signal delay",
           ("backtest/research/delay.py",), needle="def "),
    Clause("Validation", "doubled costs and lambda = 1.5 fills",
           ("backtest/research/realism.py",), needle="def sensitivity"),
    Clause("Validation", "the Deflated Sharpe over the total trial count",
           ("backtest/research/validation/deflated.py",), needle="def deflated"),
    Clause("Validation", "a locked hold-out used once, at the end",
           ("backtest/research/holdout.py",), needle="def "),
    Clause("Baselines", "a delta-equivalent position in the underlying",
           ("backtest/research/baselines.py",), needle="delta_equivalent"),
    Clause("Baselines", "random entries with the same structure, delta and DTE",
           ("backtest/research/baselines.py",), needle="random"),
    Clause("Metrics", "return, risk and risk-adjusted measures",
           ("backtest/research/investor.py",), needle="Sortino"),
    Clause("Metrics", "trade measures: win rate, holding period, turnover",
           ("backtest/research/metrics.py",), needle="def turnover"),
    Clause("Metrics", "share of profit from the top N trades",
           ("backtest/research/sweep.py",), needle="top"),
    # Against gross rebuilt at the **mid**: the spread is inside every fill price, so a
    # ratio to plain gross has it on both sides of the division -- and a ratio to a
    # negative gross is "cost as a share of the loss", which was being printed as a cost
    # ratio. The premium-traded denominator is the one that is defined either way.
    Clause("Metrics", "costs as a share of gross P&L",
           ("backtest/research/metrics.py",), needle="cost_pct_of_gross_at_mid"),
    Clause("Metrics", "average spread paid as a share of premium",
           ("backtest/research/metrics.py",), needle="def spread_paid"),
    Clause("Metrics", "IV at entry against realised volatility over the holding period",
           ("backtest/research/metrics.py",), needle="def iv_versus_realized"),
    Clause("Metrics", "theta paid and vega P&L",
           ("backtest/accounting/attribution.py",), needle="def attribute"),
    Clause("Attribution", "greeks attribution: delta, gamma, vega, theta and residual",
           ("backtest/accounting/attribution.py",), needle="residual"),
    Clause("Attribution", "the contribution of each signal component",
           ("backtest/accounting/attribution.py",), needle="def attribute_by"),
    Clause("Compare", "runs across parameters or versions",
           ("backtest/research/forms.py",), needle="def "),
    Clause("Ablation", "run the strategy with one slot switched off or neutral",
           ("backtest/research/ablation.py",), needle="def "),
    Clause("Counterfactual", "replay each closed trade under an alternative exit",
           ("backtest/research/counterfactual.py",), needle="def "),
    Clause("Metrics", "rejected and partial fills",
           ("backtest/research/metrics.py",),
           absent="a rejected fill is recorded as a dropped candidate in the event log "
                  "rather than counted in the metrics, and **there are no partial "
                  "fills**: the fill model is all-or-nothing against the volume cap. "
                  "The second is the fill model's gap, not the metrics'"),
    Clause("Metrics", "capacity: participation distribution and return decay as "
                      "capital grows",
           ("backtest/research/metrics.py",),
           absent="needs quote sizes to say what share of the book a fill took, which "
                  "the chain does not carry. Return decay can be run as a capital sweep "
                  "today; the participation distribution it should be read against "
                  "cannot be computed"),
    Clause("Validation", "results split by IV regime",
           ("backtest/research/validation/__init__.py",),
           absent="`attribute_by` can group a result by any column and `entry_sig_iv_"
                  "rank_252` is on every trade, so the cut is one call away. What is "
                  "absent is the regime **definition** -- where the boundaries sit is a "
                  "market-regularity question, and inventing thresholds would make the "
                  "split look settled"),
    Clause("Report", "equity vs benchmark, drawdowns, monthly heatmap, rolling Sharpe, "
                     "net greeks over time, HTML export",
           ("backtest/api/sdk.py",),
           absent="the numbers behind every one of these exist (`equity_metrics`, "
                  "`investor_view`, `greeks`, `export`); what is absent is the rendering "
                  "layer, which is the front end's half and not this layer's"),
)


#: Layer 7 · interfaces. Four of its eight components are the deployment half of the
#: system -- a queue, auth, a live adapter -- and those are absent for the same reason
#: throughout: there is no deployment, and building an auth model for a single researcher
#: on one machine would be ceremony rather than security.
INTERFACES: tuple[Clause, ...] = (
    Clause("PythonSDK", "run",
           ("backtest/api/sdk.py",), needle="def run("),
    Clause("PythonSDK", "sweep",
           ("backtest/api/sdk.py",), needle="def sweep"),
    Clause("PythonSDK", "data update and check",
           ("backtest/api/sdk.py",), needle="def check_data"),
    Clause("PythonSDK", "callable from a notebook",
           ("backtest/__init__.py",), needle='"run"'),
    Clause("ResultStore", "equity, positions and fills kept per run",
           ("backtest/api/sdk.py",), needle="def export"),
    Clause("PythonSDK", "report",
           ("backtest/api/sdk.py",),
           absent="`report()` returns the figures and nothing renders them here. The "
                  "same split as the Report component: numbers in the backend, "
                  "rendering in the front end"),
    Clause("CLI", "a command-line entry point",
           ("backtest/api/sdk.py",),
           absent="there is no CLI. Every verb it would wrap is a Python call away, so "
                  "this is a convenience rather than a capability -- and the one real "
                  "cost is that a scheduled data refresh has nothing to invoke"),
    Clause("JobQueue", "submit, queue, progress, cancel, timeouts, resource quotas",
           ("backtest/engine/runner.py",),
           absent="runs are synchronous. A batch of forty blocks the caller for the "
                  "length of all forty, which is a usability limit rather than a "
                  "correctness one -- and `run_batch` records each failure rather than "
                  "discarding the batch, which is the part that would matter in a queue"),
    Clause("LiveAdapter", "a live broker implementing the SimBroker interface, paper "
                          "trading, fill reconciliation, a kill switch",
           ("backtest/execution/costs.py",),
           absent="**the document's guarantee that backtest and live run the same "
                  "strategy code is therefore unverified.** `SimBroker` is the interface "
                  "a live adapter would implement -- and `SimBroker` itself does not "
                  "exist either: the engine fills directly, so there is no seam to put a "
                  "broker behind. Two absences, one of which hides the other"),
    Clause("Auth", "users, roles, strategy visibility, an audit log",
           ("backtest/api/sdk.py",),
           absent="one researcher on one machine. The audit-log half is the part that "
                  "would still be worth having with one user -- and `run_log` plus the "
                  "run fingerprint already record what was run and on what data"),
)


#: Layer 0 and the cross-cutting figures.
#:
#: **The plan is a PDF and the working copy here is a text extraction, which drops
#: images.** Every inventory in this repo was built from that extraction, so for a long
#: time three figures' worth of requirements were never checked against anything --
#: including one, the slot-interaction diagram, that the text does not restate.
#:
#: ``FIGURES`` below is checked against the PDF itself, so a figure added to the document
#: fails the guard rather than being quietly skipped again.
FIGURES: tuple[tuple[int, str], ...] = (
    (2, "layers and event flow: what each layer emits, the engine driving all of them, "
        "and the fixed nine-stage order within one timestamp"),
    (3, "the candidate lifecycle: Idle, Armed, Open, Closed, and which slots run in each"),
    (6, "slot interaction: the trade context between the seven slots, what it holds, "
        "and the five influence channels"),
)


DIAGRAMS: tuple[Clause, ...] = (
    Clause("EventLoop", "corporate actions are ordered before anything is priced",
           ("backtest/core/events.py",), needle="EventType.CORPORATE_ACTION: 10"),
    Clause("SetupRule", "Positioning runs in Armed, beside the entry trigger",
           ("backtest/signals/lifecycle.py",), needle='State.ARMED: ("setup", "entry"'),
    Clause("SetupRule", "the Closed state runs nothing; the record is finalised",
           ("backtest/signals/lifecycle.py",), needle="State.CLOSED: ()"),
    Clause("SignalCombiner", "gate: one slot decides whether another may run",
           ("backtest/signals/lifecycle.py",), needle="RUNS_IN"),
    Clause("SignalCombiner", "pass-through: one slot's output is written to the trade "
                             "context and read by later slots",
           ("backtest/core/trade_context.py",), needle="class SlotWriter"),
    Clause("SignalCombiner", "shared context: market state visible to every slot",
           ("backtest/core/dataview.py",), needle="class DataView"),
    Clause("SignalCombiner", "override: a conflict policy in the lifecycle config",
           ("backtest/signals/lifecycle.py",), needle="conflict_policy"),
    Clause("SignalCombiner", "the Open-state run order is a lifecycle setting",
           ("backtest/signals/lifecycle.py",), needle="open_order"),
    Clause("TradeContext", "holds read-only book state, positions, P&L, greeks and "
                           "fill status",
           ("backtest/signals/exit_context.py",), needle="book: Any = None"),
    Clause("TradeContext", "every write is timestamped and logged",
           ("backtest/core/trade_context.py",), needle="def writes"),
    Clause("TradeContext", "a constraint's outcome is written back so slots can see it",
           ("backtest/engine/loop.py",), needle="_last_entry_veto"),
    Clause("SignalCombiner", "request: one slot asks another to act",
           ("backtest/core/actions.py",),
           absent="the action vocabulary has HEDGE_ADD and REDUCE, and nothing **routes** "
                  "an action to the slot that owns it -- the document's example is a "
                  "headwind asking Hedge to attach a leg. It needs the hedge slot, which "
                  "is blocked on multi-leg, and a router that does not exist"),
    Clause("EventLoop", "a sequence number as the third sort key",
           ("backtest/core/events.py",),
           absent="the figure says the engine orders events by time, **phase, seq**. "
                  "Within a phase the order is publication order, kept by the list rather "
                  "than written on the event -- equivalent while the stream lives in "
                  "memory, and not equivalent the moment a persisted stream has to be "
                  "re-sorted after loading, when the order would depend on the storage"),
)


CLAUSES: tuple[Clause, ...] = (ACCOUNTING + EXECUTION + DATA
                               + SIGNALS + PORTFOLIO + ENGINE
                               + RESEARCH + INTERFACES + DIAGRAMS)
