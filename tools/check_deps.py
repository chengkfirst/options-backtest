"""The dependency-direction check.

Modules may depend only upwards from the bottom, and **pricing must not escape the
data layer**: `selection`, `engine` and `research` may not import `backtest.pricing`
directly. The layers above consume the chain's quote and greek columns and do not care
whether those numbers were observed in the market, supplied by a vendor, or derived
from a model. That constraint is what lets the data source be swapped with no change
above.

Pricing itself is a **market model**, not the synthetic chain's private property: the
synthetic chain uses it to make prices, and the file source uses it to fill in greeks a
vendor omits. So it lives in `backtest.pricing` (layer 0), available to every
implementation in the data layer.

This is a tool rather than a line in a document because a document cannot stop an
import.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "backtest"

#: The layers: a smaller number is further down. Upper layers may depend on lower
#: ones, never the reverse.
#:
#: ``report`` sits beside ``data``: it only reads the trade log and depends on nothing
#: upstream, which makes it a leaf rather than a top layer. Putting it on top would
#: make ``engine`` reusing its statistics a "reverse dependency", which would be a
#: mistake in the layering, not in the code. The constraint that actually matters is
#: expressed separately by ``REPORT_FORBIDDEN``.
LAYERS: dict[str, int] = {
    "maybe": 0, "errors": 0, "events": 0, "types": 0, "config": 0, "pricing": 0,
    #: components is the conceptual base: what each of the seven components decides in
    #: a trade and how they constrain one another. It **depends on nothing** (only text
    #: and data), hence the bottom layer; the interface, the docs and the research
    #: tools all read it, so the gap list is never written twice and left to drift.
    "components": 0,
    "data": 1, "report": 1,
    "signals": 2, "selection": 2, "costs": 2,
    "portfolio": 2, "risk": 2,
    "engine": 3,
    #: research **calls the engine to run backtests** (the sizing comparison runs one
    #: strategy many times), so it sits above the engine. risk is imported by the
    #: engine instead (aggregating daily greeks), so it sits below.
    "research": 4,
    "api": 5, "__init__": 5,
}

#: Only implementations in the data layer may import pricing — the synthetic chain
#: making prices and the file source filling in greeks both live there. Above that
#: (selection, engine, research) it is forbidden outright: they may only read the
#: chain's columns.
PRICING_MODULE = "backtest.pricing"
PRICING_ALLOWED_PREFIX = "backtest.data"

#: `report` only reads the trade log and must not depend on the engine or the data
#: layer.
REPORT_FORBIDDEN = {"data", "engine", "signals", "selection", "costs"}


def _layer_of(module: str) -> int | None:
    top = module.split(".")[1] if module.startswith(f"{PACKAGE}.") else None
    if top is None:
        return None
    return LAYERS.get(top)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return {m for m in found if m.startswith(PACKAGE)}


def check() -> list[str]:
    """Return the list of problems; empty means it passed.

    **A module not registered in LAYERS is a failure.** An earlier version skipped
    modules it did not recognise, and the newly added portfolio package escaped the
    check entirely — a defence with a silent failure of its own is worse than no
    defence.
    """
    problems: list[str] = []
    tops = {
        p.relative_to(ROOT / PACKAGE).parts[0].removesuffix(".py")
        for p in (ROOT / PACKAGE).rglob("*.py")
        if p.name != "__init__.py" or p.parent != ROOT / PACKAGE
    }
    for top in sorted(tops - set(LAYERS) - {"__pycache__"}):
        problems.append(
            f"the module {PACKAGE}.{top} is not registered in LAYERS, so its "
            f"dependency direction is not being checked. Give it a layer in LAYERS in "
            f"tools/check_deps.py."
        )
    for path in sorted((ROOT / PACKAGE).rglob("*.py")):
        rel = path.relative_to(ROOT).with_suffix("")
        module = ".".join(rel.parts)
        own_layer = _layer_of(module)
        if own_layer is None:
            continue
        own_top = module.split(".")[1]

        for imported in _imports(path):
            target_layer = _layer_of(imported)
            if target_layer is None:
                continue
            target_top = imported.split(".")[1]

            if imported.startswith(PRICING_MODULE) and not module.startswith(
                PRICING_ALLOWED_PREFIX
            ):
                problems.append(
                    f"{module} imports {imported} directly; pricing is confined to the "
                    f"data layer, and the layers above must consume the chain's columns"
                )
            if own_top == "report" and target_top in REPORT_FORBIDDEN:
                problems.append(f"{module} must not depend on {imported}; report only "
                                f"reads the trade log")
            if target_layer > own_layer:
                problems.append(
                    f"{module} (layer {own_layer}) imports {imported} "
                    f"(layer {target_layer}): a reverse dependency"
                )
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("the dependency-direction check failed:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("the dependency-direction check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
