"""What the trade-table engine produces.

Kept apart from the engine because they are two jobs: the engine is how the trades
are computed, this object is how they are looked at. Research handles the latter
over and over, and it should not grow with the former's complexity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from backtest.core.config import BacktestConfig
from backtest.engine.strategy import Strategy
from backtest.core.run_log import EventLog
from backtest.core.types import TRADE_COLUMNS, TradeRecord


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
