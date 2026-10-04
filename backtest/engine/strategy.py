"""The assembled strategy: one run's complete configuration.

## Why this is Layer 5 and not Layer 2

``Strategy`` holds slots from two different layers at once: the entry and exit rules are
signals (Layer 2), and the sizer is portfolio (Layer 3, because sizing needs book state).
An object that holds both cannot live in either — it is what the runner assembles from
the config before the run starts, which is Layer 5.

The dependency check found this: with the sizer moved to its proper layer,
``signals.strategy`` importing it became a reverse dependency. The layering was wrong,
not the code.

Everything here is **configuration**, not logic. The logic lives in the slots.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from backtest.core.errors import StartupError
from backtest.core.types import require
from backtest.portfolio.positioning import Sizing
from backtest.signals.contracts import ContractChooser, SelectionRule
from backtest.signals.plan import Planner
from backtest.signals.review import Reviewer
from backtest.signals.lifecycle import Lifecycle
from backtest.signals.strategy import EntryRule, ExitRule


@dataclass(frozen=True, slots=True)
class Strategy:
    """The complete strategy description for one run."""

    entry: EntryRule
    #: Selection. ``SelectionRule`` is declarative (fast, sweepable); a function
    #: ``f(chain, view)`` may be given instead, filtering the whole chain on greeks
    #: and iv itself — see ``selection.rules``.
    select: SelectionRule | ContractChooser
    exit: ExitRule
    #: A fixed number of contracts. The trade-table engine computes each trade
    #: independently and has no notion of current equity, so it supports only this.
    quantity: int = 1
    #: The sizing method. Everything but ``fixed`` needs portfolio state, so only the
    #: event-driven engine supports it.
    sizing: "Sizing" = field(default_factory=lambda: Sizing())
    #: Whether a repeated signal that selects the same contract may open another trade.
    #:
    #: Off by default — the signal holding for several days running and selection
    #: landing on the same contract is common, and allowing it by default bets the
    #: same judgement repeatedly. **Both engines must honour the same value**, or they
    #: diverge quietly on this point: the trade table treats trades independently and
    #: allows it naturally, while the event-driven engine holds one position per
    #: contract and naturally does not.
    allow_stacking: bool = False
    #: **Slot 1 · setup / regime.** ``f(ctx) -> bool``: is this an environment to trade
    #: in at all. Returning True *arms* the candidate; it opens nothing by itself.
    #:
    #: ``None`` means always armed, which is what the engine did before the Armed state
    #: existed -- the entry condition becoming true produced an order in the same step,
    #: so Idle and Armed were one. Keeping None as the default is why adding the state
    #: changes no result.
    #:
    #: Separating the two is what makes either measurable. While they were one test, no
    #: ablation could say what the regime filter was worth, because removing it also
    #: removed the timing.
    setup: "Any | None" = None
    #: The lifecycle settings: how many trades one candidate may hold, and when it may
    #: arm again after one closes. Per strategy rather than per engine, so two strategies
    #: being compared can differ on them and so they can be swept.
    lifecycle: "Lifecycle" = field(default_factory=lambda: Lifecycle())
    #: **A plan-based strategy**: one function decides what to buy, how much, and how
    #: to get out. Giving it bypasses entry / select / sizing, because those three
    #: cannot express the coupling between them. Only the event-driven engine supports
    #: it; the trade-table engine refuses loudly. See ``backtest.signals.plan``.
    plan: "Planner | None" = None
    #: **The daily position review**: dynamic exits, scaling in and out, and headwind
    #: all go through it. The static ExitRule remains a safety net; a review adds
    #: judgement on top rather than replacing it. Only the event-driven engine
    #: supports it. See ``backtest.signals.review``.
    review: "Reviewer | None" = None
    #: **Swapped-in component forms**, by slot: ``{"stop_loss": Bound(...)}``.
    #:
    #: A slot given here is decided by that component instead of by the matching field on
    #: ``exit``. This is what makes "settle the seven components" a comparison rather than
    #: a parameter sweep: a premium stop and an underlying stop are different forms of one
    #: slot, not different values of one parameter, and only the event-driven engine can
    #: run an arbitrary one -- the trade table computes exits vectorised over an array and
    #: refuses, the way it already refuses a plan and a daily review.
    components: Mapping[str, Any] = field(default_factory=dict)
    name: str = "unnamed"

    def __post_init__(self) -> None:
        # A component and the matching rule field both set is a silent override: one of
        # the two decides and the other looks as though it does. Refused, so a comparison
        # between two forms of a slot cannot quietly be measuring a third thing.
        for slot in self.components:
            if getattr(self.exit, slot, None) is not None:
                raise StartupError(
                    f"{slot} is given both as a component ({self.components[slot].ref}) "
                    f"and as exit.{slot}={getattr(self.exit, slot)!r}. The component "
                    f"would decide and the rule would look as though it did. Give one"
                )
        require(self.quantity >= 1, "quantity must be at least 1 contract")
