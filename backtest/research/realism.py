"""Sensitivity to a realism assumption, and the refusal to pick one.

The document's first research rule is two clauses and they pull in opposite directions:
a realism assumption is **tested for sensitivity** and **never optimised**. The first
asks for the run; the second says the run must not be read as a choice.

## Why lambda in particular

``adverse_fraction`` -- how far from the mid an order actually fills -- is the assumption
this system's results move most on. Measured on this data it is worth about 9% of gross
P&L between the mid and the ask. Nothing else in the cost model comes close, and no number
in the model is less observable: the fill a strategy would have got is the one fact a
backtest can never have.

So the figure is the obvious candidate for the quiet kind of fitting. Nobody sits down to
optimise it. What happens is that 1.0 gives a result that does not clear the bar, 0.5 is
defensible-sounding, and the run at 0.5 is the one that gets shown. The document's answer
is to fix the default at the pessimistic end and require the sweep to be reported, so the
cheaper figure is visible as a sensitivity rather than available as a result.

## What this returns, and what it refuses

The shape across the fractions, and the answer to one question: **does the strategy hold
at 1.0 or worse**. That is a pass or fail, and it is the document's, not this module's.

It does not name a best fraction and has no field that could be read as one. A function
that returned ``best_lambda`` would be the optimisation the rule forbids, dressed as a
measurement -- so the thing to leave out is not the recommendation but the field.
"""

from __future__ import annotations

from dataclasses import dataclass

from backtest.core.errors import StartupError

#: The document's list. Not a default -- see ``sensitivity``, which requires the caller to
#: pass what it wants and uses this only to say what was left out.
DOCUMENT_FRACTIONS: tuple[float, ...] = (0.5, 1.0, 1.5)

#: The fraction a result must survive. Below it the fill is between the bid and the ask,
#: which no order gets.
HONEST = 1.0


@dataclass(frozen=True, slots=True)
class Point:
    """The strategy at one adverse fraction."""

    fraction: float
    net_pnl: float
    trades: int

    @property
    def per_trade(self) -> float:
        return self.net_pnl / self.trades if self.trades else float("nan")


@dataclass(frozen=True, slots=True)
class Sensitivity:
    """What the result does as the fill moves from the mid towards the far side."""

    points: tuple[Point, ...]

    @property
    def at_honest(self) -> "Point | None":
        """The run at 1.0, which is the only one a conclusion may rest on."""
        return next((p for p in self.points if p.fraction == HONEST), None)

    def holds(self) -> "bool | None":
        """Whether the strategy makes money at 1.0 or worse.

        ``None`` when 1.0 was not run -- which is not the same as False and must not be
        rendered as one. A sweep that skipped the honest fraction has not answered the
        question, and saying "it does not hold" there would be inventing a result in the
        pessimistic direction, which is no better than inventing one in the other.
        """
        at_or_worse = [p for p in self.points if p.fraction >= HONEST]
        if not at_or_worse:
            return None
        return all(p.net_pnl > 0 for p in at_or_worse)

    def text(self, *, synthetic: bool) -> str:
        from backtest.research.boundary import Boundary, Scope, header

        lines = ["Realism sensitivity · lambda, the adverse fill fraction", "─" * 68]
        lines += header(Boundary(Scope.OPTION, synthetic),
                        what="the level of every figure in the table")
        lines += [
            "  0.0 fills at the mid, 1.0 at the far side of the quote, above 1.0 beyond",
            "  it. The default is 1.0 and a result below it is a sensitivity run, not a",
            "  result.",
            "",
            f"  {'lambda':>7} {'net P&L':>12} {'trades':>7} {'per trade':>11}",
        ]
        for point in self.points:
            marker = "  <- the honest one" if point.fraction == HONEST else ""
            lines.append(f"  {point.fraction:>7.2f} {point.net_pnl:>12,.0f} "
                         f"{point.trades:>7} {point.per_trade:>11,.1f}{marker}")

        missing = [f for f in DOCUMENT_FRACTIONS
                   if f not in {p.fraction for p in self.points}]
        lines += ["", "  " + self.read()]
        if missing:
            lines += [f"  The document asks for {DOCUMENT_FRACTIONS}; "
                      f"{tuple(missing)} was not run."]
        return "\n".join(lines)

    def read(self) -> str:
        """The document's verdict, and nothing resembling a choice."""
        held = self.holds()
        if held is None:
            return (f"lambda = {HONEST:g} was not run, so the question the document asks "
                    f"-- does this hold at the full adverse side -- is unanswered here")
        if not held:
            worst = min((p for p in self.points if p.fraction >= HONEST),
                        key=lambda p: p.net_pnl)
            return (f"**the strategy does not hold at lambda = {worst.fraction:g}**, "
                    f"where it makes {worst.net_pnl:,.0f}. A result from a lower fraction "
                    f"is a result from fills no order gets")
        return (f"the strategy holds at lambda = {HONEST:g} and above. The spread between "
                f"the fractions says how much of the result is the cost assumption, and "
                f"is not a reason to prefer one")


def sensitivity(runner, *, fractions: tuple[float, ...]) -> Sensitivity:
    """Run the strategy at each adverse fraction.

    ``runner(fraction) -> (net P&L, trade count)``, supplied by the caller for the same
    reason as in the other research tools: this layer must not reach into the engine.

    ``fractions`` is **required**, and 1.0 must be among them. The second condition is the
    unusual one and it is the point of the module: a sweep of 0.3, 0.5 and 0.7 is a search
    for a figure that makes the strategy work, and it is exactly what someone reaching for
    this tool to rescue a result would write. Refusing it here is cheaper than noticing it
    in a report later.
    """
    if not fractions:
        raise StartupError(
            f"the sensitivity run needs the fractions to try, for example "
            f"fractions={DOCUMENT_FRACTIONS}"
        )
    if any(f < 0 for f in fractions):
        raise StartupError("an adverse fraction below 0 fills beyond the mid on the "
                           "favourable side, which no order does")
    if HONEST not in fractions:
        raise StartupError(
            f"lambda = {HONEST:g} is not in {fractions}, so this sweep cannot say whether "
            f"the strategy holds at the full adverse side -- which is the one thing the "
            f"document asks of it.\n\n"
            f"A sweep that omits it reports how the result improves as the fill "
            f"assumption is relaxed, which is a search for a figure that makes the "
            f"strategy work rather than a test of whether it does."
        )
    points = [Point(fraction=float(f), net_pnl=float(pnl), trades=int(n))
              for f in sorted(fractions)
              for pnl, n in (runner(f),)]
    return Sensitivity(points=tuple(points))
