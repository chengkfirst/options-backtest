"""Shared by every exit form: what one is given each day.

Built in one place and in one shape, so every variant sees the same thing and a new
variant cannot quietly come to depend on a field only one engine supplies.
"""

from __future__ import annotations

from typing import Any


def exit_context(
    *,
    premium_ratio: float,
    underlying_move: float,
    days_held: int,
    days_to_expiry: int,
    #: Which trade this is. Required by a stateful form, whose state is per trade --
    #: see ``core/component.py``. Passed for every form, so a form becoming stateful is
    #: not also a change to every call site.
    trade_id: int | None = None,
    view: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """What an exit component is given each day.

    ``premium_ratio`` and ``underlying_move`` are both **signed for the position**:
    positive means the trade is ahead. A variant that had to work out the direction itself
    would get it wrong for a put sooner or later, and getting it wrong inverts the stop
    and the target without raising.

    ``view`` is the day's :class:`~backtest.core.dataview.DataView`, which is how the
    document says a slot reads: "every slot reads only through the DataView, so the
    available_at cut applies to all of them", and "a variant may read less than its
    default, **or more within the DataView**".

    Without it the shipped forms were safe by poverty rather than by construction -- they
    could not look ahead because they could not look at anything. That is not the same
    property, and it ends the moment a variant needs the volatility proxy or a signal: the
    author would have reached around the context for it, where nothing is cut.
    """
    return {
        "premium_ratio": premium_ratio,
        "underlying_move": underlying_move,
        "days_held": days_held,
        "days_to_expiry": days_to_expiry,
        "trade_id": trade_id,
        "view": view,
        **extra,
    }
