"""What the trade-table engine produces.

Kept apart from the engine because they are two jobs: the engine is how the trades
are computed, this object is how they are looked at. Research handles the latter
over and over, and it should not grow with the former's complexity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

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

    records: tuple[TradeRecord, ...]
    config: BacktestConfig
    strategy: Strategy
    events: EventLog
    calendar_sessions: tuple[date, ...]

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
