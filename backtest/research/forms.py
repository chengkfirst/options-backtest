"""Comparing the forms of one slot -- what settling a component actually requires.

Ablation answers "is this slot worth having". That is the first question and not the one
that settles a component. Settling it means choosing **which form** of the slot to keep: a
stop on the premium or a stop on the underlying, a target on the premium or a target on the
move. The architecture document lists exactly that pairing in its register as a group 3
choice, which is to say one the researcher decides under the research protocol and the
platform does not decide at all.

So this module runs the forms and lays them side by side. It does not rank them, and the
reason is in the output: on a handful of configurations the best number is the one that
fitted this period, and a difference smaller than the spread between neighbouring parameter
values of the *same* form is not evidence about form at all. Both of those are stated above
the table rather than left for the reader to remember.

**The comparison is not free of the synthetic chain.** A premium-based form and an
underlying-based form are not equally affected by it: the premium side is priced by
Black-Scholes while the underlying side is real data, so the comparison between them is
biased rather than merely uncertain. That is the ``MIXED`` scope, and it is the honest
label -- a reader who takes "underlying beats premium" from a constant-IV chain has learned
something about the pricing model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from backtest.core.component import Bound, Variant, get, variants
from backtest.core.errors import StartupError
from backtest.research.boundary import Boundary, Scope, header

#: One side is priced by the chain and one is not, so the bias lands on the comparison.
SCOPE = Scope.MIXED


@dataclass(frozen=True, slots=True)
class FormResult:
    """One form of one slot, run."""

    ref: str
    params: Mapping[str, Any]
    describes: str
    net_pnl: float
    trades: int

    def line(self) -> str:
        shown = ", ".join(f"{k}={v:g}" for k, v in sorted(self.params.items()))
        return f"  {self.ref:<28} {shown:<18} {self.net_pnl:>12,.0f} {self.trades:>7}"


@dataclass(frozen=True, slots=True)
class Comparison:
    """Every form of one slot, run on the same data."""

    slot: str
    results: tuple[FormResult, ...]

    @property
    def spread(self) -> float:
        """Best minus worst. The size of what is being chosen between."""
        if not self.results:
            return float("nan")
        values = [r.net_pnl for r in self.results]
        return max(values) - min(values)

    def within(self) -> dict[str, float]:
        """How far each form moves when only its own parameter changes.

        **This is the ruler the between-form gap has to be measured against.** If changing
        a premium stop from 0.4 to 0.6 moves the result more than switching to an
        underlying stop does, the comparison has not distinguished the forms -- it has
        measured parameter sensitivity and attributed it to form.
        """
        by_form: dict[str, list[float]] = {}
        for r in self.results:
            by_form.setdefault(r.ref, []).append(r.net_pnl)
        return {ref: max(v) - min(v) for ref, v in by_form.items() if len(v) > 1}

    def best_per_form(self) -> dict[str, float]:
        best: dict[str, float] = {}
        for r in self.results:
            best[r.ref] = max(best.get(r.ref, r.net_pnl), r.net_pnl)
        return best

    def distinguishes(self) -> "tuple[bool, str]":
        """Whether the run separates the forms, and the sentence saying why.

        No threshold: the test is whether the gap between the best of each form exceeds
        the largest movement either form shows under its own parameter. That is a
        comparison between two measured quantities, not a cut someone chose.
        """
        best = self.best_per_form()
        if len(best) < 2:
            return False, "only one form was run, so there is nothing to compare"
        values = sorted(best.values())
        gap = values[-1] - values[-2]
        within = self.within()
        if not within:
            return False, (f"the best of each form differ by {gap:,.0f}, and no form was "
                           f"run at more than one parameter -- so there is no ruler to "
                           f"measure that against. Run each form at two or three "
                           f"parameters before reading it")
        worst_within = max(within.values())
        ref = max(within, key=lambda k: within[k])
        # The ratio is always printed, never only the verdict. A gap of 3,576 against a
        # within-form spread of 3,566 passes `gap > worst_within` and separates nothing;
        # printing "1.00x" makes that unmissable, where the word "separated" alone would
        # have hidden it. Saying how much more rather than choosing how much more is
        # enough counts as a threshold is the point -- there is no cut here to argue with.
        ratio = gap / worst_within if worst_within else float("inf")
        if gap > worst_within:
            return True, (f"the best of each form differ by {gap:,.0f}, which is "
                          f"{ratio:.2f}x the most any single form moves under its own "
                          f"parameter ({worst_within:,.0f}, for {ref}). At 1.0x the run "
                          f"separates nothing; read the multiple, not the verdict")
        return False, (f"the best of each form differ by {gap:,.0f}, which {ref} alone "
                       f"covers by changing its own parameter ({worst_within:,.0f}). "
                       f"**This run has not distinguished the forms** -- it has measured "
                       f"parameter sensitivity")


def compare(
    strategy,
    slot: str,
    forms: Sequence[Mapping[str, Any]],
    runner: Callable[[Any], tuple[float, int]],
) -> Comparison:
    """Run each form of ``slot`` and return them side by side.

    ``forms`` is a list of ``{"variant": name, "params": {...}}``. **It is required**:
    which forms are worth comparing, and at which parameters, is the research question, and
    a platform-supplied list would answer it and then present its winner as a finding.

    ``runner(strategy) -> (net P&L, trade count)``, supplied by the caller for the same
    reason as in :mod:`~backtest.research.ablation`: this layer must not reach into the
    engine, which would put Layer 6 below Layer 5.
    """
    from dataclasses import replace

    if not forms:
        offered = ", ".join(v.name for v in variants(slot)) or "(none registered)"
        raise StartupError(
            f"compare needs the forms to run. Which forms of {slot} are worth comparing, "
            f"and at which parameters, is the research question -- a default list here "
            f"would answer it for you and then report its winner. Registered for this "
            f"slot: {offered}"
        )
    out: list[FormResult] = []
    for spec in forms:
        one = dict(spec)
        name = one.pop("variant", None)
        if name is None:
            raise StartupError(f'each form needs a "variant", got {spec!r}')
        variant: Variant = get(slot, str(name))
        bound: Bound = variant.bind(one.get("params") or {})
        # The field on `exit` that this slot would otherwise use has to be cleared, or the
        # strategy refuses the pair -- which is the right refusal, and here it would stop a
        # comparison the caller plainly meant.
        exit_rule = replace(strategy.exit, **{slot: None}) \
            if getattr(strategy.exit, slot, None) is not None else strategy.exit
        candidate = replace(strategy, exit=exit_rule,
                            components={**strategy.components, slot: bound})
        pnl, n = runner(candidate)
        out.append(FormResult(ref=bound.ref, params=dict(bound.params),
                              describes=variant.describes, net_pnl=pnl, trades=n))
    return Comparison(slot=slot, results=tuple(out))


def report(comparison: Comparison, *, synthetic: bool) -> str:
    """The forms side by side, with what the comparison can and cannot settle.

    ``synthetic`` is required, as everywhere in this layer. A premium form against an
    underlying form on a constant-IV chain is the one comparison the synthetic chain biases
    rather than merely blurs, so a forgotten argument must not be able to produce an
    unlabelled table.
    """
    lines = [f"Forms of {comparison.slot}", "─" * 72]
    lines += header(Boundary(SCOPE, synthetic), what="a comparison between forms")
    if not comparison.results:
        return "\n".join(lines + ["  nothing was run"])

    lines += [
        "  Ranked by nothing. The best number here is the one that fitted this period,",
        "  and a gap smaller than the spread between neighbouring parameter values of the",
        "  **same** form is not evidence about the form at all -- vary the parameters of",
        "  each form before concluding anything about which form.",
        "",
        f"  {'form':<28} {'parameters':<18} {'net P&L':>12} {'trades':>7}",
    ]
    lines += [r.line() for r in comparison.results]
    lines += ["", f"  spread between best and worst: {comparison.spread:,.0f}"]

    within = comparison.within()
    if within:
        lines.append("  under its own parameter, each form moves by: " +
                     ", ".join(f"{ref} {v:,.0f}" for ref, v in sorted(within.items())))
    separated, why = comparison.distinguishes()
    lines += ["", f"  {'->' if separated else '!!'} {why}"]

    counts = {r.trades for r in comparison.results}
    if len(counts) > 1:
        lines += ["", "  The forms took different numbers of trades, so part of the "
                      "difference is",
                  "  sample and not edge. Compare the per-trade figures too."]
    lines += ["", "  what each form is:"]
    for ref in dict.fromkeys(r.ref for r in comparison.results):
        describes = next(r.describes for r in comparison.results if r.ref == ref)
        lines.append(f"    {ref}: {describes}")
    return "\n".join(lines)
