"""Detecting overfitting: the research protocol as functions rather than discipline.

Section 9 of the research questions lists seven rules. Six of them were held by a person
remembering them. These modules make four checkable:

- :mod:`.splits` — walk-forward folds and purged k-fold, so a result is scored on data the
  fit never saw, and so the purge that overlapping option trades require is applied rather
  than assumed
- :mod:`.intervals` — bootstrap confidence intervals, by trade and by block, so a result is
  reported as a range rather than a point
- :mod:`.deflated` — the Deflated Sharpe, so the number of things tried is paid for

## Why this matters more here than for most strategies

A low-frequency long-options strategy produces few trades, they overlap in time, and the
payoff is fat-tailed and positively skewed. Every one of those breaks an assumption the
usual tools rest on: few trades make a point estimate meaningless, overlap makes an
ordinary bootstrap treat dependent samples as independent, and skew makes Sharpe the wrong
objective. So the corrections are not refinements here; without them the numbers are
wrong in a known direction.
"""

from backtest.research.validation.deflated import deflated_sharpe, expected_max_sharpe
from backtest.research.validation.intervals import block_bootstrap, bootstrap_trades
from backtest.research.validation.splits import Fold, purged_kfold, walk_forward

__all__ = [
    "Fold", "walk_forward", "purged_kfold",
    "bootstrap_trades", "block_bootstrap",
    "deflated_sharpe", "expected_max_sharpe",
]
