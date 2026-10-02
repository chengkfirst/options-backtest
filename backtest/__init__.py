"""A research platform for backtesting US equity option strategies.

For usage see :func:`run`. For the design and its boundaries see ``ROADMAP.md`` at the
project root.
"""

from backtest.api.sdk import Result, run
from backtest.core.config import BacktestConfig, CostConfig, SyntheticChainConfig
from backtest.research.sweep import SweepResult
from backtest.signals.strategy import ExitRule, all_of, crossover, threshold
from backtest.engine.strategy import Strategy
from backtest.core.maybe import Maybe
from backtest.signals.contracts import SelectionRule
from backtest.core.types import ExitReason, Right

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
