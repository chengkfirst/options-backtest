"""Slot 3: the forms a take profit can take.

The mirror of slot 4, and the same choice: a target on the premium is the usual form and
the one an MFE distribution in premium terms speaks to; a target on the underlying
separates "the move I expected happened" from "the option repriced", which a premium
target cannot distinguish.

See :mod:`backtest.signals.stop_loss` for why both exist and neither is chosen.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from backtest.core.component import Param, Variant, register


def _premium(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the premium has gained ``fraction`` of what was paid."""
    ratio = ctx.get("premium_ratio")
    return ratio is not None and math.isfinite(ratio) and ratio >= fraction


def _underlying(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the underlying has moved for the position by ``fraction``."""
    move = ctx.get("underlying_move")
    return move is not None and math.isfinite(move) and move >= fraction


PREMIUM = register(Variant(
    slot="take_profit", name="premium", version=1,
    describes="out when the premium gains a fraction of what was paid. The usual form, "
              "and the one an MFE distribution in premium terms speaks to",
    params=(Param("fraction", "float", "the gain on the premium to take", lo=0.0),),
    decide=_premium,
))

UNDERLYING = register(Variant(
    slot="take_profit", name="underlying", version=1,
    describes="out when the underlying has moved far enough for the position. Separates "
              "'the move I expected happened' from 'the option repriced', which a premium "
              "target cannot distinguish",
    params=(Param("fraction", "float", "the move in the underlying to take", lo=0.0),),
    decide=_underlying,
))
