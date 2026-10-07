"""The browser self-test is wired up and reachable.

`app/static/selftest.html` runs every frontend module against the cases that have broken
them — absence printed as a number, a chart given no rows, an escape safe in text and not
in an attribute, a tab that renders nothing. It runs in a browser, which this cannot do, so
what it checks is the part that rots silently: that the page exists, that it loads the
suite, and that **every module in the tree is actually exercised by it**.

A suite that quietly stops covering a module is worse than no suite, because the next
person reads "17 of 17 passed" and believes it covers what it used to.

Run the suite itself at /selftest.html.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
PAGE = STATIC / "selftest.html"
SUITE = STATIC / "js" / "selftest.js"

#: Modules the suite is not expected to import directly. `app.js` boots the shell and
#: `states.js` is itself a review surface; both are covered by the whole-page cases, which
#: load the real page in a frame.
COVERED_BY_THE_PAGE_CASES = {
    "app.js",         # boots the shell; the whole-page cases load it
    "states.js",      # itself a review surface
    "selftest.js",    # this suite
    # The views are exercised the only way that means anything: the real page, in a frame,
    # one case per tab. Importing them here would run their module body without the shell
    # they address by id and prove nothing.
    "views/bench.js", "views/run.js",
}


def modules() -> list[str]:
    """Every module in the tree, as the path the suite would import."""
    out = []
    for path in sorted((STATIC / "js").rglob("*.js")):
        out.append(str(path.relative_to(STATIC / "js")))
    return out


def main() -> int:
    problems: list[str] = []

    if not PAGE.exists():
        print(f"the self-test page is missing: {PAGE.relative_to(ROOT)}")
        return 1
    page = PAGE.read_text(encoding="utf-8")
    if "js/selftest.js" not in page:
        problems.append("selftest.html does not load js/selftest.js")
    if 'id="sandbox"' not in page:
        problems.append(
            "selftest.html has no #sandbox. The renderers look their hosts up by id, so "
            "they need somewhere off-screen to draw into."
        )
    # The rule, not the word: the comment beside the sandbox explains why it is *not*
    # display:none, and matching the prose instead of the declaration made this guard
    # report the thing it was written to prevent.
    if re.search(r"#sandbox\s*\{[^}]*display:\s*none", page):
        problems.append(
            "the sandbox is hidden with display:none. A chart measured inside it has no "
            "width and draws nothing, which would make every size assertion pass for the "
            "wrong reason."
        )

    suite = SUITE.read_text(encoding="utf-8")
    imported = set(re.findall(r'import\(["\']\.\/([^"\']+)["\']\)', suite))
    cases = len(re.findall(r"^suite\(", suite, re.M))
    if cases < 5:
        problems.append(f"only {cases} cases; the suite has stopped being one")

    for mod in modules():
        if mod in COVERED_BY_THE_PAGE_CASES:
            continue
        if mod not in imported:
            problems.append(
                f"{mod} is in the tree and the self-test never imports it. Either add a "
                f"case for it or say here why the page cases cover it."
            )

    if problems:
        print("the self-test check failed:")
        for p in problems:
            print(f"  - {p}")
        print("\nA suite that silently stops covering a module still reports that it "
              "passed.")
        return 1

    print(f"the self-test check passed ({cases} cases covering "
          f"{len(imported)} of {len(modules()) - len(COVERED_BY_THE_PAGE_CASES)} modules)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
