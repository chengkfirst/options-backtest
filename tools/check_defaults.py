"""The platform never defaults a strategy choice — checked, not trusted.

The rule was already written down, and a test already asserted it. The test looked at
``core/thresholds.py`` and confirmed its ``strategy`` group was empty, which was true and
beside the point: the strategy choices did not live there. They lived as **dataclass field
defaults**, where ``ExitRule`` supplied a take-profit of +100% and a stop of -50% to every
run that omitted them.

That default was not cosmetic. The MFE/MAE study exists to decide where a stop belongs,
and it reads the distribution of how far trades ran against the position. With a -50% stop
silently in force, nothing could ever run past -50%, so the distribution came back censored
at exactly the level the platform had chosen — the platform set the parameter, and then
removed the evidence a person would use to question it.

So the check reads the field defaults themselves. For each strategy dataclass it names the
fields that are slot parameters and requires each one to be either required (no default) or
defaulted to ``None`` (meaning "no such rule", a real configuration rather than a value in
disguise).

Two things are deliberately NOT flagged:

- **Run settings.** The period, the capital, the cost model, the engine, the symbol. The
  architecture document's register puts these among the realism and engineering groups, not
  among the strategy choices. A default there is a starting point, not a decision taken on
  the researcher's behalf.
- **A default a person can see.** The interface's form ships starting values for every
  field. That is the right place for one: it appears on screen, in the configuration, and
  in the saved run, and changing it is one edit. What this check forbids is a value that
  fills itself in when the field is *omitted* and then never appears anywhere.
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Any

#: The strategy dataclasses, and which of their fields are slot parameters.
#:
#: Named explicitly rather than inferred. A dataclass holds both kinds of field --
#: ``Sizing`` carries ``method`` (which rule, legitimately defaulted to the plainest) and
#: ``risk_pct`` (how much of the book to risk, which is a decision) -- and no rule of thumb
#: separates them. Writing the list out is also what makes a newly added parameter a
#: deliberate entry rather than something the check silently did not know about.
WATCHED: dict[str, tuple[str, tuple[str, ...], str]] = {
    "exit": (
        "backtest.signals.strategy:ExitRule",
        ("hold_days", "take_profit", "stop_loss", "dte_floor"),
        "slots 3 and 4: where to take profit and where to stop",
    ),
    "select": (
        "backtest.signals.contracts:SelectionRule",
        ("right", "target_dte", "dte_window", "target_delta", "moneyness"),
        "slot 2b: which contract -- delta and tenor are the most researched "
        "parameters an option buyer sets",
    ),
    "sizing": (
        "backtest.portfolio.positioning:Sizing",
        ("risk_pct", "notional", "target_vol"),
        "slot 6: how large, which decides the size of every position in the book",
    ),
}

#: Fields whose default is allowed, with the reason recorded here rather than in a comment
#: that drifts away from the check.
ALLOWED: dict[tuple[str, str], str] = {
    ("Sizing", "method"):
        "which sizing rule, not how much of it. 'fixed' is the plainest possible choice "
        "and shows up as a contract count in every row of the trade log",
    ("Sizing", "contracts"):
        "a fixed count of one. Visible in every trade, and the parameter the 'fixed' "
        "method exists to express",
}


def _load(path: str) -> Any:
    module, name = path.split(":")
    return getattr(__import__(module, fromlist=[name]), name)


def offenders() -> list[str]:
    """Every strategy field that carries a value as its default."""
    out: list[str] = []
    for key, (path, fields, why) in WATCHED.items():
        cls = _load(path)
        by_name = {f.name: f for f in dataclasses.fields(cls)}
        for name in fields:
            field = by_name.get(name)
            if field is None:
                out.append(
                    f"{cls.__name__}.{name} is watched by this check and does not exist. "
                    f"Either it was renamed -- update WATCHED -- or the check is guarding "
                    f"a field that is gone"
                )
                continue
            if (cls.__name__, name) in ALLOWED:
                continue
            has_default = field.default is not dataclasses.MISSING
            has_factory = field.default_factory is not dataclasses.MISSING  # type: ignore[misc]
            if has_factory:
                out.append(f"{cls.__name__}.{name} has a default_factory ({why})")
            elif has_default and field.default is not None:
                out.append(
                    f"{cls.__name__}.{name} defaults to {field.default!r} -- {why}. "
                    f"Make it required, or default it to None meaning 'no such rule'"
                )
    return out


def unreachable_allowances() -> list[str]:
    """An allowance for a field that no longer exists.

    Without this, a renamed field leaves its exemption behind and the next field to take
    that name inherits permission nobody granted.
    """
    known = set()
    for path, fields, _ in WATCHED.values():
        cls = _load(path)
        known |= {(cls.__name__, f.name) for f in dataclasses.fields(cls)}
    return [f"{c}.{n} is allowed a default and does not exist"
            for (c, n) in ALLOWED if (c, n) not in known]


def component_offenders() -> list[str]:
    """Component parameters that fill themselves in with a figure.

    The registry is the other place strategy choices live, and it was not watched. Optional
    is allowed -- a parameter whose absence means "no such rule", like a liquidation level
    that is simply never reached. What is forbidden is a substituted number: it governs the
    run and appears nowhere a person reads back, which is the whole of the rule.

    Checked against each form's own signature, because that is where such a value would
    live rather than in the schema that describes it.
    """
    import inspect

    import backtest  # noqa: F401  -- populates the registry
    from backtest.core.component import variants

    out = []
    for variant in variants():
        signature = inspect.signature(variant.decide)
        for param in variant.params:
            if param.required:
                continue
            default = signature.parameters[param.name].default
            if default is not None:
                out.append(
                    f"{variant.ref}.{param.name} is optional and defaults to "
                    f"{default!r}. Optional must mean 'no such rule', not a value the "
                    f"platform chose"
                )
    return out


def check() -> int:
    problems = offenders() + unreachable_allowances() + component_offenders()
    if problems:
        print("the platform is defaulting a strategy choice:")
        for p in problems:
            print(f"  - {p}")
        print()
        print("A strategy choice is the researcher's. The architecture document permits "
              "optimising these under its protocol; it does not permit the platform "
              "picking one. A default also hides itself: it does not appear in the "
              "configuration a person reads back when asking what the run actually did.")
        return 1
    import backtest  # noqa: F401
    from backtest.core.component import variants

    watched = sum(len(f) for _, f, _ in WATCHED.values())
    in_registry = sum(len(v.params) for v in variants())
    print(f"OK  {watched} strategy parameters across {len(WATCHED)} slots and "
          f"{in_registry} across {len(variants())} registered forms, none defaulted")
    return 0


if __name__ == "__main__":
    sys.exit(check())
