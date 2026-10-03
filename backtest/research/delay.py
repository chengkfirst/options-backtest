"""The delay test: does the result survive acting a day later.

The architecture document names two mechanisms against look-ahead, side by side -- the data
view's cut, and **the delay test**. The cut is structural: it stops a read that should not
happen. The delay test is empirical, and it catches the thing a cut cannot.

## What a cut cannot see

A cut enforces *when* a value became readable. It says nothing about a signal that is
legitimately readable and whose edge lives entirely in acting on it immediately -- a
reaction to the close that is gone by the next one. Nothing in such a strategy looks like
look-ahead, every read is in range, and the result is still not reachable: by the time an
order could be placed the edge has been competed away.

The test is blunt and that is its strength. Add a day to the execution lag and run again.
An edge that is real decays gently; one that was an artefact of acting on the same bar
falls off a cliff.

## How to read it

**Not as a pass or fail.** A real short-horizon effect genuinely does decay fast, and so
does an artefact; the shape alone cannot tell them apart, and this returns the shape rather
than a verdict. What it can say is the thing a researcher needs: *how much of this rests on
being quick*, which is a question about whether the strategy is implementable at all before
it is a question about whether the edge is real.

A strategy already filling a full session after its signal -- which is this system's
default -- has little room left to be quick, so a steep decline here is more surprising than
it would be in an intraday system, and worth more attention.
"""

from __future__ import annotations

from dataclasses import dataclass

from backtest.core.errors import StartupError


@dataclass(frozen=True, slots=True)
class Step:
    """One lag, and what the strategy did at it."""

    lag_days: int
    net_pnl: float
    trades: int

    @property
    def per_trade(self) -> float:
        return self.net_pnl / self.trades if self.trades else float("nan")


@dataclass(frozen=True, slots=True)
class Decay:
    """What happens to the result as the fill moves further from the decision."""

    steps: tuple[Step, ...]

    @property
    def base(self) -> Step:
        return self.steps[0]

    def retained(self, step: Step) -> float:
        """The share of the base result still there at this lag.

        Undefined rather than 0 or 1 when the base made nothing: a share of nothing is not
        a share, and reporting one would make a strategy that never worked look robust.
        """
        if self.base.net_pnl == 0:
            return float("nan")
        return step.net_pnl / self.base.net_pnl

    def text(self, *, synthetic: bool) -> str:
        """The shape, with what it can and cannot settle.

        ``synthetic`` is required as everywhere in this layer, though the answer here is
        the mild one: the comparison is between runs of the same chain, so the chain's
        own errors largely cancel. What does not cancel is the **level** of the figures.
        """
        from backtest.research.boundary import Boundary, Scope, header

        lines = ["Delay test · does the result survive acting later", "─" * 68]
        lines += header(Boundary(Scope.OPTION, synthetic), what="the size of the decline")
        lines += [
            "  A cut stops a read that should not happen. This catches what a cut cannot:",
            "  a signal that is legitimately readable and whose edge lives entirely in",
            "  acting on it immediately. Nothing about such a strategy looks like",
            "  look-ahead, and it is still not reachable.",
            "",
            f"  {'lag':>5} {'net P&L':>12} {'trades':>7} {'per trade':>11} {'retained':>9}",
        ]
        for step in self.steps:
            share = self.retained(step)
            shown = "    —" if share != share else f"{share:9.0%}"
            lines.append(f"  {step.lag_days:>5} {step.net_pnl:>12,.0f} "
                         f"{step.trades:>7} {step.per_trade:>11,.1f} {shown}")

        lines += ["", "  " + self.read()]
        return "\n".join(lines)

    def read(self) -> str:
        """One sentence on the shape -- never a verdict.

        A real short-horizon effect decays fast and so does an artefact. The shape cannot
        separate them, so this says what was measured and what it bears on, and stops.
        """
        if len(self.steps) < 2:
            return "only one lag was run, so there is no shape to read"
        if self.base.net_pnl == 0:
            return ("the base run made nothing, so there is no result for a delay to "
                    "take away")
        last = self.steps[-1]
        share = self.retained(last)
        if self.base.net_pnl < 0:
            return (f"the base run lost money, so 'retained' is the share of a loss and "
                    f"reads backwards. Compare the per-trade figures directly")
        if share <= 0:
            return (f"a further {last.lag_days - self.base.lag_days} day(s) of delay "
                    f"removes the result entirely. **All of it rested on being quick**, "
                    f"which is a question about reachability before it is one about edge")
        if share < 0.5:
            return (f"a further {last.lag_days - self.base.lag_days} day(s) leaves "
                    f"{share:.0%} of the result. Much of it rests on being quick; a real "
                    f"short-horizon effect looks like this too, and so does an artefact")
        return (f"a further {last.lag_days - self.base.lag_days} day(s) leaves "
                f"{share:.0%}. The result does not depend much on acting immediately, "
                f"which is what a durable effect looks like -- and is not evidence that "
                f"it is one")


def decay(runner, *, base_lag: int, extra: tuple[int, ...]) -> Decay:
    """Run the strategy at the base lag and at each additional one.

    ``runner(lag) -> (net P&L, trade count)``, supplied by the caller for the same reason
    as in the other research tools: this layer must not reach into the engine.

    ``extra`` is **required**. How much delay is worth testing is a question about the
    strategy's horizon -- a day matters to something holding a week and not to something
    holding a quarter -- and the platform does not answer it.
    """
    if not extra:
        raise StartupError(
            "the delay test needs the extra lags to try, for example extra=(1, 2, 5). "
            "How much delay is worth testing depends on how long the strategy holds, "
            "which is not something the platform knows"
        )
    steps = []
    for offset in (0, *extra):
        pnl, trades = runner(base_lag + offset)
        steps.append(Step(lag_days=base_lag + offset, net_pnl=float(pnl),
                          trades=int(trades)))
    return Decay(steps=tuple(steps))
