"""The sheet of standing assumptions is complete, and reads the record rather than itself.

Checking is the backend's work. What the analysis page owns is the sheet that says **what
the machinery assumed and where that limits the answer** — the data's limitations, the
pricing model's assumptions, what the fill model leaves out — filled in with this run's
values. Two properties make that sheet worth reading, and neither holds on its own.

**It is the same sheet every time.** A list that varies in length reads as "here is what we
happened to find", and a quiet run cannot be told from an unexamined one. So every row
states its axis whatever this run did on it, including when the answer is fine.

**Every value comes from the record.** A row that writes its own answer is a claim about
code that has moved on since. `slippage_per_contract` is the example that matters: writing
"no market impact is modelled" as a sentence is true today and silently false the day
someone sets a default, whereas reading `config.costs.slippage_per_contract` stays true by
construction.

So this checks `SHEET` in `views/run.js`:

1. every row has all three of `what`, `value` and `limits`. A row missing `limits` states a
   setting with no consequence, which is the half the reader needs.
2. every `value` reads the run record. A literal-only value is a hardcoded claim.
3. every `limits` can return `ok(...)`, **unless** the row declares `always:` — a reason
   why this platform has no configuration in which that axis is fine. Without the marker a
   row that only ever reports a fault is indistinguishable from one somebody forgot to
   finish; with it, the permanent limitations of the platform are a list you can read.
4. no two rows share a `what` inside one section.

Rule 2 is the one with history behind it. `diagnostics` went unread for months while the
engine logged 409 early-exercise disagreements on a single run — not an error, just a fact
that never reached the screen.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_VIEW = ROOT / "app" / "static" / "js" / "views" / "run.js"


def sections(src: str) -> list[tuple[str, str]]:
    """Each section of `SHEET` as (title, body)."""
    m = re.search(r"const SHEET = \[(.*?)\n\];", src, re.S)
    if not m:
        raise SystemExit(
            f"no SHEET table in {RUN_VIEW.relative_to(ROOT)}. The page is only auditable "
            f"while one declaration says what every run stands on."
        )
    body = m.group(1)
    parts = re.split(r"\n  \{\n    area: ", body)
    out = []
    for part in parts[1:]:
        title = re.search(r'title: "([^"]+)"', part)
        out.append((title.group(1) if title else "(unnamed)", part))
    return out


def rows_of(body: str) -> list[tuple[str, str]]:
    """Each row of one section as (what, body), split on the `{what:` that opens one."""
    parts = re.split(r"\n      \{what: ", body)
    out = []
    for part in parts[1:]:
        name = re.match(r'"([^"]+)"', part)
        if not name:
            raise SystemExit("a SHEET row has no `what` as a string literal")
        out.append((name.group(1), part))
    return out


def main() -> int:
    src = RUN_VIEW.read_text(encoding="utf-8")
    problems: list[str] = []
    total = 0

    for title, body in sections(src):
        seen: set[str] = set()
        for what, row in rows_of(body):
            total += 1
            where = f"{title} / {what}"

            if "value:" not in row:
                problems.append(f"{where} has no value(), so the row cannot say what this "
                                f"run did on that axis.")
            if "limits:" not in row:
                problems.append(f"{where} has no limits(), so it states a setting and "
                                f"never what that setting costs the reader.")

            # The value has to come out of the record. A value built only from literals is
            # a claim written here about code that is free to move.
            # The body must use the run, whether by reaching into it (`r.manifest`) or by
            # handing it to a helper that does (`anomaly(r, "overlap")`). Looking only for
            # `r.` missed the second and reported a row that does read the record.
            value = re.search(r"value: ([^\n]*(?:\n(?!\s{6,7}\w+:)[^\n]*)*)", row)
            body = re.sub(r"^[^=]*=>", "", value.group(1), count=1) if value else ""
            if value and not re.search(r"\br\b", body):
                problems.append(
                    f"{where} writes its value without reading the run record. A sentence "
                    f"here is true until the backend changes and false silently after; "
                    f"read the manifest field instead."
                )

            limits = re.search(r"limits: ([^\n]*(?:\n(?!\s{6}\w+:)[^\n]*)*)", row)
            permanent = re.search(r'always: "([^"]+)"', row)
            if limits and "ok(" not in limits.group(1) and not permanent:
                problems.append(
                    f"{where} has no ok() branch and does not declare `always:`. Either "
                    f"the row goes blank when the axis is fine — and a blank cannot be "
                    f"told from an axis nobody examined — or this platform has no good "
                    f"state here, which is worth saying out loud."
                )

            if what in seen:
                problems.append(f"{title} lists “{what}” twice.")
            seen.add(what)

    if problems:
        print("the standing-assumptions check failed:")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nThe sheet is read to decide how much of the rest of the page to believe. A "
            "row that is missing, blank, or written rather than read makes it say less "
            "than it appears to."
        )
        return 1

    print(f"the standing-assumptions check passed "
          f"({len(sections(src))} subjects, {total} axes, every one read from the record)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
