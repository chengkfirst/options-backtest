"""Run every guard with one command.

    python -m tools.check          # all of them
    python -m tools.check --fast   # skip the slow ones (test suite, doc examples)

## This project has exactly one convention

**A convention a tool can check is a convention. One that only lives in a
document is a wish.**

Each guard answers to one shape of **silent failure** — it ran, it produced a
result, it raised nothing, and the result is wrong:

=====================  ====================================================
Guard                   The failure shape it answers to
=====================  ====================================================
test suite              behavioural regression
dependency direction    pricing escaping the data layer; unregistered modules
regression baseline     an unintended change in results
shipped completeness    present locally, absent from git — a clone is broken
front/back contract     the UI needs a field the server never sends
doc examples            the snippets in the README do not run
component definitions   a form field that is not a run() parameter: no effect
language                mixed-language identifiers that stop matching
vocabulary              two words for one concept on two screens
standing assumptions    a sheet row that states its own answer and goes stale
self-test coverage      a suite that quietly stops covering a module
=====================  ====================================================

The bar for a new convention: **a concrete failure first, then the guard.**
Risks that have never materialised get no defence, or the guard itself becomes
noise nobody reads.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: (name, command, is it slow)
GUARDS: tuple[tuple[str, list[str], bool], ...] = (
    ("dependencies", [sys.executable, "tools/check_deps.py"], False),
    ("shipped",      [sys.executable, "tools/check_shipped.py"], False),
    ("language",     [sys.executable, "tools/check_language.py"], False),
    ("defaults",     [sys.executable, "tools/check_defaults.py"], False),
    ("plan",         [sys.executable, "tools/check_plan.py"], False),
    ("coverage map", [sys.executable, "-m", "pytest", "tests/test_coverage_map.py", "-q"],
     False),
    ("labels",       [sys.executable, "-m", "pytest", "tests/test_labels.py", "-q"],
     False),
    ("components",   [sys.executable, "-m", "pytest", "tests/test_components.py", "-q"], False),
    ("wiring",       [sys.executable, "tools/check_wiring.py"], False),
    ("contrast",     [sys.executable, "tools/check_contrast.py"], False),
    # The three interface guards. They were written and then never registered here, so a
    # convention this project holds to -- one word per concept, a standing-assumptions sheet
    # that reads the record, a self-test that still covers every module -- was checked only
    # by whoever remembered to run it. That is the state the first line of this file calls a
    # wish rather than a convention.
    ("words",        [sys.executable, "tools/check_words.py"], False),
    ("evidence",     [sys.executable, "tools/check_evidence.py"], False),
    ("self-test",    [sys.executable, "tools/check_selftest.py"], False),
    ("modules",      [sys.executable, "tools/check_modules.py"], False),
    ("baseline",     [sys.executable, "-m", "tools.baseline", "check"], False),
    ("test suite",   [sys.executable, "-m", "pytest", "tests", "-q"], True),
    ("plan parts",   [sys.executable, "-m", "pytest",
                      "tests/test_plan_components.py", "-q"], False),
    ("plan details", [sys.executable, "-m", "pytest",
                      "tests/test_plan_details.py", "-q"], False),
    ("doc claims",   [sys.executable, "tools/check_doc_claims.py"], False),
    ("doc examples", [sys.executable, "tools/check_docs.py"], True),
)


def main() -> int:
    ap = argparse.ArgumentParser(description="run every guard")
    ap.add_argument("--fast", action="store_true", help="skip the slow guards")
    args = ap.parse_args()

    results: list[tuple[str, bool, float, str]] = []
    for name, cmd, slow in GUARDS:
        if slow and args.fast:
            continue
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == 0
        tail = ""
        if not ok:
            lines = [x for x in (r.stdout + r.stderr).strip().splitlines() if x.strip()]
            tail = lines[-1] if lines else "(no output)"
        results.append((name, ok, time.time() - t0, tail))
        print(f"  {'OK  ' if ok else 'FAIL'} {name:13s} {time.time() - t0:5.1f}s"
              + (f"   {tail[:70]}" if tail else ""))

    bad = [n for n, ok, _, _ in results if not ok]
    _record(passed=not bad, partial=args.fast)
    print()
    if bad:
        print(f"FAIL  {len(bad)} guard(s) did not pass: {', '.join(bad)}")
        print("  See the hints above; run one on its own for its full output.")
        return 1
    print(f"OK  all {len(results)} guards passed"
          + ("  (--fast: slow ones skipped)" if args.fast else ""))
    return 0


def _record(*, passed: bool, partial: bool) -> None:
    """Write down whether this code passed, so a research result can say which it was.

    A figure produced while the guards were red has no backing from the suite, and nothing
    recorded which kind of result it was. ``backtest.core.run_id.guards_were_green``
    reads this and reports three states -- green, red, and **no record**, which is the
    ordinary state on a machine where the guards have not been run and must not be
    reported as either of the others.

    A ``--fast`` run is recorded as not passing: it skipped the slow guards, so it cannot
    say the code is green, and saying so would be the quietest possible way to lose the
    distinction.
    """
    import json
    from datetime import datetime

    from backtest.core.run_id import GUARD_RECORD, code_version

    target = ROOT / GUARD_RECORD
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "code": code_version(ROOT),
        "passed": bool(passed and not partial),
        "partial": bool(partial),
        "when": datetime.now().isoformat(timespec="seconds"),
    }, indent=1), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
