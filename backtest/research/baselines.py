"""Baselines: separating skill from structure.

An options strategy's return has three possible sources, and the equity curve cannot tell
them apart:

1. **The underlying went up.** A long call makes money when spot rises, whether or not
   the entry timing was any good.
2. **The structure.** Buying convexity has a cost (the volatility risk premium) and a
   payoff shape. Both show up whatever the timing.
3. **The signal.** What is left once the first two are accounted for — and the only part
   that is actually yours.

A baseline isolates one of them by holding the others fixed. Two are built here, and the
architecture document names both:

- :func:`delta_equivalent` — would the underlying have done better? If a position in the
  underlying with the same directional exposure beats the option, **the option added
  nothing** and every bit of work on selection, take-profit and stop-loss was spent on a
  worse instrument.
- :func:`random_entry` — would random timing have done as well? Entries placed at the same
  frequency, with the same contract rules and the same exits, but on **dates drawn at
  random**. If the signal cannot beat that, what looks like timing is the drift plus the
  structure.

## Why this comes before refining anything

One run of each says whether the idea is worth researching further. Tuning a take-profit
level on a strategy that cannot beat random entries is work spent on a number that does
not matter. The document puts falsification first for this reason, and of the
falsification tools these two are the cheapest to run.

## Which of these survives a synthetic chain

This matters, and the two baselines differ:

- **random_entry is valid on synthetic data.** Both sides are priced by the same chain,
  so whatever is wrong with the option prices is wrong identically on both. The question
  it asks — is this timing better than chance — is answered honestly even when the prices
  are not real.
- **delta_equivalent is not.** It compares a synthetic option P&L against a **real**
  underlying move, so a chain that misprices options biases the whole comparison. On a
  synthetic chain read its direction, not its size, and do not act on it.

:attr:`Comparison.valid_on_synthetic` carries this, so the reading instruction travels
with the number rather than living in a document.

## How to read the result

The comparison is **not** "is the strategy's return higher". It is whether the difference
is larger than the spread of the baseline itself. ``random_entry`` therefore runs many
draws and reports the distribution, with the strategy's position in it as a percentile.
A strategy at the 60th percentile of random has found nothing; the honest reading of a
single random run would have been "it beat random", which is why one draw is not offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence

import numpy as np
import pandas as pd

#: How many random-entry draws by default. Fifty gives a usable percentile without the
#: run taking longer than the patience of the person waiting for it; the sample size is
#: reported so the reader can judge the resolution rather than assume it.
DRAWS = 50


@dataclass(frozen=True, slots=True)
class Comparison:
    """One baseline's verdict, with what is needed to doubt it."""

    name: str
    strategy: float
    baseline: float
    #: The spread of the baseline when it has one (random entry), else None.
    baseline_spread: tuple[float, float] | None = None
    #: Where the strategy sits in the baseline's distribution, 0 to 1.
    percentile: float | None = None
    note: str = ""
    #: Whether this comparison means anything when the option chain is synthetic. False
    #: where one side is priced by the chain and the other is not.
    valid_on_synthetic: bool = True

    @property
    def excess(self) -> float:
        return self.strategy - self.baseline

    def verdict(self) -> str:
        """The reading, in one line, with the hedge the numbers require."""
        if not self.valid_on_synthetic:
            pass  # the caller decides; report() is where the warning is printed
        if self.percentile is None:
            direction = "beat" if self.excess > 0 else "lost to"
            return (f"the strategy {direction} {self.name} by {self.excess:,.0f}"
                    + (f". {self.note}" if self.note else ""))
        if self.percentile >= 0.95:
            read = "above all but the best few draws"
        elif self.percentile >= 0.80:
            read = "high in the distribution, but not beyond its noise"
        elif self.percentile >= 0.20:
            read = "**inside** the distribution — this is what random looks like"
        else:
            read = "**below** most random draws, which is worse than no signal"
        return (f"the strategy is at the {self.percentile:.0%} percentile of {self.name}: "
                f"{read}" + (f". {self.note}" if self.note else ""))


