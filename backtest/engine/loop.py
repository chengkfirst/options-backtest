"""The event-driven engine: stepping day by day.

There is exactly one difference from the trade-table engine, and it is the one that
matters: **it knows how much money is in the account and how many contracts are
held at any moment.** That is what makes the equity curve, drawdown, capital
constraints, concurrency limits and hedging possible at all.

Both engines read the **same** ``Strategy`` definition. Not for convenience — so
they can check each other: with no capital constraint and no concurrency limit, the
same configuration must produce row-identical trade logs on both sides, and an
error on either is shown up by the other.

The main loop's order within a day is fixed, and every step's position has a reason:

    1  advance to day T
    2  load T's data
    3  fill the intents created on T-1   <- before expiry settlement: a contract
                                            still trades on its expiry day, so an
                                            exit placed on T-1 deserves the chance
    4  settle T's expiries
    5  mark to market -> write T's snapshot  <- before the strategy callback: the
                                            strategy must see today's value; and
                                            steps 6-7 do not change the portfolio,
                                            so this is the day's final snapshot
    6  compute signals
    7  the strategy produces intents
    8  validate -> queue for T+1        <- separate from producing them: conflicts
                                            caused by the execution lag have to
                                            surface at the source
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd

from backtest.core.actions import Action, TradeAction
from backtest.core.config import BacktestConfig
from backtest.core.dataview import DataView
from backtest.core.thresholds import get as _threshold
from backtest.execution.costs import ZERO_FEES, check_tradable, execute
from backtest.data.options.chain import ChainSource
from backtest.data.market import MarketData
from backtest.signals.plan import Context, Plan
from backtest.signals.review import Add, Close, PositionView, Reduce
from backtest.signals.exit_context import exit_context
from backtest.signals.lifecycle import Candidate, State
from backtest.signals.strategy import ExitRule
from backtest.engine.runner import Checkpoint
from backtest.engine.strategy import Strategy
from backtest.core.errors import InvariantError, StartupError
from backtest.core.events import Event, EventBus, EventType
from backtest.core.trade_context import TradeContext, market_only
from backtest.portfolio.target import Adjustment, TargetPortfolio
from backtest.core.invariants import check_all as check_invariants
from backtest.core.run_log import EventKind, EventLog
from backtest.accounting.tradebook import (
    EventDrivenResult,
    trade_context,
    trades_frame,
)
from backtest.accounting.ledger import Ledger, Position, Snapshot
from backtest.portfolio.risk_actions import NOTHING, RiskAction
from backtest.portfolio.positioning import contracts_for, loss_fraction
from backtest.portfolio.scenarios import aggregate_greeks
from backtest.signals.contracts import select_contract
from backtest.signals.registry import SignalSet
from backtest.core.types import (
    ContractId,
    ExitReason,
    QuoteSource,
    Right,
    Side,
    TradeRecord,
    resolve_exit_reason,
)

ENGINE_NAME = "event_driven"


#: Which slot each exit reason belongs to, for attribution by component.
#:
#: ``risk_forced`` is headwind's: it is the only reason the book-wide rules can produce.
#:
#: The **time-based exits are their own slot**, not the stop's. They were filed under
#: ``stop_loss``, which put every holding-cap and days-to-expiry close into the stop's
#: measured contribution -- and contradicted this system's own ablation, which neutralises
#: the stop and says in as many words that "the holding cap and the days-to-expiry floor
#: still close it". Two parts of one system disagreeing about which slot owns an exit, with
#: attribution reading one of them.
#:
#: They are not the stop for a reason that survives the naming: a stop is a thesis-failure
#: rule and a holding cap is a patience rule. Ablating one and keeping the other is a
#: meaningful experiment; crediting them to the same slot makes it unmeasurable.
_SLOT_FOR_EXIT = {
    ExitReason.TAKE_PROFIT: "take_profit",
    ExitReason.STOP_LOSS: "stop_loss",
    ExitReason.RISK_FORCED: "headwind",
    ExitReason.DTE_FLOOR: "time_exit",
    ExitReason.TIME_EXIT: "time_exit",
    ExitReason.EXPIRY_ITM: "expiry",
    ExitReason.EXPIRY_OTM: "expiry",
}

#: Which exit reason a review's ``Close.kind`` files under. The statistics group on
#: this, so the mapping has to be exact.
REVIEW_EXIT_KIND = {
    "take_profit": ExitReason.TAKE_PROFIT,
    "stop_loss": ExitReason.STOP_LOSS,
    "headwind": ExitReason.RISK_FORCED,
    "signal": ExitReason.TIME_EXIT,
}


@dataclass(frozen=True, slots=True)
class Intent:
    """One trade intent. Created on T, executed on T+lag."""

    contract: ContractId
    side: Side
    quantity: int
    created: date
    execute_on: date
    trade_id: int
    #: Which action this intent carries. **An enum, never a string**: using
    #: human-readable text as a control-flow switch means rewording it executes an entry
    #: as an exit, and a misspelled string matches no branch and falls through in
    #: silence. See :mod:`backtest.core.actions` for the shared vocabulary.
    kind: Action = Action.OPEN
    reason: str = ""                  # why this order, for a person to read
    #: For a REDUCE from the book-wide rule: what fraction of the position to **keep**.
    #:
    #: Carried on the intent rather than read from the rule at fill time. The two are a
    #: day apart and the rule may say something different by then -- it frequently does,
    #: for a form that responds to a single session -- and the instruction that has to be
    #: honoured is the one that was given.
    keep: float | None = None
    #: Which slot produced it: ``entry`` / ``take_profit`` / ``stop_loss`` / ``headwind``
    #: / ``position`` / ``review``. This is what makes "which slot closed this trade"
    #: answerable, which is the prerequisite for attribution by component.
    component_id: str = ""
    exit_reasons: tuple[ExitReason, ...] = ()
    #: What a plan carries over: this trade's own exit rule, tag and note.
    exit_rule: "ExitRule | None" = None
    tag: str = ""
    note: str = ""
    #: The id of the ``ORDER`` event this intent was published as. Carried on the intent
    #: rather than looked up at fill time: the two are a day apart, and matching a fill
    #: to an order by contract and quantity is a guess that is wrong exactly when two
    #: orders for the same contract are in flight -- the case the chain exists for.
    order_event: int = 0


def _entry_scale(decision) -> float:
    """How much of a new position to open under the day's risk verdict.

    Separate from acting on the book: scaling down means new entries are smaller as well
    as existing ones being cut, and halting or liquidating means there are none at all.
    """
    if decision.action is RiskAction.SCALE_DOWN:
        return float(decision.keep)
    return 0.0 if decision.blocks_entries else 1.0


def _as_float(value) -> float:
    """A float, or NaN -- never a zero standing in for an absent number."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return out


@dataclass
class OpenTrade:
    """A logical trade not yet closed. It becomes a TradeRecord on exit."""

    trade_id: int
    contract: ContractId
    entry_date: date
    entry_price: float
    quantity: int
    side: Side                        # the entry side: BUY = long, SELL = short
    fees: float
    entry_quote: dict
    entry_signals: dict
    #: What each slot recorded while this trade was open, partitioned by slot.
    #:
    #: One structure for the whole life of the trade rather than one per day: the
    #: question a reader asks is "what did the stop think on the day it fired", and the
    #: last value before the exit is that. A per-day history is a different and larger
    #: thing -- it is what ``paths`` already carries.
    slot_record: "TradeContext" = field(default_factory=lambda: market_only({}))
    #: The entry fees **broken out**. The total alone was carried, and the record then
    #: reported all of it as commission -- so a report whose whole purpose is showing what
    #: ate the money attributed 8.00 of slippage to commission. The total was right, which
    #: is why nothing raised.
    entry_fee_parts: "Any | None" = None
    #: Sessions between the day the entry was due to fill and the day it did. Zero unless
    #: a quote was missing on the execution day.
    entry_deferred: int = 0
    #: Maximum favourable / adverse excursion while held, for MFE/MAE research
    mfe: float = 0.0
    mae: float = 0.0
    #: The same in underlying terms. See :class:`~backtest.core.types.TradeRecord`.
    #: ``entry_spot`` is NaN when the entry quote carried no underlying price, and then
    #: both stay NaN rather than being measured against a zero.
    entry_spot: float = float("nan")
    mfe_underlying: float = float("nan")
    mae_underlying: float = float("nan")
    #: +1 when the position profits from the underlying rising, -1 when from it falling.
    #: Taken from the contract's right and the entry side, never from the premium's own
    #: movement -- reading the direction off the premium would make a trade that lost to
    #: IV look like a trade whose underlying went against it.
    direction: int = 1
    #: **This trade's own** exit rule. Under a plan each can differ — the deep
    #: drawdown case widens the stop and holds longer. Empty falls back to the
    #: strategy's global rule.
    exit_rule: "ExitRule | None" = None
    #: Gross P&L already banked by reductions. **It has to be recorded separately**:
    #: the final record computes "remaining quantity x price difference", which
    #: naturally omits the contracts sold earlier. Omitting it raises nothing and
    #: simply leaves this trade's P&L quietly short.
    realised_gross: float = 0.0
    #: The tag and note attached at order time, from the Plan. They travel into the
    #: trade log for classified research afterwards.
    tag: str = ""
    note: str = ""
    #: The id of the ``ORDER`` event this intent was published as. Carried on the intent
    #: rather than looked up at fill time: the two are a day apart, and matching a fill
    #: to an order by contract and quantity is a guess that is wrong exactly when two
    #: orders for the same contract are in flight -- the case the chain exists for.
    order_event: int = 0


