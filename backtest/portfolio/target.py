"""The target portfolio: what the book **should** hold, and why each adjustment.

## What the document asks for

"``TargetPortfolioEvent``: target quantity per contract plus the reason for every
adjustment; execution turns the gap into orders."

Two halves, and the second is the one that was missing. The engine decided an exit and
produced an order for it in the same step, so there was no moment at which the intended
book existed as a thing -- and therefore nowhere for "why is this position this size" to
be answered except by replaying the decisions.

## Why the indirection earns its keep

A target looks like a longer road to the same orders, and for a single exit it is. Three
things it buys that a direct intent does not:

- **two slots asking for the same contract compose instead of racing.** The stop wanting
  out of all of it and a review wanting to halve it are two orders against one position
  today, and which one lands first decides the result. Against a target they are two
  adjustments to one number, and the number is what execution reads.
- **the reason survives the arithmetic.** An order for "sell 2" carries one reason. A
  target of 0 reached by "the stop fired" and then "the review confirmed" carries both,
  which is what a reader asking why a position went to zero actually wants.
- **the gap is auditable.** Target minus actual is a number anybody can check against the
  ledger. An intent is a statement about the future that can only be checked by waiting.

## What it deliberately is not

It is not an optimiser. Nothing here decides what the target *should* be -- slots do, and
this records what they asked for and resolves the overlaps. A target portfolio that chose
its own weights would be slot 6 (sizing) and slot 7 (risk) wearing one hat, which is the
coupling the document's register keeps apart.

It also does not net across contracts. Two positions in the same underlying at different
strikes are two positions, and a layer that collapsed them into a delta number would be
making a hedging decision nobody asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterator, Mapping

from backtest.core.errors import InvariantError, StartupError
from backtest.core.types import ContractId


@dataclass(frozen=True, slots=True)
class Adjustment:
    """One slot's request to change one contract's target, with its reason.

    Every field is required. An adjustment with no slot cannot be attributed and an
    adjustment with no reason cannot be explained -- and the requirement's own falsifier
    is "an adjustment whose reason cannot be named".
    """

    contract: ContractId
    #: The target quantity **after** this adjustment, signed: positive is long.
    #:
    #: Absolute rather than a delta. Two slots each sending "-1" against a position of 2
    #: compose to 0 or to 1 depending on whether they knew about each other, and neither
    #: slot does. An absolute target is a statement about the book that does not depend on
    #: what else happened in the same step.
    quantity: int
    #: ``slot`` or ``slot/name@version`` -- whichever the caller has.
    slot: str
    reason: str
    when: date

    def __post_init__(self) -> None:
        if not self.slot:
            raise StartupError(
                "an adjustment with no slot cannot be attributed to anything, and "
                "attribution by component is what the target exists to make possible"
            )
        if not self.reason:
            raise StartupError(
                f"the {self.slot} adjustment of {self.contract} to {self.quantity} has "
                f"no reason. 'An adjustment whose reason cannot be named' is this "
                f"requirement's own falsifier"
            )


@dataclass(frozen=True, slots=True)
class Order:
    """The gap between a target and what is held, as something to execute."""

    contract: ContractId
    #: Signed: positive buys, negative sells. Never zero -- a zero-quantity order is not
    #: an order, and emitting one would put a row in the log for a day nothing happened.
    quantity: int
    held: int
    target: int
    #: Every adjustment that contributed to this target, in the order they arrived.
    #: Plural deliberately: a position taken to zero by a stop and then confirmed by a
    #: review has two reasons, and a reader asking why it went to zero wants both.
    because: tuple[Adjustment, ...]

    @property
    def reason(self) -> str:
        return "; ".join(f"{a.slot}: {a.reason}" for a in self.because)

    def describe(self) -> str:
        return (f"{'buy' if self.quantity > 0 else 'sell'} {abs(self.quantity)} "
                f"{self.contract} (hold {self.held}, target {self.target}) -- "
                f"{self.reason}")


@dataclass(slots=True)
class TargetPortfolio:
    """What the book should hold, per contract, with the reasons.

    Mutable, like :class:`~backtest.signals.lifecycle.Candidate` and for the same reason:
    this is the one kind of object that is *about* accumulating a day's requests.
    """

    #: contract -> target quantity. A contract absent from here has no opinion attached
    #: to it, which is **not** the same as a target of zero: no opinion leaves a position
    #: alone, and a target of zero closes it. Collapsing the two would make "no slot
    #: looked at this today" indistinguishable from "every slot wants it gone".
    _targets: dict[ContractId, int] = field(default_factory=dict)
    _reasons: dict[ContractId, list[Adjustment]] = field(default_factory=dict)

    def want(self, adjustment: Adjustment) -> None:
        """Record one slot's target for one contract.

        A later adjustment replaces the quantity and **appends** the reason. Replacing
        the reason too would lose the earlier slot's part in a target two slots agreed
        on, which is the case the reasons are plural for.
        """
        if adjustment.contract in self._targets:
            held = self._targets[adjustment.contract]
            if (held > 0) != (adjustment.quantity > 0) and adjustment.quantity != 0 \
                    and held != 0:
                raise InvariantError(
                    f"{adjustment.slot} wants {adjustment.contract} at "
                    f"{adjustment.quantity} and another slot wants it at {held}. One of "
                    f"them is long and the other short, which is not an overlap this "
                    f"resolves -- it is two strategies in one book, and silently taking "
                    f"the later one would run neither"
                )
        self._targets[adjustment.contract] = adjustment.quantity
        self._reasons.setdefault(adjustment.contract, []).append(adjustment)

    def target_for(self, contract: ContractId) -> "int | None":
        """The target, or ``None`` when no slot has an opinion. See ``_targets``."""
        return self._targets.get(contract)

    def reasons_for(self, contract: ContractId) -> tuple[Adjustment, ...]:
        return tuple(self._reasons.get(contract, ()))

    def orders(self, held: Mapping[ContractId, int]) -> tuple[Order, ...]:
        """The gap between the targets and what is held, as orders.

        **This is the diffing step.** It is the whole of what execution needs to know, and
        it is a subtraction -- which is the point: anybody can check it against the
        ledger, where an intent could only be checked by waiting to see what it did.

        Contracts with no target are untouched. A contract whose target equals what is
        held produces no order, which is why a slot restating a position's current size
        costs nothing.
        """
        out: list[Order] = []
        for contract in sorted(self._targets, key=str):
            target = self._targets[contract]
            have = held.get(contract, 0)
            if target == have:
                continue
            out.append(Order(contract=contract, quantity=target - have, held=have,
                              target=target, because=self.reasons_for(contract)))
        return tuple(out)

    def clear(self) -> None:
        """Forget the day's targets.

        Called once per step rather than accumulating: a target is what the book should
        hold **now**, and one carried into tomorrow would re-issue today's order against
        tomorrow's book -- which is how an exit that failed to fill comes to be decided
        twice. Carrying an unfilled order forward is the queue's job and it is a
        different thing: the queue carries the *instruction*, this holds the *intent*.
        """
        self._targets.clear()
        self._reasons.clear()

    def __len__(self) -> int:
        return len(self._targets)

    def __iter__(self) -> Iterator[ContractId]:
        return iter(self._targets)

    def describe(self) -> str:
        if not self._targets:
            return "no slot asked for anything this step"
        lines = []
        for contract in sorted(self._targets, key=str):
            reasons = "; ".join(f"{a.slot}: {a.reason}"
                                for a in self.reasons_for(contract))
            lines.append(f"  {contract} -> {self._targets[contract]}  ({reasons})")
        return "\n".join(lines)
