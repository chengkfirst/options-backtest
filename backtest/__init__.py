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

# The shipped component forms. Imported here, at the composition layer, because the
# registry lives in Layer 0 and the forms live in Layer 2: Layer 0 cannot reach them
# without inverting the dependency direction. Importing them at the call site instead
# would make "is this variant registered" depend on import order.
from backtest.signals import variants as _variants  # noqa: E402,F401