class EventDrivenEngine:
    """The backtest engine that steps day by day."""

    def __init__(
        self,
        market: MarketData,
        chain: ChainSource,
        signals: SignalSet,
        config: BacktestConfig | None = None,
        events: EventLog | None = None,
        bus: "EventBus | None" = None,
        *,
        max_concurrent: int | None = None,
        enforce_capital: bool = True,
        limits=None,
        brake=None,
        shadow_days: int | None = None,
        scenario_limit=None,
    ) -> None:
        self.market = market
        self.chain = chain
        self.signals = signals
        self.config = config if config is not None else market.config
        # ``is not None``, not ``or``. ``EventLog`` defines ``__len__``, so an empty one
        # is falsy -- and a caller passing a fresh log to keep its events separate had it
        # silently replaced by the shared one. Nothing failed; the events simply went
        # somewhere else, which is how a test asserting "nothing was recorded" came to
        # read another test's events.
        self.events = (events if events is not None
                       else market.events)
        #: The document's bus. Its own, not the market's: a bus is a record of **this
        #: run's** causal chain, and two runs sharing one would interleave their chains
        #: with nothing in a trace saying which run a link came from.
        self.bus = bus if bus is not None else EventBus()
        #: The scenario loss limit, or None. Read before an entry is placed: the stress
        #: grid reported for a long time and nothing consulted it when it mattered.
        self.scenario_limit = scenario_limit
        #: None means no limit. With None and enforce_capital=False, this engine
        #: should agree with the trade-table engine.
        self.max_concurrent = max_concurrent
        self.enforce_capital = enforce_capital
        #: Portfolio exposure limits (ExposureLimits) and the drawdown brake
        #: (DrawdownBrake). Both only block new entries; neither reduces an existing
        #: position — reducing is headwind's job, which is not built.
        self.limits = limits
        self.brake = brake
        #: How many trading days to keep quoting a contract **after** its trade closed.
        #:
        #: This is the one thing counterfactual exit research cannot do without, and the
        #: one thing that cannot be reconstructed afterwards: once a run has finished,
        #: there is no record of what the contract did after the exit, so "would a wider
        #: stop have worked" is unanswerable. Collecting it costs one chain query per
        #: closed trade per day and changes no result.
        #:
        #: 0 turns it off. The default is on because the asymmetry is total: the cost of
        #: collecting it is small and the cost of not having it is a study you cannot run.
        self.shadow_days = (shadow_days if shadow_days is not None
                            else int(_threshold("shadow_days")))

    # ── guards ────────────────────────────────────────────
    def _guard(self, strategy: Strategy) -> None:
        self.signals.require(list(strategy.entry.requires))
        if strategy.exit.hold_days is None and strategy.exit.dte_floor is None:
            raise StartupError("no time-based exit, so positions get dragged into expiry")
        lag = self.config.execution_lag_days
        floor = strategy.exit.dte_floor
        if floor is not None and floor < lag + 1:
            raise StartupError(
                f"the days-to-expiry floor {floor} is too small: with an execution lag "
                f"of {lag} days it must be at least {lag + 1}"
            )
        # When selection is a function it decides direction itself, which cannot and
        # should not be checked here.
        if not callable(strategy.select) and strategy.select.right is Right.CALL \
                and getattr(strategy, "short", False):
            raise StartupError("a naked short call is not supported: there is no margin "
                               "model, so any number given would be wrong")
        if strategy.allow_stacking:
            # A real account nets positions by contract: buying the same contract
            # twice is one position at the broker, not two independent ones. The
            # ledger does this faithfully, but the engine tracks logical trades by
            # trade_id, so the second one never sees "its" position closed and is
            # silently dropped. Supporting it needs a lot-tracking layer, which does
            # not exist — hence a refusal rather than a drop.
            raise StartupError(
                "the event-driven engine does not support allow_stacking: a real "
                "account nets positions by contract, so several entries in the same "
                "contract merge into one position and cannot be split back into "
                "several logical trades. That needs a lot-tracking layer, which is "
                "not implemented. The trade-table engine can take this flag, but "
                "then the two engines' results are no longer comparable."
            )

    # ── the main loop ─────────────────────────────────────
    def run(self, strategy: Strategy, *, until: "date | None" = None,
            resume_from: "Checkpoint | None" = None) -> EventDrivenResult:
        """Run the strategy over the configured range.

        ``until`` stops after that session and puts a :class:`Checkpoint` on the result,
        so the run can be continued. ``resume_from`` continues one.

        Both are optional and absent by default, which is the whole run in one piece --
        the shape every caller had before resume existed, and the shape the equality test
        compares against.
        """
        self._guard(strategy)
        if resume_from is not None:
            if resume_from.strategy_name != strategy.name:
                raise StartupError(
                    f"this checkpoint was taken from {resume_from.strategy_name!r} and "
                    f"is being resumed under {strategy.name!r}. Continuing one "
                    f"strategy's book under another builds a book nobody ran"
                )
            if until is not None and until <= resume_from.through:
                raise StartupError(
                    f"this checkpoint already covers through {resume_from.through} and "
                    f"the run is asked to stop at {until}, so there is nothing to do. "
                    f"A no-op resume returning a result would read as a run"
                )
        #: Held so the state protocol can reach the bound components. The engine is given
        #: the strategy per run rather than per construction, so this is where it lands.
        self._strategy = strategy
        cal = self.market.calendar
        held = resume_from
        ledger = (held.ledger if held is not None
                  else Ledger(self.config.notional_capital, self.config.contract))
        queue: list[Intent] = list(held.queue) if held is not None else []
        open_trades: dict[int, OpenTrade] = (dict(held.open_trades) if held is not None
                                             else {})
        records: list[TradeRecord] = list(held.records) if held is not None else []
        snapshots: list[Snapshot] = list(held.snapshots) if held is not None else []
        greeks_daily: list = list(held.greeks) if held is not None else []
        next_trade_id = held.next_trade_id if held is not None else 1
        counters = dict(held.counters) if held is not None else {}
        start_equity = float(self.config.notional_capital)
        self._last_equity = counters.get("last_equity", start_equity)
        # **Restored, not restarted.** A resumed run whose peak equity began again at the
        # initial capital reports no drawdown until it exceeds the old peak -- so a
        # drawdown brake would be released by the act of resuming, which is the kind of
        # difference nothing else in a result would show.
        self._peak_equity = counters.get("peak_equity", start_equity)
        self._drawdown = counters.get("drawdown", 0.0)
        paths: list[dict] = list(held.paths) if held is not None else []
        shadow: list[dict] = list(held.shadow) if held is not None else []
        actions: list[TradeAction] = list(held.actions) if held is not None else []
        #: trade_id -> what is needed to keep quoting a contract after its trade closed.
        shadowing: dict[int, dict] = dict(held.shadowing) if held is not None else {}
        #: symbol -> the day its shares were delivered, so they can be sold on the next
        #: session. Delivery is overnight, so selling on the delivery day itself would be
        #: a fill before the shares existed.
        deliveries: dict[str, date] = (dict(held.deliveries) if held is not None
                                       else {})
        #: symbol -> its candidate. One per underlying, which is the scope the document
        #: gives the Idle and Armed states; Open and Closed belong to a trade_id. Without
        #: the separation, "may this underlying trade again yet" has nowhere to live and
        #: ends up decided by accident in the engine.
        candidates: dict[str, Candidate] = (
            dict(held.candidates) if held is not None
            else {self.config.symbol: Candidate(symbol=self.config.symbol)})
        self._brake_state = counters.get("brake_state", "none")
        self._risk = NOTHING
        self._daily_return = counters.get("daily_return", 0.0)
        self._prev_equity = counters.get("prev_equity", 0.0)
        self._last_greeks = None
        self._closed_pnl = records          # the same list, referenced not copied
        if held is not None:
            for slot, state in held.component_state.items():
                component = strategy.components.get(slot)
                if component is None:
                    raise StartupError(
                        f"the checkpoint holds state for the {slot!r} slot and this run "
                        f"has no component there. Resuming would discard it, and a "
                        f"trailing stop that resumed from its entry is a fixed stop for "
                        f"the rest of the trade"
                    )
                component.set_state(state)
        if held is not None and held.events:
            # The stream follows on, so an order in flight still names a real event.
            self.bus.resume(held.events, held.next_event_id)

        sessions = [d for d in self.market.sessions()
                    if (held is None or d > held.through)
                    and (until is None or d <= until)]
        if not sessions:
            raise StartupError(
                f"there are no sessions left to run"
                + (f" after {held.through}" if held is not None else "")
                + (f" up to {until}" if until is not None else "")
                + ". A run over no sessions returns a result that looks like a backtest"
            )
        for day in sessions:
            # The day's events, published as they happen and delivered at the end of it
            # in the document's order. Published rather than delivered inline: delivering
            # on publish would run observers in the order the loop happens to emit, which
            # is the dependence on who-ran-first that the phase table exists to remove.
            #
            # The loop still **acts** inline. What the bus carries is the causal chain --
            # requirement 0.6 -- and the order, which is why the requirements map says
            # 0.1 is partial and names what is missing rather than claiming it whole.
            dividend = self.market.dividends_between(
                cal.previous_session(day) or day, day)
            if dividend > 0:
                self.bus.emit(EventType.CORPORATE_ACTION, day, "data",
                              reason=f"ex-dividend, {dividend:.4f} per share",
                              data={"cash_per_share": dividend})
            spot_now = self.market.spot(day)
            market_event = self.bus.emit(
                EventType.MARKET, day, "data",
                reason=("the underlying's close" if spot_now.is_present
                        else f"no close: {spot_now.reason}"),
                data={"spot": spot_now.or_else(float("nan"))})

            # 3  fill yesterday's intents
            # **Due now, and anything that was due and could not fill.** The second half
            # was missing: an intent whose execution day had no quote was dropped, not
            # retried -- the position still exited, because the next day's rules decided
            # again, but the decision was remade rather than carried, `deferred_days` never
            # left zero, and the trade-table engine (which does carry it) reported a
            # different number for the same path.
            #
            # Carried rather than remade matters beyond the field: a stop that fired on
            # Monday and could not fill until Wednesday is a stop that fired on Monday.
            # Re-deciding on Wednesday asks whether it would fire *now*, which is a
            # different question and the kinder one.
            due = [i for i in queue if i.execute_on <= day]
            queue = [i for i in queue if i.execute_on > day]
            for intent in due:
                # No quote today: the order stands and waits, rather than being dropped.
                # Bounded by the contract's own expiry, which settles whatever is left --
                # so a contract that never trades again cannot hold an order for ever.
                if intent.contract.expiry > day and \
                        self.chain.series(intent.contract, [day]).empty:
                    self.events.record(
                        EventKind.EXIT_DEFERRED, day,
                        f"no quote, so the {intent.kind.value} decided on "
                        f"{intent.created} still stands and waits",
                        str(intent.contract))
                    queue.append(intent)
                    continue
                closed = self._execute(intent, ledger, open_trades, day, actions)
                if closed is not None:
                    records.append(closed)
                    candidates[closed.contract.symbol].closed_trade(day)
                    self._start_shadow(closed, shadowing)

            # 3b  dispose of shares delivered at a physical settlement
            #
            # ``Ledger.liquidate_stock`` says in its own docstring that shares arriving
            # from exercise should not be kept, because holding them is no longer an
            # option strategy -- and nothing called it. Delivered shares sat in the book
            # for the rest of the run as unhedged exposure to the underlying, inside a
            # result reported as an options result.
            #
            # Sold on the **next** session rather than on the expiry day: delivery is
            # overnight, and selling at the close of the day the option expired would be
            # a fill before the shares existed. The overnight gap is a real cost of the
            # approximation and is named in the event rather than removed.
            self._dispose_deliveries(ledger, deliveries, day)

            # 3c  early exercise, where the chain says the European price is wrong
            #
            # **The flag screens and the tree decides.** The chain already flags where a
            # European price is wrong -- a call before a dividend, a deep in-the-money
            # put against the carry on the strike -- and that flag is one subtraction per
            # row. The tree is 800 steps and about 320,000 nodes, which is milliseconds
            # but not milliseconds times every position times every session.
            #
            # So the flag is the screen and the tree is the decision, which is also the
            # right division of labour: the flag says "the European price may be wrong
            # here", and acting on that alone would be exercising on the failure of an
            # approximation rather than on a comparison. The tree answers the comparison:
            # is the intrinsic worth more than holding.
            for pos in list(ledger.positions):
                if pos.contract.expiry <= day:
                    continue              # settled below, as an expiry
                rec = self._early_exercise(pos, ledger, open_trades, day)
                if rec is not None:
                    records.append(rec)
                    candidates[rec.contract.symbol].closed_trade(day)
                    before = {p.symbol for p in ledger.stock_positions}
                    for p in ledger.stock_positions:
                        if p.symbol not in before or p.symbol in deliveries:
                            deliveries[p.symbol] = day
                    self._start_shadow(rec, shadowing, expired=True)

            # 4  settle expiries
            for pos in list(ledger.positions):
                if pos.contract.expiry <= day:
                    self.bus.emit(EventType.EXPIRY, day, "ledger",
                                  trade_id=pos.trade_id,
                                  reason=f"{pos.contract} reached its expiry",
                                  data={"contract": str(pos.contract)})
                    before = {p.symbol for p in ledger.stock_positions}
                    rec = self._settle(pos, ledger, open_trades, day)
                    for p in ledger.stock_positions:
                        if p.symbol not in before or p.symbol in deliveries:
                            deliveries[p.symbol] = day
                    if rec is not None:
                        records.append(rec)
                        candidates[rec.contract.symbol].closed_trade(day)
                        # An expiry leaves nothing to shadow: the contract is gone, and
                        # there is no later price for it to have. Recording an empty
                        # shadow would make "no data" indistinguishable from "it never
                        # moved".
                        self._start_shadow(rec, shadowing, expired=True)

            # 5  mark to market
            quotes = self._quotes(day, ledger.positions)
            snap = ledger.snapshot(day, quotes, self.market.spot(day))
            # Two layers of checking, deliberately. The ledger's identity is about money
            # (equity = opening + realised − fees + unrealised); the invariant set is
            # about state (cash non-negative, nothing expired still held, equity equal to
            # what is actually held). A bug can satisfy one and break the other.
            ledger.check_invariant(snap)
            check_invariants(
                when=day,
                cash=snap.cash,
                equity=snap.equity_mid,
                position_value=snap.option_value_mid,
                stock_value=snap.stock_value,
                contracts=[p.contract for p in ledger.positions],
            )
            snapshots.append(snap)
            # Accounting, and it is last in the phase table for the reason the audit
            # found: a mark taken before the day's fills does not include them. Here it
            # is published after the fill and expiry events of the same day, so the
            # order in the stream is the document's whatever order the loop runs in.
            self.bus.emit(EventType.ACCOUNTING, day, "ledger",
                          reason=f"marked to market: equity {snap.equity_mid:,.2f}",
                          data={"equity_mid": snap.equity_mid, "cash": snap.cash,
                                "positions": len(ledger)})
            self._last_equity = snap.equity_mid
            self._peak_equity = max(self._peak_equity, snap.equity_mid)
            # The drawdown is computed unconditionally. Computing it only inside the
            # brake's branch left a plan function unable to read it — and how deep
            # the drawdown is happens to be the commonest input to sizing and
            # reduction decisions.
            self._drawdown = snap.equity_mid / self._peak_equity - 1.0
            # One session's return, which the daily-loss form reads. Computed here beside
            # the drawdown and unconditionally, for the same reason: a figure available
            # only inside the rule's own branch cannot be read by a plan or a review.
            self._daily_return = (snap.equity_mid / self._prev_equity - 1.0
                                  if self._prev_equity else 0.0)
            self._prev_equity = snap.equity_mid
            g = aggregate_greeks(ledger.positions, self.chain, day,
                                 self.market.spot(day).or_else(float("nan")), snap.equity_mid)
            greeks_daily.append(g)
            self._last_greeks = g
            # 4  the book-wide risk rule. Its verdict is one of the document's four
            # actions, and **it acts on the book** -- scaling down reduces the positions
            # that are open, not only the size of the next entry. The brake used to do the
            # latter while reading as the former.
            # The book-wide risk rule is a **component**, like the stop and the target.
            # It used to be one hardcoded shape -- step down, halt, release -- whose
            # numbers were required but whose structure was the platform's. The document
            # lists this slot's responses as "to be defined" and names two examples; both
            # are registered, and which one runs is the researcher's.
            self._risk = self.brake({
                "drawdown": self._drawdown,
                "daily_return": self._daily_return,
                "equity": snap.equity_mid,
                "positions": len(ledger),
            }) if self.brake is not None else NOTHING
            if self.brake is not None and self._risk.action.value != self._brake_state:
                self.bus.emit(
                    EventType.RISK, day, "portfolio",
                    component_id=getattr(self.brake, "component_id", "headwind"),
                    reason=f"the book-wide rule moved to {self._risk.action.value}: "
                           f"{getattr(self._risk, 'reason', '')}",
                    data={"action": self._risk.action.value,
                          "drawdown": self._drawdown,
                          "daily_return": self._daily_return})
                self.events.record(
                    EventKind.CANDIDATE_DROPPED, day,
                    f"{self.brake.ref}: {self._brake_state} -> "
                    f"{self._risk.action.value} ({self._risk.reason})")
                self._brake_state = self._risk.action.value
            self._track_excursions(ledger, open_trades, quotes, day, paths)
            self._track_shadow(shadowing, day, shadow)

            # 6-7  signals -> the strategy
            view = self.signals.on(day)
            intents: list[Intent] = []
            #: contract -> what the Intent needs beyond the quantity. The target holds the
            #: quantity and the reasons, which is what the document asks of it; the exit
            #: reasons and the slot ride here rather than being pushed into the target,
            #: where they would make it a second copy of the decision.
            detail = self._detail = {}

            # **The target portfolio.** Every slot that wants an existing position changed
            # writes a target quantity for its contract with its reason; execution then
            # turns the gap between the targets and the book into orders. The document
            # asks for exactly that, and the engine used to go straight from a decision to
            # an order -- so there was no moment at which the intended book existed, and
            # nowhere for "why is this position this size" to be answered except by
            # replaying the decisions.
            #
            # What it buys here, concretely: the book-wide risk rule and a position's own
            # stop wanting the same contract on the same day used to be two orders, and
            # whichever was queued first decided the attribution. Against a target they
            # are two adjustments to one number, and both reasons survive.
            #
            # **Entries do not go through it.** A new contract has nothing held to diff
            # against, so target minus held is the identity and the indirection would add
            # a step without composing anything.
            targets = TargetPortfolio()
            held_now = {p.contract: p.quantity for p in ledger.positions}

            # The risk rule writes its targets before the per-trade rules do, so a
            # position both want is resolved as an overlap rather than a race.
            self._risk_targets(targets, ledger, open_trades, queue, day)

            for pos in ledger.positions:
                trade = open_trades.get(pos.trade_id)
                if trade is None or self._already_queued(queue, pos.contract):
                    continue
                # The static rules are tested first. They are a **safety net**: the
                # time-based exits may not be circumvented by a review, or one review
                # function written wrong quietly drags a position into expiry.
                reasons = self._exit_reasons(pos, trade, quotes, day, strategy)
                action = None
                if not reasons and strategy.review is not None:
                    action = self._review_action(
                        strategy, self._market_ctx(day, ledger), pos, trade, quotes, day)

                target = cal.shift(day, self.config.execution_lag_days)
                if target is None or target > self.config.end:
                    continue               # end of the range: leave it to expiry

                if reasons or isinstance(action, Close):
                    if isinstance(action, Close) and not reasons:
                        reasons = [REVIEW_EXIT_KIND[action.kind]]
                        self.events.record(EventKind.CANDIDATE_DROPPED, day,
                                           f"review closed it: {action.reason}",
                                           str(pos.contract))
                    # The slot credited is the one whose reasoning closed it: the first
                    # exit reason by precedence, or the review's own kind when the review
                    # acted. Crediting "exit" for all of them would make attribution by
                    # component useless, which is the whole reason the field exists.
                    first = resolve_exit_reason(list(reasons)) if reasons else None
                    slot = _SLOT_FOR_EXIT.get(first, "exit")
                    targets.want(Adjustment(
                        contract=pos.contract, quantity=0, slot=slot, when=day,
                        reason=(", ".join(r.value for r in reasons) if reasons
                                else "the review closed it")))
                    detail[pos.contract] = dict(
                        trade_id=pos.trade_id, kind=Action.CLOSE, reason="exit",
                        exit_reasons=tuple(reasons), component_id=slot,
                        execute_on=target)
                elif isinstance(action, Reduce):
                    # Round down, and keep at least 1 — reducing to 0 should be
                    # expressed as Close; letting a reduction quietly become a close
                    # leaves the statistics unable to tell them apart.
                    cut = int(pos.abs_quantity * action.fraction)
                    if cut < 1 or cut >= pos.abs_quantity:
                        self.events.record(
                            EventKind.CANDIDATE_DROPPED, day,
                            f"a {action.fraction:.0%} reduction computes to {cut} "
                            f"contracts (holding {pos.abs_quantity}), skipped",
                            str(pos.contract))
                    else:
                        keep = pos.abs_quantity - cut
                        targets.want(Adjustment(
                            contract=pos.contract,
                            quantity=keep if pos.quantity > 0 else -keep,
                            slot="review", when=day,
                            reason=action.reason or "reduced by review"))
                        detail[pos.contract] = dict(
                            trade_id=pos.trade_id, kind=Action.REDUCE,
                            reason=action.reason or "reduced by review",
                            component_id="review", execute_on=target)
                elif isinstance(action, Add):
                    grown = pos.abs_quantity + action.contracts
                    targets.want(Adjustment(
                        contract=pos.contract,
                        quantity=grown if pos.quantity > 0 else -grown,
                        slot="review", when=day,
                        reason=action.reason or "added by review"))
                    detail[pos.contract] = dict(
                        trade_id=pos.trade_id, kind=Action.ADD,
                        reason=action.reason or "added by review",
                        component_id="review", execute_on=target)

            # Setup, then entry. **Two steps, not one.** Slot 1 decides whether this is
            # an environment to trade in and *arms* the candidate; slot 2 looks for
            # timing inside the armed window. The engine used to collapse them -- the
            # entry condition becoming true produced an order in the same step -- so no
            # ablation could say what a regime filter was worth, because removing it also
            # removed the timing.
            candidate = candidates[self.config.symbol]
            # **The diffing step.** Target minus held, per contract, and the result is
            # what execution gets. A subtraction anybody can check against the ledger,
            # where an intent could only be checked by waiting to see what it did.
            for order in targets.orders(held_now):
                carried = detail.get(order.contract)
                if carried is None:
                    raise InvariantError(
                        f"a target for {order.contract} has no decision detail behind "
                        f"it, so an order would be placed with no exit reason and no "
                        f"slot. The target and the detail are written together"
                    )
                intents.append(Intent(
                    contract=order.contract,
                    side=Side.BUY if order.quantity > 0 else Side.SELL,
                    quantity=abs(order.quantity), created=day, **carried))

            if strategy.setup is None:
                # No setup slot: always armed, which is exactly what the engine did
                # before the state existed. This is why the state changes no result.
                candidate.arm("no setup slot, so every session is armed")
            else:
                note = candidate.state
                armed = bool(strategy.setup(self._market_ctx(day, ledger)))
                if armed:
                    candidate.arm("the setup slot armed this session")
                else:
                    candidate.disarm("the setup slot did not arm this session")
                if candidate.state is not note:
                    self.bus.emit(
                        EventType.SIGNAL, day, "signals",
                        component_id="setup",
                        reason=f"the candidate moved to {candidate.state.value}: "
                               f"{candidate.reason}",
                        data={"state": candidate.state.value})

            refusal = candidate.may_enter(day, strategy.lifecycle, calendar=cal)
            if refusal:
                if strategy.entry.condition(view):
                    # Recorded only when the entry condition *was* true: a refusal on a
                    # day nothing wanted to trade is not information, and logging it
                    # every session would bury the days it mattered.
                    self.events.record(EventKind.CANDIDATE_DROPPED, day, refusal)
            elif strategy.entry.condition(view):
                intent = self._entry_intent(day, strategy, ledger, queue, next_trade_id)
                if intent is not None:
                    intents.append(intent)
                    next_trade_id += 1

            # 8  validate and queue, and publish the chain the fill will name
            #
            # Three events per intent, in the document's order: the signal (which slot
            # decided, and why), the target (what the book should hold as a result), and
            # the order (the gap between target and actual). The fill, a day or more
            # later, names the order -- and `Intent.order_event` is what carries the id
            # across that gap.
            #
            # A rejected intent publishes the signal and the target and then a REJECT
            # rather than an ORDER. Collapsing the two would lose the count 4.2 asks for:
            # "log every rejected order" is a number a researcher reads, and a rejection
            # that looks like an order that never filled is indistinguishable from a bug
            # in the fill path.
            for intent in intents:
                signal = self.bus.emit(
                    EventType.SIGNAL, day, "signals", trade_id=intent.trade_id,
                    component_id=intent.component_id,
                    reason=intent.reason or intent.kind.value,
                    data={"action": intent.kind.value,
                          "contract": str(intent.contract),
                          "exit_reasons": [r.value for r in intent.exit_reasons]})
                # **From the target portfolio, not recomputed from the intent.**
                #
                # This derived the target by adding the intent's quantity back onto the
                # position, which is a second computation of a number the target already
                # holds -- and a second record of a fact the system records once is the
                # failure that put 22 decision-log rows against 34 fill events earlier
                # today. An entry has no target (nothing is held to diff against), so it
                # falls back to the arithmetic and says so.
                wanted = targets.target_for(intent.contract)
                adjustments = targets.reasons_for(intent.contract)
                current = held_now.get(intent.contract, 0)
                if wanted is None:
                    current = 0
                    wanted = (intent.quantity if intent.side is Side.BUY
                              else -intent.quantity)
                    why = (f"{intent.reason or intent.kind.value} "
                           f"(a new position, so there is no gap to close)")
                else:
                    why = "; ".join(f"{a.slot}: {a.reason}" for a in adjustments)
                target_event = self.bus.emit(
                    EventType.TARGET_PORTFOLIO, day, "portfolio",
                    caused_by=signal.id, trade_id=intent.trade_id,
                    component_id=intent.component_id,
                    reason=f"hold {wanted} of {intent.contract} (now {current}): {why}",
                    data={"contract": str(intent.contract), "target": wanted,
                          "current": current,
                          "adjustments": [{"slot": a.slot, "reason": a.reason}
                                          for a in adjustments]})
                if not self._validate(intent, ledger, day):
                    self.bus.emit(
                        EventType.REJECT, day, "execution", caused_by=target_event.id,
                        trade_id=intent.trade_id, component_id=intent.component_id,
                        reason=f"the {intent.kind.value} order was not placed",
                        data={"contract": str(intent.contract)})
                    continue
                order = self.bus.emit(
                    EventType.ORDER, day, "execution", caused_by=target_event.id,
                    trade_id=intent.trade_id, component_id=intent.component_id,
                    reason=f"{intent.side.value} {intent.quantity} {intent.contract}, "
                           f"due {intent.execute_on}",
                    data={"contract": str(intent.contract), "side": intent.side.value,
                          "quantity": intent.quantity,
                          "execute_on": intent.execute_on.isoformat()})
                queue.append(replace(intent, order_event=order.id))
                if intent.kind is Action.OPEN:
                    # **At the order, not at the fill.** They are a day apart under the
                    # execution lag, and this gate is asked at the decision -- so
                    # counting at the fill let `reentry_policy='once'` queue eleven
                    # entries before the first one landed, each passing a check against
                    # a candidate that had opened nothing yet.
                    candidates[intent.contract.symbol].opened_trade()

            # The day is over: deliver what it published, in the phase order. Nothing is
            # published during the drain -- the loop has finished the day -- so no event
            # can arrive late for its phase here. That check earns its keep when a
            # handler publishes, which is what the live path will do.
            self.bus.drain(day)

        # **A position still open when the run ends is not a trade and must not be
        # silently absent.**
        #
        # There is no forced close at the range end -- an exit decided with no session
        # left to fill it is skipped, and the position simply stays. That is the right
        # behaviour: closing it would invent a fill on a day the range does not cover, and
        # recording it as a time exit would put a trade that never ended into the exit-
        # reason statistics. What is wrong is saying nothing, because fewer trades with no
        # explanation reads exactly like a signal that did not fire.
        if len(ledger):
            # Every one of them, not the first five. The list was truncated to five with
            # nothing saying so, in the one notice whose whole purpose is that a position
            # missing from the trade log does not go unmentioned -- so with seven open,
            # two were as absent as before.
            self.events.record(
                EventKind.UNRESOLVED_POSITION, self.config.end,
                f"{len(ledger)} position(s) were still open when the range ended and are "
                f"not in the trade log: they have no exit, and inventing one would be "
                f"either a fill outside the range or an exit reason that did not happen",
                "; ".join(f"{p.contract} x{p.abs_quantity}" for p in ledger.positions))

        return EventDrivenResult(
            paths=tuple(paths), shadow=tuple(shadow), actions=tuple(actions),
            records=tuple(records), snapshots=tuple(snapshots), greeks=tuple(greeks_daily),
            config=self.config, strategy=strategy, events=self.events, ledger=ledger,
            risk_rule=self.brake, bus=self.bus,
            limits={
                "max_concurrent": self.max_concurrent,
                "enforce_capital": self.enforce_capital,
                "execution_lag_days": self.config.execution_lag_days,
                # The shadow window changes nothing about the trades, only how far past
                # each exit the prices are kept. It is in the register as an engineering
                # choice, and it is in the fingerprint because a run without it cannot
                # answer a counterfactual question a run with it can.
                "shadow_days": self.shadow_days,
                # It blocks entries, so it changes which trades happen. Fifth time
                # something that moves the result has had to be added here explicitly.
                "scenario_limit": (
                    {"max_loss": self.scenario_limit.max_loss,
                     "scenarios": list(self.scenario_limit.scenarios)}
                    if self.scenario_limit is not None else None),
            },
            checkpoint=(self._checkpoint(
                strategy, sessions[-1], ledger=ledger, queue=queue,
                open_trades=open_trades, records=records, snapshots=snapshots,
                greeks=greeks_daily, paths=paths, shadow=shadow, actions=actions,
                shadowing=shadowing, deliveries=deliveries, candidates=candidates,
                next_trade_id=next_trade_id)
                if until is not None else None),
        )

    def _checkpoint(self, strategy, through: date, **state) -> "Checkpoint":
        """The run's state at the end of ``through``.

        Built in one place from named arguments rather than read off the instance,
        because the failure mode here is **omission** -- a field the loop keeps and this
        does not, producing a resumed run that is plausible and different. A keyword here
        is a field someone had to name; an attribute scan would silently cover whatever
        happened to be set.
        """
        return Checkpoint(
            through=through,
            strategy_name=strategy.name,
            run_id="",                  # filled by the API layer, which owns the hash
            ledger=state["ledger"],
            queue=tuple(state["queue"]),
            open_trades=dict(state["open_trades"]),
            records=tuple(state["records"]),
            snapshots=tuple(state["snapshots"]),
            greeks=tuple(state["greeks"]),
            paths=tuple(state["paths"]),
            shadow=tuple(state["shadow"]),
            actions=tuple(state["actions"]),
            shadowing=dict(state["shadowing"]),
            deliveries=dict(state["deliveries"]),
            candidates=dict(state["candidates"]),
            next_trade_id=state["next_trade_id"],
            component_state={slot: c.get_state()
                             for slot, c in self._stateful_components().items()},
            events=self.bus.history,
            next_event_id=len(self.bus.history) + len(self.bus.pending()) + 1,
            counters={
                "last_equity": self._last_equity,
                "peak_equity": self._peak_equity,
                "drawdown": self._drawdown,
                "daily_return": self._daily_return,
                "prev_equity": self._prev_equity,
                "brake_state": self._brake_state,
            },
        )

    # ── the steps ─────────────────────────────────────────
    def _quotes(self, day: date, positions) -> dict[ContractId, tuple]:
        out: dict[ContractId, tuple] = {}
        for pos in positions:
            frame = self.chain.series(pos.contract, [day])
            if frame.empty:
                self.events.record(EventKind.QUOTE_MISSING, day, "no quote to mark against",
                                   str(pos.contract))
                continue
            row = frame.iloc[0]
            # Carry the whole row: trajectory collection needs iv and delta.
            # Returning only (bid, ask) would mean querying the chain again to record
            # one more field, which is a wasted pass.
            out[pos.contract] = (float(row["bid"]), float(row["ask"]), row)
        return out

    def _track_excursions(self, ledger, open_trades, quotes, day, paths) -> None:
        """Collect every held position's state, each day.

        Two uses:

        1. **MFE / MAE** — where the take-profit and the stop belong; reading the
           distribution is far more direct than sweeping parameters.
        2. **The daily trajectory** — a **hard prerequisite** for headwind research
           (5). With only an entry and an exit point there is no way to see what
           happened in between, and so no way to answer whether the positions that
           ended badly gave a warning beforehand.

        The data can only be collected while a position is held and **cannot be
        reconstructed afterwards** — which is why collection comes first, even
        though the headwind research tools are not written.
        """
        for pos in ledger.positions:
            trade = open_trades.get(pos.trade_id)
            quote = quotes.get(pos.contract)
            if trade is None or quote is None:
                continue
            mark = (quote[0] + quote[1]) / 2.0
            ratio = (mark / trade.entry_price - 1.0) * (1 if pos.quantity > 0 else -1)
            trade.mfe = max(trade.mfe, ratio)
            trade.mae = min(trade.mae, ratio)

            spot = self.market.spot(day).or_else(float("nan"))
            # The underlying-terms excursion. Guarded on both sides: a missing entry spot
            # or a missing mark leaves both at NaN rather than measuring against a zero,
            # which would read as a 100% move.
            if math.isfinite(trade.entry_spot) and math.isfinite(spot):
                move = (spot / trade.entry_spot - 1.0) * trade.direction
                trade.mfe_underlying = (move if not math.isfinite(trade.mfe_underlying)
                                        else max(trade.mfe_underlying, move))
                trade.mae_underlying = (move if not math.isfinite(trade.mae_underlying)
                                        else min(trade.mae_underlying, move))

            row = quote[2] if len(quote) > 2 else None
            paths.append({
                "trade_id": trade.trade_id,
                "date": day,
                "days_held": self.market.calendar.sessions_between(trade.entry_date, day),
                "dte": self.market.calendar.sessions_between(day, pos.contract.expiry),
                "mark": mark,
                "ratio": ratio,              # P&L as a fraction of the entry price
                "mfe_so_far": trade.mfe,     # best/worst so far, **not looked back on**
                "mae_so_far": trade.mae,
                "underlying": self.market.spot(day).or_else(float("nan")),
                "iv": float(row["iv"]) if row is not None and "iv" in row else float("nan"),
                "delta": float(row["delta"]) if row is not None and "delta" in row else float("nan"),
                "tag": trade.tag,
            })

    def _start_shadow(self, record, shadowing: dict, *, expired: bool = False) -> None:
        """Begin following a contract after its trade closed.

        Nothing is followed when the window is 0, or when the contract expired — an
        expired contract has no later price, and an empty shadow would make "no data"
        look like "it never moved".
        """
        if self.shadow_days <= 0 or expired:
            return
        shadowing[record.trade_id] = {
            "contract": record.contract,
            "entry_price": record.entry_price,
            "exit_date": record.exit_date,
            "exit_price": record.exit_price,
            "direction": 1 if record.quantity > 0 else 1,
            "left": self.shadow_days,
        }

    def _track_shadow(self, shadowing: dict, day: date, shadow: list[dict]) -> None:
        """What each closed trade's contract did after the exit.

        The mirror of :meth:`_track_excursions`, and needed for the same reason: a price
        path cannot be reconstructed after the run. The difference is which question it
        answers — the excursions say where the price went **while** the trade was open,
        and this says where it went **after** the exit, which is the only way to ask
        whether the exit was early.

        ``ratio`` is against the **entry** price throughout, so it is directly comparable
        with the excursions. ``vs_exit`` is against the exit, which is what "did I leave
        money behind" reads off.
        """
        if not shadowing:
            return
        for trade_id, st in list(shadowing.items()):
            if st["left"] <= 0 or day <= st["exit_date"]:
                if st["left"] <= 0:
                    del shadowing[trade_id]
                continue
            if day > st["contract"].expiry:
                # Past expiry there is no contract to quote. Stop rather than record a
                # mark of 0, which would read as a worthless position instead of an
                # absent one.
                del shadowing[trade_id]
                continue
            frame = self.chain.series(st["contract"], [day])
            if frame.empty:
                st["left"] -= 1
                continue
            row = frame.iloc[0]
            mark = (float(row["bid"]) + float(row["ask"])) / 2.0
            shadow.append({
                "trade_id": trade_id,
                "date": day,
                "days_after_exit": self.market.calendar.sessions_between(
                    st["exit_date"], day),
                "mark": mark,
                "bid": float(row["bid"]),
                "ask": float(row["ask"]),
                "ratio": mark / st["entry_price"] - 1.0,
                "vs_exit": mark / st["exit_price"] - 1.0 if st["exit_price"] else float("nan"),
                "underlying": self.market.spot(day).or_else(float("nan")),
                "iv": float(row["iv"]) if "iv" in row else float("nan"),
            })
            st["left"] -= 1

    def _risk_targets(self, targets, ledger, open_trades, queue, day: date) -> None:
        """Write the day's risk verdict into the target portfolio.

        It used to build orders directly, and ran before the per-trade rules so a position
        it was closing would not also be queued by its own stop -- a race resolved by call
        order. Against a target the two are adjustments to one number and both reasons
        survive, which is what the target exists for.

        ``no_new_positions`` needs nothing here -- it is read at entry. ``scale_down`` and
        ``liquidate`` act on what is already held, which is what makes them different from
        a brake on sizing, and what the document means by "reduce all positions by a
        ratio" and "close the whole book".

        Every intent is credited to ``headwind``, so the decision log can tell a position
        the book-wide rule cut from one its own stop closed. They are different events and
        a trade log cannot distinguish them without this.
        """
        if self._risk.action not in (RiskAction.SCALE_DOWN, RiskAction.LIQUIDATE):
            return
        due = self.market.calendar.shift(day, self.config.execution_lag_days)
        if due is None or due > self.config.end:
            return
        wanted = 0
        skipped = 0
        for pos in list(ledger.positions):
            if open_trades.get(pos.trade_id) is None or \
                    self._already_queued(queue, pos.contract):
                continue
            if self._risk.action is RiskAction.LIQUIDATE:
                quantity, kind = pos.abs_quantity, Action.CLOSE
            else:
                # Round down what is cut, so scaling never accidentally increases a
                # position and a one-contract position is left alone rather than being
                # closed by a rule that said "reduce".
                quantity = pos.abs_quantity - int(pos.abs_quantity * self._risk.keep)
                kind = Action.REDUCE
                if quantity <= 0 or quantity >= pos.abs_quantity:
                    skipped += 1
                    continue
            keep_qty = pos.abs_quantity - quantity
            targets.want(Adjustment(
                contract=pos.contract,
                quantity=keep_qty if pos.quantity > 0 else -keep_qty,
                slot="headwind", when=day, reason=self._risk.describe()))
            self._detail[pos.contract] = dict(
                trade_id=pos.trade_id, kind=kind, reason=self._risk.describe(),
                exit_reasons=(ExitReason.RISK_FORCED,) if kind is Action.CLOSE else (),
                keep=self._risk.keep if kind is Action.REDUCE else None,
                component_id="headwind", execute_on=due)
            wanted += 1
        if wanted:
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               f"book-wide risk rule: {self._risk.describe()} "
                               f"({wanted} positions)")
        elif skipped:
            # **The rule fired and did nothing, and that has to be visible.** Every held
            # position was too small to reduce without closing it, so the scale-down is
            # correctly a no-op -- and a reader seeing no reduction cannot otherwise tell
            # that from a rule which never triggered. The two call for opposite responses:
            # one means the brake is working, the other means it is not configured.
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               f"book-wide risk rule: {self._risk.describe()}, but all "
                               f"{skipped} position(s) are too small to reduce without "
                               f"closing them, so nothing was cut")

    def _exit_reasons(self, pos: Position, trade: OpenTrade, quotes, day: date, strategy: Strategy):
        # This trade's own rule wins. Under a plan each trade's take-profit and stop
        # can differ — which is where the seven components' coupling actually lands:
        # one judgement decides both what to buy and how to get out.
        rule = trade.exit_rule or strategy.exit
        cal = self.market.calendar
        reasons: list[ExitReason] = []
        quote = quotes.get(pos.contract)
        if quote is not None:
            exit_price = quote[0] if pos.quantity > 0 else quote[1]
            ratio = (exit_price / trade.entry_price - 1.0) * (1 if pos.quantity > 0 else -1)
            # A swapped-in component decides its slot **instead of** the matching field on
            # the rule -- never as well. Running both would mean a trade could be closed by
            # the form that was supposed to have been replaced, and the comparison between
            # two forms would be measuring neither.
            spot = self.market.spot(day).or_else(float("nan"))
            ctx = exit_context(
                # The trade a stateful form's state belongs to. Two open trades under one
                # trailing stop have two high-water marks; without this they would share
                # one, and the second trade's stop would move because the first one's did.
                trade_id=trade.trade_id,
                premium_ratio=ratio,
                underlying_move=((spot / trade.entry_spot - 1.0) * trade.direction
                                 if math.isfinite(trade.entry_spot) and math.isfinite(spot)
                                 else float("nan")),
                days_held=cal.sessions_between(trade.entry_date, day),
                days_to_expiry=cal.sessions_between(day, pos.contract.expiry),
                iv=float(quote[2]["iv"]) if len(quote) > 2 and quote[2] is not None
                   and "iv" in quote[2] else float("nan"),
                # Cut at today, so a variant that reaches for more reads only what had
                # happened. The document: every slot reads through the DataView, and a
                # variant may read more **within** it.
                view=DataView.at(day, market=self.market, signals=self.signals,
                                 chain=self.chain),
            )
            # **Each slot writes only into its own namespace.** The writer is bound to
            # the slot name and has nowhere else to put anything, so two slots both
            # recording `level` -- which they naturally do -- keep two values instead of
            # the second overwriting the first in silence.
            #
            # The engine records the verdict and the figure it turned on, always, so a
            # slot that ran is never indistinguishable from a slot that did not. A form
            # that wants to add its own reasoning takes `ctx["write"]`, which is the same
            # bound writer: the component writes, and it cannot write elsewhere.
            take = strategy.components.get("take_profit")
            stop = strategy.components.get("stop_loss")

            tp_note = trade.slot_record.writer("take_profit")
            tp_note.update({"premium_ratio": ratio,
                            "level": rule.take_profit,
                            "form": "component" if take is not None else "rule"})
            if take is not None:
                fired = bool(take({**ctx, "write": tp_note}))
            else:
                fired = rule.take_profit is not None and ratio >= rule.take_profit
            tp_note.write("fired", fired)
            if fired:
                reasons.append(ExitReason.TAKE_PROFIT)

            sl_note = trade.slot_record.writer("stop_loss")
            sl_note.update({"premium_ratio": ratio,
                            "level": rule.stop_loss,
                            "form": "component" if stop is not None else "rule"})
            if stop is not None:
                fired = bool(stop({**ctx, "write": sl_note}))
            else:
                fired = rule.stop_loss is not None and ratio <= -rule.stop_loss
            sl_note.write("fired", fired)
            if fired:
                reasons.append(ExitReason.STOP_LOSS)
        # `time_exit` is the holding cap and the days-to-expiry floor. It records in its
        # own namespace for the reason the whole partition exists: its exits were once
        # credited to the stop-loss slot, and nothing structural tied the recorded fact to
        # the slot that produced it.
        dte = cal.sessions_between(day, pos.contract.expiry)
        held = cal.sessions_between(trade.entry_date, day)
        time_note = trade.slot_record.writer("time_exit")
        time_note.update({"days_to_expiry": dte, "days_held": held,
                          "dte_floor": rule.dte_floor, "hold_days": rule.hold_days})
        # Two independent conditions, not a chain. Both can be true on one day, and
        # `suppressed_reasons` records the one that did not win -- an `elif` here would
        # have dropped it and made the precedence invisible.
        fired: list[str] = []
        if rule.dte_floor is not None and dte <= rule.dte_floor:
            reasons.append(ExitReason.DTE_FLOOR)
            fired.append("dte_floor")
        if rule.hold_days is not None and held >= rule.hold_days:
            reasons.append(ExitReason.TIME_EXIT)
            fired.append("hold_days")
        time_note.write("fired", ", ".join(fired) if fired else False)
        return reasons

    def _entry_intent(self, day, strategy, ledger, queue, trade_id) -> Intent | None:
        if self.max_concurrent is not None and len(ledger) >= self.max_concurrent:
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               f"at the concurrency limit of {self.max_concurrent}")
            return None
        if self._risk.blocks_entries:
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               f"no new positions: {self._risk.describe()}")
            return None
        if self.limits is not None and self._last_greeks is not None:
            allowed, why = self.limits.check(self._last_greeks, self._last_equity)
            if not allowed:
                self.events.record(EventKind.CANDIDATE_DROPPED, day,
                                   f"exposure limit: {why}")
                return None
        target = self.market.calendar.shift(day, self.config.execution_lag_days)
        if target is None or target > self.config.end:
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               "the execution day falls outside the backtest range")
            return None

        if strategy.plan is not None:
            return self._plan_intent(day, target, strategy, ledger, queue, trade_id)

        selection = select_contract(
            self.chain.snapshot(target), strategy.select, self.config, self.market.calendar, target,
            # The chain is the execution day's (target); the view is cut at the
            # **decision** day, so a selector that reaches for the fill day's move
            # raises rather than quietly picking a strike out of it.
            DataView.at(day, market=self.market, signals=self.signals, chain=self.chain),
        )
        if not selection.ok:
            self.events.record(EventKind.SELECTION_FAILED, target, selection.describe())
            return None
        # Something was chosen, but a constraint was skipped — that must not stay
        # only in the trace; a warning the user cannot see is no warning.
        for why in selection.skipped_filters:
            self.events.record(EventKind.CANDIDATE_DROPPED, target,
                               f"selection note: {why}")
        contract = selection.contract.unwrap()
        if not strategy.allow_stacking and (
            ledger.position(contract) is not None or self._already_queued(queue, contract)
        ):
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                "this contract is already held and allow_stacking is false",
                str(contract))
            return None
        quantity = self._size(strategy, ledger, contract, day, target)
        if self.brake is not None and self._brake_state == "reduced":
            quantity = int(quantity * _entry_scale(self._risk))
        if quantity <= 0:
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                f"sizing computes to {quantity} contracts (method "
                f"{strategy.sizing.method}) — less than one means no trade, and it is "
                f"never rounded up", str(contract))
            return None
        # **The scenario limit, read before the order is placed.** The stress grid has
        # reported for a long time and nothing consulted it at the moment it mattered, so
        # a limit a researcher had set lived in a report rather than in the book.
        #
        # Checked against the book **as it would be** with this trade in it, which is the
        # only version of the question worth asking: a limit on the book as it stands
        # would pass every trade that breaches it.
        if self.scenario_limit is not None:
            blocked = self._scenario_breaches(ledger, contract, quantity, day)
            if blocked:
                from backtest.portfolio.scenario_rule import describe

                self.events.record(
                    EventKind.CANDIDATE_DROPPED, day,
                    describe(blocked, self.scenario_limit), str(contract))
                self.bus.emit(
                    EventType.RISK, day, "portfolio", component_id="headwind",
                    reason=f"the scenario limit blocked an entry: "
                           f"{blocked[0].describe()}",
                    data={"breaches": len(blocked)})
                return None
        return Intent(contract=contract, side=Side.BUY, quantity=quantity,
                      created=day, execute_on=target, trade_id=trade_id,
                      kind=Action.OPEN, reason="entry", component_id="entry")

    def _scenario_breaches(self, ledger, contract, quantity: int, day: date):
        """The scenarios this trade would push past the limit.

        The candidate's greeks are added to the book's, which is an approximation and a
        stated one: it ignores that the new contract's gamma makes the book's delta move
        differently from the sum of the two. For a limit whose job is to stop a position
        being opened that is the right side of the approximation -- the sum overstates
        nothing and understates the interaction, so a breach reported here is real.
        """
        from backtest.portfolio.scenario_rule import check
        from backtest.portfolio.scenarios import PortfolioGreeks, aggregate_greeks

        frame = self.chain.series(contract, [day])
        if frame.empty:
            # No quote for the candidate, so its contribution cannot be priced. The
            # entry will fail at the fill for the same reason, and refusing here with a
            # scenario reason would attribute it to the limit.
            return ()
        row = frame.iloc[0]
        spot = self.market.spot(day).or_else(float("nan"))
        book = aggregate_greeks(ledger.positions, self.chain, day, spot,
                                self._last_equity)
        mult = self.config.contract.multiplier
        added = {g: float(row[g]) * quantity * mult if g in row else 0.0
                 for g in ("delta", "gamma", "vega", "theta")}
        with_trade = PortfolioGreeks(
            day=day, spot=spot,
            delta=book.delta + added["delta"],
            gamma=book.gamma + added["gamma"],
            vega=book.vega + added["vega"],
            theta=book.theta + added["theta"],
            n_positions=book.n_positions + 1,
            equity=book.equity,
            missing=book.missing,
        )
        return check(with_trade, self.scenario_limit)

    def _market_ctx(self, day, ledger) -> Context:
        """The market and portfolio context for a review.

        **It carries no candidate chain**: a review looks at a position already held,
        not at choosing a new contract. Passing an empty frame rather than the day's
        full chain avoids suggesting that a review could switch contracts — that is a
        roll, and needs its own support (see review.Roll).
        """
        import pandas as pd

        return Context(
            day=day, chain=pd.DataFrame(), spot=self.market.spot(day).or_else(float("nan")),
            equity=self._last_equity, cash=ledger.cash, positions=len(ledger),
            drawdown=self._drawdown, _view=self.signals.on(day),
            data=DataView.at(day, market=self.market, signals=self.signals,
                             chain=self.chain),
        )

    def _review_action(self, strategy, ctx, pos, trade, quotes, day):
        """Run the user's review function. Exceptions are never swallowed; they are
        raised with the day and the contract."""
        quote = quotes.get(pos.contract)
        if quote is None:
            return None                     # no quote today: leave it to the static rules
        row = quote[2] if len(quote) > 2 else None
        mark = (quote[0] + quote[1]) / 2.0
        view = PositionView(
            contract=pos.contract, entry_date=trade.entry_date,
            entry_price=trade.entry_price, quantity=pos.abs_quantity,
            days_held=self.market.calendar.sessions_between(trade.entry_date, day),
            dte=self.market.calendar.sessions_between(day, pos.contract.expiry),
            mark=mark,
            ratio=(mark / trade.entry_price - 1.0) * (1 if pos.quantity > 0 else -1),
            mfe_so_far=trade.mfe, mae_so_far=trade.mae,
            iv=float(row["iv"]) if row is not None and "iv" in row else float("nan"),
            delta=float(row["delta"]) if row is not None and "delta" in row else float("nan"),
            tag=trade.tag,
        )
        try:
            action = strategy.review(ctx, view)
        except Exception as exc:  # noqa: BLE001
            raise StartupError(
                f"the review function raised on {day} handling {pos.contract}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if action is not None and not isinstance(action, (Close, Reduce, Add)):
            raise StartupError(
                f"the review function returned {type(action).__name__} on {day}; it "
                f"must return one of Close / Reduce / Add, or None to keep holding."
            )
        return action

    def _plan_intent(self, day, target, strategy, ledger, queue, trade_id) -> Intent | None:
        """Entry by plan: one function decides what to buy, how much, and how to get
        out.

        This is where the seven components are coupled — one judgement yields the
        contract, the size and the exit levels. It is exactly what the parameterised
        form cannot express.
        """
        from backtest.signals.contracts import enrich_chain

        chain = enrich_chain(
            self.chain.snapshot(target), self.config, self.market.calendar, target
        )
        if chain.empty:
            self.events.record(EventKind.SELECTION_FAILED, target,
                               "no executable contract that day")
            return None

        ctx = Context(
            day=day, chain=chain, spot=self.market.spot(day),
            equity=self._last_equity, cash=ledger.cash, positions=len(ledger),
            drawdown=self._drawdown, _view=self.signals.on(day),
            # Cut at the **decision** day, not the execution day. The chain above is the
            # execution day's on purpose; everything a plan reads through ctx.data is the
            # decision day's, so a plan cannot use the fill day's move to pick a strike.
            data=DataView.at(day, market=self.market, signals=self.signals,
                             chain=self.chain),
        )
        try:
            plan = strategy.plan(ctx)
        except Exception as exc:  # noqa: BLE001
            # Never swallowed. An error inside the plan function is raised with the
            # day it happened on.
            raise StartupError(
                f"the plan function raised on {day}: {type(exc).__name__}: {exc}"
            ) from exc

        if plan is None:
            return None
        if not isinstance(plan, Plan):
            raise StartupError(
                f"the plan function returned {type(plan).__name__} on {day}; it must "
                f"return a Plan or None."
            )

        row = plan.contract
        contract = ContractId(
            symbol=str(row["symbol"]),
            expiry=row["expiry"] if isinstance(row["expiry"], date)
            else pd.Timestamp(row["expiry"]).date(),
            right=Right(row["right"]),
            strike=float(row["strike"]),
        )
        # The user may have assembled a row elsewhere, or modified the chain before
        # filtering. Verify it really is on that day's executable chain.
        key = chain[(chain["symbol"].astype(str) == contract.symbol)
                    & (chain["strike"].astype(float) == contract.strike)
                    & (chain["right"].astype(str) == contract.right.value)]
        if key.empty:
            raise StartupError(
                f"the contract the plan chose on {day} is not on that day's "
                f"executable chain: {contract}. Pick a row from ctx.chain; do not "
                f"assemble one."
            )

        if not strategy.allow_stacking and (
            ledger.position(contract) is not None or self._already_queued(queue, contract)
        ):
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               "this contract is already held and allow_stacking is "
                               "false", str(contract))
            return None

        quantity = self._plan_size(plan, contract, target, strategy)
        if quantity <= 0:
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               f"the plan computes to 0 contracts "
                               f"(equity {ctx.equity:,.0f})", plan.tag)
            return None
        if self.brake is not None and self._brake_state == "reduced":
            quantity = int(quantity * _entry_scale(self._risk))
            if quantity <= 0:
                self.events.record(EventKind.CANDIDATE_DROPPED, day,
                                   "0 contracts after the drawdown brake cut the size")
                return None

        return Intent(
            contract=contract, side=Side.BUY, quantity=quantity, created=day,
            execute_on=target, trade_id=trade_id,
            kind=Action.OPEN, reason=plan.note or plan.tag or "entry by plan",
            component_id="plan",
            exit_rule=self._plan_exit_rule(plan, strategy),
            tag=plan.tag, note=plan.note,
        )

    def _plan_exit_rule(self, plan: Plan, strategy: Strategy) -> ExitRule:
        """An exit the plan did not give falls back to the strategy's global rule,
        never to "no limit".

        Falling back to no limit turns "the plan forgot dte_floor" into "this trade
        got dragged into expiry", and raises nothing. Falling back to the global value
        at least keeps the time-based-exit guard in force.
        """
        base = strategy.exit
        return ExitRule(
            hold_days=plan.max_hold if plan.max_hold is not None else base.hold_days,
            take_profit=plan.take_profit if plan.take_profit is not None else base.take_profit,
            stop_loss=plan.stop_loss if plan.stop_loss is not None else base.stop_loss,
            dte_floor=plan.dte_floor if plan.dte_floor is not None else base.dte_floor,
        )

    def _premium(self, contract, execute_on) -> float:
        """One contract's cost at the execution day's ask, multiplier included.

        Data access, which is why it is here and not in the sizer.
        """
        frame = self.chain.series(contract, [execute_on])
        if frame.empty:
            return 0.0
        return float(frame.iloc[0]["ask"]) * self.config.contract.multiplier

    def _plan_size(self, plan: Plan, contract, execute_on, strategy) -> int:
        """The quantity from whichever sizing the plan gave. With none of the three, fall
        back to the strategy's method."""
        if plan.contracts is not None:
            return plan.contracts
        if plan.risk_pct is None and plan.notional is None:
            return self._size(strategy, None, contract, execute_on, execute_on)

        premium = self._premium(contract, execute_on)
        if premium <= 0:
            return 0
        budget = (plan.risk_pct * self._last_equity) if plan.risk_pct is not None else plan.notional
        return int(budget // premium)

    def _size(self, strategy, ledger, contract, day, execute_on) -> int:
        """Fetch what the sizer needs, then let the portfolio layer do the arithmetic.

        The split matters: the engine knows how to reach the chain and the volatility
        series, and the sizer knows the rule. Keeping the rule here made it reachable
        only through a full backtest.
        """
        sizing = strategy.sizing
        if sizing.method == "fixed":
            return contracts_for(sizing, equity=0.0, premium=1.0, stop_loss=None)

        volatility = None
        if sizing.method == "scale_by_vol":
            vol = self.market.volatility(day)
            if vol.is_missing:
                self.events.record(EventKind.CANDIDATE_DROPPED, day,
                                   f"scale_by_vol needs a volatility, unavailable "
                                   f"that day: {vol.reason}")
                return 0
            volatility = vol.unwrap()

        return contracts_for(
            sizing,
            equity=self._last_equity,
            premium=self._premium(contract, execute_on),
            stop_loss=strategy.exit.stop_loss,
            volatility=volatility,
        )

    @staticmethod
    def _already_queued(queue, contract) -> bool:
        return any(i.contract == contract for i in queue)

    def _validate(self, intent: Intent, ledger: Ledger, day: date) -> bool:
        """Conflicts caused by the execution lag have to surface at the source, not
        fail at execution time."""
        if intent.contract.expiry <= intent.execute_on:
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                f"{intent.contract} has expired by its execution day "
                f"{intent.execute_on}", str(intent.contract))
            return False
        # `kind`, never `reason`. `reason` is free text for a person to read, and a
        # plan sets it to its own note, so testing it here skipped the capital check
        # for every plan-based entry.
        if intent.kind is Action.OPEN and self.enforce_capital:
            frame = self.chain.series(intent.contract, [intent.execute_on])
            if not frame.empty:
                need = float(frame.iloc[0]["ask"]) * intent.quantity * self.config.contract.multiplier
                if need > ledger.cash:
                    self.events.record(
                        EventKind.NOT_TRADABLE, day,
                        f"not enough cash: {need:,.0f} needed, {ledger.cash:,.0f} "
                        f"available", str(intent.contract))
                    return False
        return True

    def _execute(self, intent: Intent, ledger: Ledger, open_trades, day: date,
                 actions: list | None = None) -> TradeRecord | None:
        frame = self.chain.series(intent.contract, [day])
        actions = actions if actions is not None else []
        if frame.empty:
            self.events.record(EventKind.NOT_TRADABLE, day,
                               "no quote on the execution day", str(intent.contract))
            return None
        row = frame.iloc[0]
        # **A sell is sized against the book on the fill day, not the decision day.**
        #
        # The two are a day apart, and the position can shrink in between -- another
        # reduction filled, a partial close. A quantity carried over unchanged then
        # exceeds what is held, which the ledger refuses outright: "reversing through zero
        # is not supported". It surfaced the moment a risk rule that can fire on
        # consecutive days was added; the drawdown form changes state too slowly to
        # overlap with itself, so nothing had ever queued two sells against one position.
        #
        # A close takes whatever is held, because that is what closing means. A reduction
        # is **recomputed from its own ratio** rather than clamped: clamping would turn
        # "keep half" into "keep whatever is left", which is a different instruction.
        if intent.kind in (Action.REDUCE, Action.CLOSE):
            position = ledger.position(intent.contract)
            held = position.abs_quantity if position is not None else 0
            if held <= 0:
                self.events.record(
                    EventKind.NOT_TRADABLE, day,
                    f"the position was already gone by the fill day, so the "
                    f"{intent.kind.value} was not placed", str(intent.contract))
                return None
            if intent.kind is Action.CLOSE:
                wanted = held
            else:
                wanted = (held - int(held * intent.keep) if intent.keep is not None
                          else min(intent.quantity, max(held - 1, 0)))
                if wanted <= 0 or wanted >= held:
                    self.events.record(
                        EventKind.NOT_TRADABLE, day,
                        f"a reduction of {intent.quantity} against a position of {held} "
                        f"would take all of it, so it was not placed",
                        str(intent.contract))
                    return None
            if wanted != intent.quantity:
                self.events.record(
                    EventKind.EXIT_DEFERRED, day,
                    f"the {intent.kind.value} was decided on {intent.quantity} and {held} "
                    f"was held at the fill, so it is {wanted}", str(intent.contract))
                intent = replace(intent, quantity=wanted)
        tradable = check_tradable(row["bid"], row["ask"], self.config.costs)
        if not tradable.ok:
            self.events.record(EventKind.NOT_TRADABLE, day, tradable.reason or "", str(intent.contract))
            return None

        price, fees = execute(
            bid=row["bid"], ask=row["ask"], side=intent.side, quantity=intent.quantity,
            multiplier=self.config.contract.multiplier, costs=self.config.costs,
        )
        ledger.apply_fill(contract=intent.contract, side=intent.side, quantity=intent.quantity,
                          price=price, fees=fees, when=day, trade_id=intent.trade_id)
        # **The FILL event goes where the fill is**, on the line after it, beside the
        # decision log's own row. It was published by the caller, before this method ran,
        # and `_execute` has four paths that fill nothing -- no quote, the position gone
        # by the fill day, a reduction that would take all of it, an untradable quote.
        # Each of those left a FILL event with no fill behind it, and an intent that is
        # carried forward published one again every day it was retried: one close of one
        # trade produced five.
        #
        # Nothing failed. The trade table was right, the ledger was right, and the event
        # stream said twelve things had happened that had not. Two records of the same
        # fact, written in two places, is the whole mechanism of that failure -- so they
        # are written in one place now, and a test holds them one to one.
        self.bus.emit(
            EventType.FILL, day, "broker", caused_by=intent.order_event,
            trade_id=intent.trade_id, component_id=intent.component_id,
            reason=f"{intent.kind.value} {intent.quantity} {intent.contract}: "
                   f"{intent.reason}",
            data={"contract": str(intent.contract), "side": intent.side.value,
                  "quantity": intent.quantity, "price": price, "fees": fees.total,
                  "decided_on": intent.created.isoformat()})
        # The decision log: one TradeAction per fill, carrying the slot that asked for it
        # and the values it acted on. This is what turns "which slot closed this trade"
        # from unanswerable into a lookup, and it is the prerequisite for attribution by
        # component.
        actions.append(TradeAction(
            action=intent.kind, trade_id=intent.trade_id, when=day,
            component_id=intent.component_id, reason=intent.reason,
            values={"price": price, "quantity": intent.quantity,
                    "fees": round(fees.total, 4), "contract": str(intent.contract)},
            fraction=None, contracts=intent.quantity if intent.kind is Action.ADD else None,
        ))

        if intent.kind is Action.OPEN:
            open_trades[intent.trade_id] = OpenTrade(
                trade_id=intent.trade_id, contract=intent.contract, entry_date=day,
                entry_price=price, quantity=intent.quantity, side=intent.side,
                fees=fees.total, entry_quote=row.to_dict(),
                entry_signals={n: self.signals.on(day)[n] for n in self.signals.names},
                exit_rule=intent.exit_rule, tag=intent.tag, note=intent.note,
                entry_fee_parts=fees,
                entry_deferred=self.market.calendar.sessions_between(
                    intent.execute_on, day),
                entry_spot=_as_float(row.get("underlying_price")),
                # Favourable means the direction this position profits from: up for a
                # long call and a short put, down for a long put and a short call.
                direction=(1 if intent.contract.right is Right.CALL else -1)
                          * (1 if intent.side is Side.BUY else -1),
            )
            return None

        if intent.kind in (Action.REDUCE, Action.ADD):
            # **No trade record, and the OpenTrade is not popped.** After a
            # reduction or an addition it is still one logical trade — counted by
            # position, one trade becomes several and both the win rate and the
            # holding period are distorted.
            trade = open_trades.get(intent.trade_id)
            if trade is None:
                raise InvariantError(f"the {intent.kind.value} intent {intent.trade_id} has "
                                     f"no matching open trade")
            if intent.kind is Action.ADD:
                total = trade.entry_price * trade.quantity + price * intent.quantity
                trade.quantity += intent.quantity
                trade.entry_price = total / trade.quantity   # weighted average cost
            else:
                direction = 1 if trade.side is Side.BUY else -1
                trade.realised_gross += (
                    (price - trade.entry_price) * self.config.contract.multiplier
                    * intent.quantity * direction
                )
                trade.quantity -= intent.quantity
            trade.fees += fees.total
            self.events.record(
                EventKind.CANDIDATE_DROPPED, day,
                f"{'added' if intent.kind is Action.ADD else 'reduced by'} "
                f"{intent.quantity} contracts: {intent.reason} "
                f"(now {trade.quantity})", str(intent.contract))
            return None

        trade = open_trades.pop(intent.trade_id, None)
        if trade is None:
            raise InvariantError(f"the exit intent {intent.trade_id} has no matching "
                                 f"open trade")
        # The trade is over: its components forget it. Without this a long run keeps one
        # state entry per trade for ever, a snapshot of it grows without bound, and a
        # trade_id reused later inherits a closed trade's high-water mark.
        for bound in self._stateful_components().values():
            bound.forget(intent.trade_id)
        reason = resolve_exit_reason(list(intent.exit_reasons)) if intent.exit_reasons else ExitReason.TIME_EXIT
        return self._record(trade, exit_day=day, exit_price=price, exit_fees=fees,
                            exit_deferred=self.market.calendar.sessions_between(
                                intent.execute_on, day),
                            exit_quote=row.to_dict(), reason=reason,
                            suppressed=tuple(r for r in intent.exit_reasons if r is not reason))

    def _dispose_deliveries(self, ledger: Ledger, deliveries: dict, day: date) -> None:
        """Sell shares delivered at a physical settlement, on the session after delivery.

        A method rather than inline in the day loop so it can be driven directly. The
        tests for it previously reached for a run that happened to produce an
        in-the-money expiry, and skipped themselves when one did not occur -- which on the
        offline fixture is always, so they covered nothing at all.
        """
        for symbol in list(deliveries):
            if deliveries[symbol] >= day:
                continue
            spot = self.market.spot(day)
            if spot.is_missing:
                continue
            # ZERO_FEES, and said rather than assumed: the cost model in this system
            # prices option orders -- per contract, with an option's regulatory fees. A
            # share sale has neither, and charging an option commission on it would be a
            # made-up number in a cost model whose whole purpose is that its numbers are
            # not made up. The omission is small and it is stated.
            proceeds = ledger.liquidate_stock(symbol, spot.unwrap(), day, ZERO_FEES)
            self.events.record(
                EventKind.PASSIVE_EXPIRY, day,
                f"sold the {symbol} shares delivered on {deliveries[symbol]} at "
                f"{spot.unwrap():.2f} for {proceeds:,.2f}, with no commission -- this "
                f"cost model prices option orders and has no share rate. They were held "
                f"overnight because delivery is, so one gap in the underlying sits in "
                f"this figure and is not a cost the strategy chose",
                symbol)
            del deliveries[symbol]

    def _stateful_components(self) -> dict:
        """The bound components that keep state, by slot.

        Read from the strategy each time rather than cached: a cache would have to be
        invalidated when a component is swapped, and a stale one would snapshot the state
        of a form that is no longer running.
        """
        strategy = getattr(self, "_strategy", None)
        if strategy is None:
            return {}
        return {slot: c for slot, c in strategy.components.items()
                if getattr(getattr(c, "variant", None), "stateful", False)}

    def _early_exercise(self, pos: Position, ledger: Ledger, open_trades,
                        day: date) -> "TradeRecord | None":
        """Exercise a position early when holding is worth less than exercising.

        Returns the closed trade's record, or ``None`` when it was not exercised -- which
        is nearly always, and cheaply: the chain's flag is checked before the tree is
        built.

        **Only for a long position.** A short option is exercised *against* you by
        whoever is long it, and modelling that needs an assumption about the other side's
        behaviour -- they may exercise optimally, early, or never. That is a different
        question with a different answer, and guessing it is worse than not asking.
        """
        if self.config.contract.exercise.value != "american":
            return None
        if pos.quantity <= 0:
            return None
        trade = open_trades.get(pos.trade_id)
        if trade is None:
            return None

        frame = self.chain.series(pos.contract, [day])
        if frame.empty:
            return None
        row = frame.iloc[0]
        if "early_exercise_value" not in str(row.get("quality_flags", "")):
            return None

        spot = self.market.spot(day)
        if spot.is_missing:
            return None
        years = self.config.time.year_fraction(day, pos.contract.expiry)
        iv = float(row["iv"]) if "iv" in row and row["iv"] == row["iv"] else None
        if years <= 0 or iv is None or iv <= 0:
            return None

        from backtest.data.options.american import value

        verdict = value(
            spot=spot.unwrap(), strike=pos.contract.strike, years=years,
            rate=self.market.risk_free_rate(day), vol=iv,
            dividend_yield=self.market.dividend_yield(day, pos.contract.expiry,
                                                      spot.unwrap()),
            right=pos.contract.right,
        )
        if not verdict.exercise_now:
            # The flag fired and the tree disagreed, which is the flag doing its job:
            # it is a screen, and a screen that never over-selects is a decision.
            self.events.record(
                EventKind.EARLY_EXERCISE_RISK, day,
                f"flagged for early exercise and the tree disagrees: "
                f"{verdict.describe()}", str(pos.contract))
            return None

        self.events.record(
            EventKind.EARLY_EXERCISE_RISK, day,
            f"exercised early: {verdict.describe()}", str(pos.contract))
        self.bus.emit(
            EventType.EXPIRY, day, "ledger", trade_id=pos.trade_id,
            component_id="exercise",
            reason=f"exercised early, {verdict.describe()}",
            data={"contract": str(pos.contract), "intrinsic": verdict.intrinsic,
                  "hold": verdict.hold})

        kind = ledger.settle_expiry(pos.contract, spot, day)
        popped = open_trades.pop(pos.trade_id, None)
        if popped is None:
            return None
        quote = dict(popped.entry_quote)
        quote.update({"asof": day, "underlying_price": spot.unwrap(),
                      "bid": verdict.intrinsic, "ask": verdict.intrinsic})
        return self._record(popped, exit_day=day, exit_price=verdict.intrinsic,
                            exit_fees=None, exit_quote=quote,
                            reason=ExitReason.EARLY_EXERCISE, suppressed=())

    def _settle(self, pos: Position, ledger: Ledger, open_trades, day: date) -> TradeRecord | None:
        spot = self.market.spot(day)
        kind = ledger.settle_expiry(pos.contract, spot, day)
        trade = open_trades.pop(pos.trade_id, None)
        if trade is None:
            return None
        s = spot.unwrap()
        intrinsic = (max(s - pos.contract.strike, 0.0) if pos.contract.right is Right.CALL
                     else max(pos.contract.strike - s, 0.0))
        reason = ExitReason.EXPIRY_OTM if kind == "worthless" else ExitReason.EXPIRY_ITM
        self.events.record(EventKind.PASSIVE_EXPIRY, day,
                           f"expired passively ({kind}), intrinsic {intrinsic:.2f}",
                           str(pos.contract))
        if reason is ExitReason.EXPIRY_ITM and kind == "physical":
            # The note used to say this settled to intrinsic in cash and that physical
            # delivery was an approximation not built. The ledger delivers the shares
            # now, so the note described the opposite of what the code does -- which is
            # worse than the gap it was written for, because a reader checks the note and
            # stops. What remains true is the capital: taking delivery needs the strike
            # in cash, and the entry check sizes the trade on the premium.
            needed = (pos.contract.strike * pos.abs_quantity
                      * self.config.contract.multiplier)
            self.events.record(
                EventKind.EARLY_EXERCISE_RISK, day,
                f"settled physically: "
                f"{pos.abs_quantity * self.config.contract.multiplier} shares at "
                f"{pos.contract.strike:.2f}, needing {needed:,.0f} in cash to take "
                f"delivery, which the entry capital check does not see -- it sized this "
                f"trade on the premium. The shares are sold on the next session, so one "
                f"overnight gap in the underlying lands in the result",
                str(pos.contract))
        quote = dict(trade.entry_quote)
        quote.update({"asof": day, "underlying_price": s, "bid": intrinsic, "ask": intrinsic})
        return self._record(trade, exit_day=day, exit_price=intrinsic, exit_fees=None,
                            exit_quote=quote, reason=reason, suppressed=())

    def _record(self, trade: OpenTrade, *, exit_day, exit_price, exit_fees, exit_quote,
                reason, suppressed, exit_deferred: int = 0):
        mult = self.config.contract.multiplier
        direction = 1 if trade.side is Side.BUY else -1
        # The remaining contracts' P&L plus what reductions already banked. Without
        # the second term, a trade that was reduced reports too little P&L and raises
        # nothing — the easiest thing to omit in a change like this, and the hardest
        # to notice.
        gross = ((exit_price - trade.entry_price) * mult * trade.quantity * direction
                 + trade.realised_gross)
        # The parts, not just the total. `exit_fees` is a FeeBreakdown, or None when the
        # contract settled at expiry and nothing was traded.
        parts = (trade.entry_fee_parts or ZERO_FEES) + (exit_fees or ZERO_FEES)
        fees = parts.total
        cal = self.market.calendar
        ctx_in = trade_context(trade.entry_quote, trade.entry_date, trade.contract,
                               calendar=cal, signals=trade.entry_signals)
        ctx_out = trade_context(exit_quote, exit_day, trade.contract, calendar=cal,
                                signals={n: self.signals.on(exit_day)[n]
                                         for n in self.signals.names})
        return TradeRecord(
            trade_id=trade.trade_id, engine=ENGINE_NAME, symbol=trade.contract.symbol,
            entry_date=trade.entry_date, exit_date=exit_day, contract=trade.contract,
            quantity=trade.quantity, entry_price=trade.entry_price, exit_price=exit_price,
            multiplier=mult, gross_pnl=gross, fees=fees,
            fee_commission=parts.commission, fee_regulatory=parts.regulatory,
            fee_slippage=parts.slippage,
            net_pnl=gross - fees, exit_reason=reason, suppressed_reasons=suppressed,
            holding_days=self.market.calendar.sessions_between(trade.entry_date, exit_day),
            source=QuoteSource.SYNTHETIC
            if trade.entry_quote.get("source") == QuoteSource.SYNTHETIC.value else QuoteSource.VENDOR,
            entry_context=ctx_in, exit_context=ctx_out,
            slot_record=trade.slot_record.flat(),
            mfe=trade.mfe, mae=trade.mae,
            mfe_underlying=trade.mfe_underlying, mae_underlying=trade.mae_underlying,
            deferred_days=trade.entry_deferred + exit_deferred,
            tag=trade.tag, note=trade.note,
        )
