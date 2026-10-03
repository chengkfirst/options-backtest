"""A research platform for backtesting US equity option strategies.

For usage see :func:`run`. The architecture is ``Plan/backtest-system-architecture.pdf``;
what is built against it, and the boundaries that follow, are in ``requirements.md``.
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
