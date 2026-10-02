"""Reviewing positions daily: the dynamic form of (3)(4)(5)(6).

## Why these four are one mechanism

Fixing the take-profit and stop as ratios at the moment of entry assumes something:
**that whether this trade is right or wrong was already known when it opened, and
will not change.** In real trading it does not hold:

- **(3) take profit** need not be a fixed ratio. A trailing stop (out after giving
  back a third of the gain), one set by the underlying's volatility (out at 2 ATR),
  one set by the thesis (out once the thing you waited for happened) — all of those
  have to be recomputed daily.
- **(4) stop loss** likewise. "Down 50%" is a number; "the thesis failed" is a
  judgement, and the judgement usually comes first.
- **(6) size** need not be settled once either. Taking half now and adding on
  confirmation is common practice.
- **(5) headwind** was always a thing that happens while holding.

All four want the same thing: **ask the question of every position, every day, and
then be able to act.** So they are one hook, not four.

## Usage

    def review(ctx, pos):
        # A trailing stop: out after giving back a third of the peak gain
        if pos.mfe_so_far > 0.5 and pos.ratio < pos.mfe_so_far * 0.67:
            return Close("gave back a third of the gain")

        # The thesis failed (this usually fires earlier than a fixed stop)
        if ctx.signal("close") < ctx.signal("sma_50"):
            return Close("below the 50-day; the reason for being long is gone")

        # The middle ground: cut half, rather than all or nothing
        if pos.days_held > 10 and pos.ratio < 0:
            return Reduce(0.5, "ten days and nothing has happened; cut half")

        return None      # keep holding

## Boundaries

- **The static exit rules still apply.** A review adds judgement on top of them;
  it does not replace them. The time-based exits (``hold_days`` / ``dte_floor``)
  are a safety net and may not be circumvented by a review — otherwise one review
  function written wrong drags a position into expiry.
- Only the event-driven engine supports it. The trade-table engine computes exits
  vectorised and cannot review day by day.
- **A partial close produces no new trade record**: counted by position, something
  rolled three times becomes four trades, and both win rate and holding period are
  distorted. After a reduction it is still one logical trade, until it is closed
  out entirely.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from backtest.errors import StartupError
from backtest.types import require


@dataclass(frozen=True, slots=True)
class Close:
    """Close the whole position."""

    reason: str = ""
    #: Which exit reason this files under. It drives the statistical grouping, so
    #: fill it in honestly.
    #: ``take_profit`` / ``stop_loss`` / ``headwind`` / ``signal``
    kind: str = "signal"

    def __post_init__(self) -> None:
        allowed = {"take_profit", "stop_loss", "headwind", "signal"}
        require(self.kind in allowed,
                f"unknown exit kind {self.kind!r}; one of {sorted(allowed)}")


@dataclass(frozen=True, slots=True)
class Reduce:
    """Reduce the position. ``fraction`` is the **share removed**: 0.5 cuts it in
    half.

    Afterwards it is still **one logical trade**; no extra row appears in the log.
    """

    fraction: float
    reason: str = ""

    def __post_init__(self) -> None:
        require(0 < self.fraction < 1,
                f"the reduction must lie between 0 and 1 (0.5 cuts it in half), got "
                f"{self.fraction}")


@dataclass(frozen=True, slots=True)
class Add:
    """Add to the position. Same contract, same logical trade, cost updated as a
    weighted average."""

    contracts: int
    reason: str = ""

    def __post_init__(self) -> None:
        require(self.contracts >= 1, "an addition must be at least 1 contract")


@dataclass(frozen=True, slots=True)
class Roll:
    """Rolling — **not implemented**.

    It is not simply "close and open another": by this project's convention a roll
    is still **one logical trade**. Counted as two, both win rate and holding period
    are distorted. Supporting it properly requires ``TradeRecord`` to hold "one
    trade that passed through several contracts", which is an architectural change.

    Returning this raises, rather than quietly being treated as a close — the latter
    would give a wrong result that looks right.
    """

    to_contract: Any
    reason: str = ""

    def __post_init__(self) -> None:
        raise StartupError(
            "rolling is not implemented. It requires one logical trade to span "
            "several contracts, and TradeRecord currently holds one contract per "
            "row; treating it as close-and-reopen distorts the win rate and the "
            "holding period. It can be approximated with Close() plus a plan that "
            "reopens the next day, but that counts as two trades."
        )


@dataclass(frozen=True, slots=True)
class PositionView:
    """What the review sees of this position.

    Everything ``*_so_far`` is **as of today**, never looked back on afterwards — a
    review function cannot see the future.
    """

    contract: Any
    entry_date: date
    entry_price: float
    quantity: int
    days_held: int
    #: Days to expiry (trading days)
    dte: int
    #: The current mid
    mark: float
    #: P&L as a fraction of the entry price. 0.2 means up 20%
    ratio: float
    #: The best and worst **so far**. Trailing stops read these
    mfe_so_far: float
    mae_so_far: float
    iv: float
    delta: float
    #: The tag attached at order time, from the Plan
    tag: str = ""


#: A review function: takes the market context and this position's state, and
#: returns an action or ``None`` (keep holding).
Reviewer = Callable[[Any, PositionView], "Close | Reduce | Add | None"]
