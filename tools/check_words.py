"""One word per concept, on screen.

The design plan's addendum gives a terms table: the interface uses the architecture's
vocabulary so that screens, logs, configs and the API say the same thing. A reader who sees
"component" on one page and "slot" on the next has to work out whether they are the same
thing, and the answer is not always yes — which is the whole reason the table exists.

This checks the **user-visible** strings. Identifiers are exempt and must be: `components`
is a field in the API payload and renaming it here would break the contract the wiring
check holds. What is checked is text that reaches a screen.

It also holds two microcopy rules from the same page, both of which are about a reader
being able to act:

- a button says a verb **and its object** — "clear range", not "clear"; never "OK" or
  "Submit", which name neither
- no exclamation marks, and no "please"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURFACES = [
    ROOT / "app" / "static" / "index.html",
    ROOT / "app" / "static" / "js" / "app.js",
    ROOT / "app" / "static" / "js" / "views" / "bench.js",
    ROOT / "app" / "static" / "js" / "views" / "run.js",
]

#: word on screen -> the word the terms table asks for, and why.
INSTEAD = {
    "component": ("slot", "the position in the strategy"),
    "components": ("slots", "the position in the strategy"),
    "module": ("slot", "the position in the strategy"),
    "plugin": ("variant", "what fills a slot"),
    "implementation": ("variant", "what fills a slot"),
    "test set": ("hold-out", "locked per idea, revealed once"),
    "backtest result": ("run", "a run is the result; job is only queue status"),
    "exit reason": ("closing slot and action", "e.g. Stop Loss · CLOSE"),
}

#: A bare verb names no object. The table's examples are "Run", "Compare with baseline",
#: "Ablate Stop Loss", "Reveal hold-out" — the first is a verb whose object is the page it
#: sits on, which is why it is the one single word the rule allows.
BARE_VERBS = {"ok", "submit", "cancel", "apply", "go", "clear", "save", "update",
              "continue", "next", "back", "close", "open", "yes", "no"}


def visible(src: str) -> list[str]:
    """Strings that reach a screen: markup text and quoted labels, comments removed."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    out = []
    # Text between tags, and the contents of a title or aria-label.
    out += re.findall(r">([^<>{}`$]{2,90})<", src)
    out += re.findall(r'(?:title|aria-label|placeholder)="([^"]{2,90})"', src)
    return [t.strip() for t in out if t.strip()]


def buttons(src: str) -> list[str]:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return [t.strip() for t in re.findall(r">([^<>{}`$]{1,40})</button>", src)]


def main() -> int:
    problems: list[str] = []

    for path in SURFACES:
        if not path.exists():
            continue
        name = path.name
        src = path.read_text(encoding="utf-8")

        for text in visible(src):
            low = text.lower()
            for word, (want, why) in INSTEAD.items():
                if re.search(r"\b" + re.escape(word) + r"\b", low):
                    problems.append(
                        f'{name}: "{text[:56]}" says "{word}"; the terms table asks for '
                        f'"{want}" — {why}'
                    )
            if "!" in text and not text.endswith("!="):
                problems.append(f'{name}: "{text[:56]}" has an exclamation mark')
            if re.search(r"\bplease\b", low):
                problems.append(f'{name}: "{text[:56]}" says please')

        for label in buttons(src):
            if label.lower() in BARE_VERBS:
                problems.append(
                    f'{name}: the button "{label}" names a verb and no object. A reader '
                    f'decides from the label alone what pressing it does.'
                )

    if problems:
        print("the vocabulary check failed:")
        for p in sorted(set(problems)):
            print(f"  - {p}")
        print("\nOne word per concept: a reader who meets two for the same thing has to "
              "work out whether they are the same thing.")
        return 1

    print(f"the vocabulary check passed ({len(SURFACES)} surfaces)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
