"""Parameter sweeps.

This is the trade-table engine's main value: hundreds of parameter sets in seconds,
where stepping day by day would take hours.

But a sweep is **the main source of overfitting**. An option strategy naturally has
many parameter dimensions (DTE, delta, take profit, stop loss, holding period,
signal parameters), and ten years of data may hold only one or two hundred
independent trades; under those conditions the "optimal parameters" a sweep finds
are almost certainly noise. So the **number of trials** is recorded here by force —
the multiple-testing correction needs it, and nobody gets to pretend they tried once.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from backtest.core.config import BacktestConfig
from backtest.data.options.chain import ChainSource
from backtest.data.market import MarketData
from backtest.signals.strategy import ExitRule
from backtest.engine.strategy import Strategy
from backtest.engine.vectorized import TradeTableEngine
from backtest.core.errors import StartupError
from backtest.research import metrics as R
from backtest.signals.contracts import SelectionRule
from backtest.signals.registry import SignalSet

#: The sweepable parameters and which rule object each belongs to.
SWEEPABLE = {
    "target_dte": "select",
    "target_delta": "select",
    "moneyness": "select",
    "hold_days": "exit",
    "take_profit": "exit",
    "stop_loss": "exit",
    "dte_floor": "exit",
}


@dataclass(frozen=True, slots=True)
class SweepResult:
    """One row per parameter set, plus the number of trials."""

    table: pd.DataFrame
    trials: int
    grid: Mapping[str, Sequence[Any]]

    def top(self, n: int = 10, by: str = "net_pnl") -> pd.DataFrame:
        """The top n sets, ordered by one column.

        **This ranking is not itself a conclusion**: whether the gap between adjacent
        ranks exceeds the noise is a separate question. This only lays the results out.
        """
        return self.table.sort_values(by, ascending=False).head(n)


def _apply(strategy: Strategy, params: Mapping[str, Any]) -> Strategy:
    select_changes = {k: v for k, v in params.items() if SWEEPABLE[k] == "select"}
    exit_changes = {k: v for k, v in params.items() if SWEEPABLE[k] == "exit"}
    new_select: SelectionRule = strategy.select
    if select_changes and callable(new_select):
        # Once selection is a function, sweeping target_dte means nothing — that
        # number lives in the function, not in the configuration. Silently skipping it
        # would leave the impression it was swept, so this refuses.
        raise StartupError(
            f"selection is a custom function, so {sorted(select_changes)} cannot be "
            f"swept — those parameters are inside your function, not in the "
            f"configuration. To sweep selection parameters, go back to a declarative "
            f"SelectionRule, or make the quantity a parameter of your function and "
            f"loop outside."
        )
    if select_changes:
        if "target_dte" in select_changes:
            dte = select_changes["target_dte"]
            lo, hi = new_select.dte_window
            if not lo <= dte <= hi:
                select_changes["dte_window"] = (max(1, dte - 9), dte + 15)
        new_select = replace(new_select, **select_changes)
    new_exit: ExitRule = replace(strategy.exit, **exit_changes) if exit_changes else strategy.exit
    return replace(strategy, select=new_select, exit=new_exit)


def sweep(
    market: MarketData,
    chain: ChainSource,
    signals: SignalSet,
    config: BacktestConfig,
    strategy: Strategy,
    grid: Mapping[str, Sequence[Any]],
) -> SweepResult:
    """Run every set in the grid; each is independent and deterministic."""
    unknown = sorted(set(grid) - set(SWEEPABLE))
    if unknown:
        raise StartupError(f"parameters {unknown} cannot be swept; sweepable: "
                           f"{sorted(SWEEPABLE)}")
    if not grid:
        raise StartupError("the parameter grid is empty")

    names = sorted(grid)
    rows: list[dict[str, Any]] = []
    for combo in itertools.product(*(grid[name] for name in names)):
        params = dict(zip(names, combo))
        variant = _apply(strategy, params)
        result = TradeTableEngine(market, chain, signals, config).run(variant)
        trades = result.trades
        summary = R.trade_stats(trades)
        rows.append(
            {
                **params,
                "count": summary["count"],
                "win_rate": summary.get("win_rate", float("nan")),
                "net_pnl": summary.get("net_pnl", 0.0),
                "avg_pnl": summary.get("net_pnl", 0.0) / summary["count"]
                if summary["count"]
                else float("nan"),
                "median_pnl": float(trades["net_pnl"].median()) if len(trades) else float("nan"),
                "peak_positions": result.capital_check()["peak_positions"],
            }
        )
    return SweepResult(pd.DataFrame(rows), trials=len(rows), grid=dict(grid))
