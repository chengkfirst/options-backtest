"""美股期权策略回测研究平台 · 阶段 1（地基 + 成交表引擎）。

用法见 :func:`run`。设计与边界见项目根目录的 ``ROADMAP.md``。
"""

from backtest.api import Result, run
from backtest.config import BacktestConfig, CostConfig, SyntheticChainConfig
from backtest.engine.sweep import SweepResult
from backtest.engine.strategy import ExitRule, Strategy, all_of, crossover, threshold
from backtest.maybe import Maybe
from backtest.selection.rules import SelectionRule
from backtest.types import ExitReason, Right

__all__ = [
    "run",
    "Result",
    "BacktestConfig",
    "CostConfig",
    "SyntheticChainConfig",
    "SelectionRule",
    "ExitRule",
    "Strategy",
    "Right",
    "ExitReason",
    "Maybe",
    "SweepResult",
    "crossover",
    "all_of",
    "threshold",
]
