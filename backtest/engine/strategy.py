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

from backtest.core.types import require
from backtest.portfolio.positioning import Sizing
from backtest.signals.contracts import ContractChooser, SelectionRule
from backtest.signals.plan import Planner
from backtest.signals.review import Reviewer
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
    name: str = "unnamed"

    def __post_init__(self) -> None:
        require(self.quantity >= 1, "quantity must be at least 1 contract")
