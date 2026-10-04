"""What the trade-table engine produces.

Kept apart from the engine because they are two jobs: the engine is how the trades
are computed, this object is how they are looked at. Research handles the latter
over and over, and it should not grow with the former's complexity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, ClassVar, Mapping

import numpy as np
import pandas as pd

from backtest.accounting.ledger import Ledger, Snapshot
from backtest.core.clock import TradingCalendar
from backtest.core.config import BacktestConfig
from backtest.core.maybe import Maybe
from backtest.core.run_log import EventLog
from backtest.core.types import TRADE_COLUMNS, ContractId, TradeRecord
from backtest.engine.strategy import Strategy


def trades_frame(records: "tuple[TradeRecord, ...]") -> pd.DataFrame:
    """Trade records -> DataFrame.

    **Both engines share this one function**, so the table the research layer gets
    looks exactly the same and need not know which engine produced it. This is where
    the one-trade-log contract actually lands.
    """
    if not records:
        return pd.DataFrame(columns=list(TRADE_COLUMNS))
    rows = []
    for r in records:
        row = {
            "trade_id": r.trade_id, "engine": r.engine, "symbol": r.symbol,
            "entry_date": r.entry_date, "exit_date": r.exit_date,
            "contract": str(r.contract), "quantity": r.quantity,
            "entry_price": r.entry_price, "exit_price": r.exit_price,
            "multiplier": r.multiplier, "gross_pnl": r.gross_pnl, "fees": r.fees,
            "fee_commission": r.fee_commission, "fee_regulatory": r.fee_regulatory,
            "fee_slippage": r.fee_slippage, "net_pnl": r.net_pnl,
            "exit_reason": r.exit_reason.value,
            "suppressed_reasons": "|".join(x.value for x in r.suppressed_reasons),
            "holding_days": r.holding_days, "source": r.source.value,
            "quote_gaps": r.quote_gaps, "deferred_days": r.deferred_days,
            "mfe": r.mfe, "mae": r.mae,
            "mfe_underlying": r.mfe_underlying, "mae_underlying": r.mae_underlying,
            "tag": r.tag, "note": r.note,
        }
        for prefix, ctx in (("entry", r.entry_context), ("exit", r.exit_context)):
            for k, v in ctx.items():
                row[f"{prefix}_{k}"] = v.or_else(float("nan"))
        rows.append(row)
    return pd.DataFrame(rows)


@dataclass(frozen=True, slots=True)
class TradeTableResult:
    """What the trade-table engine produces."""

    #: Which engine produced it. Part of the run fingerprint: the same strategy on the
    #: same data computed two ways is two results, not one.
    ENGINE: ClassVar[str] = "trade_table"

    records: tuple[TradeRecord, ...]
    config: BacktestConfig
    strategy: Strategy
    events: EventLog
    calendar_sessions: tuple[date, ...]
    #: **The limits the run actually ran under**, as the engine saw them. In the
    #: fingerprint because they change which trades happen -- a concurrency limit is not
    #: a presentation choice. Each engine fills what applies to it; this one has no
    #: capital or concurrency constraint by construction.
    limits: Mapping[str, Any] = field(default_factory=dict)

    @property
    def trades(self) -> pd.DataFrame:
        return trades_frame(self.records)

    @property
    def concurrency(self) -> pd.Series:
        """Concurrent positions on each trading day. This is what makes the gap in
        capital feasibility visible."""
        counts = {d: 0 for d in self.calendar_sessions}
        for r in self.records:
            for day in self.calendar_sessions:
                if r.entry_date <= day < r.exit_date:
                    counts[day] += 1
        return pd.Series(counts, name="concurrent_positions")

    def capital_check(self) -> dict[str, float | bool]:
        """Peak capital required, inferred from per-trade size, against the
        notional."""
        if not self.records:
            return {"peak_positions": 0, "peak_capital": 0.0, "feasible": True}
        peak = int(self.concurrency.max())
        per_trade = max(
            r.entry_price * r.multiplier * r.quantity for r in self.records
        )
        need = peak * per_trade
        return {
            "peak_positions": peak,
            "peak_capital": need,
            "notional_capital": self.config.notional_capital,
            "feasible": need <= self.config.notional_capital,
        }


@dataclass(frozen=True, slots=True)
class EventDrivenResult:
    #: Which engine produced it. See :attr:`TradeTableResult.ENGINE`.
    ENGINE: ClassVar[str] = "event_driven"

    records: tuple[TradeRecord, ...]
    snapshots: tuple[Snapshot, ...]
    config: BacktestConfig
    strategy: Strategy
    events: EventLog
    ledger: Ledger
    #: Daily portfolio greeks. It has a default, so it must follow the fields
    #: that do not.
    greeks: tuple = ()
    #: **The daily position trajectory**: every held position's state each day. A
    #: hard prerequisite for headwind research (5) — with only an entry and an exit
    #: point there is no way to see what happened in between. It cannot be
    #: reconstructed afterwards, so it is collected from the start.
    paths: tuple = ()
    #: **What each closed trade's contract did afterwards.** The mirror of ``paths``, and
    #: the one thing counterfactual exit research cannot do without: after a run there is
    #: no record of the price after the exit, so "would a wider stop have worked" is
    #: unanswerable without this.
    shadow: tuple = ()
    #: **The decision log.** One :class:`~backtest.core.actions.TradeAction` per fill,
    #: carrying the slot that asked for it and the values it acted on. This is what makes
    #: a fill traceable back to a decision, and the prerequisite for attribution by
    #: component — without it, "which slot closed this trade" has no answer.
    actions: tuple = ()
    #: The limits the run ran under. See :attr:`TradeTableResult.limits`.
    limits: Mapping[str, Any] = field(default_factory=dict)
    #: The book-wide risk rule, if one ran. In the fingerprint: it changes which trades
    #: happen and how large they are.
    risk_rule: Any = None
    #: **The event stream.** Carried on the result rather than discarded with the engine:
    #: a causal chain that only exists during the run answers requirement 0.6 for nobody.
    #: ``result.bus.trace(fill_id)`` is how a fill is followed back to the component that
    #: decided it.
    bus: Any = None
    #: The run's state at its last session, when the run was asked to stop there.
    #: None for a whole run: a checkpoint of a finished run is a thing nobody can
    #: continue, and offering one would invite the attempt.
    checkpoint: Any = None

    @property
    def actions_frame(self):
        """The decision log as a table. One row = one fill, with the slot that caused it."""
        import pandas as pd

        if not self.actions:
            return pd.DataFrame(columns=["trade_id", "date", "action", "component_id",
                                         "reason", "price", "quantity", "contract"])
        return pd.DataFrame([{
            "trade_id": a.trade_id, "date": a.when, "action": a.action.value,
            "component_id": a.component_id, "reason": a.reason,
            **{k: v for k, v in a.values.items()},
        } for a in self.actions])

    def trace(self, trade_id: int):
        """Every decision that touched one trade, in order.

        The document's traceability requirement is a fill traced back through order and
        signal to the component. This is the part of that chain the engine can supply:
        there are no orders or targets as events yet, so the chain is fill -> action ->
        slot rather than the full five links.
        """
        frame = self.actions_frame
        if frame.empty:
            return frame
        return frame.loc[frame["trade_id"] == trade_id].sort_values("date")

    @property
    def shadow_frame(self):
        """Post-exit quotes. One row = one closed trade on one day after its exit."""
        import pandas as pd

        if not self.shadow:
            return pd.DataFrame(columns=[
                "trade_id", "date", "days_after_exit", "mark", "bid", "ask", "ratio",
                "vs_exit", "underlying", "iv"])
        return pd.DataFrame(list(self.shadow))

    @property
    def paths_frame(self):
        """The daily trajectory table. One row = one trade on one day."""
        import pandas as pd

        if not self.paths:
            return pd.DataFrame(columns=[
                "trade_id", "date", "days_held", "dte", "mark", "ratio",
                "mfe_so_far", "mae_so_far", "underlying", "iv", "delta", "tag"])
        return pd.DataFrame(list(self.paths))

    @property
    def trades(self) -> pd.DataFrame:
        """The same table the trade-table engine produces — the research layer need
        not know which engine ran."""
        return trades_frame(self.records)

    @property
    def greeks_frame(self) -> pd.DataFrame:
        """Daily portfolio greeks. Flat days are present too, at 0 — a missing row
        and "no exposure that day" are different things."""
        if not self.greeks:
            return pd.DataFrame()
        return pd.DataFrame([g.as_dict() for g in self.greeks]).set_index("date")

    @property
    def concurrency(self) -> pd.Series:
        """Concurrent positions per day. This engine actually knows, where the trade
        table has to infer it afterwards."""
        return pd.Series(
            {s.day: s.n_positions for s in self.snapshots}, name="concurrent_positions"
        )

    def capital_check(self) -> dict:
        """Whether capital sufficed. This engine already enforced the constraint, so
        this is confirmation after the fact."""
        peak = max((s.n_positions for s in self.snapshots), default=0)
        peak_deployed = max((s.deployed for s in self.snapshots), default=0.0)
        return {
            "peak_positions": peak, "peak_capital": peak_deployed,
            "notional_capital": self.config.notional_capital,
            "feasible": peak_deployed <= self.config.notional_capital,
        }

    @property
    def equity(self) -> pd.DataFrame:
        """Both equity curves plus capital utilisation."""
        return pd.DataFrame(
            [
                {
                    "date": s.day, "cash": s.cash,
                    "equity_mid": s.equity_mid, "equity_liq": s.equity_liq,
                    "option_value": s.option_value_mid, "stock_value": s.stock_value,
                    "positions": s.n_positions, "deployed": s.deployed,
                }
                for s in self.snapshots
            ]
        ).set_index("date")


def trade_context(
    quote: dict,
    day: date,
    contract: ContractId,
    *,
    calendar: TradingCalendar,
    signals: Mapping[str, Maybe[float]],
) -> dict[str, Maybe[float]]:
    """The context at entry and at exit, for grouping the analysis any way afterwards
    without rerunning the backtest.

    Two sources. The **chain** gives the contract's own state (IV, delta, moneyness);
    the **signal layer** gives the underlying's state at the time (moving averages, RSI,
    volatility). The second cannot be dropped — "what was RSI at entry" is one of the
    commonest cuts in grouped analysis, and it cannot be reconstructed afterwards.

    **Both engines call this one function.** They used to build the context separately,
    with identical behaviour and no mechanism keeping it that way. The two engines being
    required to produce row-identical trade logs is the project's main cross-check, and
    the context columns are part of that log — so a drift here would have quietly
    weakened the check that is supposed to catch drift. One function, no drift.

    Every value is a ``Maybe``: an absent delta must never arrive downstream as 0.
    """
    def numeric(key: str) -> Maybe[float]:
        value = quote.get(key)
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return Maybe.missing(f"{key} is unavailable")
        return Maybe.some(float(value))

    spot = numeric("underlying_price")
    context: dict[str, Maybe[float]] = {
        "underlying_price": spot,
        "iv": numeric("iv"),
        "delta": numeric("delta"),
        "gamma": numeric("gamma"),
        "vega": numeric("vega"),
        "theta": numeric("theta"),
        "moneyness": (Maybe.some(float(spot.unwrap() / contract.strike - 1.0))
                      if spot.is_present
                      else Maybe.missing("the underlying price is unavailable")),
        "dte_calendar": Maybe.some(float((contract.expiry - day).days)),
        "dte_trading": Maybe.some(
            float(calendar.sessions_between(day, contract.expiry))),
    }
    for name, value in signals.items():
        context[f"sig_{name}"] = value
    return context
