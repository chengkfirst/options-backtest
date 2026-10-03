"""The Deflated Sharpe: paying for the number of things you tried.

## The problem it solves

Try one strategy and a Sharpe of 1.5 is evidence. Try two hundred and keep the best, and a
Sharpe of 1.5 is what you would expect from noise — the maximum of two hundred draws from a
distribution centred on zero is comfortably positive. The usual significance test does not
know how many times you looked, so it reports the same p-value either way.

The Deflated Sharpe (Bailey and López de Prado) asks the right question: **given that I
tried N things, how surprising is the best one?** It does two corrections at once:

1. It subtracts the Sharpe you would expect from the best of N tries on worthless
   strategies — :func:`expected_max_sharpe`.
2. It accounts for the shape of the returns. Sharpe assumes they are roughly normal.
   A long-options book is positively skewed with fat tails, and both change how much a
   given Sharpe is worth; ignoring them overstates the significance of exactly the payoff
   shape this strategy has.

## What counts as a trial

Every parameter set, every variant, **and every grouping explored**. Stratifying results by
five different dimensions and reporting the one that separated is five trials, not one.
That is the count people undercount, which is why `research/sweep.py` records trials
automatically and why section 9 rule 2 says to count across all slots and settings.

## What it does not do

It gives a probability, not a verdict. A DSR of 0.6 is not a pass; it means that given the
search, a result this good would arise from noise four times in ten. The project's rule is
to present weak evidence and label it, not to gate on it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

#: The Euler-Mascheroni constant, from the expected maximum of N normal draws.
EULER = 0.5772156649015329


def expected_max_sharpe(trials: int, variance_of_sharpe: float) -> float:
    """The Sharpe the best of ``trials`` worthless strategies would show.

    This is the benchmark a real result has to beat. It grows with the number of trials —
    slowly, as the square root of a logarithm, which is why a hundred trials is not ten
    times worse than ten but is still meaningfully worse.
    """
    if trials < 1:
        raise ValueError(f"{trials} trials is not a search")
    if trials == 1:
        return 0.0
    if variance_of_sharpe < 0:
        raise ValueError("the variance of the Sharpe estimates cannot be negative")
    sd = math.sqrt(variance_of_sharpe)
    a = norm.ppf(1.0 - 1.0 / trials)
    b = norm.ppf(1.0 - 1.0 / (trials * math.e))
    return sd * ((1.0 - EULER) * a + EULER * b)


@dataclass(frozen=True, slots=True)
class Deflated:
    """A Sharpe, and the probability it is not noise given the search behind it."""

    sharpe: float
    trials: int
    periods: int
    benchmark: float
    probability: float
    skew: float
    kurtosis: float

    def verdict(self) -> str:
        if self.probability >= 0.95:
            read = "unlikely to be noise given the search"
        elif self.probability >= 0.80:
            read = "suggestive; not established"
        elif self.probability >= 0.50:
            read = "**not distinguishable from the best of this many tries**"
        else:
            read = "**worse than the best of this many tries would be expected to look**"
        return (f"Sharpe {self.sharpe:.2f} over {self.periods} periods, "
                f"{self.trials} trials: the best of that many worthless strategies would "
                f"show {self.benchmark:.2f}. Deflated probability "
                f"{self.probability:.0%} — {read}")


def deflated_sharpe(
    sharpe: float,
    *,
    trials: int,
    periods: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    variance_of_sharpe: float | None = None,
    sharpes: np.ndarray | None = None,
) -> Deflated:
    """The probability that ``sharpe`` is not what the search alone would produce.

    ``periods`` is the number of return observations the Sharpe was computed from, and
    ``sharpe`` must be on the same frequency — a daily Sharpe with the number of days.
    Mixing an annualised Sharpe with a count of days is the easy error and inflates the
    result, so it is checked for and refused.

    ``kurtosis`` is the **non-excess** kurtosis: 3 for a normal distribution. Pandas'
    ``.kurtosis()`` returns excess, so add 3.

    The variance across trials comes from ``sharpes`` when the individual trial results are
    available, which is the honest input. Without them, pass ``variance_of_sharpe``; with
    neither, 1/periods is used as the single-strategy variance and the result is
    conservative in the wrong direction, so passing the trials is strongly preferred.
    """
    if periods < 2:
        raise ValueError(f"{periods} periods cannot support a Sharpe")
    # A heuristic, and a deliberately tight one. With more than 60 observations the data
    # is sub-annual, where a per-period Sharpe of 0.5 is an annualised 8 — a figure that
    # has never existed. Pairing an annualised Sharpe with a count of days is the easy
    # error here and it overstates the deflated result badly, so the loose case is refused
    # rather than quietly accepted.
    from backtest.core.thresholds import get

    ceiling = get("per_period_sharpe_ceiling")
    if abs(sharpe) > ceiling and periods > 60:
        raise ValueError(
            f"a per-period Sharpe of {sharpe:.2f} over {periods} periods implies an "
            f"annualised {sharpe * math.sqrt(252):.0f}, which is almost certainly an "
            f"annualised figure paired with a count of days. Deflating that overstates "
            f"the result badly. Pass the per-period Sharpe, or pass the number of years"
        )
    if sharpes is not None:
        arr = np.asarray(sharpes, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size < 2:
            raise ValueError("at least two trial Sharpes are needed for their variance")
        var = float(np.var(arr, ddof=1))
        trials = max(trials, arr.size)
    elif variance_of_sharpe is not None:
        var = float(variance_of_sharpe)
    else:
        var = 1.0 / periods

    benchmark = expected_max_sharpe(trials, var)
    # Bailey and Lopez de Prado's probabilistic Sharpe, evaluated against the benchmark
    # rather than zero. The denominator is where skew and fat tails are paid for.
    excess_kurtosis_term = (kurtosis - 1.0) / 4.0 * sharpe ** 2
    denom = math.sqrt(max(1.0 - skew * sharpe + excess_kurtosis_term, 1e-12))
    z = (sharpe - benchmark) * math.sqrt(periods - 1) / denom
    return Deflated(sharpe=sharpe, trials=trials, periods=periods, benchmark=benchmark,
                    probability=float(norm.cdf(z)), skew=skew, kurtosis=kurtosis)
