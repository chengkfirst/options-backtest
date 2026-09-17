"""参数扫描。

成交表引擎的主要价值：几百组参数秒级跑完，而逐日推进要几小时。

但扫描是**过拟合的主要来源**。期权策略参数维度天然多（DTE、delta、止盈、止损、
持有期、信号参数），十年数据可能只有一两百笔独立交易；在这种条件下，扫出来的"最优
参数"几乎必然是噪音。因此这里强制记录**试验次数**——研究工作台（路线图阶段 2）的
多重检验修正要用它，不让人假装只试了一次。

本阶段只产出扫描结果与试验次数；显著性评估、有效样本量、证据分级在阶段 2。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from backtest.config import BacktestConfig
from backtest.data.chain import ChainSource
from backtest.data.market import MarketData
from backtest.engine.strategy import ExitRule, Strategy
from backtest.engine.trade_table import TradeTableEngine
from backtest.errors import StartupError
from backtest.report import stats as R
from backtest.selection.rules import SelectionRule
from backtest.signals.registry import SignalSet

#: 可扫描的参数及其所属的规则对象。
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
    """每组参数一行，外加试验次数。"""

    table: pd.DataFrame
    trials: int
    grid: Mapping[str, Sequence[Any]]

    def top(self, n: int = 10, by: str = "net_pnl") -> pd.DataFrame:
        """按某列排序取前 n 组。

        **这个排名本身不构成结论**：相邻名次的差异是否超出噪音，要到阶段 2 的证据
        分级才能回答。这里只是把结果摆出来。
        """
        return self.table.sort_values(by, ascending=False).head(n)


def _apply(strategy: Strategy, params: Mapping[str, Any]) -> Strategy:
    select_changes = {k: v for k, v in params.items() if SWEEPABLE[k] == "select"}
    exit_changes = {k: v for k, v in params.items() if SWEEPABLE[k] == "exit"}
    new_select: SelectionRule = strategy.select
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
    """对参数网格逐组运行，各组独立且结果确定。"""
    unknown = sorted(set(grid) - set(SWEEPABLE))
    if unknown:
        raise StartupError(f"不可扫描的参数 {unknown}；可扫描：{sorted(SWEEPABLE)}")
    if not grid:
        raise StartupError("参数网格为空")

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
