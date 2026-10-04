"""A scenario loss limit that **blocks** a trade — requirement 3.3.

## What was there and what was missing

``portfolio/scenarios.py`` builds the stress grid: underlying x volatility, each cell the
change in portfolio value. It reports. Nothing read it before placing an order, so a book
could walk into a position whose stress profile the researcher had set a limit on and the
limit would be in a report nobody consulted at the moment it mattered.

## The grid's refusal is the whole design problem here

The grid gives **no number** outside roughly a 10% move, and that is deliberate and
correct: for a buyer's book gamma is positive, and under a large shock the second-order
term overwhelms the delta term and produces results whose *direction* is wrong -- "the
underlying falls 20% and we make money". The grid returns ``NaN`` there rather than a
confidently wrong figure.

A rule reading that grid therefore faces a cell that is **unknown**, not zero. And the
easy thing to write -- ``if loss > limit: block`` -- treats a ``NaN`` as a pass, because
every comparison with ``NaN`` is false. So the one scenario a researcher most wants a
limit on, the large shock, is the one that would silently never block.

This refuses instead: an unknown cell inside the scenarios being checked is a **reason to
stop**, not a reason to proceed. Which is why ``ScenarioLimit`` takes the shocks it is
allowed to assess and refuses a configuration asking about shocks the grid cannot price.

## What it does not do

It does not size. A breach blocks the trade rather than shrinking it, because shrinking
is slot 6's decision and a rule that did both would make the two unattributable -- the
coupling the document's register keeps apart. A researcher who wants a smaller position
under stress asks slot 6 for one.

It also does not look at history. A scenario limit on a replayed 2008 is a different and
better tool (``scenario_replay``), and it needs the book to exist over that period, which
a candidate being considered today does not.

## The sticky assumption, which the document requires to be explicit

A spot shock moves the underlying. What happens to each contract's implied volatility as
it moves is **not** given by the shock -- it is an assumption, and the document names the
two:

- **sticky strike**: each strike keeps its IV. The option's moneyness changes, so its IV
  no longer sits where it did on the smile.
- **sticky delta** (sticky moneyness): the smile moves with the spot, so a contract's IV
  changes to whatever sits at its new moneyness.

The two give different scenario losses, and the register's rule is that **a risk limit uses
the more conservative of them**.

This grid is **sticky strike**, and it is sticky strike for a reason that is also its
limitation: the grid revalues from the book's aggregate greeks, and a single vega number
cannot express a smile moving. Expressing sticky delta needs per-contract IV at the shocked
moneyness, which needs a surface -- and this system has no surface builder, so the
alternative cannot be computed rather than has not been.

What that costs is directional and stated rather than estimated: for a long book, a spot
fall under sticky delta usually comes with the IV at the new moneyness being **higher**
than the IV at the old strike, so sticky strike understates the vega gain and therefore
**overstates the loss**. For a loss limit that is the conservative side -- which is the
register's requirement, met by accident of the model rather than by choice, and worth
saying so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from backtest.core.errors import StartupError

#: The shocks the second-order grid can price. Outside this it returns no number, and a
#: limit asking about a larger shock is refused rather than silently never binding.
#:
#: Read from the grid's own figure rather than written again here: the two drifting apart
#: would make a limit that looks enforced and is not.
def assessable_shock() -> float:
    from backtest.portfolio.scenarios import _large_shock

    return _large_shock()


@dataclass(frozen=True, slots=True)
class Breach:
    """One scenario whose loss exceeds the limit, or one that cannot be assessed."""

    spot_shock: float
    vol_shock: float
    #: The loss in dollars, positive. ``nan`` when the grid gave no number.
    loss: float
    limit: float
    #: Empty when the cell was priced; otherwise why it was not.
    unknown: str = ""

    def describe(self) -> str:
        where = f"spot {self.spot_shock:+.0%} / IV {self.vol_shock:+.0%}"
        if self.unknown:
            return f"{where}: {self.unknown}"
        return (f"{where}: a loss of {self.loss:,.0f} against a limit of "
                f"{self.limit:,.0f}")


#: The sticky assumption this grid makes. One value, because the grid revalues from
#: aggregate greeks and a single vega cannot express a smile that moves -- see the module
#: docstring. Named rather than implied, because the document requires the assumption to
#: be explicit and the two give different losses.
STICKY = "strike"


@dataclass(frozen=True, slots=True)
class ScenarioLimit:
    """A loss limit over a set of scenarios, checked before a trade is placed.

    ``max_loss`` is in the currency, not a fraction: a fraction of what is the question
    a fraction invites, and the two plausible answers -- of equity, of capital at risk --
    differ by a factor nobody notices until a drawdown.
    """

    max_loss: float
    #: The scenarios to check, as ``(spot shock, vol shock)`` pairs. **Required**: which
    #: shocks a strategy should survive is a research choice, and a platform-supplied set
    #: would be the platform deciding what risk means.
    scenarios: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        if self.max_loss <= 0:
            raise StartupError(
                f"max_loss={self.max_loss} blocks every trade, including the first. "
                f"A limit of zero is not a limit, it is a halt"
            )
        if not self.scenarios:
            raise StartupError(
                "a scenario limit with no scenarios blocks nothing. Which shocks a "
                "strategy should survive is a research choice, so there is no default -- "
                "for example scenarios=((-0.05, 0.50), (-0.10, 1.00))"
            )
        reach = assessable_shock()
        beyond = sorted({s for s, _ in self.scenarios if abs(s) > reach})
        if beyond:
            raise StartupError(
                f"these spot shocks are outside what the stress grid can price: "
                f"{[f'{s:+.0%}' for s in beyond]} (the grid stops at ±{reach:.0%}).\n\n"
                f"Outside that range the second-order expansion's gamma term overwhelms "
                f"the delta term and the result's **direction** is wrong -- a buyer's "
                f"book 'makes money' on a 20% fall. The grid returns no number there, "
                f"and a limit over no number would never bind: every comparison with a "
                f"missing value is false, so the scenario a limit is most wanted for "
                f"would be the one it silently ignored.\n\n"
                f"For larger shocks use `scenarios.scenario_replay`, which is real "
                f"history and contains no approximation."
            )


def check(greeks, limit: ScenarioLimit) -> tuple[Breach, ...]:
    """Every scenario in ``limit`` whose loss exceeds it, or cannot be assessed.

    ``greeks`` is a :class:`~backtest.portfolio.scenarios.PortfolioGreeks` for the book
    **as it would be** if the trade were placed -- the caller composes that, because what
    a candidate adds to the book is the engine's arithmetic and not this module's.

    An unknown cell is returned as a breach with ``unknown`` set. Reporting it as a pass
    would be the failure this module's docstring opens with; reporting it as a loss of
    zero would be worse, because it would read as a figure.
    """
    from backtest.portfolio.scenarios import stress_grid

    out: list[Breach] = []
    for spot_shock, vol_shock in limit.scenarios:
        grid = stress_grid(greeks, spot_shocks=(spot_shock,), vol_shocks=(vol_shock,))
        change = float(grid.iloc[0][f"IV{vol_shock:+.0%}"])
        if math.isnan(change):
            out.append(Breach(
                spot_shock=spot_shock, vol_shock=vol_shock, loss=float("nan"),
                limit=limit.max_loss,
                unknown="the stress grid gives no number here, so the limit cannot be "
                        "assessed. Treating that as a pass is how a limit comes to never "
                        "bind on the scenario it was written for"))
            continue
        loss = -change
        if loss > limit.max_loss:
            out.append(Breach(spot_shock=spot_shock, vol_shock=vol_shock, loss=loss,
                               limit=limit.max_loss))
    return tuple(out)


def describe(breaches: Sequence[Breach], limit: ScenarioLimit, *,
             greeks_model_derived: bool = False) -> str:
    """Why a trade was blocked, for the event log and for a person reading it.

    The sticky assumption is in every line, because a scenario loss without it is a
    number whose meaning depends on something the reader cannot see.

    ``greeks_model_derived`` says the greeks came from the same model that priced the
    chain, which is the case on a synthetic chain: the loss is then self-consistent model
    output rather than a measurement, and a reader deciding whether to act on a blocked
    trade needs that before the figure.
    """
    where = f"sticky {STICKY}"
    if greeks_model_derived:
        where += ", from model-derived greeks on a synthetic chain"
    if not breaches:
        return (f"every scenario is within the {limit.max_loss:,.0f} limit ({where})")
    lines = [f"{len(breaches)} of {len(limit.scenarios)} scenario(s) breach the "
             f"{limit.max_loss:,.0f} limit ({where}):"]
    lines += [f"  {b.describe()}" for b in breaches]
    return "\n".join(lines)
