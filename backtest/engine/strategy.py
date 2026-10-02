"""The declarative description of a strategy.

At this level a strategy is **configuration**, not code: an entry condition, a
selection rule and an exit rule, each independent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backtest.errors import StartupError
from backtest.engine.plan import Planner
from backtest.engine.review import Reviewer
from backtest.selection.rules import ContractChooser, SelectionRule
from backtest.signals.registry import SignalView
from backtest.types import require

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
    """

    hold_days: int | None = 10
    take_profit: float | None = 1.0
    stop_loss: float | None = 0.5
    dte_floor: int | None = 5

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


@dataclass(frozen=True, slots=True)
class Sizing:
    """How position size is determined.

    For an option buyer the **maximum loss is the whole premium paid**, which makes
    "risk per trade" a clean concept: risk amount / premium per contract = contracts.
    It does not hold for a seller (whose maximum loss far exceeds the premium
    received), so selling is refused — that needs a margin model.

    ``method`` takes:

    ``"fixed"``          a fixed number of contracts. The plainest, but as equity
                         moves each trade's fraction of risk drifts.
    ``"risk_pct"``       a fixed fraction of equity at risk per trade. **The
                         default for a buyer.**
    ``"fixed_notional"`` a fixed amount committed per trade.
    ``"scale_by_vol"``   scaled inversely with the underlying's volatility: buy less
                         when it is more volatile. **Not a portfolio volatility
                         target.**

    There is no Kelly: the parameters it needs (win rate, payoff ratio) are mostly
    estimation noise over a few hundred option trades, and it sets the bet size
    directly from them; the binary formula also does not apply to options with a stop
    (a loss is not a total loss). Keeping a method that cannot be estimated only
    invites its use.
    """

    method: str = "fixed"
    contracts: int = 1                 # fixed
    risk_pct: float = 0.02             # risk_pct: at most 2% of equity per trade
    notional: float = 5_000.0          # fixed_notional
    target_vol: float = 0.20           # scale_by_vol: the reference annual volatility
    max_contracts: int = 50            # no method may exceed this

    def __post_init__(self) -> None:
        allowed = {"fixed", "risk_pct", "fixed_notional", "scale_by_vol"}
        require(self.method in allowed,
                f"unknown sizing method {self.method!r}; one of {sorted(allowed)}")
        require(self.contracts >= 1, "contracts must be at least 1")
        require(0 < self.risk_pct <= 1, "risk_pct must lie between 0 and 1")
        require(self.notional > 0, "notional must be positive")
        require(self.target_vol > 0, "target_vol must be positive")
        require(self.max_contracts >= 1, "max_contracts must be at least 1")


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
    #: it; the trade-table engine refuses loudly. See ``backtest.engine.plan``.
    plan: "Planner | None" = None
    #: **The daily position review**: dynamic exits, scaling in and out, and headwind
    #: all go through it. The static ExitRule remains a safety net; a review adds
    #: judgement on top rather than replacing it. Only the event-driven engine
    #: supports it. See ``backtest.engine.review``.
    review: "Reviewer | None" = None
    name: str = "unnamed"

    def __post_init__(self) -> None:
        require(self.quantity >= 1, "quantity must be at least 1 contract")


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
