"""The whole project's state on one screen.

## Why this exists

A project loses its coherence for one reason almost every time: **the same fact is
written in several places and then drifts.** The typical symptom is an interface still
showing "(5) headwind does not exist at all" when reductions are built, or a README
pointing at an engine that already landed.

What those have in common is that they are **hand-written** in several places: true
when written, and left behind when the code moved on. And they look authoritative,
which is worse than not being there.

## What to do about it

1. **Every fact is defined in exactly one place.** What a component is ->
   ``COMPONENTS`` in ``backtest/core/slots.py``; the dependency direction -> ``tools/check_deps.py``;
   whether the results changed -> ``tools/baseline.py``.
2. **Derived views are generated, never hand-written.** The interface's form, the gap
   list, and this status.
3. **A constraint a tool can check does not belong in a document alone** — a document
   cannot stop code.

This script is where the second rule lands: the overall picture is **computed**, so it
cannot go stale.

    python -m tools.status
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = {"done": "[x] ", "partial": "[~] ", "missing": "[ ] "}


def _components() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from backtest.core.slots import COMPONENTS, LAYERS

    lines = []
    for layer, what in LAYERS:
        members = [c for c in COMPONENTS if c.layer == layer]
        worst = ("missing" if any(c.status == "missing" for c in members)
                 else "partial" if any(c.status == "partial" for c in members) else "done")
        lines.append(f"\n  {MARK[worst]}{layer}   {what}")
        for c in members:
            tail = ""
            if c.origin == "discovered":
                tail = "   <- cannot be filled in; has to be researched" if c.status != "done" else ""
            lines.append(f"      {MARK[c.status]}{c.name}   {c.output}{tail}")
            if c.gap:
                lines.append(f"          missing: {c.gap[:64]}")
    return lines


def _lifecycle() -> list[str]:
    """The lifecycle settings the architecture leaves open.

    Shown beside the components because they change strategy behaviour just as much, and
    because four of the five are currently decided by accident rather than chosen.
    """
    sys.path.insert(0, str(ROOT))
    from backtest.signals.lifecycle import SETTINGS

    lines = []
    for s in SETTINGS:
        mark = MARK["done"] if not s.gap else MARK["partial"] if s.enforced_by else MARK["missing"]
        lines.append(f"  {mark}{s.name:22s} {s.enforced_by or 'nothing enforces it'}")
        if s.gap:
            lines.append(f"      missing: {s.gap[:84]}")
    return lines


def _thresholds() -> list[str]:
    """Every number the system assumes, in the architecture document's four groups.

    Printed because the alternative is that they live in call sites, where a judgement
    ("a plateau") is indistinguishable from a measurement. The group decides what may be
    done with each: a realism assumption is never optimised, a method choice is settled
    before looking at results.
    """
    sys.path.insert(0, str(ROOT))
    from backtest.core.thresholds import GROUPS, THRESHOLDS, get

    lines = []
    for group, rule in GROUPS.items():
        members = [t for t in THRESHOLDS.values() if t.kind == group]
        lines.append(f"\n  {group}  —  {rule}")
        if not members:
            lines.append("      (nothing: a strategy choice is never defaulted here)")
            continue
        for t in members:
            current = get(t.name)
            mark = " *changed*" if current != t.value else ""
            lines.append(f"      {t.name:28s} {current!r:>8}{mark}   {t.decides[:58]}")
    return lines


def _requirements() -> list[str]:
    """Count the acceptance criteria in requirements.md.

    These statuses are currently **hand-written** — a known inconsistency. Deriving
    them from the code would be better, but criteria like "the interface is clear" or
    "the statistics are honest" cannot be judged automatically. So this only counts
    them and says they are hand-written, rather than pretending they were computed.
    """
    path = ROOT / "docs" / "requirements.md"
    if not path.exists():
        return ["  (docs/requirements.md is absent)"]
    text = path.read_text(encoding="utf-8")
    rows = re.findall(r"^\| (\d+\.\d+) \| (.+?) \| (.+?) \|", text, re.M)
    counts = {"done": 0, "partial": 0, "missing": 0}
    for _, _, st in rows:
        for k in counts:
            if k in st:
                counts[k] += 1
                break
    total = sum(counts.values())
    return [
        f"  {total} acceptance criteria (**hand-written**, requirements.md): "
        f"{counts['done']} done · {counts['partial']} partial · {counts['missing']} missing",
    ]


def _guards() -> list[str]:
    out = []
    for name, cmd in (
        ("dependencies", [sys.executable, "tools/check_deps.py"]),
        ("baseline", [sys.executable, "-m", "tools.baseline", "check"]),
        ("language", [sys.executable, "tools/check_language.py"]),
    ):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out.append(f"  {'OK  ' if r.returncode == 0 else 'FAIL'} {name}")
    n = len(list((ROOT / "tests").glob("test_*.py")))
    out.append(f"  ·  {n} test files (run them: pytest)")
    return out


def main() -> int:
    print("=" * 62)
    print("  Options backtest platform · current state")
    print("=" * 62)
    print("\n[ the seven components ]  in four layers, ordered as a trade happens")
    print("\n".join(_components()))
    print("\n[ lifecycle ]  settings the architecture leaves open")
    print("\n".join(_lifecycle()))
    print("\n[ assumed numbers ]  what has been decided for you, and what may be done with it")
    print("\n".join(_thresholds()))
    print("\n[ acceptance ]")
    print("\n".join(_requirements()))
    print("\n[ guards ]  the constraints a tool can check")
    print("\n".join(_guards()))
    print("\n[ next ]  see the order of work in requirements.md")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
