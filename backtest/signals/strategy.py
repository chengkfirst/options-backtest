"""The signal layer's rules: when to enter, and the static levels for getting out.

These are **configuration**, not logic — an entry condition and the four exit levels,
each independent. The assembled strategy that binds them to a sizer and the optional
plan and review hooks is one layer up, in :mod:`backtest.engine.strategy`, because a
sizer belongs to the portfolio layer and an object holding both cannot live in either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backtest.core.errors import StartupError
from backtest.core.types import require
from backtest.signals.registry import SignalView

#: An entry condition: reads the day's signal view and returns whether to enter.
#: With a signal unavailable it should return False rather than raise.
EntryCondition = Callable[[SignalView], bool]


@dataclass(frozen=True, slots=True)
class EntryRule:
    name: str
    condition: EntryCondition
    #: The signals this condition uses, validated at startup so a misspelling
    #: raises at once rather than failing part-way through a run.
    requires: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExitRule:
    """Exit rules. All four are in force, and the **first to fire** wins.

    - ``hold_days`` / ``dte_floor`` are in trading days
    - ``take_profit`` / ``stop_loss`` are fractions of the entry price (0.5 = 50%)

    **Nothing here is defaulted to a level.** These are slots 3 and 4 of the seven, and
    the architecture document permits optimising them under its protocol -- it does not
    permit the platform picking them. The cost of a default here is not theoretical: a run
    that omitted ``stop_loss`` used to be silently capped at -50%, and the MAE distribution
    a researcher then reads *in order to choose a stop* came back censored at exactly that
    level. The platform set the parameter and then hid the evidence for setting it.

    ``None`` means **no such exit**, which is a real configuration rather than a stand-in.
    At least one of the two time exits is still required, because a position left to drag
    into expiry is a mechanism failure rather than a choice.
    """

    hold_days: int | None = None
    take_profit: float | None = None
    stop_loss: float | None = None
    dte_floor: int | None = None

    def __post_init__(self) -> None:
        if self.hold_days is not None:
            require(self.hold_days >= 1, "hold_days must be at least 1 trading day")
        if self.take_profit is not None:
            require(self.take_profit > 0, "take_profit must be positive")
        if self.stop_loss is not None:
            require(0 < self.stop_loss <= 1, "stop_loss must lie between 0 and 1")
        if self.dte_floor is not None:
            require(self.dte_floor >= 0, "dte_floor must not be negative")
        require(
            any(x is not None for x in (self.hold_days, self.dte_floor)),
            "at least one time-based exit is required (hold_days or dte_floor), or "
            "positions get dragged into expiry",
        )


def crossover(fast: str, slow: str) -> EntryCondition:
    """A static condition for ``fast`` above ``slow`` on the day."""

    def condition(view: SignalView) -> bool:
        a, b = view[fast], view[slow]
        if a.is_missing or b.is_missing:
            return False
        return a.unwrap() > b.unwrap()

    return condition


def all_of(*conditions: EntryCondition) -> EntryCondition:
    def condition(view: SignalView) -> bool:
        return all(c(view) for c in conditions)

    return condition


def threshold(name: str, *, below: float | None = None, above: float | None = None) -> EntryCondition:
    if (below is None) == (above is None):
        raise StartupError("threshold takes exactly one of below or above")

    def condition(view: SignalView) -> bool:
        value = view[name]
        if value.is_missing:
            return False
        v = value.unwrap()
        return v < below if below is not None else v > above

    return condition
