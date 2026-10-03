"""The registered forms of each slot.

The architecture document's register names **"stop loss on premium vs on the underlying"**
as a group 3 choice -- one to research, under its protocol. A choice can only be researched
if both sides exist, so the platform offers both and picks neither.

The difference is not cosmetic. A premium stop reacts to implied volatility and to spread
noise as well as to the underlying's move, and it does so with the position's leverage
applied: on the test fixture a -40% premium stop fires on a **1.6%** move in the underlying.
That is a question about noise. An underlying stop fires on a move in the thing the thesis
was about, and leaves the premium to do whatever volatility makes it do. Which is right is
the researcher's question; running the comparison is what this module makes possible.

Every variant here reads the trade's context and returns a bool. It never looks at the
ledger and never calls another slot: both are what make per-slot ablation mean anything,
and neither is enforceable by the type system, so they are held here by review.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from backtest.core.component import Param, Variant, register

# ── stop loss ─────────────────────────────────────────────────────────────


def _premium_stop(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the premium has fallen by ``fraction`` of what was paid."""
    ratio = ctx.get("premium_ratio")
    return ratio is not None and math.isfinite(ratio) and ratio <= -fraction


def _underlying_stop(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the underlying has moved against the position by ``fraction``."""
    move = ctx.get("underlying_move")
    return move is not None and math.isfinite(move) and move <= -fraction


STOP_PREMIUM = register(Variant(
    slot="stop_loss", name="premium", version=1,
    describes="out when the premium falls by a fraction of what was paid. Reacts to "
              "implied volatility and spread noise as well as to the underlying, and "
              "applies the position's full leverage to the move",
    params=(Param("fraction", "float", "how much of the premium may be lost", lo=0.0,
                  hi=1.0),),
    decide=_premium_stop,
))

STOP_UNDERLYING = register(Variant(
    slot="stop_loss", name="underlying", version=1,
    describes="out when the underlying moves against the position by a fraction. Fires on "
              "the thing the thesis was about, and leaves the premium to volatility. The "
              "premium loss when it fires is whatever the leverage made it",
    params=(Param("fraction", "float", "how far the underlying may move against the "
                  "position", lo=0.0, hi=1.0),),
    decide=_underlying_stop,
))


# ── take profit ───────────────────────────────────────────────────────────


def _premium_target(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the premium has gained ``fraction`` of what was paid."""
    ratio = ctx.get("premium_ratio")
    return ratio is not None and math.isfinite(ratio) and ratio >= fraction


def _underlying_target(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the underlying has moved for the position by ``fraction``."""
    move = ctx.get("underlying_move")
    return move is not None and math.isfinite(move) and move >= fraction


TAKE_PREMIUM = register(Variant(
    slot="take_profit", name="premium", version=1,
    describes="out when the premium gains a fraction of what was paid. The usual form, "
              "and the one an MFE distribution in premium terms speaks to",
    params=(Param("fraction", "float", "the gain on the premium to take", lo=0.0),),
    decide=_premium_target,
))

TAKE_UNDERLYING = register(Variant(
    slot="take_profit", name="underlying", version=1,
    describes="out when the underlying has moved far enough for the position. Separates "
              "'the move I expected happened' from 'the option repriced', which a premium "
              "target cannot distinguish",
    params=(Param("fraction", "float", "the move in the underlying to take", lo=0.0),),
    decide=_underlying_target,
))


def exit_context(
    *,
    premium_ratio: float,
    underlying_move: float,
    days_held: int,
    days_to_expiry: int,
    **extra: Any,
) -> dict[str, Any]:
    """What an exit component is given each day.

    Built in one place and in one shape, so every variant sees the same thing and a new
    variant cannot quietly come to depend on a field only one engine supplies.

    ``premium_ratio`` and ``underlying_move`` are both **signed for the position**:
    positive means the trade is ahead. A variant that had to work out the direction itself
    would get it wrong for a put sooner or later, and getting it wrong inverts the stop
    and the target without raising.
    """
    return {
        "premium_ratio": premium_ratio,
        "underlying_move": underlying_move,
        "days_held": days_held,
        "days_to_expiry": days_to_expiry,
        **extra,
    }