def delta_equivalent(trades: pd.DataFrame, bars, *, multiplier: int = 100) -> Comparison:
    """What the same directional exposure in the underlying would have earned.

    For each trade, the equivalent is ``entry_delta × quantity × multiplier`` shares held
    over the same dates. That is the position with the same first-order exposure to spot
    at entry, so the comparison asks exactly one question: **did the option add anything
    over the shares?**

    What the option buys is convexity and a capped loss; what it costs is time decay and
    the spread. This number is the net of the two, in dollars.

    It needs ``entry_delta``: a trade whose delta is absent is excluded and counted,
    never treated as 0 — a delta read as 0 would contribute no equivalent position and
    silently flatter the option.
    """
    need = {"entry_date", "exit_date", "entry_delta", "quantity", "net_pnl"}
    missing = need - set(trades.columns)
    if missing:
        raise KeyError(f"delta_equivalent needs {sorted(missing)}")
    if trades.empty:
        # valid_on_synthetic is a property of **how** this comparison is made, not of
        # whether there happened to be trades. Leaving it at the default here would say
        # an empty comparison is safe on synthetic data while a populated one is not.
        return Comparison("a delta-equivalent position in the underlying", 0.0, 0.0,
                          note="no trades", valid_on_synthetic=False)

    spot = bars.traded("close")
    usable = trades.dropna(subset=["entry_delta"])
    excluded = len(trades) - len(usable)

    equivalent = 0.0
    counted = 0
    for _, t in usable.iterrows():
        a, b = pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])
        if a not in spot.index or b not in spot.index:
            continue
        shares = float(t["entry_delta"]) * int(t["quantity"]) * multiplier
        equivalent += shares * float(spot.loc[b] - spot.loc[a])
        counted += 1

    note = f"{counted} trades compared"
    if excluded:
        note += f"; {excluded} excluded for an absent entry delta"
    return Comparison(
        "a delta-equivalent position in the underlying",
        strategy=float(usable["net_pnl"].sum()),
        baseline=equivalent,
        note=note,
        # One side is priced by the chain and the other by real underlying prices, so a
        # synthetic chain biases the comparison itself.
        valid_on_synthetic=False,
    )


def random_entry_dates(
    sessions: Sequence[date], n: int, *, seed: int, avoid: Sequence[date] = ()
) -> list[date]:
    """``n`` session dates drawn without replacement, excluding ``avoid``.

    Drawn from the **sessions the strategy could have traded**, not from the calendar, so
    the baseline is not handed days the strategy never had access to.
    """
    pool = [d for d in sessions if d not in set(avoid)]
    if n > len(pool):
        n = len(pool)
    rng = np.random.default_rng(seed)
    picked = rng.choice(len(pool), size=n, replace=False)
    return sorted(pool[int(i)] for i in picked)


def random_entry(
    runner,
    *,
    n_entries: int,
    sessions: Sequence[date],
    draws: int = DRAWS,
    seed: int = 20260917,
    strategy_pnl: float,
) -> Comparison:
    """Compare the strategy against entries placed on random dates.

    ``runner(dates) -> float`` runs one draw and returns its net P&L. The caller supplies
    it because only the caller knows how to build a run — this module must not reach into
    the engine, which would put Layer 6 below Layer 5.

    Everything but the dates is held fixed: the same contract rules, the same sizing, the
    same exits, the same number of entries. **Only the timing is randomised**, so what the
    comparison isolates is the timing.

    The result is a percentile, not a win or a loss. One random draw says nothing; the
    distribution is the point.
    """
    if draws < 10:
        raise ValueError(f"{draws} draws cannot support a percentile; use at least 10")

    results: list[float] = []
    for i in range(draws):
        dates = random_entry_dates(sessions, n_entries, seed=seed + i)
        results.append(float(runner(dates)))

    arr = np.asarray(results, dtype=float)
    pct = float((arr < strategy_pnl).mean())
    return Comparison(
        "random entries with the same structure",
        strategy=strategy_pnl,
        baseline=float(np.median(arr)),
        baseline_spread=(float(np.quantile(arr, 0.05)), float(np.quantile(arr, 0.95))),
        percentile=pct,
        note=f"{draws} draws of {n_entries} entries; median {np.median(arr):,.0f}, "
             f"5th–95th {np.quantile(arr, 0.05):,.0f} to {np.quantile(arr, 0.95):,.0f}",
    )


def report(comparisons: Sequence[Comparison], *, synthetic: bool = False) -> str:
    """The baselines as one block of text, each with its verdict.

    ``synthetic`` says the option chain was synthetic, which disqualifies any comparison
    whose two sides are not priced by the same chain. The warning is printed beside that
    comparison rather than at the top, because a caveat at the top of a report is read
    once and then scrolled past.
    """
    if not comparisons:
        return "no baselines were run."
    lines = ["Baselines", "─" * 64]
    for c in comparisons:
        lines.append(f"  {c.name}")
        lines.append(f"    strategy {c.strategy:>14,.0f}")
        lines.append(f"    baseline {c.baseline:>14,.0f}   excess {c.excess:+,.0f}")
        lines.append(f"    → {c.verdict()}")
        if synthetic and not c.valid_on_synthetic:
            lines.append("    !! on a synthetic chain this comparison is biased: one "
                         "side is priced by the chain and")
            lines.append("       the other by real underlying prices. Read the "
                         "direction, not the size, and do not act on it.")
        lines.append("")
    return "\n".join(lines)
