"""Slot 5 at the book level: the forms a risk rule can take.

The architecture document lists this slot's responses as **"to be defined"**, and names two
examples: a drawdown breaker and a daily loss limit. Both are here, and neither is the
platform's choice -- which was the problem. The drawdown brake used to be the only shape
a risk rule could have, so although every one of its numbers was required, its *structure*
-- step down, stop opening, release on recovery -- was decided by the platform and appeared
nowhere in the configuration as a decision anyone had made.

A form here answers one question each day: given the book's state, which of the document's
four actions applies. It returns a :class:`~backtest.portfolio.risk_actions.RiskDecision`
and nothing else; it never touches positions. Execution is the engine's.

**Neither form is recommended over the other.** They respond to different things -- one to
the distance from the high-water mark, the other to a single session -- and which matters
is a question about the strategy, not about the platform.
"""

from __future__ import annotations

from typing import Any, Mapping

from backtest.core.component import Param, Variant, register
from backtest.portfolio.risk_actions import NOTHING, RiskAction, RiskDecision


def _stages(drawdown: float, *, reduce_at: float, halt_at: float, keep: float,
            liquidate_at: float | None) -> RiskDecision:
    """The shared ladder: liquidate, halt, scale down, or nothing."""
    deep = abs(drawdown)
    if liquidate_at is not None and deep >= liquidate_at:
        return RiskDecision(RiskAction.LIQUIDATE,
                            reason=f"drawdown {deep:.1%} reached the liquidation level "
                                   f"of {liquidate_at:.1%}")
    if deep >= halt_at:
        return RiskDecision(RiskAction.NO_NEW_POSITIONS,
                            reason=f"drawdown {deep:.1%} is past the {halt_at:.1%} halt "
                                   f"level")
    if deep >= reduce_at:
        return RiskDecision(RiskAction.SCALE_DOWN, keep=keep,
                            reason=f"drawdown {deep:.1%} is past the {reduce_at:.1%} "
                                   f"reduce level")
    return NOTHING


def _drawdown(ctx: Mapping[str, Any], *, reduce_at: float, halt_at: float, keep: float,
              liquidate_at: float | None = None) -> RiskDecision:
    """Respond to the distance from the equity high-water mark.

    Slow to fire and slow to release: a book that has been sliding for a month is in the
    same state as one that fell the same distance yesterday, which is the point -- it
    measures the position you are in, not the day you have had.
    """
    return _stages(float(ctx.get("drawdown", 0.0)), reduce_at=reduce_at, halt_at=halt_at,
                   keep=keep, liquidate_at=liquidate_at)


def _daily_loss(ctx: Mapping[str, Any], *, reduce_at: float, halt_at: float, keep: float,
                liquidate_at: float | None = None) -> RiskDecision:
    """Respond to **one session's** loss as a fraction of the equity it started with.

    The opposite temperament to the drawdown breaker: it fires on a single bad day however
    healthy the book is overall, and forgets it the next morning. For an option buyer the
    two can disagree sharply -- a long gamma book has days that look catastrophic and
    recover, and a drawdown breaker sits through them while this one does not.
    """
    loss = float(ctx.get("daily_return", 0.0))
    return _stages(min(loss, 0.0), reduce_at=reduce_at, halt_at=halt_at, keep=keep,
                   liquidate_at=liquidate_at)


#: What both forms take. The same four, so the two can be compared with one change.
_PARAMS = (
    Param("reduce_at", "float", "the loss at which to scale the book down", lo=0.0,
          hi=1.0),
    Param("halt_at", "float", "the loss at which to stop opening anything new", lo=0.0,
          hi=1.0),
    Param("keep", "float", "what fraction of each position to hold on to when scaling "
          "down", lo=0.0, hi=1.0),
    Param("liquidate_at", "float", "the loss at which to close the book entirely",
          lo=0.0, hi=1.0, required=False),
)

DRAWDOWN = register(Variant(
    slot="headwind", name="drawdown", version=1,
    describes="responds to the distance from the equity high-water mark. Measures the "
              "position the book is in rather than the day it has had, so it is slow to "
              "fire and slow to release",
    params=_PARAMS,
    decide=_drawdown,
))

DAILY_LOSS = register(Variant(
    slot="headwind", name="daily_loss", version=1,
    describes="responds to one session's loss as a fraction of the equity it opened "
              "with. Fires on a single bad day however healthy the book is, and forgets "
              "it the next morning",
    params=_PARAMS,
    decide=_daily_loss,
))
