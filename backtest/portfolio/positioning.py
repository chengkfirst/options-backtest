"""Slot 6 · Positioning — how much to buy.

## Why this is Layer 3 and not Layer 2

Sizing cannot be decided from the signal alone. It needs book state: current equity, the
premium already at risk, the book's greeks, and the liquidity cap on how much can
actually be bought. Those belong to the portfolio layer, so the sizer does too — even
though the decision feels like part of the same thought as the entry.

## The coupling that matters most

    position size = risk budget / stop distance

Fix two of the three and the third is fixed. ``risk_pct`` divides by the stop distance
for exactly this reason: with the stop at -50%, a trade's real risk is half the premium,
and treating it as a total loss halves the position. Set the stop and the size
independently and the computed position no longer matches the risk you think you took.

:func:`loss_fraction` is that divisor, and :func:`contracts_for` is where the
division happens. The engine fetches the premium and the volatility and calls them; it no
longer holds the rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from backtest.core.errors import StartupError
from backtest.core.types import require


@dataclass(frozen=True, slots=True)
class Sizing:
    """How position size is determined.

    For an option buyer the **maximum loss is the whole premium paid**, which makes
    "risk per trade" a clean concept: risk amount / premium per contract = contracts.
    It does not hold for a seller (whose maximum loss far exceeds the premium
    received), so selling is refused — that needs a margin model.

    ``method`` takes:

    ``"fixed"``          a fixed number of contracts. The plainest, but as equity
                         moves each trade's fraction of risk drifts.
    ``"risk_pct"``       a fixed fraction of equity at risk per trade. **The
                         default for a buyer.**
    ``"fixed_notional"`` a fixed amount committed per trade.
    ``"scale_by_vol"``   scaled inversely with the underlying's volatility: buy less
                         when it is more volatile. **Not a portfolio volatility
                         target.**

    There is no Kelly: the parameters it needs (win rate, payoff ratio) are mostly
    estimation noise over a few hundred option trades, and it sets the bet size
    directly from them; the binary formula also does not apply to options with a stop
    (a loss is not a total loss). Keeping a method that cannot be estimated only
    invites its use.
    """

    method: str = "fixed"
    contracts: int = 1                 # fixed
    risk_pct: float = 0.02             # risk_pct: at most 2% of equity per trade
    notional: float = 5_000.0          # fixed_notional
    target_vol: float = 0.20           # scale_by_vol: the reference annual volatility
    max_contracts: int = 50            # no method may exceed this

    def __post_init__(self) -> None:
        allowed = {"fixed", "risk_pct", "fixed_notional", "scale_by_vol"}
        require(self.method in allowed,
                f"unknown sizing method {self.method!r}; one of {sorted(allowed)}")
        require(self.contracts >= 1, "contracts must be at least 1")
        require(0 < self.risk_pct <= 1, "risk_pct must lie between 0 and 1")
        require(self.notional > 0, "notional must be positive")
        require(self.target_vol > 0, "target_vol must be positive")
        require(self.max_contracts >= 1, "max_contracts must be at least 1")


def loss_fraction(stop_loss: float | None) -> float:
    """What fraction of the premium a trade loses in the worst case.

    A stop of 0.5 means out at -50%, so the real risk is half the premium. Only with no
    stop is it the whole premium (a buyer's maximum loss).

    **This is where "size = risk budget / stop distance" lands in the code.** Set the
    stop and the size independently and the computed position no longer matches the risk
    you think you took.
    """
    if stop_loss is None or not 0 < stop_loss <= 1:
        return 1.0
    return float(stop_loss)


def contracts_for(
    sizing: Sizing,
    *,
    equity: float,
    premium: float,
    stop_loss: float | None,
    volatility: float | None = None,
) -> int:
    """How many contracts the method asks for. Pure arithmetic.

    It takes numbers rather than the engine's objects on purpose: the sizer can then be
    tested on its own, instead of only through a whole backtest. Fetching the premium and
    the volatility is the engine's job, because that is data access; dividing the budget
    is this layer's.

    ``premium`` is the cost of one contract, multiplier included. ``volatility`` is
    required only by ``scale_by_vol``; passing ``None`` there returns 0 rather than
    inventing a figure.

    Zero contracts means no trade. **It is never rounded up to 1**, which would quietly
    turn "2% at risk" into "8% at risk".
    """
    if sizing.method == "fixed":
        return min(sizing.contracts, sizing.max_contracts)
    if premium <= 0:
        return 0

    budget_at_risk = equity * sizing.risk_pct / loss_fraction(stop_loss)

    if sizing.method == "risk_pct":
        budget = budget_at_risk
    elif sizing.method == "fixed_notional":
        budget = sizing.notional
    elif sizing.method == "scale_by_vol":
        if volatility is None:
            return 0
        # Buy less when the underlying is more volatile. **This is not a portfolio
        # volatility target** — that would need a forecast of portfolio volatility, which
        # for options has to come from the greeks. The name says what it actually does.
        budget = budget_at_risk * (sizing.target_vol / max(volatility, 1e-6))
    else:
        raise StartupError(f"unimplemented sizing method {sizing.method}")

    return min(int(budget // premium), sizing.max_contracts)
