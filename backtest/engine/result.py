"""成交表引擎的产出。

与引擎分开，是因为它们是两件事：引擎负责**怎么算出**这些交易，产出对象负责**怎么看**
它们。研究时反复摆弄的是后者，它不该跟着前者的复杂度一起长。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from backtest.config import BacktestConfig
from backtest.engine.strategy import Strategy
from backtest.events import EventLog
from backtest.types import TRADE_COLUMNS, TradeRecord


@dataclass(frozen=True, slots=True)
class TradeTableResult:
    """成交表引擎的产出。"""

    records: tuple[TradeRecord, ...]
    config: BacktestConfig
    strategy: Strategy
    events: EventLog
    calendar_sessions: tuple[date, ...]

    @property
    def trades(self) -> pd.DataFrame:
        if not self.records:
            return pd.DataFrame(columns=list(TRADE_COLUMNS))
        rows = []
        for r in self.records:
            row = {
                "trade_id": r.trade_id,
                "engine": r.engine,
                "symbol": r.symbol,
                "entry_date": r.entry_date,
                "exit_date": r.exit_date,
                "contract": str(r.contract),
                "quantity": r.quantity,
                "entry_price": r.entry_price,
                "exit_price": r.exit_price,
                "multiplier": r.multiplier,
                "gross_pnl": r.gross_pnl,
                "fees": r.fees,
                "fee_commission": r.fee_commission,
                "fee_regulatory": r.fee_regulatory,
                "fee_slippage": r.fee_slippage,
                "net_pnl": r.net_pnl,
                "exit_reason": r.exit_reason.value,
                "suppressed_reasons": "|".join(x.value for x in r.suppressed_reasons),
                "holding_days": r.holding_days,
                "source": r.source.value,
                "quote_gaps": r.quote_gaps,
                "deferred_days": r.deferred_days,
            }
            for prefix, ctx in (("entry", r.entry_context), ("exit", r.exit_context)):
                for k, v in ctx.items():
                    row[f"{prefix}_{k}"] = v.or_else(float("nan"))
            rows.append(row)
        return pd.DataFrame(rows)

    @property
    def concurrency(self) -> pd.Series:
        """每个交易日的同时持仓数。资金可行性的缺口靠它显形。"""
        counts = {d: 0 for d in self.calendar_sessions}
        for r in self.records:
            for day in self.calendar_sessions:
                if r.entry_date <= day < r.exit_date:
                    counts[day] += 1
        return pd.Series(counts, name="concurrent_positions")

    def capital_check(self) -> dict[str, float | bool]:
        """按单笔规模推算峰值资金需求，与名义资金比较。"""
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
