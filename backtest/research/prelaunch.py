"""The document's section 6.2: the pre-launch checklist, as a gate rather than a habit.

## Why this exists as code

Every one of the eight criteria already had a tool. Nothing put them together, so
"is this ready" was answered by remembering to run eight things and remembering what each
one's answer should look like -- which is the form in which a check gets skipped on the
run that most needed it.

## The three states, and why there are three

A criterion is ``pass``, ``fail`` or ``unknown``. **Unknown is not a soft pass.** Most of
these need a second run -- a hold-out, a delayed-signal run, a sweep -- and a checklist
that quietly scored an unrun check as satisfied would be worse than no checklist: it
would put a tick beside work nobody did. So an unrun criterion says what to run, and
``ready`` is false while any criterion is unknown.

## What it does not do

It does not tune anything and it names no good value. Where a threshold is involved it
comes from ``core/thresholds.py``, where it is recorded with its reason and its group;
nothing here invents one. And it takes the **thesis** as an argument rather than guessing
it: "greeks attribution matches the thesis" is unanswerable without knowing what the
thesis was, and a tool that assumed one would be marking its own homework.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"


@dataclass(frozen=True, slots=True)
class Check:
    """One criterion's verdict."""

    name: str
    #: The document's own wording.
    says: str
    status: str
    #: What was measured, or what is missing and how to get it.
    detail: str
    #: The figures behind the verdict, for a reader who wants them.
    numbers: Mapping[str, Any] = None

    @property
    def ok(self) -> bool:
        return self.status == PASS


@dataclass(frozen=True, slots=True)
class Checklist:
    checks: tuple[Check, ...]

    @property
    def ready(self) -> bool:
        """**Every criterion passed.** An unknown counts against, deliberately."""
        return all(c.status == PASS for c in self.checks)

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == FAIL)

    @property
    def unknown(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.status == UNKNOWN)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "passed": sum(1 for c in self.checks if c.status == PASS),
            "failed": len(self.failed),
            "unknown": len(self.unknown),
            "checks": [{"name": c.name, "says": c.says, "status": c.status,
                        "detail": c.detail, "numbers": dict(c.numbers or {})}
                       for c in self.checks],
        }

    def text(self) -> str:
        mark = {PASS: "ok", FAIL: " x", UNKNOWN: " ?"}
        out = ["Pre-launch checklist (the document's 6.2)", "─" * 72]
        for c in self.checks:
            out += [f"\n{mark[c.status]} {c.says}", f"     {c.detail}"]
        d = self.as_dict()
        out += ["", "─" * 72,
                f"{d['passed']} passed · {d['failed']} failed · {d['unknown']} not run"]
        if self.unknown:
            out.append("  Not run is not a pass: a criterion nobody measured is a tick "
                       "beside work nobody did.")
        out.append(f"  ready: {self.ready}")
        return "\n".join(out)


def _cut(name: str) -> float:
    from backtest.core.thresholds import get

    return float(get(name))


# ── the eight criteria ────────────────────────────────────────────────────────

def _out_of_sample(result) -> Check:
    says = "out-of-sample performance does not collapse"
    held = getattr(result, "hold_out", None) or {}
    # A result always carries a `hold_out` mapping; when none was declared it holds a
    # note and no range. Reading "is there a mapping" rather than "is there a range" made
    # an undeclared hold-out report as a reserved-and-locked one -- a green-ish state for
    # research that reserved nothing.
    reserved = held.get("hold_out")
    if not reserved:
        return Check("out_of_sample", says, UNKNOWN,
                     "no hold-out was reserved. Pass hold_out=(start, end) to run(), "
                     "and unlock it once, at the end -- the document's first research "
                     "rule", held)
    if not held.get("touched"):
        return Check("out_of_sample", says, UNKNOWN,
                     f"a hold-out is reserved ({reserved}) and this run does not reach "
                     f"it. That is the **correct state** while the research is still "
                     f"going on, and it is why this is unknown rather than failed -- the "
                     f"criterion is answerable once, at the end", held)
    return Check("out_of_sample", says, PASS,
                 f"the run covers the hold-out ({reserved}); compare its figures with "
                 f"the in-sample ones yourself -- what counts as a collapse is a "
                 f"judgement this tool does not make for you", held)


