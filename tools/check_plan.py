"""Every section of the architecture document is accounted for — checked, not remembered.

The document is 38 pages of prose and a tool cannot read it. What a tool *can* do is hold
the list of its sections and refuse a `requirements.md` that does not say something about
each one. That turns "did I miss anything" from a question about my attention into a
question with an answer.

It is deliberately a weak check, and the weakness is the point. It cannot tell whether a
row is *true*; it can only tell whether a section has been walked at all. Three separate
rounds of going through the document by eye each turned up sections that had been skipped
without anyone noticing, which is exactly the failure mode a list prevents and judgement
does not.

The second half checks the document's **directory structure**, which it gives explicitly.
That one is mechanical: the document names a directory per slot for that slot's variants,
and a tool can see whether it exists.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.md"

#: Every numbered section of the architecture document, with a short title.
#:
#: Transcribed once from the document's own headings. A section added to the document and
#: not added here would be invisible to this check -- so the count is asserted below, and
#: changing it is a deliberate edit rather than a side effect.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("0.1", "Events"),
    ("0.2", "Component protocol"),
    ("0.3", "Clock and data view"),
    ("0.4", "Error prevention"),
    ("0.5", "Configuration and versioning"),
    ("0.6", "Logging and traceability"),
    ("1", "Data"),
    ("2.1", "Signal output"),
    ("2.2", "Two interface styles"),
    ("2.3", "Boundaries"),
    ("3.1", "Risk actions"),
    ("3.2", "Output (TargetPortfolioEvent)"),
    ("3.3", "Who owns which risk"),
    ("4.1", "Option fill price"),
    ("4.2", "Suggested defaults"),
    ("5.1", "Engine"),
    ("5.2", "Accounting"),
    ("5.3", "Greeks P&L attribution"),
    ("6.1", "Metrics"),
    ("6.2", "Pre-launch checklist"),
    ("6.3", "Component research"),
    ("6.4", "Research protocol"),
    ("7.1", "REST API draft"),
    ("7.2", "Switching between backtest and live"),
    ("R1", "Register: realism assumptions"),
    ("R2", "Register: engineering trade-offs"),
    ("R3", "Register: strategy choices to research"),
    ("R4", "Register: research-method trade-offs"),
    ("S", "Strategy model: 7 slots, lifecycle, trade context"),
    ("D", "Directory structure"),
)

#: The directories the document names, and what each is for. A missing one is reported
#: rather than failing: several are for work that is not built, and a directory created to
#: satisfy a check would be worse than an honest gap.
DIRECTORIES: dict[str, str] = {
    "backtest/core": "events, actions, component, registry, clock, dataview, invariants",
    "backtest/data": "ingest, validate, clean, adjust, pit, universe, features, store",
    "backtest/data/options": "contracts, quotes, rates, iv, greeks, surface",
    "backtest/signals": "lifecycle, universe, filters, combiner, strategy",
    "backtest/signals/setup": "slot 1 variants",
    "backtest/signals/entry": "slot 2 variants",
    "backtest/signals/take_profit": "slot 3 variants",
    "backtest/signals/stop_loss": "slot 4 variants",
    "backtest/signals/headwind": "slot 5 variants, per trade",
    "backtest/signals/hedge": "slot 7 variants, per trade",
    "backtest/signals/rolls": "roll rules",
    "backtest/portfolio": "rebalance, optimizers, risk models, greeks, scenarios, "
                          "constraints, risk rules, hedges",
    "backtest/portfolio/positioning": "slot 6 variants",
    "backtest/execution": "orders, market rules, fill models, slippage, costs, exercise, "
                          "sim broker",
    "backtest/engine": "loop, scheduler, runner, batch, vectorized",
    "backtest/research": "the research tools",
    "backtest/api": "the SDK",
}

#: The heading the conformance table lives under, and the pattern of a row in it.
TABLE_HEADING = "## Conformance with the architecture document"
ROW = re.compile(r"^\|\s*([0-9]+(?:\.[0-9]+)?|R[1-4]|S|D)\s*\|", re.M)


def covered() -> set[str]:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    if TABLE_HEADING not in text:
        return set()
    table = text.split(TABLE_HEADING, 1)[1].split("\n## ", 1)[0]
    return {m.group(1) for m in ROW.finditer(table)}


def missing_directories() -> list[str]:
    return [f"{d} ({why})" for d, why in DIRECTORIES.items() if not (ROOT / d).is_dir()]


def check() -> int:
    if len(SECTIONS) != 30:
        print(f"SECTIONS holds {len(SECTIONS)} entries and the count asserted here is 30. "
              f"Adding a section is a deliberate edit; update both.")
        return 1

    have = covered()
    gaps = [(num, title) for num, title in SECTIONS if num not in have]
    if gaps:
        print("these sections of the architecture document have no row in "
              "requirements.md:")
        for num, title in gaps:
            print(f"  - {num}  {title}")
        print()
        print(f"Add a row under '{TABLE_HEADING}' saying what the section asks for and "
              f"where the system stands. A row saying 'not built, and here is why' is a "
              f"complete answer; no row at all means nobody has read that section against "
              f"the system.")
        return 1

    absent = missing_directories()
    print(f"OK  {len(SECTIONS)} document sections, all accounted for")
    if absent:
        # Reported, not failed. Several of these are for work that does not exist, and a
        # directory created to satisfy a check is worse than an honest gap.
        print(f"    {len(absent)} of the document's {len(DIRECTORIES)} directories do not "
              f"exist yet:")
        for d in absent:
            print(f"      - {d}")
    return 0


if __name__ == "__main__":
    sys.exit(check())
