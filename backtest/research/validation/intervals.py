"""Confidence intervals, so a result is a range rather than a point.

## Why a point estimate is not enough here

"Net P&L 17,874" invites the question "compared with what". With 150 trades, fat tails and
a handful of them carrying most of the profit, the number that would have come out of a
slightly different sample is a long way away. A bootstrap says how far.

## Two bootstraps, and when each is wrong

:func:`bootstrap_trades` resamples trades. It is the right tool when trades are
independent — and **options trades usually are not**. Positions held at the same time move
together, so resampling them as if each were a fresh draw treats dependent observations as
independent and produces an interval that is too narrow. How much too narrow depends on the
overlap, which the anomaly scan already reports.

:func:`block_bootstrap` resamples contiguous **blocks of days** from the book's daily P&L.
Keeping a block whole keeps whatever autocorrelation is inside it, so the interval widens
to something honest. The block length has to be at least the typical holding period —
shorter and the dependence crosses a block boundary, where resampling destroys it.

So: the daily block bootstrap on the book is the default for a return or a risk number
(section 9 rule 7), and the trade bootstrap is for a per-trade statistic such as the win
rate, with its limitation stated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Interval:
    """A statistic with the range a different sample would plausibly have given."""

    name: str
    estimate: float
    low: float
    high: float
    level: float
    draws: int
    #: Set when the method is known to understate the width for this data.
    caveat: str = ""

    @property
    def crosses_zero(self) -> bool:
        """Whether the interval contains zero.

        The question a point estimate hides: a positive result whose interval crosses zero
        has not shown that the true value is positive.
        """
        return self.low <= 0.0 <= self.high

    def verdict(self) -> str:
        line = (f"{self.name}: {self.estimate:,.4g} "
                f"[{self.low:,.4g}, {self.high:,.4g}] at {self.level:.0%}")
        if self.crosses_zero:
            line += " — **the interval crosses zero**, so the sign is not established"
        if self.caveat:
            line += f". {self.caveat}"
        return line


def _percentile_interval(samples: np.ndarray, level: float) -> tuple[float, float]:
    tail = (1.0 - level) / 2.0
    return float(np.quantile(samples, tail)), float(np.quantile(samples, 1.0 - tail))


def bootstrap_trades(
    values: Sequence[float],
    *,
    statistic: Callable[[np.ndarray], float] = np.sum,
    draws: int = 2000,
    level: float = 0.95,
    seed: int = 20260917,
    name: str = "the statistic",
    overlapping: bool = True,
) -> Interval:
    """Resample trades with replacement.

    ``overlapping`` defaults True and adds the caveat, because for options it is the normal
    case and the quiet error is to forget it. Pass False only when the trades genuinely do
    not overlap — the anomaly scan reports how many of them do.
    """
    from backtest.core.thresholds import get

    floor = int(get("bootstrap_min_observations"))
    arr = np.asarray([v for v in values if np.isfinite(v)], dtype=float)
    if arr.size < floor:
        raise ValueError(
            f"{arr.size} usable values cannot support a bootstrap interval; at least "
            f"{floor} are needed. A number from fewer observations should be reported as "
            f"the observations, not as a statistic with a range"
        )
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(draws, arr.size))
    samples = np.apply_along_axis(statistic, 1, arr[idx])
    low, high = _percentile_interval(samples, level)
    return Interval(
        name=name, estimate=float(statistic(arr)), low=low, high=high, level=level,
        draws=draws,
        caveat=("trades resampled as if independent; where they overlap in time this "
                "interval is **too narrow**" if overlapping else ""),
    )


def block_bootstrap(
    daily: Sequence[float],
    *,
    block: int,
    statistic: Callable[[np.ndarray], float] = np.sum,
    draws: int = 2000,
    level: float = 0.95,
    seed: int = 20260917,
    name: str = "the statistic",
) -> Interval:
    """Resample contiguous blocks of days from the book's daily P&L.

    ``block`` must be at least the typical holding period: a block shorter than a trade's
    life cuts the dependence in half and hands back the too-narrow interval the trade
    bootstrap gives. It is required rather than defaulted, because the right value is a
    property of the strategy and a default would be a guess presented as a choice.
    """
    from backtest.core.thresholds import get

    arr = np.asarray([v for v in daily if np.isfinite(v)], dtype=float)
    if block < 1:
        raise ValueError(f"a block of {block} days is not a block")
    min_blocks = int(get("block_bootstrap_min_blocks"))
    if arr.size < block * min_blocks:
        raise ValueError(
            f"{arr.size} days cannot be resampled in blocks of {block}: fewer than "
            f"{min_blocks} blocks fit, so every draw would be nearly the same data"
        )
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(arr.size / block))
    starts = rng.integers(0, arr.size - block + 1, size=(draws, n_blocks))
    samples = np.empty(draws, dtype=float)
    for d in range(draws):
        pieces = [arr[s:s + block] for s in starts[d]]
        samples[d] = statistic(np.concatenate(pieces)[:arr.size])
    low, high = _percentile_interval(samples, level)
    return Interval(name=name, estimate=float(statistic(arr)), low=low, high=high,
                    level=level, draws=draws,
                    caveat=f"blocks of {block} days, so dependence inside a block is kept")


def report(intervals: Sequence[Interval]) -> str:
    lines = ["Confidence intervals", "─" * 68]
    for i in intervals:
        lines.append(f"  {i.verdict()}")
    if any(i.crosses_zero for i in intervals):
        lines += ["", "  An interval that crosses zero has not established a sign. That is",
                  "  a result, not a failure — it says the sample cannot answer yet."]
    return "\n".join(lines)
