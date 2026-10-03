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
    "run_from",
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
from backtest.signals import stop_loss as _stop_loss  # noqa: E402,F401
from backtest.signals import take_profit as _take_profit  # noqa: E402,F401
from backtest.portfolio import risk_rules as _risk_rules  # noqa: E402,F401


def run_from(path):
    """Run the backtest described by a config file.

    The file is the complete input: the same file, the same code and the same data give
    the same result, and the fingerprint written into it is the hash of exactly that.
    """
    from backtest.api.config_file import load
    from backtest.api.sdk import run as _run

    return _run(**load(path))

