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
from dataclasses import dataclass, replace
from datetime import date

import pandas as pd

from backtest.core.actions import Action, TradeAction
from backtest.core.config import BacktestConfig
from backtest.core.dataview import DataView
from backtest.core.thresholds import get as _threshold
from backtest.execution.costs import check_tradable, execute
from backtest.data.options.chain import ChainSource
from backtest.data.market import MarketData
from backtest.signals.plan import Context, Plan
from backtest.signals.review import Add, Close, PositionView, Reduce
from backtest.signals.exit_context import exit_context
from backtest.signals.strategy import ExitRule
from backtest.engine.strategy import Strategy
from backtest.core.errors import InvariantError, StartupError
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


class EventDrivenEngine:
    """The backtest engine that steps day by day."""

    def __init__(
        self,
        market: MarketData,
        chain: ChainSource,
        signals: SignalSet,
        config: BacktestConfig | None = None,
        events: EventLog | None = None,
        *,
        max_concurrent: int | None = None,
        enforce_capital: bool = True,
        limits=None,
        brake=None,
        shadow_days: int | None = None,
    ) -> None:
        self.market = market
        self.chain = chain
        self.signals = signals
        self.config = config or market.config
        self.events = events or market.events
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
    def run(self, strategy: Strategy) -> EventDrivenResult:
        self._guard(strategy)
        cal = self.market.calendar
        ledger = Ledger(self.config.notional_capital, self.config.contract)
        queue: list[Intent] = []
        open_trades: dict[int, OpenTrade] = {}
        records: list[TradeRecord] = []
        snapshots: list[Snapshot] = []
        greeks_daily: list = []
        next_trade_id = 1
        self._last_equity = float(self.config.notional_capital)
        self._peak_equity = float(self.config.notional_capital)
        self._drawdown = 0.0
        paths: list[dict] = []
        shadow: list[dict] = []
        actions: list[TradeAction] = []
        #: trade_id -> what is needed to keep quoting a contract after its trade closed.
        shadowing: dict[int, dict] = {}
        self._brake_state = "none"
        self._risk = NOTHING
        self._daily_return = 0.0
        self._prev_equity = 0.0
        self._last_greeks = None
        self._closed_pnl = records          # the same list, referenced not copied

        for day in self.market.sessions():
            # 3  fill yesterday's intents
            due, queue = [i for i in queue if i.execute_on == day], [i for i in queue if i.execute_on > day]
            for intent in due:
                closed = self._execute(intent, ledger, open_trades, day, actions)
                if closed is not None:
                    records.append(closed)
                    self._start_shadow(closed, shadowing)

            # 4  settle expiries
            for pos in list(ledger.positions):
                if pos.contract.expiry <= day:
                    rec = self._settle(pos, ledger, open_trades, day)
                    if rec is not None:
                        records.append(rec)
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

            # The risk rule acts before the per-trade rules do. It has to: a position the
            # book-wide rule is closing should not also be queued by its own stop on the
            # same day, and whichever ran first would otherwise decide the attribution.
            intents.extend(self._risk_intents(ledger, open_trades, queue, day))

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
                    intents.append(Intent(
                        contract=pos.contract,
                        side=Side.SELL if pos.quantity > 0 else Side.BUY,
                        quantity=pos.abs_quantity, created=day, execute_on=target,
                        trade_id=pos.trade_id, kind=Action.CLOSE, reason="exit",
                        exit_reasons=tuple(reasons),
                        component_id=_SLOT_FOR_EXIT.get(first, "exit"),
                    ))
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
                        intents.append(Intent(
                            contract=pos.contract,
                            side=Side.SELL if pos.quantity > 0 else Side.BUY,
                            quantity=cut, created=day, execute_on=target,
                            trade_id=pos.trade_id, kind=Action.REDUCE,
                            reason=action.reason or "reduced by review",
                            component_id="review",
                        ))
                elif isinstance(action, Add):
                    intents.append(Intent(
                        contract=pos.contract,
                        side=Side.BUY if pos.quantity > 0 else Side.SELL,
                        quantity=action.contracts, created=day, execute_on=target,
                        trade_id=pos.trade_id, kind=Action.ADD,
                        reason=action.reason or "added by review",
                        component_id="review",
                    ))

            if strategy.entry.condition(view):
                intent = self._entry_intent(day, strategy, ledger, queue, next_trade_id)
                if intent is not None:
                    intents.append(intent)
                    next_trade_id += 1

            # 8  validate and queue
            for intent in intents:
                if self._validate(intent, ledger, day):
                    queue.append(intent)

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
            self.events.record(
                EventKind.UNRESOLVED_POSITION, self.config.end,
                f"{len(ledger)} position(s) were still open when the range ended and are "
                f"not in the trade log: they have no exit, and inventing one would be "
                f"either a fill outside the range or an exit reason that did not happen",
                ", ".join(str(p.contract) for p in list(ledger.positions)[:5]))

        return EventDrivenResult(
            paths=tuple(paths), shadow=tuple(shadow), actions=tuple(actions),
            records=tuple(records), snapshots=tuple(snapshots), greeks=tuple(greeks_daily),
            config=self.config, strategy=strategy, events=self.events, ledger=ledger,
            risk_rule=self.brake,
            limits={
                "max_concurrent": self.max_concurrent,
                "enforce_capital": self.enforce_capital,
                "execution_lag_days": self.config.execution_lag_days,
                # The shadow window changes nothing about the trades, only how far past
                # each exit the prices are kept. It is in the register as an engineering
                # choice, and it is in the fingerprint because a run without it cannot
                # answer a counterfactual question a run with it can.
                "shadow_days": self.shadow_days,
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

    def _risk_intents(self, ledger, open_trades, queue, day: date) -> list[Intent]:
        """Turn the day's risk verdict into orders on the existing book.

        ``no_new_positions`` needs nothing here -- it is read at entry. ``scale_down`` and
        ``liquidate`` act on what is already held, which is what makes them different from
        a brake on sizing, and what the document means by "reduce all positions by a
        ratio" and "close the whole book".

        Every intent is credited to ``headwind``, so the decision log can tell a position
        the book-wide rule cut from one its own stop closed. They are different events and
        a trade log cannot distinguish them without this.
        """
        if self._risk.action not in (RiskAction.SCALE_DOWN, RiskAction.LIQUIDATE):
            return []
        target = self.market.calendar.shift(day, self.config.execution_lag_days)
        if target is None or target > self.config.end:
            return []
        out: list[Intent] = []
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
            out.append(Intent(
                contract=pos.contract,
                side=Side.SELL if pos.quantity > 0 else Side.BUY,
                quantity=quantity, created=day, execute_on=target,
                trade_id=pos.trade_id, kind=kind, reason=self._risk.describe(),
                exit_reasons=(ExitReason.RISK_FORCED,) if kind is Action.CLOSE else (),
                keep=self._risk.keep if kind is Action.REDUCE else None,
                component_id="headwind",
            ))
        if out:
            self.events.record(EventKind.CANDIDATE_DROPPED, day,
                               f"book-wide risk rule: {self._risk.describe()} "
                               f"({len(out)} positions)")
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
        return out

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
                premium_ratio=ratio,
                underlying_move=((spot / trade.entry_spot - 1.0) * trade.direction
                                 if math.isfinite(trade.entry_spot) and math.isfinite(spot)
                                 else float("nan")),
                days_held=cal.sessions_between(trade.entry_date, day),
                days_to_expiry=cal.sessions_between(day, pos.contract.expiry),
                iv=float(quote[2]["iv"]) if len(quote) > 2 and quote[2] is not None
                   and "iv" in quote[2] else float("nan"),
            )
            take = strategy.components.get("take_profit")
            stop = strategy.components.get("stop_loss")
            if take is not None:
                if take(ctx):
                    reasons.append(ExitReason.TAKE_PROFIT)
            elif rule.take_profit is not None and ratio >= rule.take_profit:
                reasons.append(ExitReason.TAKE_PROFIT)
            if stop is not None:
                if stop(ctx):
                    reasons.append(ExitReason.STOP_LOSS)
            elif rule.stop_loss is not None and ratio <= -rule.stop_loss:
                reasons.append(ExitReason.STOP_LOSS)
        if rule.dte_floor is not None and cal.sessions_between(day, pos.contract.expiry) <= rule.dte_floor:
            reasons.append(ExitReason.DTE_FLOOR)
        if rule.hold_days is not None and cal.sessions_between(trade.entry_date, day) >= rule.hold_days:
            reasons.append(ExitReason.TIME_EXIT)
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
        return Intent(contract=contract, side=Side.BUY, quantity=quantity,
                      created=day, execute_on=target, trade_id=trade_id,
                      kind=Action.OPEN, reason="entry", component_id="entry")

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
        reason = resolve_exit_reason(list(intent.exit_reasons)) if intent.exit_reasons else ExitReason.TIME_EXIT
        return self._record(trade, exit_day=day, exit_price=price, exit_fees=fees.total,
                            exit_quote=row.to_dict(), reason=reason,
                            suppressed=tuple(r for r in intent.exit_reasons if r is not reason))

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
        quote = dict(trade.entry_quote)
        quote.update({"asof": day, "underlying_price": s, "bid": intrinsic, "ask": intrinsic})
        return self._record(trade, exit_day=day, exit_price=intrinsic, exit_fees=0.0,
                            exit_quote=quote, reason=reason, suppressed=())

    def _record(self, trade: OpenTrade, *, exit_day, exit_price, exit_fees, exit_quote, reason, suppressed):
        mult = self.config.contract.multiplier
        direction = 1 if trade.side is Side.BUY else -1
        # The remaining contracts' P&L plus what reductions already banked. Without
        # the second term, a trade that was reduced reports too little P&L and raises
        # nothing — the easiest thing to omit in a change like this, and the hardest
        # to notice.
        gross = ((exit_price - trade.entry_price) * mult * trade.quantity * direction
                 + trade.realised_gross)
        fees = trade.fees + exit_fees
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
            fee_commission=fees, fee_regulatory=0.0, fee_slippage=0.0,
            net_pnl=gross - fees, exit_reason=reason, suppressed_reasons=suppressed,
            holding_days=self.market.calendar.sessions_between(trade.entry_date, exit_day),
            source=QuoteSource.SYNTHETIC
            if trade.entry_quote.get("source") == QuoteSource.SYNTHETIC.value else QuoteSource.VENDOR,
            entry_context=ctx_in, exit_context=ctx_out,
            mfe=trade.mfe, mae=trade.mae,
            mfe_underlying=trade.mfe_underlying, mae_underlying=trade.mae_underlying,
            tag=trade.tag, note=trade.note,
        )
