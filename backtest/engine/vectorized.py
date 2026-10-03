"""The trade-table engine: producing a trade log through table operations rather
than a day-by-day simulation.

The insight: **an option trade with definite entry and exit rules is a table lookup,
not a simulation.** Take-profit and stop-loss are widely assumed to require stepping
bar by bar — what they actually need is that contract's daily quotes over the holding
window, and the first day that crosses the threshold. One slice replaces the loop.

**The timing convention runs throughout**: every decision happens at the close of T
and the corresponding fill at the close of T+lag. Entries and exits alike. Using T's
close for both the decision and the fill is the commonest source of look-ahead, and
it is excluded here architecturally.

**What this engine does not do**: each trade is computed independently, with no
capital constraint, no concurrency limit and no portfolio equity curve. So it answers
"does this idea have an edge", not "can this strategy be run". The series of
concurrent positions is emitted alongside, which turns the gap in capital feasibility
from invisible into obvious.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

import numpy as np
import pandas as pd

from backtest.core.config import BacktestConfig
from backtest.execution.costs import (
    FeeBreakdown,
    ZERO_FEES,
    check_tradable,
    execute,
)
from backtest.data.options.chain import ChainSource
from backtest.data.market import MarketData
from backtest.accounting.tradebook import TradeTableResult, trade_context
from backtest.engine.strategy import Strategy
from backtest.core.dataview import DataView
from backtest.core.errors import InvariantError, StartupError
from backtest.core.run_log import EventKind, EventLog
from backtest.core.maybe import Maybe
from backtest.signals.contracts import SelectionResult, select_contract
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

ENGINE_NAME = "trade_table"


@dataclass(frozen=True, slots=True)
class _ExitDecision:
    day: date
    reasons: tuple[ExitReason, ...]


class TradeTableEngine:
    """Seven steps: enumerate -> select -> entry quote -> slice -> ratios -> first
    trigger -> exit."""

    def __init__(
        self,
        market: MarketData,
        chain: ChainSource,
        signals: SignalSet,
        config: BacktestConfig | None = None,
        events: EventLog | None = None,
    ) -> None:
        self.market = market
        self.chain = chain
        self.signals = signals
        self.config = config or market.config
        self.events = events or market.events

    # ---- guards ----
    #: Strategy features this engine does not support. Returning an approximation is
    #: the worst option — the user receives a number that looks fine without knowing
    #: the rule behind it was never actually executed.
    UNSUPPORTED = {
        "roll": "rolling: the new contract depends on when the old one exits, which "
                "independent per-trade computation cannot express",
        "sizing_by_equity": "sizing as a fraction of equity: with each trade computed "
                            "independently there is no current equity",
        "portfolio_limits": "portfolio exposure limits: these need state across positions",
        "hedge": "hedging across positions: this needs a portfolio view",
        "max_concurrent": "a concurrency limit: trades are computed independently and "
                          "know nothing of each other",
    }

    def _guard(self, strategy: Strategy) -> None:
        """Refuse a configuration this engine cannot handle correctly, rather than
        returning an approximation."""
        if strategy.review is not None:
            raise StartupError(
                "a daily review needs the event-driven engine. The trade-table engine "
                "computes exits vectorised — the first trigger over the whole holding "
                "window at once — so there is no 'each day' step at all. Use "
                "engine='event_driven'."
            )
        if strategy.plan is not None:
            raise StartupError(
                "a plan-based strategy needs the event-driven engine. The trade-table "
                "engine treats trades independently and computes exits vectorised, so "
                "it cannot express 'this trade has these exit rules' — which is the "
                "whole point of a plan. Use engine='event_driven'."
            )
        self.signals.require(list(strategy.entry.requires))
        if strategy.exit.hold_days is None and strategy.exit.dte_floor is None:
            raise StartupError("no time-based exit, so positions get dragged into expiry")

        # A one-day lag means the decision is taken with dte_floor days left and the
        # fill happens with dte_floor-1 left. Below lag+1 the fill day lands on or
        # after the expiry, the exit intent comes to nothing, and the position is
        # silently dragged into settlement.
        lag = self.config.execution_lag_days
        floor = strategy.exit.dte_floor
        if floor is not None and floor < lag + 1:
            raise StartupError(
                f"the days-to-expiry floor of {floor} trading days is too small: with "
                f"an execution lag of {lag} days the fill is {lag} days after the "
                f"decision, so the floor must be at least {lag + 1}. Otherwise the "
                f"exit intent comes to nothing and the position is silently dragged "
                f"into expiry settlement."
            )
        if strategy.sizing.method != "fixed":
            raise StartupError(
                f"the trade-table engine supports only a fixed number of contracts: "
                f"trades are computed independently, there is no current equity, and "
                f"so {strategy.sizing.method} has nothing to compute from. Use "
                f"engine=\"event_driven\"."
            )
        for feature, why in self.UNSUPPORTED.items():
            if getattr(strategy, feature, None):
                raise StartupError(
                    f"the trade-table engine does not support {feature} ({why}); this "
                    f"strategy needs the event-driven engine"
                )

    # ---- the main flow ----
    def run(self, strategy: Strategy) -> "TradeTableResult":
        self._guard(strategy)
        sessions = self.market.sessions()
        records: list[TradeRecord] = []
        trade_id = 0

        #: Accepted trades, used to tell whether a contract is already held. The
        #: trade table is otherwise stateless; this is the one exception, and it exists
        #: so the two engines agree semantically.
        held: list[TradeRecord] = []

        for signal_day in sessions:
            view = self.signals.on(signal_day)
            if not strategy.entry.condition(view):
                continue

            entry_day = self.market.calendar.shift(signal_day, self.config.execution_lag_days)
            if entry_day is None or entry_day > self.config.end:
                self.events.record(
                    EventKind.CANDIDATE_DROPPED, signal_day,
                    "the execution day falls outside the backtest range"
                )
                continue

            record = self._build_trade(trade_id + 1, signal_day, entry_day, strategy)
            if record is None:
                continue
            if not strategy.allow_stacking and any(
                r.contract == record.contract and r.entry_date <= entry_day < r.exit_date
                for r in held
            ):
                self.events.record(
                    EventKind.CANDIDATE_DROPPED, entry_day,
                    "this contract is already held and allow_stacking is false",
                    str(record.contract))
                continue
            trade_id += 1
            record = replace(record, trade_id=trade_id)
            records.append(record)
            held.append(record)

        return TradeTableResult(
            records=tuple(records),
            config=self.config,
            strategy=strategy,
            events=self.events,
            limits={
                # This engine has no capital or concurrency constraint by construction,
                # recorded rather than left out: "not applied" and "not recorded" look the
                # same in a fingerprint otherwise.
                "max_concurrent": None,
                "enforce_capital": False,
                "execution_lag_days": self.config.execution_lag_days,
            },
            calendar_sessions=sessions,
        )

    # ---- one trade ----
    def _build_trade(
        self, trade_id: int, signal_day: date, entry_day: date, strategy: Strategy
    ) -> TradeRecord | None:
        selection = select_contract(
            self.chain.snapshot(entry_day),
            strategy.select,
            self.config,
            self.market.calendar,
            entry_day,
            # The chain is the execution day's, the view cut at the **decision day**.
            # A custom selector may decide what kind of contract to want from what T
            # showed, but must not see T+1's market — that would be picking a strike out
            # of the fill day's move. The cut is what makes that a raise rather than a
            # convention.
            DataView.at(signal_day, market=self.market, signals=self.signals,
                        chain=self.chain),
        )
        if not selection.ok:
            self.events.record(
                EventKind.SELECTION_FAILED, entry_day, selection.describe()
            )
            return None

        contract = selection.contract.unwrap()
        entry_quote = selection.quote
        assert entry_quote is not None
        tradable = check_tradable(entry_quote["bid"], entry_quote["ask"], self.config.costs)
        if not tradable.ok:
            self.events.record(
                EventKind.NOT_TRADABLE, entry_day,
                f"the entry is untradable: {tradable.reason}", str(contract)
            )
            return None

        entry_price, entry_fees = execute(
            bid=entry_quote["bid"],
            ask=entry_quote["ask"],
            side=Side.BUY,
            quantity=strategy.quantity,
            multiplier=self.config.contract.multiplier,
            costs=self.config.costs,
        )

        window = self._holding_window(entry_day, contract, strategy)
        quotes = self.chain.series(contract, window)
        gaps = len(window) - len(quotes)
        if gaps:
            self.events.record(
                EventKind.QUOTE_MISSING, entry_day,
                f"{gaps} days without a quote inside the holding window", str(contract)
            )

        decision = self._first_trigger(entry_day, entry_price, contract, quotes, strategy, window)
        return self._close_trade(
            trade_id=trade_id,
            contract=contract,
            entry_day=entry_day,
            entry_price=entry_price,
            entry_fees=entry_fees,
            entry_quote=entry_quote,
            decision=decision,
            quotes=quotes,
            strategy=strategy,
            gaps=gaps,
        )

    def _holding_window(self, entry_day: date, contract: ContractId, strategy: Strategy) -> list[date]:
        """The quote window: from the entry day to the expiry.

        It is one execution lag longer than the **decision** window — a decision
        happens at one close and the fill at the next session's close, so the fill
        day's quote has to be in this same slice; otherwise every trade needs an extra
        query and the window boundary no longer matches the execution boundary.
        """
        cal = self.market.calendar
        last = contract.expiry
        if strategy.exit.hold_days is not None:
            capped = cal.shift(entry_day, strategy.exit.hold_days + self.config.execution_lag_days)
            if capped is not None:
                last = min(last, capped)
        return [d for d in cal.sessions_in(entry_day, last)]

    def _decision_window(self, window: list[date], entry_day: date, strategy: Strategy) -> list[date]:
        """The decision window: the quote window minus the days reserved at the end
        for execution."""
        cal = self.market.calendar
        if strategy.exit.hold_days is None:
            return [d for d in window if d > entry_day]
        limit = cal.shift(entry_day, strategy.exit.hold_days)
        return [d for d in window if entry_day < d <= (limit or window[-1])]

    def _exit_price_of(self, row: pd.Series) -> Maybe[float]:
        """The exit price: selling, so the adverse side (the bid)."""
        tradable = check_tradable(row["bid"], row["ask"], self.config.costs)
        if not tradable.ok:
            return Maybe.missing(tradable.reason or "untradable")
        from backtest.execution.costs import fill_price

        return Maybe.some(fill_price(row["bid"], row["ask"], Side.SELL, self.config.costs))

    def _first_trigger(
        self,
        entry_day: date,
        entry_price: float,
        contract: ContractId,
        quotes: pd.DataFrame,
        strategy: Strategy,
        window: list[date],
    ) -> _ExitDecision:
        """Find the **first** exit condition to fire inside the holding window.

        A decision happens at a close, so what is returned here is the decision day;
        the fill happens on the decision day plus the execution lag.
        """
        rule = strategy.exit
        cal = self.market.calendar
        if quotes.empty:
            return _ExitDecision(entry_day, (ExitReason.TIME_EXIT,))

        decision_days = set(self._decision_window(window, entry_day, strategy))
        asof = quotes["asof"].map(lambda x: x if isinstance(x, date) else pd.Timestamp(x).date())
        frame = quotes.loc[asof.isin(decision_days)].copy()
        if frame.empty:
            return _ExitDecision(entry_day, (ExitReason.TIME_EXIT,))

        exit_prices = frame.apply(self._exit_price_of, axis=1)
        ratios = np.array(
            [
                (p.unwrap() / entry_price - 1.0) if p.is_present else np.nan
                for p in exit_prices
            ]
        )
        days = [d if isinstance(d, date) else pd.Timestamp(d).date() for d in frame["asof"]]

        triggers: list[tuple[int, ExitReason]] = []
        if rule.take_profit is not None:
            hit = np.where(ratios >= rule.take_profit)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.TAKE_PROFIT))
        if rule.stop_loss is not None:
            hit = np.where(ratios <= -rule.stop_loss)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.STOP_LOSS))
        if rule.dte_floor is not None:
            remaining = np.array([cal.sessions_between(d, contract.expiry) for d in days])
            hit = np.where(remaining <= rule.dte_floor)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.DTE_FLOOR))
        if rule.hold_days is not None:
            held = np.array([cal.sessions_between(entry_day, d) for d in days])
            hit = np.where(held >= rule.hold_days)[0]
            if hit.size:
                triggers.append((int(hit[0]), ExitReason.TIME_EXIT))

        if not triggers:
            # Nothing fired: the last day of the window is the decision day.
            return _ExitDecision(days[-1], (ExitReason.TIME_EXIT,))

        earliest = min(idx for idx, _ in triggers)
        same_day = tuple(reason for idx, reason in triggers if idx == earliest)
        return _ExitDecision(days[earliest], same_day)

    def _close_trade(
        self,
        *,
        trade_id: int,
        contract: ContractId,
        entry_day: date,
        entry_price: float,
        entry_fees: FeeBreakdown,
        entry_quote: dict,
        decision: _ExitDecision,
        quotes: pd.DataFrame,
        strategy: Strategy,
        gaps: int,
    ) -> TradeRecord | None:
        cal = self.market.calendar
        lag = self.config.execution_lag_days
        multiplier = self.config.contract.multiplier
        qty = strategy.quantity

        settle_day = cal.shift(decision.day, lag)
        reasons = list(decision.reasons)
        deferred = 0
        exit_price: float | None = None
        exit_row: pd.Series | None = None

        # After the decision day, find a tradable day at the execution lag; defer
        # when it is untradable.
        candidates = [d for d in cal.sessions_in(settle_day or decision.day, contract.expiry)] if settle_day else []
        for day in candidates:
            if day > self.config.end:
                break
            row = quotes.loc[quotes["asof"].map(lambda x: (x if isinstance(x, date) else pd.Timestamp(x).date())) == day]
            if row.empty:
                fresh = self.chain.series(contract, [day])
                if fresh.empty:
                    deferred += 1
                    continue
                row = fresh
            price = self._exit_price_of(row.iloc[0])
            if price.is_present:
                exit_price = price.unwrap()
                exit_row = row.iloc[0]
                exit_day = day
                break
            deferred += 1

        if exit_price is None:
            # Still unfilled at expiry -> a passive expiry, settled at intrinsic value.
            return self._settle_at_expiry(
                trade_id=trade_id,
                contract=contract,
                entry_day=entry_day,
                entry_price=entry_price,
                entry_fees=entry_fees,
                entry_quote=entry_quote,
                strategy=strategy,
                gaps=gaps,
                deferred=deferred,
            )

        if deferred:
            self.events.record(
                EventKind.EXIT_DEFERRED, decision.day,
                f"the exit was deferred {deferred} trading days", str(contract)
            )

        _, exit_fees = execute(
            bid=exit_row["bid"],
            ask=exit_row["ask"],
            side=Side.SELL,
            quantity=qty,
            multiplier=multiplier,
            costs=self.config.costs,
        )
        return self._record(
            trade_id=trade_id,
            contract=contract,
            entry_day=entry_day,
            exit_day=exit_day,
            entry_price=entry_price,
            exit_price=exit_price,
            fees=entry_fees + exit_fees,
            reason=resolve_exit_reason(reasons),
            suppressed=tuple(r for r in reasons if r is not resolve_exit_reason(reasons)),
            entry_quote=entry_quote,
            exit_quote=exit_row.to_dict(),
            strategy=strategy,
            gaps=gaps,
            deferred=deferred,
        )

    def _settle_at_expiry(
        self, *, trade_id, contract, entry_day, entry_price, entry_fees, entry_quote, strategy, gaps, deferred
    ) -> TradeRecord | None:
        spot = self.market.spot(contract.expiry)
        if spot.is_missing:
            self.events.record(
                EventKind.QUOTE_MISSING, contract.expiry,
                "no underlying close on the expiry day, so this trade is void",
                str(contract)
            )
            return None
        s = spot.unwrap()
        intrinsic = (
            max(s - contract.strike, 0.0)
            if contract.right is Right.CALL
            else max(contract.strike - s, 0.0)
        )
        itm = intrinsic > 0
        reason = ExitReason.EXPIRY_ITM if itm else ExitReason.EXPIRY_OTM
        self.events.record(
            EventKind.PASSIVE_EXPIRY,
            contract.expiry,
            f"expired passively ({'in' if itm else 'out of'} the money), intrinsic "
            f"{intrinsic:.2f}",
            str(contract),
        )
        exit_quote = dict(entry_quote)
        exit_quote.update({"asof": contract.expiry, "underlying_price": s, "bid": intrinsic, "ask": intrinsic})
        return self._record(
            trade_id=trade_id,
            contract=contract,
            entry_day=entry_day,
            exit_day=contract.expiry,
            entry_price=entry_price,
            exit_price=intrinsic,
            fees=entry_fees,
            reason=reason,
            suppressed=(),
            entry_quote=entry_quote,
            exit_quote=exit_quote,
            strategy=strategy,
            gaps=gaps,
            deferred=deferred,
        )

    def _trade_context(self, quote: dict, day: date, contract: ContractId):
        """Both engines build the context with the same function, so they cannot drift
        apart on the columns the research layer slices on."""
        view = self.signals.on(day)
        return trade_context(
            quote, day, contract,
            calendar=self.market.calendar,
            signals={name: view[name] for name in self.signals.names},
        )

    def _record(
        self, *, trade_id, contract, entry_day, exit_day, entry_price, exit_price,
        fees: FeeBreakdown, reason, suppressed, entry_quote, exit_quote, strategy, gaps, deferred
    ) -> TradeRecord:
        multiplier = self.config.contract.multiplier
        qty = strategy.quantity
        gross = (exit_price - entry_price) * multiplier * qty
        net = gross - fees.total
        if not np.isfinite(net):
            raise InvariantError(f"trade {trade_id} has a non-finite P&L")
        return TradeRecord(
            trade_id=trade_id,
            engine=ENGINE_NAME,
            symbol=contract.symbol,
            entry_date=entry_day,
            exit_date=exit_day,
            contract=contract,
            quantity=qty,
            entry_price=entry_price,
            exit_price=exit_price,
            multiplier=multiplier,
            gross_pnl=gross,
            fees=fees.total,
            fee_commission=fees.commission,
            fee_regulatory=fees.regulatory,
            fee_slippage=fees.slippage,
            net_pnl=net,
            exit_reason=reason,
            suppressed_reasons=tuple(suppressed),
            holding_days=self.market.calendar.sessions_between(entry_day, exit_day),
            source=QuoteSource.SYNTHETIC
            if entry_quote.get("source") == QuoteSource.SYNTHETIC.value
            else QuoteSource.VENDOR,
            entry_context=self._trade_context(entry_quote, entry_day, contract),
            exit_context=self._trade_context(exit_quote, exit_day, contract),
            quote_gaps=gaps,
            deferred_days=deferred,
        )
