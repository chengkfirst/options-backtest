"""Where each of the three kinds of failure belongs.

1. :class:`StartupError` — missing or conflicting configuration, a rule the
   engine cannot honour, an incompatible data source. Must surface before the
   first trading day.
2. Non-fatal conditions during a run — never raised; recorded in the event
   stream (:mod:`backtest.core.run_log`).
3. :class:`InvariantError` — P&L that does not balance, state that contradicts
   itself. These are code bugs: abort at once, because continuing only produces
   wrong conclusions.
"""

from __future__ import annotations


class BacktestError(Exception):
    """Base class for every exception this project raises."""


class StartupError(BacktestError):
    """A problem decidable at startup; must be raised before the run begins."""


class InvariantError(BacktestError):
    """An invariant was broken: a code bug. Abort immediately."""


class DataQualityError(BacktestError):
    """The input data itself is invalid and cannot be carried on with a flag."""
