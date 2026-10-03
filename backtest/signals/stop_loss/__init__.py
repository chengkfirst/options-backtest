"""Slot 4: the forms a stop loss can take.

The architecture document's register names **"stop loss on premium vs on the underlying"**
as a group 3 choice -- one to research, under its protocol. A choice can only be researched
if both sides exist, so the platform offers both and picks neither.

The difference is not cosmetic. A premium stop reacts to implied volatility and to spread
noise as well as to the underlying's move, and it does so with the position's leverage
applied: on the test fixture a -40% premium stop fires on a **1.6%** move in the underlying.
That is a question about noise. An underlying stop fires on a move in the thing the thesis
was about, and leaves the premium to do whatever volatility makes it do. Which is right is
the researcher's question; running the comparison is what this module makes possible.

Each form reads the trade's context and returns a bool. It never looks at the ledger and
never calls another slot: both are what make per-slot ablation mean anything, and neither
is enforceable by the type system, so they are held here by review.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from backtest.core.component import Param, Variant, register


def _premium(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the premium has fallen by ``fraction`` of what was paid."""
    ratio = ctx.get("premium_ratio")
    return ratio is not None and math.isfinite(ratio) and ratio <= -fraction


def _underlying(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the underlying has moved against the position by ``fraction``."""
    move = ctx.get("underlying_move")
    return move is not None and math.isfinite(move) and move <= -fraction


PREMIUM = register(Variant(
    slot="stop_loss", name="premium", version=1,
    describes="out when the premium falls by a fraction of what was paid. Reacts to "
              "implied volatility and spread noise as well as to the underlying, and "
              "applies the position's full leverage to the move",
    params=(Param("fraction", "float", "how much of the premium may be lost", lo=0.0,
                  hi=1.0),),
    decide=_premium,
))

UNDERLYING = register(Variant(
    slot="stop_loss", name="underlying", version=1,
    describes="out when the underlying moves against the position by a fraction. Fires on "
              "the thing the thesis was about, and leaves the premium to volatility. The "
              "premium loss when it fires is whatever the leverage made it",
    params=(Param("fraction", "float", "how far the underlying may move against the "
                  "position", lo=0.0, hi=1.0),),
    decide=_underlying,
))
