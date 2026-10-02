"""The dependency-direction check.

Modules may depend only upwards from the bottom, and **pricing must not escape the
data layer**: `selection`, `engine` and `research` may not import `backtest.data.options.pricing`
directly. The layers above consume the chain's quote and greek columns and do not care
whether those numbers were observed in the market, supplied by a vendor, or derived
from a model. That constraint is what lets the data source be swapped with no change
above.

Pricing itself is a **market model**, not the synthetic chain's private property: the
synthetic chain uses it to make prices, and the file source uses it to fill in greeks a
vendor omits. So it lives in `backtest.data.options.pricing` (layer 0), available to every
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

#: The layers, numbered as in the architecture document. A smaller number is further
#: down; an upper layer may import a lower one, never the reverse. Two packages on the
#: same layer may import each other.
LAYERS: dict[str, int] = {
    #: Layer 0 · Foundation. The contract every layer shares.
    #: `slots` is the conceptual base: what each of the seven components decides in a
    #: trade and how they constrain one another. It **depends on nothing** (only text
    #: and data), so it belongs at the bottom; the interface, the docs and the research
    #: tools all read it, which is why the gap list is never written twice and left to
    #: drift.
    "core": 0,
    #: Layer 1 · Data. Raw data to clean data that was genuinely available at t.
    "data": 1,
    #: Layer 2 · Signals. What to buy or sell, and how strongly. Hosts the Setup,
    #: Entry, Take Profit, Stop Loss, Headwind and per-trade Hedge slots.
    "signals": 2,
    #: Layer 3 · Portfolio and risk. Signals to target positions, within limits.
    #: Hosts the Positioning slot and the book-wide Headwind and Hedge slots.
    "portfolio": 3,
    #: Layer 4 · Execution simulation. How much fills, and at what price.
    "execution": 4,
    #: Layer 5 · Engine and accounting. The engine drives the order of events;
    #: accounting records every movement of money. They sit on one layer because the
    #: engine writes to the ledger and reads positions back each period.
    "engine": 5, "accounting": 5,
    #: Layer 6 · Research and reports. Is the result real.
    "research": 6,
    #: Layer 7 · Interfaces. The SDK, the CLI and the local web app.
    "api": 7, "__init__": 7,
}

#: Only implementations in the data layer may import pricing — the synthetic chain
#: making prices and the file source filling in greeks both live there. Above that
#: (selection, engine, research) it is forbidden outright: they may only read the
#: chain's columns.
PRICING_MODULE = "backtest.data.options.pricing"
PRICING_ALLOWED_PREFIX = "backtest.data"

#: `research.metrics` only reads a trade log. Keeping it free of the engine and the
#: data layer is what lets any layer compute statistics from a trade log without
#: dragging the whole system in. This is the constraint the old "report sits beside
#: data" layering was really about.
METRICS_MODULE = "backtest.research.metrics"
METRICS_FORBIDDEN = {"data", "engine", "signals", "execution", "portfolio"}


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
            if module == METRICS_MODULE and target_top in METRICS_FORBIDDEN:
                problems.append(f"{module} must not depend on {imported}; metrics only "
                                f"reads a trade log")
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
