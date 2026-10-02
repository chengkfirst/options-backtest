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

The engine's ``_loss_fraction`` is where that division happens.
"""

from __future__ import annotations

from dataclasses import dataclass

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
