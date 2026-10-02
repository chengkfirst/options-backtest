"""The seven strategy components: what each one decides in a trade, and how they
constrain one another.

**This module is the project's conceptual base.** How the interface is divided,
where the engine leaves hooks, what the research tools have to answer — all of
it follows from here, not the other way round.

## Why they are not "seven equal modules"

Listing them side by side as a table of components hides the thing that matters
most: **they decide one another.**

The clearest case is (4) stop loss and (6) positioning. In a real trade:

    position size = risk budget / stop distance

Fix two of the three quantities and the third is fixed. Offering "stop 0.5" and
"1 contract" as two independent inputs asks the user to enter a contradiction —
and nothing raises, because the system does not know the two numbers are related.

Once each component's job in a trade is written down, the couplings surface on
their own; see :data:`COUPLINGS`.

## Four layers, not seven boxes

Ordered by **when they happen in a trade**, the seven components fall on four
layers:

================  ===========================  =====================================
Layer              Components                    Why it is a layer of its own
================  ===========================  =====================================
before deciding    (1)                           places no order; decides whether to
                                                 trade today at all, and at what size
one trade          (2) (2b) (3) (4) (6)          these are facets of a single judgement
while holding      (5)                           the middle ground when the thesis is
                                                 partly broken; a stop is all or nothing
portfolio          (7)                           belongs to no individual trade
================  ===========================  =====================================

This layering decides the code structure directly. The "one trade" layer is
:class:`backtest.engine.plan.Plan` — one function yields the contract, the size
and the exit points together. The "while holding" layer needs a different hook:
a daily review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Layer = Literal["before deciding", "one trade", "while holding", "portfolio"]
Status = Literal["done", "partial", "missing"]


#: How a component's details get settled. The distinction decides what kind of
#: tool the platform owes you.
#:
#: ``"configured"``
#:     Just fill it in. You already know what you want and the system complies.
#:     Contract selection (2b) and the levels in (3)/(4) are of this kind,
#:     given real data.
#: ``"discovered"``
#:     **Cannot be filled in; has to be researched first.** There is no way to
#:     know a priori that "VIX > 25 and term structure < 1" is the right cut, or
#:     which signal means "the thesis is starting to fail". These components need
#:     a **discovery procedure**, not an input box — an input box is actively
#:     harmful here, because it implies you ought to have known the answer.
Origin = Literal["configured", "discovered"]


@dataclass(frozen=True, slots=True)
class Field:
    """One field a component asks for.

    **The interface is generated from here; the HTML is not hand-written.**
    Hand-written, this definition and the interface drift apart: the meaning of a
    component changes and the interface does not, or the reverse. A caption that
    stopped being true still looks authoritative on screen.
    """

    name: str                       # form field name; must match the backend payload
    label: str
    kind: str                       # number / ratio / int / select / text / code
    default: Any = None
    unit: str = ""
    help: str = ""
    options: tuple[tuple[str, str], ...] = ()     # select: (value, display text)
    #: Shown only when another field holds a given value, written "field=value".
    depends: str = ""
    #: Permitted range, for validation and for number controls.
    lo: float | None = None
    hi: float | None = None


@dataclass(frozen=True, slots=True)
class Component:
    key: str
    name: str
    layer: Layer
    #: **What this component actually decides in one real trade.** This single
    #: sentence is the point of the whole module.
    role: str
    #: What it emits — not how it computes, what it hands over.
    output: str
    status: Status
    #: Whether this component's details are configured or discovered.
    #: See :data:`Origin`.
    origin: Origin = "configured"
    #: What is missing right now. Empty when ``status`` is done.
    gap: str = ""
    #: When ``origin`` is discovered: **how to find it**. Without this, the
    #: component can only be guessed at.
    how_to_find: str = ""
    #: What this component asks for. The interface builds its form from this.
    #: An empty tuple means the component cannot currently be configured.
    inputs: tuple[Field, ...] = ()
    #: Which part of the plan function decides this component in plan mode.
    #: A value here means it is coupled to other components, and filling them in
    #: separately makes them fight.
    in_plan: str = ""


COMPONENTS: tuple[Component, ...] = (
    Component(
        "setup", "(1) setup / regime", "before deciding",
        role="Decides whether this kind of trade should be on right now, and "
             "under which set of parameters. **It produces no trade itself.**",
        output="an on/off switch and a multiplier",
        status="partial",
        origin="discovered",
        gap="rule-based classification (VIX / term structure / trend) exists but "
            "is not wired up as a modulator; statistical regimes (HMM) are out of "
            "scope, see the placeholder",
        how_to_find=(
            "Do not pick the cut first and validate it after — that is drawing "
            "the target around the arrow. The right order is:\n"
            "1. Run a version with **no regime condition at all** and keep every "
            "trade\n"
            "2. For each candidate dimension (VIX level, term structure, trend, "
            "credit spreads, realised volatility) look at performance by bucket\n"
            "3. Ask three questions at once: did it separate (effect size) - is "
            "each bucket big enough - does it hold on another sub-period\n"
            "4. **Write down how many dimensions you tried** - exploring buckets "
            "is statistically the same as sweeping parameters; try ten and keep "
            "the best and some of that best is luck\n"
            "5. Only a dimension that passes all three is worth making a "
            "condition\n"
            "Tool: backtest.research.discovery.regime_candidates()"
        ),
    ),
    Component(
        "entry", "(2) entry", "one trade",
        role="Decides the timing — today, not last week and not next week.",
        output="trade / do not trade",
        status="done",
        in_plan="the test above the `return None`",
        inputs=(
            # The same thing, two ways to express it; pick one. With both boxes
            # on screen the user cannot tell which one applies, and the backend
            # can only guess — and a wrong guess runs a different strategy.
            Field("entry_mode", "how to write it", "select", "expr", options=(
                ("expr", "expression"), ("code", "code"))),
            Field("entry", "entry condition", "text", "close < sma_20 * 0.98",
                  depends="entry_mode=expr",
                  help="comparisons, and/or/not, and arithmetic only"),
            Field("entry_code", "entry(view)", "code", "",
                  depends="entry_mode=code",
                  help="return True to enter. A missing signal never enters"),
        ),
    ),
    Component(
        "select", "(2b) contract selection", "one trade",
        role="Turns 'I am bullish' into 'this particular contract'. Beyond "
             "direction it also fixes this trade's greek exposure. The level of "
             "IV decides **whether premium is worth buying at all**, which "
             "belongs to the entry judgement (via the iv_rank_252 signal), not "
             "to this step.",
        output="one contract",
        status="done",
        in_plan="Plan(contract=...)",
        inputs=(
            # The professional form is "feasible region plus a ranking
            # objective", not a single pinned point. Pinning delta = 0.30 has two
            # problems: the chain may not hold that contract, and a conclusion
            # that only holds at one point is noise.
            Field("right", "type", "select", "call",
                  options=(("call", "call"), ("put", "put"))),
            Field("dte_lo", "days to expiry from", "int", 21,
                  unit="calendar days", lo=3, hi=400),
            Field("dte_hi", "to", "int", 45, unit="calendar days", lo=3, hi=400),
            Field("delta_lo", "delta from", "ratio", 0.25, lo=0.01, hi=0.99),
            Field("delta_hi", "to", "ratio", 0.45, lo=0.01, hi=0.99),
            Field("max_spread", "max bid/ask spread", "ratio", 0.08, lo=0.001, hi=1,
                  unit="x mid",
                  help="in live trading this is often the real binding constraint"),
            Field("min_open_interest", "min open interest", "int", None, lo=0,
                  help="blank means no limit. Skipped and flagged when the data "
                       "source does not supply it"),
            Field("delta", "target delta", "ratio", 0.35, lo=0.05, hi=0.95,
                  help="the contract in the feasible region closest to this"),
        ),
    ),
    Component(
        "take_profit", "(3) take profit", "one trade",
        role="Decides what counts as having been paid. **Its level follows from "
             "the reason for entering** — why you went in decides what counts as "
             "'the thing happened'.",
        output="a realisation point",
        status="partial",
        in_plan="Plan(take_profit=...)",
        inputs=(
            # A fixed ratio is settled at the moment of entry. A trailing stop,
            # exiting because the thesis broke, adjusting the target as time
            # decay bites — all of those have to be recomputed daily, which is
            # the review() hook on the "while holding" layer.
            Field("exit_mode", "how it is set", "select", "fixed", options=(
                ("fixed", "fixed ratio"), ("rule", "a rule, reviewed daily"))),
            Field("take_profit", "take profit", "ratio", 1.0, unit="x", lo=0,
                  depends="exit_mode=fixed",
                  help="relative to the entry price. 1.0 means leave only after "
                       "doubling, which historically only about 16% of trades "
                       "ever reach"),
        ),
        gap="the logic is complete, but the levels cannot be trusted on a "
            "synthetic chain; the MFE distribution is what sets them",
    ),
    Component(
        "stop_loss", "(4) stop loss", "one trade",
        role="Decides what would mean you were wrong. **A stop is not 'how much "
             "I lost', it is 'the thesis failed'** — and that difference decides "
             "where it belongs.",
        output="the point at which the thesis has failed",
        status="partial",
        in_plan="Plan(stop_loss=...)",
        inputs=(
            Field("stop_loss", "stop loss", "ratio", 0.5, unit="x", lo=0, hi=1,
                  depends="exit_mode=fixed",
                  help="relative to the entry price. 0.5 means leave after "
                       "losing 50%"),
            # Time-based exits are a **safety net** and apply in both modes: a
            # rule written wrong must not be able to drag a position into
            # expiry. So they carry no `depends`.
            Field("hold_days", "max holding period", "int", 10,
                  unit="trading days", lo=1, hi=120,
                  help="a safety net; applies in rule mode too"),
            Field("dte_floor", "minimum days to expiry", "int", 5,
                  unit="trading days", lo=2, hi=60,
                  help="force an exit at this remaining life, so nothing gets "
                       "dragged into expiry"),
        ),
        gap="the logic is complete, but the levels cannot be trusted on a "
            "synthetic chain; the MAE distribution is what sets them, with "
            "censoring in mind",
    ),
    Component(
        "headwind", "(5) headwind", "while holding",
        role="What to do when the thesis is **partly** broken. A stop is all or "
             "nothing; this is the middle ground: reduce, roll, hedge off one "
             "slice of the risk.",
        output="reduce / roll / hold",
        status="partial",
        inputs=(
            Field("review_code", "daily review", "code", "",
                  depends="exit_mode=rule",
                  help="define review(ctx, pos). Take profit (3), stop loss (4) "
                       "and reduce/roll (5) all live here — they were always one "
                       "mechanism: ask the question of every position, every "
                       "day. Return Close / Reduce / Add, or None."),
        ),
        origin="discovered",
        gap="the mechanism exists (a daily review can reduce or close "
            "dynamically) but **how to find the signals** does not — which "
            "signals mean the thesis is starting to fail has to be researched "
            "out of the trajectory data; rolling is unimplemented",
        how_to_find=(
            "The core question: **for the positions that ended badly, was there "
            "an observable warning beforehand.**\n"
            "1. This needs **daily position trajectories** — with only an entry "
            "and an exit point there is no way to see what happened in between. "
            "It is a hard prerequisite; without it the study cannot be done\n"
            "2. Split trades into two groups by final outcome and look at "
            "whether their state on holding day k separates: the underlying's "
            "path, the change in IV, delta drift, distance to the stop, MFE and "
            "MAE so far\n"
            "3. Of each candidate warning signal ask: how early does it fire - "
            "hit rate - false-alarm rate - what acting on it would have done "
            "(against not acting)\n"
            "4. **The trap: a stop censors the data.** With the stop at -50% you "
            "never see the paths that would have fallen to -80% and come back. "
            "Studying headwind requires first running a version with **active "
            "exits turned off**, to observe the complete trajectory\n"
            "Tool: backtest.research.discovery.headwind_signals() "
            "(unimplemented; the data it needs is already collected)"
        ),
    ),
    Component(
        "position", "(6) positioning", "one trade",
        role="Decides what this judgement is worth. **It is the same number as "
             "(4) seen from the other side**: risk budget / stop distance = size.",
        output="a number of contracts",
        status="partial",
        in_plan="Plan(risk_pct / notional / contracts)",
        inputs=(
            Field("sizing_method", "how size is set", "select", "risk_pct", options=(
                ("risk_pct", "a fixed fraction of equity at risk per trade"),
                ("fixed", "a fixed number of contracts"),
                ("fixed_notional", "a fixed amount per trade"),
                ("scale_by_vol", "scaled by the underlying's volatility"),
            )),
            Field("risk_pct", "risk per trade", "ratio", 0.02, unit="x equity",
                  lo=0.001, hi=1, depends="sizing_method=risk_pct",
                  help="the fraction of equity lost if the stop is hit. "
                       "Size = risk budget / stop distance"),
            Field("quantity", "contracts", "int", 1, lo=1, hi=50,
                  depends="sizing_method=fixed",
                  help="the easiest to understand and the worst: as equity "
                       "moves, the fraction at risk drifts and you will not "
                       "notice"),
            Field("notional", "amount per trade", "number", 5000, unit="USD",
                  lo=100, depends="sizing_method=fixed_notional",
                  help="the amount is fixed but the risk is not — a cheap "
                       "contract buys a great many of them"),
            Field("target_vol", "reference annual volatility", "ratio", 0.20,
                  lo=0.01, hi=2, depends="sizing_method=scale_by_vol",
                  help="buy less when the underlying is more volatile than "
                       "this, more when it is less"),
        ),
        gap="in plan mode size can follow risk_pct / notional; in parameter mode "
            "it is still an independent fixed count, which fights the stop",
    ),
    Component(
        "hedge", "(7) hedge", "portfolio",
        role="Decides how to strip off the risk you do not want to carry. "
             "**Belongs to no individual trade.**",
        output="portfolio limits and hedge positions",
        status="partial",
        gap="exposure limits, a drawdown circuit breaker, stress tests and a "
            "hedge-excluded control all exist; none is wired to the interface; "
            "multi-leg is not built",
    ),
)

#: How the components constrain one another. **These are not couplings of the
#: design, they are relationships the trade itself has** — the system splitting
#: them apart does not make them independent.
COUPLINGS: tuple[tuple[str, str, str], ...] = (
    ("stop_loss", "position",
     "risk budget / stop distance = size. Fix two of the three and the third is "
     "fixed; filling them in separately must produce a contradiction"),
    ("entry", "take_profit",
     "the reason for entering decides what counts as paid. Change the reason and "
     "the take-profit level moves with it"),
    ("entry", "stop_loss",
     "the reason for entering decides what counts as being wrong. A stop belongs "
     "where the thesis fails, not at some fixed percentage"),
    ("setup", "position",
     "regime modulates size: the same signal should not get the same bet in "
     "different environments"),
    ("setup", "select",
     "regime modulates selection: a high-volatility environment calls for longer "
     "and deeper in-the-money contracts"),
    ("stop_loss", "headwind",
     "headwind is the continuous version of a stop. Having only a stop means a "
     "failing thesis can only be handled as all or nothing"),
)

#: The four layers in order, and what each one is for.
LAYERS: tuple[tuple[Layer, str], ...] = (
    ("before deciding", "A switch and a multiplier. Places no order."),
    ("one trade", "One judgement yields all of: what to buy, how much, how to exit."),
    ("while holding", "A daily review: does the thesis still hold, and how far not?"),
    ("portfolio", "Limits and hedges across positions."),
)


def as_form_spec() -> list[dict]:
    """The definition the interface needs: four layers, their components, and
    each component's input fields.

    **The interface is generated from here, not hand-written.** That way "what
    this component is for" is defined in exactly one place and stays in step;
    hand-written, the two sides are certain to drift.
    """
    from dataclasses import asdict

    out = []
    for layer, what in LAYERS:
        out.append({
            "layer": layer,
            "what": what,
            "components": [{
                "key": c.key, "name": c.name, "role": c.role, "output": c.output,
                "status": c.status, "origin": c.origin, "gap": c.gap,
                "how_to_find": c.how_to_find, "in_plan": c.in_plan,
                "inputs": [asdict(f) for f in c.inputs],
            } for c in by_layer(layer)],
        })
    return out


def by_layer(layer: Layer) -> tuple[Component, ...]:
    return tuple(c for c in COMPONENTS if c.layer == layer)


def gaps() -> list[str]:
    """The components that do not yet stand up. The interface has to make the
    gaps visible rather than feign completeness.

    Generated from :data:`COMPONENTS`, not hand-written — a hand-written list
    goes stale once the feature lands, and the interface then shows a warning
    that stopped being true long ago.
    """
    return [f"{c.name}: {c.gap}" for c in COMPONENTS if c.status != "done" and c.gap]


def describe():
    """What each of the seven components is responsible for. The interface, the
    docs and the notebook all read this one table."""
    import pandas as pd

    return pd.DataFrame([{
        "component": c.name, "layer": c.layer, "decides": c.role,
        "output": c.output,
        "status": {"done": "done", "partial": "partial", "missing": "missing"}[c.status],
        "missing": c.gap,
    } for c in COMPONENTS]).set_index("component")


def describe_couplings():
    """How the components constrain one another. This table says which
    parameters must not be filled in separately."""
    import pandas as pd

    names = {c.key: c.name for c in COMPONENTS}
    return pd.DataFrame([
        {"one": names[a], "other": names[b], "relationship": why}
        for a, b, why in COUPLINGS
    ])
