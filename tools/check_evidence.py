"""Every channel the backend can report a limitation on reaches the screen.

The analysis page's Limitations list is the thing a reader uses to decide how much of the
rest of the page to believe, so the question it has to answer is not "are these true" but
"**is this all of them**". A list that silently omits a channel is worse than no list: it
reads as a clean bill of health.

That omission is not hypothetical. The engine writes `diagnostics` while it runs — on one
stored run, 409 days where the American early-exercise tree disagreed with the flag about
whether holding beat exercising — and for months the interface used them only to place
event markers on a chart. The page drew a tick for each one and never said that the option
model had been uncertain four hundred times over. Nothing raised, because not reading a
field is not an error.

So `views/run.js` declares `EVIDENCE_CHANNELS`, and every `add(...)` inside
`limitationItems` names the channel it read. This checks the two against each other:

1. every declared channel is consumed by at least one `add()`
2. every channel an `add()` names is declared

Rule 1 catches the real failure — a backend channel nobody reads. Rule 2 catches the
bookkeeping drifting out of step with the code, which is how rule 1 stops meaning
anything.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_VIEW = ROOT / "app" / "static" / "js" / "views" / "run.js"


def declared(src: str) -> list[str]:
    """The channels `EVIDENCE_CHANNELS` lists."""
    m = re.search(r"const EVIDENCE_CHANNELS = \[(.*?)\];", src, re.S)
    if not m:
        raise SystemExit(
            f"no EVIDENCE_CHANNELS declaration in {RUN_VIEW.relative_to(ROOT)}. The "
            f"Limitations list is only auditable while it says what it reads."
        )
    return re.findall(r'"([^"]+)"', m.group(1))


def consumed(src: str) -> list[str]:
    """The channels `limitationItems` actually reads, from its `add()` calls.

    Bounded to that function: `add` is a short name and a scan of the whole file would
    pick up any other. The body runs to the first line that closes it at column zero.
    """
    start = src.index("function limitationItems(){")
    end = src.index("\n}", start)
    return re.findall(r'\badd\(\s*"([^"]+)"', src[start:end])


def main() -> int:
    src = RUN_VIEW.read_text(encoding="utf-8")
    says, reads = declared(src), set(consumed(src))

    unread = [c for c in says if c not in reads]
    undeclared = sorted(reads - set(says))

    problems = []
    for channel in unread:
        problems.append(
            f"{channel} is declared as a source of limitations and no add() reads it. "
            f"Either the interface is dropping what the backend reports there, or the "
            f"channel is gone and the declaration should go with it."
        )
    for channel in undeclared:
        problems.append(
            f'limitationItems() reads "{channel}", which EVIDENCE_CHANNELS does not list. '
            f"Add it, so the list of what this page reads stays checkable."
        )

    if problems:
        print("the evidence-channel check failed:")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nA limitation nobody renders is not a smaller list, it is a wrong one: the "
            "page reads as though nothing was found."
        )
        return 1

    print(f"the evidence-channel check passed ({len(says)} channels, all consumed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
