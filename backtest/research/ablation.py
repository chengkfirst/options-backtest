"""Ablation: what is each slot actually worth.

## The question

A strategy makes money. Which part of it did? The equity curve cannot say, and neither
can intuition — a stop that feels essential may be cutting more winners than losers, and
a take-profit that feels prudent may be giving away the tail that pays for everything else.

Ablation answers it the only way available: **run the strategy again with one slot
switched off, and compare.** The difference is that slot's contribution, measured rather
than assumed.

## Why a neutral variant rather than removing the slot

"Off" has to mean something precise, or the comparison is not a comparison. For each slot
there is exactly one neutral setting — the value that makes it have no effect while leaving
everything else identical:

| Slot | Neutral means | Why that and not something else |
|---|---|---|
| take profit | no target | removing the *target*, not the exit: the holding cap and the days-to-expiry floor stay, or the trade would never close and the run would not be comparable |
| stop loss | no stop | same reasoning. Note this also changes **sizing**, because risk_pct divides by the stop distance — see the warning below |
| positioning | one contract per trade | flat size. Not "no sizing", which would mean no trade |
| setup | no regime gate | every candidate armed |
| review | no daily review | the static rules alone |

## The one that is not a clean ablation

Switching off the stop also changes the position size, because
``size = risk budget / stop distance``. Removing the stop makes the worst case the whole
premium, so the same risk budget buys fewer contracts. The ablated run therefore differs
in **two** ways, and attributing the whole difference to the stop would be wrong.

This module does not hide that: :func:`ablate` refuses the stop-loss ablation under
``risk_pct`` sizing unless the caller passes ``accept_coupled=True``, and the result says
so. The honest way to ablate a stop is with fixed sizing, where the two are not coupled.

That refusal is the point. The coupling is the thing this project keeps finding, and an
ablation tool that quietly produced a two-variable comparison would turn it into a wrong
number with a confident label.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from backtest.core.errors import StartupError
from backtest.portfolio.positioning import Sizing

#: What "off" means for each slot, as an override of the assembled strategy.
#:
#: Keyed by slot name so it lines up with :data:`backtest.core.slots.COMPONENTS`. A slot
#: absent from here cannot be ablated, and :func:`ablate` says why rather than silently
#: doing nothing.
NEUTRAL: Mapping[str, str] = {
    "take_profit": "no target; the holding cap and the days-to-expiry floor still close it",
    "stop_loss": "no stop; the holding cap and the days-to-expiry floor still close it",
    "position": "one contract per trade, so size carries no information",
    "review": "no daily review; the static exit rules alone",
}

#: Slots the architecture defines that cannot be ablated yet, and what is missing. Listed
#: rather than omitted: "not in the dict" reads as "not a slot", and these are slots.
NOT_YET: Mapping[str, str] = {
    "setup": "the regime classifier is not wired into the engine as a gate, so there is "
             "nothing to switch off",
    "entry": "ablating the entry is the random-entry baseline, not an ablation — see "
             "backtest.research.baselines",
    "select": "a neutral contract selector would still have to choose a contract; the "
              "nearest thing is the selection-perturbation test, which is not built",
    "headwind": "the slot does not exist",
    "hedge": "the slot does not exist",
}


@dataclass(frozen=True, slots=True)
class Ablation:
    """One slot switched off, and what it cost or saved."""

    slot: str
    neutral: str
    full: float
    ablated: float
    full_trades: int
    ablated_trades: int
    #: Set when the comparison changed more than the one slot.
    caveat: str = ""
    #: True when the slot was already neutral, so there was nothing to switch off.
    was_unused: bool = False

    @property
    def contribution(self) -> float:
        """What the slot added. Positive means the strategy is better with it."""
        return self.full - self.ablated

    def verdict(self) -> str:
        if self.was_unused:
            # Reporting a contribution of 0 here would read as "this slot is worthless",
            # which is a different claim from "this slot was never switched on".
            return (f"{self.slot} was not in use in this run, so there was nothing to "
                    f"switch off")
        sign = "adds" if self.contribution > 0 else "costs"
        line = (f"{self.slot} {sign} {abs(self.contribution):,.0f} "
                f"({self.full:,.0f} with it, {self.ablated:,.0f} without)")
        if self.full_trades != self.ablated_trades:
            line += (f"; the trade count moved {self.full_trades} → "
                     f"{self.ablated_trades}, so the two runs are not the same trades")
        if self.caveat:
            line += f". {self.caveat}"
        return line


def is_already_neutral(strategy, slot: str) -> bool:
    """Whether the slot is already off, so ablating it would change nothing.

    Checked before running, because the alternative is reporting a contribution of 0 —
    and "this slot is worth nothing" is a very different claim from "this slot was never
    switched on".
    """
    if slot == "take_profit":
        return strategy.exit.take_profit is None
    if slot == "stop_loss":
        return strategy.exit.stop_loss is None
    if slot == "position":
        return strategy.sizing.method == "fixed" and strategy.sizing.contracts == 1
    if slot == "review":
        return strategy.review is None
    return False


def neutralise(strategy, slot: str, *, accept_coupled: bool = False) -> tuple[Any, str]:
    """The strategy with one slot neutral, and the caveat that applies.

    Raises for a slot that cannot be ablated, with the reason, rather than returning the
    strategy unchanged — an ablation that silently did nothing would report a contribution
    of zero, which is a wrong answer rather than no answer.
    """
    if slot in NOT_YET:
        raise StartupError(f"{slot} cannot be ablated: {NOT_YET[slot]}")
    if slot not in NEUTRAL:
        raise StartupError(
            f"{slot!r} is not an ablatable slot. Ablatable: {sorted(NEUTRAL)}; "
            f"not yet: {sorted(NOT_YET)}"
        )

    caveat = ""
    if slot == "take_profit":
        variant = replace(strategy, exit=replace(strategy.exit, take_profit=None))
    elif slot == "stop_loss":
        if strategy.sizing.method in ("risk_pct", "scale_by_vol") and not accept_coupled:
            raise StartupError(
                "ablating the stop under risk_pct sizing changes two things at once: the "
                "stop, and the position size, because size = risk budget / stop distance. "
                "Attributing the whole difference to the stop would be wrong. Either "
                "ablate with fixed sizing, or pass accept_coupled=True and read the "
                "result as the joint effect of both."
            )
        variant = replace(strategy, exit=replace(strategy.exit, stop_loss=None))
        if strategy.sizing.method in ("risk_pct", "scale_by_vol"):
            caveat = ("this is the joint effect of the stop and the size it implied, not "
                      "the stop alone")
    elif slot == "position":
        variant = replace(strategy, sizing=Sizing(method="fixed", contracts=1))
    elif slot == "review":
        variant = replace(strategy, review=None)
    else:  # unreachable while NEUTRAL and this branch agree
        raise StartupError(f"{slot} is listed as ablatable and has no neutral defined")
    return variant, caveat


def ablate(
    strategy,
    slot: str,
    runner: Callable[[Any], tuple[float, int]],
    *,
    accept_coupled: bool = False,
) -> Ablation:
    """Run the strategy with and without one slot.

    ``runner(strategy) -> (net P&L, trade count)``. The caller supplies it because only
    the caller knows how to build a run; this module must not reach into the engine, which
    would put Layer 6 below Layer 5.
    """
    if is_already_neutral(strategy, slot):
        full_pnl, full_n = runner(strategy)
        return Ablation(slot=slot, neutral=NEUTRAL[slot], full=full_pnl,
                        ablated=full_pnl, full_trades=full_n, ablated_trades=full_n,
                        was_unused=True)
    variant, caveat = neutralise(strategy, slot, accept_coupled=accept_coupled)
    full_pnl, full_n = runner(strategy)
    off_pnl, off_n = runner(variant)
    return Ablation(slot=slot, neutral=NEUTRAL[slot], full=full_pnl, ablated=off_pnl,
                    full_trades=full_n, ablated_trades=off_n, caveat=caveat)


def report(results) -> str:
    """The ablations as one block, largest contribution first."""
    # Unused slots last: they carry no measurement and would otherwise sort among the
    # small contributions, where they read as a result.
    results = sorted(results, key=lambda a: (a.was_unused, -abs(a.contribution)))
    if not results:
        return "no slots were ablated."
    lines = ["Ablation · what each slot is worth", "─" * 64]
    for a in results:
        lines.append(f"  {a.verdict()}")
        if not a.was_unused:
            lines.append(f"      off means: {a.neutral}")
        lines.append("")
    lines.append("A slot whose contribution is small is a parameter you are tuning for "
                 "nothing.")
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class Interaction:
    """Two slots varied together, against the sum of varying each alone."""

    slot_a: str
    slot_b: str
    #: Each slot's contribution measured on its own.
    alone_a: float
    alone_b: float
    #: What switching off both costs, measured in one run.
    together: float

    @property
    def additive(self) -> float:
        """What the joint effect would be if the two did not interact."""
        return self.alone_a + self.alone_b

    @property
    def gap(self) -> float:
        """How far the joint effect is from the sum of the parts."""
        return self.together - self.additive

    def verdict(self) -> str:
        """The reading, with the threshold that makes a gap worth acting on.

        The comparison is relative: a gap of 1,000 means something different against parts
        summing to 2,000 than against parts summing to 200,000.
        """
        from backtest.core.thresholds import get

        cut = get("additive_share")
        scale = max(abs(self.additive), 1.0)
        share = abs(self.gap) / scale
        if share < cut:
            read = (f"roughly additive at the {cut:.0%} cut, so the two can be researched "
                    f"and tuned separately")
        elif self.gap > 0:
            read = ("**they reinforce each other**: together they are worth more than "
                    "the sum, so tuning one at a time understates both")
        else:
            read = ("**they overlap**: together they are worth less than the sum, so "
                    "tuning one at a time double-counts the same effect")
        return (f"{self.slot_a} x {self.slot_b}: alone {self.alone_a:+,.0f} and "
                f"{self.alone_b:+,.0f} (sum {self.additive:+,.0f}), together "
                f"{self.together:+,.0f} — a gap of {self.gap:+,.0f} ({share:.0%}). {read}")


def interaction(
    strategy,
    slot_a: str,
    slot_b: str,
    runner: Callable[[Any], tuple[float, int]],
    *,
    accept_coupled: bool = False,
) -> Interaction:
    """Whether two slots have to be researched together.

    Four runs: the full strategy, each slot off alone, and both off. If switching off both
    costs about what switching off each separately costs, the two are independent and can
    be tuned one at a time. If it does not, tuning them separately is measuring the wrong
    thing — and the document's research protocol says to finish with exactly this check.

    The obvious pair to run is take profit against stop loss: they divide the same price
    path between them, so an interaction is expected rather than surprising.
    """
    full, _ = runner(strategy)
    off_a, _ = runner(neutralise(strategy, slot_a, accept_coupled=accept_coupled)[0])
    off_b, _ = runner(neutralise(strategy, slot_b, accept_coupled=accept_coupled)[0])

    both_strategy, _ = neutralise(strategy, slot_a, accept_coupled=accept_coupled)
    both_strategy, _ = neutralise(both_strategy, slot_b, accept_coupled=accept_coupled)
    off_both, _ = runner(both_strategy)

    return Interaction(slot_a=slot_a, slot_b=slot_b,
                       alone_a=full - off_a, alone_b=full - off_b,
                       together=full - off_both)
