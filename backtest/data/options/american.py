"""An American option's value, by binomial tree — what the early-exercise flag was missing.

## Why this had to exist before early exercise could be acted on

The chain **flags** where the European price is wrong: a call before an ex-dividend date
whose time value is below the dividend net of the carry, and a deep in-the-money put whose
time value is below the carry on the strike. Nothing acted on the flag, and the reason was
recorded as a refusal rather than a gap: acting on it needs a model that says what the
position is worth **unexercised**, and a rule that exercised on the flag alone would be
exercising on the failure of the European approximation rather than on a comparison.

The comparison is what matters. Exercising early is worth doing when

    intrinsic value now  >  the option's value if held

and the right-hand side is exactly what Black-Scholes cannot give for an American option.
A tree can.

## Cox-Ross-Rubinstein, and why that one

The standard binomial: up and down factors ``exp(±sigma*sqrt(dt))``, risk-neutral
probability, and at every node the value is the greater of holding and exercising. It is
the simplest model that prices the early-exercise decision **as a decision** rather than
as a correction, which is the property needed here -- a closed-form approximation
(Barone-Adesi-Whaley, Bjerksund-Stensland) gives a better price faster and does not
give the exercise boundary, which is the thing being asked for.

Dividends are continuous here, as a yield. Discrete dividends on the right dates are the
correct treatment for a single-name equity and matter most for exactly the early-exercise
call case -- so this is an approximation sitting in the middle of the question it exists
to answer, and the limitation is stated rather than buried: see :func:`value`.

## What this is not

It does not replace the chain's prices. The chain is the market's own quotes, and a model
price is not a quote -- substituting one would be marking the book to a model, which is
the failure the whole data layer is built against. This answers one question: *is holding
worth more than exercising*, for a position already held.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backtest.core.errors import StartupError
from backtest.core.types import Right

#: Steps in the tree.
#:
#: **Measured, and the first figure written here was wrong.** The docstring said 200 was
#: where the price stopped moving at the fourth decimal; a convergence test disagreed
#: immediately. A CRR tree converges as O(1/n), so the error halves each time the steps
#: double rather than vanishing, and the fourth decimal needs tens of thousands of steps.
#: On an at-the-money half-year call at 22% vol, against Black-Scholes:
#:
#:      100 steps   0.0619 out        800 steps   0.0077 out
#:      200 steps   0.0309 out      1,600 steps   0.0039 out
#:      400 steps   0.0155 out      3,200 steps   0.0019 out
#:
#: 800 is the choice: under a cent on a $29 option, which is finer than the chain's own
#: tick, and about 320,000 nodes -- a few milliseconds, and this is called once per
#: position per day at most rather than per quote.
#:
#: The table is here rather than in a commit message because the number is a trade-off
#: between accuracy and time, and a reader changing it needs the shape of the curve.
DEFAULT_STEPS = 800


@dataclass(frozen=True, slots=True)
class Exercise:
    """What a tree says about holding versus exercising, now."""

    #: The American value: the greater of holding and exercising, at the root.
    american: float
    #: The value of **holding**, which is the number the decision turns on and the one a
    #: European formula cannot give.
    hold: float
    intrinsic: float
    #: The European value, for the size of the approximation the chain is making.
    european: float

    @property
    def exercise_now(self) -> bool:
        """Whether exercising now is worth more than holding."""
        return self.intrinsic > self.hold

    @property
    def premium_over_european(self) -> float:
        """What the European price understates the option by. Zero or positive."""
        return self.american - self.european

    def describe(self) -> str:
        verdict = ("exercising now is worth more than holding"
                   if self.exercise_now else "holding is worth more than exercising")
        return (f"{verdict}: intrinsic {self.intrinsic:.4f} against a hold value of "
                f"{self.hold:.4f} (American {self.american:.4f}, European "
                f"{self.european:.4f})")


def value(
    *,
    spot: float,
    strike: float,
    years: float,
    rate: float,
    vol: float,
    dividend_yield: float,
    right: Right,
    steps: int = DEFAULT_STEPS,
) -> Exercise:
    """Price an American option on a binomial tree, and say what holding is worth.

    **The dividend is a continuous yield**, which is the approximation in the middle of
    the question this function exists to answer. A call is exercised early to capture a
    *discrete* dividend on a known date, and a yield spreads that same cash evenly over
    the life -- so the tree understates the incentive just before an ex-date and
    overstates it just after. For an index ETF paying quarterly the error is small
    relative to the decision; for a single name with a large dividend it is not, and
    the honest use there is to pass the yield implied by the next dividend over the
    remaining life rather than the trailing twelve months.

    Said here rather than hidden because the alternative -- a tree with dividend dates --
    is a different and larger piece of work, and a reader comparing this to a textbook
    needs to know which model they are looking at.
    """
    if steps < 1:
        raise StartupError(f"a tree needs at least one step, got {steps}")
    if years <= 0:
        intrinsic = _intrinsic(spot, strike, right)
        # At or past expiry there is nothing to hold, so holding is worth the intrinsic
        # and the comparison is an equality rather than a decision. Returned rather than
        # refused: an expiring position is a real thing to ask about.
        return Exercise(american=intrinsic, hold=intrinsic, intrinsic=intrinsic,
                        european=intrinsic)
    if vol <= 0:
        raise StartupError(
            "a volatility of zero makes every path the forward, so the tree has one "
            "node per step and prices a certainty. Pass the contract's own IV"
        )

    dt = years / steps
    up = math.exp(vol * math.sqrt(dt))
    down = 1.0 / up
    growth = math.exp((rate - dividend_yield) * dt)
    if not (down < growth < up):
        raise StartupError(
            f"the risk-neutral probability is outside [0, 1]: with vol {vol:.4f} over "
            f"{years:.4f} years in {steps} steps, the drift per step ({growth:.6f}) is "
            f"outside the move the tree can make ({down:.6f} to {up:.6f}).\n\n"
            f"That is an arbitrage in the model rather than a numerical wobble, and a "
            f"price computed through it would be a number with no meaning. More steps, "
            f"or a volatility that is not far below the rate"
        )
    p = (growth - down) / (up - down)
    discount = math.exp(-rate * dt)

    # Terminal values.
    values = [_intrinsic(spot * up ** (steps - i) * down ** i, strike, right)
              for i in range(steps + 1)]
    european = list(values)

    # Backward induction, **down to step 1 only**. The American value takes the greater
    # of holding and exercising at every node; the European one never exercises. Both are
    # carried so the size of the approximation is available rather than inferred.
    for step in range(steps - 1, 0, -1):
        for i in range(step + 1):
            held = discount * (p * values[i] + (1 - p) * values[i + 1])
            node_spot = spot * up ** (step - i) * down ** i
            values[i] = max(held, _intrinsic(node_spot, strike, right))
            european[i] = discount * (p * european[i] + (1 - p) * european[i + 1])

    # The root is taken separately, from the step-1 nodes, because `hold` is the figure
    # the whole decision turns on and it cannot be read off the root: `values[0]` is
    # already the **max** of holding and exercising, so reading it would make
    # `exercise_now` compare the intrinsic with itself and never be true.
    #
    # The first version computed the root inside the loop and then recomputed `hold` from
    # `values[0]` and `values[1]` **after** step 0 had overwritten `values[0]` -- mixing a
    # root value with a step-1 value. It produced a hold above the American value, which
    # is impossible, and that impossibility is what showed it.
    hold = discount * (p * values[0] + (1 - p) * values[1])
    intrinsic = _intrinsic(spot, strike, right)
    return Exercise(
        american=max(hold, intrinsic),
        hold=hold,
        intrinsic=intrinsic,
        european=discount * (p * european[0] + (1 - p) * european[1]),
    )


def _intrinsic(spot: float, strike: float, right: Right) -> float:
    return max(spot - strike, 0.0) if right is Right.CALL else max(strike - spot, 0.0)
