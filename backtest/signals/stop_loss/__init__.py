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


def _trailing_initial(ctx: Mapping[str, Any]) -> dict:
    """The high-water mark a trade starts at.

    The **entry**, not zero. A trailing stop that began at zero would allow the premium
    to halve before the mark caught up, which is a fixed stop for the first part of the
    trade and a trailing one afterwards -- two rules wearing one name.

    ``premium_ratio`` is 0.0 at entry by construction, so this is 0.0 in practice and is
    written as a read rather than a constant: a form that hardcoded the entry would be
    wrong the first time one is restored mid-trade from a snapshot.
    """
    ratio = ctx.get("premium_ratio")
    return {"high_water": float(ratio) if ratio is not None and math.isfinite(ratio)
                           else 0.0}


def _trailing(ctx: Mapping[str, Any], *, fraction: float) -> bool:
    """Out when the premium falls ``fraction`` **from its best**, not from entry.

    **The first stateful form**, and what the state protocol was built for. The document's
    register names the trailing stop as the case that needs it: the high-water mark is a
    fact about the trade's history, and no amount of context about today can reconstruct
    it.

    Why it is a different rule rather than a better one: a fixed stop asks "how much of
    what I paid am I prepared to lose", and this asks "how much of what I had am I
    prepared to give back". On a trade that doubled and then fell by half, a 50% fixed
    stop does not fire and a 50% trailing stop does. Which question is the right one is
    the researcher's; having both is what makes it askable.
    """
    state = ctx["state"]
    ratio = ctx.get("premium_ratio")
    if ratio is None or not math.isfinite(ratio):
        # No quote today: the mark does not move and the stop does not fire. Treating a
        # missing quote as a fall would exit on a data gap, which is the commonest way a
        # trailing stop in a backtest beats one in an account.
        return False
    ratio = float(ratio)
    if ratio > state["high_water"]:
        state["high_water"] = ratio
    # Measured in the premium's own terms: from +100% back to +50% is a give-back of
    # 0.5 of the entry premium, not 0.25 of the peak value. The alternative -- a fraction
    # of the peak -- makes the stop tighten as the trade wins, which is a third rule.
    return (state["high_water"] - ratio) >= fraction


def _ratchet(ctx: Mapping[str, Any], *, fraction: float, trigger: float,
             to: float) -> bool:
    """A fixed stop that **moves once**, when the trade is far enough ahead.

    The commonest real stop rule there is -- "once it is up 30%, move the stop to
    breakeven" -- and the one form that could not be written here, because moving a stop
    was not an action the system had.

    ``fraction`` is the stop it starts at, ``trigger`` is how far ahead the trade must be
    for the move, and ``to`` is the stop it moves to. ``to=0.0`` is breakeven; a negative
    value is refused by the engine, because a stop that fires while the trade is ahead is
    a take-profit wearing the wrong name.

    **Why this is not the trailing stop.** A trailing stop moves every session and gives
    back a fixed fraction of the best. This moves once, to a level the researcher chose,
    and then stops moving. On a trade that runs up and chops sideways the trailing stop
    exits and this one does not; which is right is the question the pair makes askable.

    The move is a logged SET_STOP action, so a reader can see when the stop moved and
    which slot asked -- the difference between this and a component keeping the level in
    its own state, where the move is invisible.
    """
    ratio = ctx.get("premium_ratio")
    if ratio is None or not math.isfinite(ratio):
        return False
    ratio = float(ratio)

    state = ctx["state"]
    if not state.get("moved") and ratio >= trigger:
        setter = ctx.get("set_stop")
        if setter is not None:
            setter(to, f"up {ratio:.1%}, past the {trigger:.1%} trigger: the stop moves "
                       f"from {fraction:.1%} to {to:.1%}")
            state["moved"] = True
            state["level"] = to

    # `None` means "not moved yet", so the parameter is the level. The
    # initial-state hook cannot see the parameters, which is why this is a
    # fallback here rather than a value written at the start.
    level = state.get("level") if state.get("level") is not None else fraction
    return ratio <= -level


RATCHET = register(Variant(
    slot="stop_loss", name="ratchet", version=1,
    describes="starts at a fixed stop and moves it once, when the trade is far enough "
              "ahead -- 'up 30%, stop to breakeven'. Unlike the trailing stop it moves "
              "once to a chosen level rather than every session by a fixed give-back, "
              "and the move is a logged SET_STOP action rather than private state",
    params=(
        Param("fraction", "float", "the stop it starts at, as a loss fraction",
              lo=0.0, hi=1.0),
        Param("trigger", "float", "how far ahead the trade must be before the stop moves",
              lo=0.0, hi=5.0),
        Param("to", "float", "the stop it moves to; 0.0 is breakeven", lo=0.0, hi=1.0),
    ),
    decide=_ratchet,
    stateful=True,
    initial_state=lambda ctx: {"moved": False, "level": None},
))


TRAILING = register(Variant(
    slot="stop_loss", name="trailing", version=1,
    describes="out when the premium falls by a fraction **from its best**, not from "
              "entry. Asks how much of what the trade had it may give back, where a "
              "fixed stop asks how much of what was paid it may lose. On a trade that "
              "doubled and halved, a 0.5 fixed stop does not fire and a 0.5 trailing "
              "stop does",
    params=(Param("fraction", "float", "how much of the best premium may be given back",
                  lo=0.0, hi=2.0),),
    decide=_trailing,
    stateful=True,
    initial_state=_trailing_initial,
))