def _stable_parameters(result, sweep=None) -> Check:
    says = "results are stable around the chosen parameters"
    if sweep is None:
        return Check("stable", says, UNKNOWN,
                     "no sweep was given. Run result.sweep({...}) over the parameters "
                     "you chose and pass it as sweep=")
    verdict = getattr(sweep, "verdict", None)
    note = verdict() if callable(verdict) else verdict
    if note is None:
        return Check("stable", says, UNKNOWN,
                     "the sweep carries no plateau verdict", {"sweep": str(sweep)})
    settled = "plateau" in str(note).lower()
    return Check("stable", says, PASS if settled else FAIL, str(note))


def _one_bar_delay(result, delay=None) -> Check:
    says = "still works with signals delayed by one bar"
    if delay is None:
        return Check("delay", says, UNKNOWN,
                     "not run. result.delay_test() reruns the strategy with the signal "
                     "delayed one session")
    survives = delay.get("survives") if isinstance(delay, Mapping) else getattr(
        delay, "survives", None)
    if survives is None:
        return Check("delay", says, UNKNOWN, "the delay test reported no verdict",
                     dict(delay) if isinstance(delay, Mapping) else {})
    return Check("delay", says, PASS if survives else FAIL,
                 str(delay.get("note", "")) if isinstance(delay, Mapping) else str(delay),
                 dict(delay) if isinstance(delay, Mapping) else {})


def _doubled_costs(result, realism=None) -> Check:
    says = "still positive with doubled costs and lambda = 1.5 fills"
    if realism is None:
        return Check("costs", says, UNKNOWN,
                     "not run. result.realism_test() reruns at the harsher cost "
                     "assumptions the document names")
    holds = getattr(realism, "holds", None)
    if holds is None:
        return Check("costs", says, UNKNOWN, "the realism test reported no verdict")
    return Check("costs", says, PASS if holds else FAIL,
                 getattr(realism, "read", lambda: "")() or
                 "the realism sensitivity ran and reported its verdict")


def _not_concentrated(result) -> Check:
    """Two separate concentrations: one trade, and one year."""
    says = "profit is not concentrated in a handful of trades or dates"
    trades = result.trades
    if trades.empty:
        return Check("concentration", says, UNKNOWN, "the run produced no trades")

    pnl = trades["net_pnl"].astype(float)
    total = float(pnl.sum())
    if total <= 0:
        return Check("concentration", says, UNKNOWN,
                     f"net P&L is {total:,.0f}, so there is no profit to be "
                     f"concentrated. The criterion is about a winning strategy",
                     {"net_pnl": total})

    top = float(pnl.max()) / total
    by_year = pnl.groupby(pd.to_datetime(trades["exit_date"]).dt.year).sum()
    trade_cut, year_cut = _cut("single_trade_share"), _cut("one_year_share")
    numbers = {"top_trade_share": top, "years": int(len(by_year)),
               "single_trade_share_limit": trade_cut, "one_year_share_limit": year_cut}

    # **A one-year run cannot fail a one-year concentration test**, and the first
    # version of this reported exactly that: 100% of the profit in the best year, over
    # the 70% limit, on a run that covered 2023. The dates half of the criterion is
    # unmeasurable here rather than failed -- and saying so is the point, because a
    # failure a reader cannot act on trains them to ignore the checklist.
    if len(by_year) < 2:
        if top > trade_cut:
            return Check("concentration", says, FAIL,
                         f"the best single trade is {top:.0%} of the profit, over the "
                         f"{trade_cut:.0%} limit. (The dates half cannot be measured: "
                         f"the run covers one year)", numbers)
        return Check("concentration", says, UNKNOWN,
                     f"the best single trade is {top:.0%} of the profit, inside the "
                     f"{trade_cut:.0%} limit -- but the run covers **one year**, so "
                     f"concentration in dates cannot be measured at all. Run a longer "
                     f"range before reading this as passed", numbers)

    worst_year = float(by_year.max()) / total
    numbers["top_year_share"] = worst_year
    if top > trade_cut or worst_year > year_cut:
        return Check("concentration", says, FAIL,
                     f"the best single trade is {top:.0%} of the profit (limit "
                     f"{trade_cut:.0%}) and the best of {len(by_year)} years is "
                     f"{worst_year:.0%} (limit {year_cut:.0%}). A result resting on one "
                     f"trade or one year is a sample of one", numbers)
    return Check("concentration", says, PASS,
                 f"the best single trade is {top:.0%} of the profit and the best of "
                 f"{len(by_year)} years {worst_year:.0%}, both inside the limits",
                 numbers)


