"""The shared action vocabulary.

Every component emits actions from this one vocabulary, so every decision can be
logged, compared and attributed the same way. The architecture document fixes the
vocabulary; this module is its single definition in code.

## Why an enum rather than strings

``Intent.kind`` used to be a bare string, ``"entry" | "exit" | "reduce" | "add"``.
A misspelling matched no branch, the intent fell through, and nothing raised — the
same shape of failure as deciding control flow from free text. An enum makes a wrong
value impossible to construct.

## What is built and what is not

Six of the nine actions are built; three are not, and saying which is which is the
point of :data:`BUILT`. A component that returns an unbuilt action is refused at the
source rather than silently treated as something else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Mapping


class Action(str, Enum):
    """The nine actions. ``str`` so a value is readable in a log or a JSON payload."""

    OPEN = "open"                      # open a new trade with its legs
    ADD = "add"                        # increase an open trade (scale in)
    REDUCE = "reduce"                  # close a fraction of the trade
    CLOSE = "close"                    # close the whole trade, hedge legs included
    ROLL = "roll"                      # replace legs with a later expiry or new strike
    HEDGE_ADD = "hedge_add"            # attach protective legs
    HEDGE_REMOVE = "hedge_remove"      # remove protective legs
    SET_STOP = "set_stop"              # move the stop level
    HOLD = "hold"                      # an explicit no-change, logged with its reason


#: Which actions the engine can currently execute, and what is missing for the rest.
#:
#: Keeping this beside the vocabulary rather than in a document is deliberate: a
#: component can ask at run time whether what it wants to emit will actually happen,
#: and :func:`require_built` refuses the rest at the source. An unbuilt action quietly
#: treated as something else would be the worst outcome — the run finishes and the rule
#: the user wrote was never executed.
BUILT: Mapping[Action, str] = {
    Action.OPEN: "",
    Action.ADD: "",
    Action.REDUCE: "",
    Action.CLOSE: "",
    Action.HOLD: "",
    Action.ROLL: "one logical trade would have to span several contracts, which "
                "TradeRecord cannot hold; counted as close-and-reopen it distorts the "
                "win rate and the holding period",
    Action.HEDGE_ADD: "per-trade hedging needs the Hedge slot and a second leg under "
                      "the same trade_id; neither exists",
    Action.HEDGE_REMOVE: "there is nothing to remove until HEDGE_ADD exists, and "
                        "removing one leg of a trade needs the per-leg main/hedge role "
                        "that TradeRecord does not carry",
    Action.SET_STOP: "the stop is currently a constant on the ExitRule, with nowhere "
                     "to record a stop that moves during the trade",
}


def require_built(action: Action) -> None:
    """Raise unless ``action`` can actually be executed.

    Called where a component's return value enters the engine, so an unbuilt action
    fails loudly at the point it was asked for rather than being mapped to the nearest
    thing that does work.
    """
    from backtest.core.errors import StartupError

    why = BUILT.get(action)
    if why:
        raise StartupError(f"the action {action.value} is not implemented: {why}")
    if why is None:
        raise StartupError(f"{action!r} is not in the action vocabulary")


@dataclass(frozen=True, slots=True)
class TradeAction:
    """One action with everything needed to attribute it afterwards.

    The envelope matters as much as the action. Without ``component_id`` and
    ``variant`` there is no way to answer "which slot closed this trade", which is what
    attribution by component needs; without ``values`` there is no way to see what the
    decision was actually looking at.
    """

    action: Action
    trade_id: int
    when: date
    #: Which slot produced it, e.g. ``"stop_loss"``. See :data:`backtest.core.slots`.
    component_id: str = ""
    #: The variant and version that produced it, e.g. ``"atr_stop@2"``. Empty until the
    #: component protocol and the variant registry exist.
    variant: str = ""
    #: One line for a person: why, at the time.
    reason: str = ""
    #: The data values that triggered it, so research can trace what the decision saw.
    values: Mapping[str, Any] = field(default_factory=dict)
    #: For REDUCE: the share of the trade affected.
    fraction: float | None = None
    #: For ADD: how many contracts.
    contracts: int | None = None

    def __post_init__(self) -> None:
        require_built(self.action)
