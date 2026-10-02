"""A research platform for backtesting US equity option strategies.

For usage see :func:`run`. For the design and its boundaries see ``ROADMAP.md`` at the
project root.
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