def _beats_random(result, baselines=None) -> Check:
    says = "beats the random-entry baseline with the same structure"
    if baselines is None:
        return Check("baseline", says, UNKNOWN,
                     "not run. result.baselines() draws random entries with the same "
                     "structure, delta and days to expiry")
    rows = baselines if isinstance(baselines, (list, tuple)) else [baselines]
    random_row = next((r for r in rows if "random" in str(getattr(r, "name", r)).lower()),
                      None)
    if random_row is None:
        return Check("baseline", says, UNKNOWN,
                     "the baselines ran and none of them is the random-entry draw")
    percentile = getattr(random_row, "percentile", None)
    if percentile is None:
        return Check("baseline", says, UNKNOWN,
                     "the random-entry baseline reported no percentile, which is how "
                     "'beats' is measured here -- a position in a distribution rather "
                     "than a single comparison")
    return Check("baseline", says, PASS if percentile >= 0.95 else FAIL,
                 f"the strategy sits at the {percentile:.0%} percentile of the "
                 f"random-entry draws",
                 {"percentile": float(percentile)})


def _attribution_matches(result, thesis: str | None = None) -> Check:
    says = "greeks attribution matches the thesis"
    if thesis is None:
        return Check("attribution", says, UNKNOWN,
                     "no thesis was given. Pass thesis='delta' (or gamma, vega, theta) "
                     "-- what the strategy is **supposed** to earn from. A tool that "
                     "guessed it would be marking its own homework")
    try:
        attribution = result.attribution_daily()
    except Exception as exc:                                   # noqa: BLE001
        return Check("attribution", says, UNKNOWN,
                     f"the daily attribution could not run: {exc}")

    totals = {k: v for k, v in attribution.totals.items()
              if k not in ("residual", "spread")}
    if not totals or attribution.magnitude == 0:
        return Check("attribution", says, UNKNOWN, "nothing to attribute")
    if thesis not in totals:
        return Check("attribution", says, UNKNOWN,
                     f"{thesis!r} is not an attribution term; one of "
                     f"{', '.join(sorted(totals))}")

    largest = max(totals, key=lambda k: abs(totals[k]))
    share = attribution.totals[thesis] / attribution.magnitude
    numbers = {"thesis": thesis, "largest_term": largest,
               "thesis_share": share,
               "residual_share": attribution.totals["residual"] / attribution.magnitude,
               "untrustworthy_trades": attribution.high_residual}

    if abs(numbers["residual_share"]) > _cut("attribution_residual_warning"):
        return Check("attribution", says, UNKNOWN,
                     f"the residual is {numbers['residual_share']:.0%} of the movement, "
                     f"past the {_cut('attribution_residual_warning'):.0%} the register "
                     f"sets -- the expansion is not describing this P&L, so what it says "
                     f"about the thesis cannot be read either", numbers)
    if largest != thesis:
        return Check("attribution", says, FAIL,
                     f"the thesis is {thesis} and the largest term is {largest} "
                     f"({attribution.totals[largest]:,.0f} against "
                     f"{attribution.totals[thesis]:,.0f}). The strategy is earning from "
                     f"something other than what it claims", numbers)
    return Check("attribution", says, PASS,
                 f"{thesis} is the largest term at {share:.0%} of the movement", numbers)


def _trials_recorded(result, trials=None) -> Check:
    says = "the total number of parameter sets tried is recorded"
    if trials is None:
        return Check("trials", says, UNKNOWN,
                     "no trial count was given. The document's rule is that every trial "
                     "across all slots and settings is counted, and the Deflated Sharpe "
                     "uses the total -- so this is a number you keep, not one a single "
                     "run can know")
    total = getattr(trials, "total", trials)
    total = total() if callable(total) else total
    if not total or int(total) < 1:
        return Check("trials", says, FAIL,
                     f"the recorded trial count is {total}, and at least this run "
                     f"happened", {"total": total})
    return Check("trials", says, PASS,
                 f"{int(total)} parameter sets recorded", {"total": int(total)})


def checklist(result, *, thesis: str | None = None, sweep=None, delay=None,
              realism=None, baselines=None, trials=None) -> Checklist:
    """The document's 6.2, run against a finished result.

    The cheap criteria are computed from the result. The ones that need a second run are
    passed in -- running five more backtests inside a reporting call would be a surprise
    measured in minutes, and the document's own protocol has them run deliberately.
    """
    return Checklist(checks=(
        _out_of_sample(result),
        _stable_parameters(result, sweep),
        _one_bar_delay(result, delay),
        _doubled_costs(result, realism),
        _not_concentrated(result),
        _beats_random(result, baselines),
        _attribution_matches(result, thesis),
        _trials_recorded(result, trials),
    ))
