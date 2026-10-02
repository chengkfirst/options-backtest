"""Fill prices and fees. **Both engines share one set of cost assumptions; these
must not diverge.**

The trade-table engine may not take the shortcut of "simplify costs to the mid" —
that would make its results incomparable with the event-driven engine's, and the
cross-engine consistency check would mean nothing.

The default is to fill on the adverse side (pay the ask, receive the bid). For
options the bid/ask spread is the dominant cost, far larger than commission, and
filling at the mid produces a result that looks good on paper and cannot be had.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backtest.core.config import CostConfig
from backtest.core.maybe import Maybe
from backtest.core.types import Side


@dataclass(frozen=True, slots=True)
class Tradability:
    """Whether a contract can be traded at a given moment, and why not."""

    ok: bool
    reason: str | None = None
    relative_spread: float | None = None

    @classmethod
    def yes(cls, spread: float) -> "Tradability":
        return cls(True, None, spread)

    @classmethod
    def no(cls, reason: str, spread: float | None = None) -> "Tradability":
        return cls(False, reason, spread)


def check_tradable(bid, ask, costs: CostConfig) -> Tradability:
    """An absent quote, or a spread over the limit, means untradable.

    An estimated price is never substituted for an absent quote — that would let
    the backtest fill on a contract that had no quote at all.
    """
    try:
        bid_f, ask_f = float(bid), float(ask)
    except (TypeError, ValueError):
        return Tradability.no("no quote")
    if math.isnan(bid_f) or math.isnan(ask_f):
        return Tradability.no("no quote")
    if ask_f <= 0 or bid_f < 0:
        return Tradability.no("non-positive quote")
    if ask_f < bid_f:
        return Tradability.no("ask below bid")
    mid = (bid_f + ask_f) / 2.0
    if mid <= 0:
        return Tradability.no("non-positive mid")
    spread = (ask_f - bid_f) / mid
    if spread > costs.max_relative_spread:
        return Tradability.no(
            f"relative spread {spread:.1%} is over the {costs.max_relative_spread:.1%} "
            f"limit", spread
        )
    return Tradability.yes(spread)


def fill_price(bid: float, ask: float, side: Side, costs: CostConfig) -> float:
    """The fill price.

    ``adverse_fraction`` of 0 takes the mid, 1 takes the adverse side, and values
    in between interpolate. Slippage is added on top, always in the direction
    **against the order**.
    """
    mid = (bid + ask) / 2.0
    adverse = ask if side is Side.BUY else bid
    price = mid + (adverse - mid) * costs.adverse_fraction
    direction = 1.0 if side is Side.BUY else -1.0
    price += direction * costs.slippage_per_contract
    return max(price, 0.0)


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """Fees broken out. In aggregate nothing looks wrong; broken out, it is clear
    what ate the money."""

    commission: float
    regulatory: float
    slippage: float

    @property
    def total(self) -> float:
        return self.commission + self.regulatory + self.slippage

    def __add__(self, other: "FeeBreakdown") -> "FeeBreakdown":
        return FeeBreakdown(
            self.commission + other.commission,
            self.regulatory + other.regulatory,
            self.slippage + other.slippage,
        )


ZERO_FEES = FeeBreakdown(0.0, 0.0, 0.0)


def order_fees(quantity: int, costs: CostConfig, multiplier: int) -> FeeBreakdown:
    """One order's fees, broken out.

    Slippage is listed separately in cash so the report can set it beside
    commission and regulatory fees — it is usually the largest of the three, and
    folded into the fill price it is invisible.
    """
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    commission = max(
        quantity * costs.commission_per_contract, costs.min_commission_per_order
    )
    regulatory = quantity * costs.regulatory_fee_per_contract
    slippage = quantity * costs.slippage_per_contract * multiplier
    return FeeBreakdown(commission, regulatory, slippage)


def exercise_fees(quantity: int, costs: CostConfig) -> FeeBreakdown:
    return FeeBreakdown(quantity * costs.exercise_fee, 0.0, 0.0)


def execute(
    *, bid: float, ask: float, side: Side, quantity: int, multiplier: int, costs: CostConfig
) -> tuple[float, FeeBreakdown]:
    """The fill price and the fees. :func:`check_tradable` must pass first."""
    price = fill_price(bid, ask, side, costs)
    return price, order_fees(quantity, costs, multiplier)
