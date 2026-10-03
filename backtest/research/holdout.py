"""A locked hold-out: the one period a result may be scored on once, and not before.

The architecture document's research protocol opens with it -- "lock a final hold-out
period at the start and use it once, at the end" -- and nothing in this system enforced it.
A run could be scored on data already looked at, repeatedly, and neither the result nor the
record of it would say so.

## Why it needs a mechanism rather than discipline

Every other defence here exists because a rule that rests on remembering is a rule that
holds until the week it matters. This one is worse than most: looking at the hold-out does
not fail, does not warn, and produces a number that is **better** than the honest one. The
incentive points the wrong way and the feedback is silent, which is the exact combination
nothing survives by intention.

## What it does

``HoldOut(start, end)`` is a period the researcher declares before the work begins. A run
whose range overlaps it raises, naming the overlap -- unless it is passed ``unlock=`` with
a reason, which is recorded in the run's provenance and in the store. The point is not to
make it impossible; it is to make it **impossible to do silently**.

## What it deliberately does not do

It does not pick the period. How much to hold back and from when is a research-method
choice -- group 4 in the document's register, decided before the research and not after
seeing what helped -- so it is required, like every other such choice here.

It also does not stop the second use. A hold-out used twice is no longer a hold-out, and a
tool that silently refused a second one would push the work into another process where it
could not be seen. What the tracker records is how many times it has been touched and with
what reason, which is the thing a reader needs when deciding how much the final number is
worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backtest.core.errors import StartupError


@dataclass(frozen=True, slots=True)
class HoldOut:
    """A period reserved from the research, declared before it starts."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise StartupError(
                f"a hold-out ends before it begins: {self.start} to {self.end}"
            )

    def overlaps(self, start: date, end: date) -> "tuple[date, date] | None":
        """The overlapping stretch, or ``None``."""
        lo, hi = max(self.start, start), min(self.end, end)
        return (lo, hi) if lo <= hi else None

    def describe(self) -> str:
        return f"{self.start} to {self.end}"


def check(hold_out: "HoldOut | None", *, start: date, end: date,
          unlock: str = "") -> dict:
    """Refuse a run that reaches into the hold-out, unless it is unlocked with a reason.

    Returns what belongs in the run's provenance: whether a hold-out was declared, whether
    this run touched it, and the reason given. A run that did **not** touch it says so too
    -- "no hold-out was declared" and "declared and respected" are different states, and a
    reader comparing two results needs to tell them apart.
    """
    if hold_out is None:
        return {"hold_out": None, "touched": False,
                "note": "no hold-out was declared, so nothing was reserved from this "
                        "research and no result here is out of sample"}

    overlap = hold_out.overlaps(start, end)
    if overlap is None:
        return {"hold_out": hold_out.describe(), "touched": False,
                "note": f"the run ends before the hold-out ({hold_out.describe()}) "
                        f"begins"}

    if not unlock:
        raise StartupError(
            f"this run covers {overlap[0]} to {overlap[1]}, which is inside the hold-out "
            f"{hold_out.describe()}.\n\n"
            f"The hold-out exists to be used once, at the end. Scoring on it earlier does "
            f"not fail and does not warn -- it just returns a better number than the "
            f"honest one, which is why this refuses rather than cautions.\n\n"
            f"If this is the final evaluation, pass unlock=\"<why>\". The reason is "
            f"recorded with the run, and the tracker counts how many times the hold-out "
            f"has been touched."
        )

    return {"hold_out": hold_out.describe(), "touched": True, "reason": unlock,
            "note": f"this run was scored on {overlap[0]} to {overlap[1]}, inside the "
                    f"hold-out. Every later result on this data is in sample"}


def touches(records) -> list[dict]:
    """Every stored run that reached into its hold-out, with the reason given.

    The number a person needs when deciding what a final figure is worth: a hold-out
    touched once is the protocol working, and touched five times is a validation period
    that has quietly become a training one.
    """
    out = []
    for record in records:
        provenance = record.get("hold_out") or {}
        if provenance.get("touched"):
            out.append({
                "run_id": record.get("run_id", ""),
                "when": record.get("when", ""),
                "reason": provenance.get("reason", ""),
            })
    return out
