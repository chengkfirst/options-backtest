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
    **extra: Any,
) -> dict[str, Any]:
    """What an exit component is given each day.

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
