"""The architecture document's requirements, as a list a tool can check against.

``tools/check_plan.py`` already asks whether every *section* of the document has been read
against the system. This is the finer grain: the individual requirements inside those
sections, and which test claims each one.

## Why the list is written out rather than parsed

The document is prose and a PDF. Transcribing it once, by hand, with the section it came
from, is the only way to get an identifier a test can carry -- and the transcription being
manual is also what makes adding one a deliberate act rather than a side effect of a
reformat.

## What a requirement entry has to say

``falsified_by`` is the field that keeps this honest. A requirement whose entry cannot say
what observation would show it broken is not a requirement, it is a wish, and a test
claiming to cover it will be a test that cannot fail. This was not hypothetical: a test in
this suite was called "a broken rule does not kill the scan" and its body was a bare call
with the comment "it is enough that it does not raise".

## What this does not do

It does not check that a test tagged with a requirement actually verifies it. Nothing can.
What it does is make the **absence** of any claim visible, which is the failure that kept
recurring -- three separate passes over the document by eye each missed different things.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Requirement:
    """One thing the document asks for."""

    id: str
    section: str
    text: str
    #: What would show it broken. **Required**, and the point of the whole structure: a
    #: requirement that cannot name its own falsification will collect a test that cannot
    #: fail.
    falsified_by: str
    #: Set when the system does not meet it, with the reason. A requirement may be
    #: uncovered *because it is not built* -- that is an answer, and a different one from
    #: "nobody looked".
    not_built: str = ""


#: Transcribed from the document, section by section.
REQUIREMENTS: tuple[Requirement, ...] = (
    # ── 0.1 Events ────────────────────────────────────────────────────────
    Requirement(
        "0.1.bus", "0.1",
        "Events are the only way layers communicate",
        "a layer calling another directly",
        not_built="no event bus exists; layers call each other directly. The largest "
                  "structural gap, and 0.2's on_event, 3.2's target portfolio and 5.1's "
                  "scheduler all wait on it",
    ),
    Requirement(
        "0.1.order", "0.1",
        "Processing order within a timestamp: corporate actions -> market data -> "
        "signals -> portfolio -> orders -> fills -> expiry -> accounting",
        "accounting running before something that changes the portfolio",
        not_built="the engine marks at step 5, before the strategy runs. Equivalent "
                  "under a one-day lag; wrong the moment an intraday fill lands on its "
                  "signal's day",
    ),
    # ── 0.2 Component protocol ────────────────────────────────────────────
    Requirement(
        "0.2.registry", "0.2",
        "Components register by name, with a version and a parameter schema",
        "two different forms resolving to the same reference, or a parameter accepted "
        "that the form does not take",
    ),
    Requirement(
        "0.2.schema", "0.2",
        "The parameter schema drives both config validation and the generated form",
        "a form field that no component declares, or a declared parameter the form "
        "cannot set",
    ),
    Requirement(
        "0.2.state", "0.2",
        "get_state / set_state, for resume",
        "a resumed run differing from a continuous one",
        not_built="no form keeps state; a variant declaring itself stateful is refused "
                  "with the reason",
    ),
    # ── 0.3 Clock and data view ───────────────────────────────────────────
    Requirement(
        "0.3.cut", "0.3",
        "DataView(t) returns only data with available_at <= t",
        "a read returning a row that had not arrived by t",
    ),
    Requirement(
        "0.3.raises", "0.3",
        "Out-of-range access raises instead of returning silently",
        "a read past as_of returning a value",
    ),
    Requirement(
        "0.3.sessions", "0.3",
        "A clock with trading calendar, session phases and the expiry calendar",
        "an expiry on a day the market is shut, or a session count that disagrees with "
        "the exchange",
    ),
    Requirement(
        "0.3.phases", "0.3",
        "Session phases -- opening cross, continuous trading, closing cross -- and a "
        "single `now`",
        "a decision taken at a time of day the clock cannot express",
        not_built="the engine is daily and passes a date. Needs intraday quotes",
    ),
    # ── 0.4 Error prevention ──────────────────────────────────────────────
    Requirement("0.4.cash", "0.4", "Cash stays non-negative in a non-margin account",
                "a run finishing with cash below zero and no error"),
    Requirement("0.4.position", "0.4", "Position quantity equals the sum of its fills",
                "a position whose quantity does not reconcile to its fills"),
    Requirement("0.4.equity", "0.4", "Equity equals cash plus what is held",
                "a snapshot where the three do not balance"),
    Requirement("0.4.pnl", "0.4",
                "The day's P&L equals the change in equity minus external flows",
                "a daily P&L that does not reconcile"),
    Requirement("0.4.expired", "0.4", "Nothing expired is still held",
                "a position in a contract past its expiry"),
    Requirement("0.4.lookahead", "0.4",
                "Look-ahead is prevented by the data view cutting at available_at",
                "a signal at t computed from a bar after t"),
    Requirement("0.4.fills", "0.4",
                "Buys fill at the ask or worse, sells at the bid or worse",
                "a fill between the bid and the ask at lambda >= 1"),
    Requirement("0.4.reproducible", "0.4",
                "Fixed event order and a result hash; the same inputs give the same run",
                "two runs of one configuration differing, or two configurations sharing "
                "a hash"),
    Requirement("0.4.regression", "0.4",
                "Golden strategies locked as a regression test",
                "a code change moving the baseline without anyone noticing"),
    Requirement("0.4.survivorship", "0.4",
                "Historical constituents, delisted and expired contracts included",
                "a universe that holds only names that still exist",
                not_built="needs a point-in-time universe, which needs data"),
    Requirement("0.4.stale", "0.4",
                "Option and underlying quotes must fall within a time tolerance; stale "
                "quotes are flagged",
                "a trade marked against a quote hours older than its underlying",
                not_built="daily data has one quote per day; the loader flags a stale "
                          "*share* but there is no per-row tolerance"),
    # ── 0.5 Configuration ─────────────────────────────────────────────────
    Requirement("0.5.hash", "0.5",
                "Strategy config, code commit, data snapshot and run settings hash to "
                "the run_id",
                "two configurations that produce different trades sharing a run_id"),
    Requirement("0.5.unknown", "0.5", "Unknown config fields raise at load",
                "a misspelled key being ignored"),
    Requirement("0.5.file", "0.5", "Config is a file, validated against a schema",
                "a written config that does not reproduce its run"),
    # ── 0.6 Logging ───────────────────────────────────────────────────────
    Requirement("0.6.trace", "0.6",
                "A fill traces back to the component that caused it, with the values the "
                "decision saw",
                "a fill whose originating slot cannot be named"),
    Requirement("0.6.chain", "0.6",
                "The full chain: fill -> order -> target -> signal -> component",
                "a fill that can be traced to a slot but not to the order that carried it",
                not_built="orders and targets are not events, so two of the five links "
                          "do not exist. They arrive with the bus (0.1)"),
    # ── Layer 1 Data ──────────────────────────────────────────────────────
    Requirement("1.rule1", "1",
                "No data enters a backtest without an available_at",
                "a row used on a day it had not been published"),
    Requirement("1.rule2", "1",
                "Cleaning, adjustment and IV calculation are replayable",
                "the same raw data and rule version giving two different results",
                not_built="no cleaning or adjustment stage of our own; the adjustment "
                          "comes from the vendor's column and is not point-in-time"),
    Requirement("1.rule3", "1",
                "An option price and the underlying used with it come from the same "
                "timestamp window",
                "a mid computed against an underlying from another moment",
                not_built="daily closes, so they share a day by construction and the "
                          "window cannot be checked"),
    Requirement("1.rule4", "1",
                "Anything fitted is fitted only on data before t",
                "a signal whose value at t changes when later data is removed"),
    Requirement("1.quality", "1",
                "Bad data is rejected: price jumps, zero volume, gaps, duplicate "
                "timestamps, crossed quotes",
                "a file with duplicate primary keys or ask below bid loading cleanly"),
    Requirement("1.corporate", "1",
                "Corporate actions and contract adjustments are applied",
                "a split changing a position's value",
                not_built="absent entirely; needs a corporate action feed"),
    # ── 2.x Signals ───────────────────────────────────────────────────────
    Requirement("2.1.vocabulary", "2.1",
                "One action vocabulary: OPEN, ADD, REDUCE, CLOSE, ROLL, HEDGE_ADD, "
                "HEDGE_REMOVE, SET_STOP, HOLD",
                "an action outside the vocabulary reaching the engine"),
    Requirement("2.1.component_id", "2.1",
                "Every signal carries trade_id, action, reason and component_id",
                "a fill that cannot be attributed to a slot"),
    Requirement("2.2.styles", "2.2",
                "Cross-sectional and time-series styles on one protocol",
                "a cross-sectional strategy that cannot be expressed",
                not_built="time-series only; cross_section raises. One underlying at a "
                          "time, so there is nothing to score across"),
    Requirement("2.3.boundary", "2.3",
                "Exit, headwind and hedge may read positions but never place orders",
                "a component mutating the ledger"),
    # ── 3.x Portfolio and risk ────────────────────────────────────────────
    Requirement("3.1.actions", "3.1",
                "A risk rule may only scale down, stop new positions, liquidate, or pause",
                "a rule doing something outside the four"),
    Requirement("3.1.acts", "3.1",
                "Scale down reduces **all positions** by a ratio",
                "a scale-down that leaves open positions untouched"),
    Requirement("3.2.target", "3.2",
                "A target portfolio holds the target quantity per contract plus the "
                "reason for each adjustment",
                "an adjustment whose reason cannot be named",
                not_built="no target state and no diffing step; the engine turns an "
                          "intent into a fill directly"),
    Requirement("3.3.scenario", "3.3",
                "A scenario rule blocks trades that breach a scenario loss limit",
                "a trade accepted that the grid says breaches the limit",
                not_built="the grid reports and does not constrain"),
    # ── 4.x Execution ─────────────────────────────────────────────────────
    Requirement("4.1.lambda", "4.1",
                "fill = mid + lambda x (ask - mid), lambda >= 1",
                "a fill better than the mid on a buy"),
    Requirement("4.1.impact", "4.1",
                "Square-root impact for size in the underlying",
                "a large order filling at the same price as a small one",
                not_built="needs ADV and a calibration from live fills"),
    Requirement("4.2.latency", "4.2",
                "Signal at snapshot t, fill at t plus a decision latency; never the "
                "same quote",
                "a fill priced from the quote the signal was computed from"),
    Requirement("4.2.fees", "4.2",
                "Commission, exchange and regulatory fees, each identified",
                "a fee total that does not equal the sum of its parts"),
    Requirement("4.2.settle", "4.2",
                "An expiring contract settles to its intrinsic value",
                "an in-the-money contract expiring worthless, or the subtraction the "
                "wrong way round for a put"),
    Requirement("4.2.exercise", "4.2",
                "OCC exercise by exception at $0.01, early exercise before ex-dividend, "
                "physical delivery of shares",
                "an in-the-money call settling to cash when the account should have "
                "received shares",
                not_built="settlement is to intrinsic in cash. Early exercise is flagged "
                          "but never acted on, and there is no $0.01 threshold"),
    Requirement("4.2.reject", "4.2",
                "Reject orders where the spread or open interest fails the limit, and "
                "log every rejection",
                "a zero-bid contract being traded"),
    Requirement("4.2.participation", "4.2",
                "A participation cap on daily contract volume",
                "an order larger than the day's volume filling whole",
                not_built="needs daily contract volume, which the synthetic chain "
                          "does not produce and no real feed is wired yet"),
    # ── 5.x Engine and accounting ─────────────────────────────────────────
    Requirement("5.1.two_engines", "5.1",
                "A vectorized engine for screening and an event-driven one for "
                "validation, agreeing where both apply",
                "the two engines producing different trades on one configuration"),
    Requirement("5.1.resume", "5.1",
                "A runner with state snapshots for resume, and a batch runner",
                "a run that cannot be continued",
                not_built="no component state, so nothing to snapshot"),
    Requirement("5.2.tradebook", "5.2",
                "One record per trade with every action and the component that issued "
                "it, MFE and MAE in premium **and underlying** terms",
                "an action in the log with no slot, or an excursion in one unit only"),
    Requirement("5.2.shadow", "5.2",
                "Contracts keep being tracked after a trade closes, for counterfactuals",
                "a counterfactual exit that cannot be scored because nothing was kept"),
    Requirement("5.2.legs", "5.2",
                "P&L split into main and hedge legs",
                "a hedge whose cost cannot be separated",
                not_built="no hedge legs; needs multi-leg"),
    Requirement("5.3.attribution", "5.3",
                "Daily P&L split into delta, gamma, vega, theta and residual",
                "a split whose parts do not sum to the P&L they describe"),
    # ── 6.x Research ──────────────────────────────────────────────────────
    Requirement("6.1.baselines", "6.1",
                "A delta-equivalent position and random entries with the same structure",
                "a baseline drawn from a different period than the strategy traded"),
    Requirement("6.1.metrics", "6.1",
                "Return, risk, risk-adjusted, trade and option metrics",
                "a Sharpe computed from trade P&L rather than the daily book"),
    Requirement("6.1.report", "6.1",
                "A visual report: equity vs benchmark, drawdowns, monthly heatmap, "
                "rolling Sharpe, net Greeks",
                "a result that can only be read as numbers",
                not_built="no HTML or notebook export; the interface is the only view"),
    Requirement("6.3.variant_swap", "6.3",
                "Every slot researched with the same tools, each comparison a pair of "
                "runs differing in one component",
                "a slot with only one form, which cannot be compared"),
    Requirement("6.4.holdout", "6.4",
                "A final hold-out locked at the start and used once",
                "a result scored on data already looked at without saying so"),
    Requirement("6.4.trials", "6.4",
                "Every trial counted and the best deflated by the size of the search",
                "a rerun counted as a new trial, or a trial not counted"),
    Requirement("6.4.intervals", "6.4",
                "Confidence intervals, not point estimates; bootstrap by trade and by "
                "block",
                "a figure reported with no interval"),
    Requirement("6.4.in_sample", "6.4",
                "Counterfactual exit results are in-sample by construction and say so",
                "a counterfactual grid presented without that caveat"),
    Requirement("6.4.daily_pnl", "6.4",
                "Return and risk metrics from the daily mark-to-market of the whole book",
                "a drawdown computed from closed trades"),
    Requirement("6.4.pooling", "6.4",
                "Pool across underlyings, requiring the effect to hold on each",
                "a pooled result driven by one name",
                not_built="one underlying at a time"),
    # ── 7.x Interfaces ────────────────────────────────────────────────────
    Requirement("7.1.api", "7.1",
                "A REST API with a job queue and a result store",
                "a run that blocks its request",
                not_built="the local service has a handful of endpoints and no queue"),
    Requirement("7.1.store", "7.1",
                "Results kept, with metadata queryable and the trade log on disk",
                "a run that cannot be found again or re-run"),
    Requirement("7.2.live", "7.2",
                "The same component code runs backtest and live",
                "a component that only works in one",
                not_built="the prerequisite is the event bus"),
    # ── The register ──────────────────────────────────────────────────────
    Requirement("R1.never_optimised", "R1",
                "A realism assumption is tested for sensitivity and never optimised",
                "a realism value chosen because it improved the result"),
    Requirement("R3.no_defaults", "R3",
                "The platform never supplies a strategy choice",
                "a run proceeding with a level nobody set"),
    Requirement("R4.method_first", "R4",
                "Research-method choices are made before the research, and recorded",
                "a threshold chosen after seeing which value helped"),
    # ── The strategy model ────────────────────────────────────────────────
    Requirement("S.lifecycle", "S",
                "A four-state lifecycle: Idle, Armed, Open, Closed",
                "a candidate re-entering before its reentry policy allows",
                not_built="no Armed state; a candidate re-enters as soon as the "
                          "condition is true, the most permissive policy there is"),
    Requirement("S.context", "S",
                "One shared trade context, written by the slots that run before",
                "two slots reading different values for one trade on one day"),
    Requirement("S.range", "S",
                "A trade is reported only if the range can resolve it",
                "a trade settled from prices after the backtest ended"),
    Requirement("S.unresolved", "S",
                "A position the range could not resolve is reported as such, not "
                "silently absent from the trade log",
                "fewer trades than entries, with nothing saying why"),
)

#: Every requirement id, for the meta-test.
IDS: frozenset[str] = frozenset(r.id for r in REQUIREMENTS)
BY_ID: dict[str, Requirement] = {r.id: r for r in REQUIREMENTS}
