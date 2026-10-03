"""What a book-wide risk rule may do, and which of those are built.

The architecture document limits a risk rule to four actions, and gives the reason: so
they can be **audited**. A rule that can do anything leaves no way to ask afterwards what
was done and why. Four named actions can be logged, counted, and attributed.

The same arrangement as :mod:`backtest.core.actions`: every action is in the vocabulary
from the start, and :data:`BUILT` says which ones execute. An action that is not built is
refused at the point of use with the reason, rather than being absent from the enum --
absent would mean a rule asking for it got an attribute error somewhere unhelpful, and
present-but-silently-ignored would mean a risk rule that appears to fire and does nothing.

## Where this sits

This is slot 5, headwind, at the **book** level. Per-trade headwind is the review hook.
The split matters for attribution: a position closed because the whole book was being cut
is not the same event as one closed because that trade's own thesis broke, and the decision
log records which.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backtest.core.errors import StartupError


class RiskAction(str, Enum):
    """The four. Nothing else is permitted, which is what makes them auditable."""

    NONE = "none"                         # no intervention today
    SCALE_DOWN = "scale_down"             # reduce every position by a ratio
    NO_NEW_POSITIONS = "no_new_positions"  # reductions only
    LIQUIDATE = "liquidate"               # close the whole book
    PAUSE = "pause"                       # stop until a person or a rule resumes


#: Empty string = executes. Anything else is the reason it does not, shown when something
#: asks for it.
BUILT: dict[RiskAction, str] = {
    RiskAction.NONE: "",
    RiskAction.SCALE_DOWN: "",
    RiskAction.NO_NEW_POSITIONS: "",
    RiskAction.LIQUIDATE: "",
    RiskAction.PAUSE: (
        "pausing until a person resumes needs a run that can stop and be restarted, "
        "which needs component state to be saved and restored. A backtest that paused "
        "with no way to resume would simply be a backtest that ended early, reported as "
        "though it had run to the end"
    ),
}


def require_built(action: RiskAction) -> None:
    """Refuse an unbuilt action at the point of use."""
    why = BUILT.get(action)
    if why is None:
        raise StartupError(f"{action!r} is not a risk action; one of "
                           f"{', '.join(a.value for a in RiskAction)}")
    if why:
        raise StartupError(f"the {action.value} risk action is not built: {why}")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """One rule's verdict for one day.

    ``reason`` is not decoration. A position cut by a book-wide rule and one cut by its own
    stop look identical in a trade log unless the reason travels with the action, and
    telling them apart is the whole point of separating slot 5 from slots 3 and 4.
    """

    action: RiskAction
    reason: str = ""
    #: For ``SCALE_DOWN``: the fraction of each position to **keep**, between 0 and 1.
    #: 0.5 halves the book. Required for that action and meaningless for the others.
    keep: float | None = None

    def __post_init__(self) -> None:
        require_built(self.action)
        if self.action is RiskAction.SCALE_DOWN:
            if self.keep is None:
                raise StartupError(
                    "scale_down needs `keep`: what fraction of each position to hold on "
                    "to. There is no default -- how hard to cut is a risk decision, and "
                    "a platform-chosen ratio would size the whole book without appearing "
                    "in the configuration"
                )
            if not 0 <= self.keep < 1:
                raise StartupError(
                    f"keep must be between 0 and 1 (and below 1, or nothing is being "
                    f"scaled down), got {self.keep}"
                )
        elif self.keep is not None:
            raise StartupError(
                f"`keep` only means something for scale_down, not for {self.action.value}"
            )

    @property
    def blocks_entries(self) -> bool:
        """Whether new positions are off today.

        Liquidating while still opening would be incoherent, so liquidation blocks too.
        """
        return self.action in (RiskAction.NO_NEW_POSITIONS, RiskAction.LIQUIDATE,
                               RiskAction.PAUSE)

    def describe(self) -> str:
        if self.action is RiskAction.NONE:
            return "no intervention"
        extra = f", keeping {self.keep:.0%}" if self.keep is not None else ""
        return f"{self.action.value}{extra}: {self.reason}" if self.reason \
            else f"{self.action.value}{extra}"


#: The decision taken when nothing fires. A module-level constant rather than a fresh
#: object each day: it is compared by identity in the engine's "did anything change" check.
NOTHING = RiskDecision(RiskAction.NONE)
