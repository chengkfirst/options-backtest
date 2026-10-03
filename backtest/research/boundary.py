"""Which conclusions survive a synthetic chain, and which do not.

## The claim this module has to make true

The project states that the synthetic chain's limits are "determined automatically from the
``source`` column, with no manual switch". That was true of ``Result.summary()`` and of
nothing else. Every research tool added since computed an option-side number and said
nothing about whether it could be believed — the MFE/MAE distributions, the counterfactual
exits, the ablations, the parameter surfaces. A tool that produces a number nobody may act
on, and does not say so, is worse than a tool that refuses: the number gets quoted.

## Why labelling and not refusing

The arithmetic is valid; the **interpretation** is not. A counterfactual exit sweep on a
synthetic chain really does compute what those levels would have returned *in that chain* —
and that chain's IV is a constant with no skew, so the answer describes Black-Scholes
rather than the market. Refusing would also throw away the legitimate use: verifying that
the tool works, which is what a synthetic chain is for.

So the result carries its scope, the report states the boundary, and ``synthetic`` is a
**required** argument on every research report. A default would let a forgotten argument
produce an unlabelled report, which is the hole this module exists to close.

## The three scopes

``UNDERLYING``
    Uses only the underlying's prices, so it holds on a synthetic chain. Event studies are
    the clearest case: they never touch an option. The random-entry baseline also qualifies,
    for a different reason — both sides are priced by the same chain, so whatever is wrong
    with it is wrong identically on both.

``OPTION``
    Depends on option prices, so a synthetic chain makes the conclusion describe the pricing
    model. Exit levels, MFE/MAE, selection, anything in premium terms.

``MIXED``
    One side from each, which is the worst case rather than a middle one: the bias does not
    cancel, it lands entirely on the comparison. The delta-equivalent baseline is the
    example — synthetic option P&L against a real underlying move.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Scope(str, Enum):
    """What a conclusion rests on."""

    UNDERLYING = "underlying"
    OPTION = "option"
    MIXED = "mixed"


#: What each scope means for a conclusion drawn on a synthetic chain.
MEANING: dict[Scope, str] = {
    Scope.UNDERLYING:
        "rests only on the underlying's prices, so a synthetic option chain does not "
        "affect it",
    Scope.OPTION:
        "rests on option prices. On a synthetic chain whose IV is a constant with no skew "
        "and no term structure, the conclusion describes the pricing model rather than the "
        "market",
    Scope.MIXED:
        "compares an option-priced side against a real underlying side, so a synthetic "
        "chain biases the comparison itself — the bias does not cancel",
}


@dataclass(frozen=True, slots=True)
class Boundary:
    """What may and may not be concluded from one result."""

    scope: Scope
    synthetic: bool

    @property
    def usable(self) -> bool:
        """Whether a conclusion may be acted on."""
        return not self.synthetic or self.scope is Scope.UNDERLYING

    def note(self) -> str:
        """The line to print beside the number. Empty when there is nothing to warn about."""
        if self.usable:
            return ""
        if self.scope is Scope.MIXED:
            return ("!! biased by the synthetic chain: one side is priced by it and the "
                    "other is not. Read the direction, not the size, and do not act on it.")
        return ("!! this is an option-side conclusion on a synthetic chain, so it describes "
                "Black-Scholes rather than the market. Valid for checking the tool; not "
                "something to set a parameter from.")

    def describe(self) -> str:
        return f"{self.scope.value}: {MEANING[self.scope]}"


def header(boundary: Boundary, *, what: str) -> list[str]:
    """The opening lines of a report, stating the boundary before the numbers.

    Before, not after. A caveat printed under a table is read once and then scrolled past;
    the number above it is what gets copied into a decision.
    """
    if boundary.usable:
        return []
    return ["", f"  {boundary.note()}", f"  ({what} {MEANING[boundary.scope]})", ""]
