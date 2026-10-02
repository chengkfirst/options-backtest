"""Shipped completeness: what runs locally has to run from a fresh clone.

## Why this exists

`.gitignore` contained `research/`, meant to exclude local research notes. But **a
pattern without a leading slash matches a directory of that name at any depth**, so the
whole `backtest/research/` package went unpublished — six modules (MFE/MAE, regime
stratification, the sizing comparison, event studies, the trade-off surface) did not
exist on the remote, while their tests did. A fresh clone crashed outright.

It went unnoticed because this kind of error **leaves no trace**: `git status` was
clean, `git add -A` raised nothing, and the local tests were green. Only a clone on
another machine shows it.

So the rule has to be held by a tool: **every .py in the package must be tracked by
git.**
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: Code in these directories must be published. Research notes and caches are meant
#: to be excluded and are not listed here.
SHIPPED = ("backtest", "tests", "tools", "app")


def tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return set(out.stdout.splitlines())


def main() -> int:
    known = tracked()
    if not known:
        print("not a git repository; skipping the shipped-completeness check")
        return 0

    missing: list[str] = []
    for top in SHIPPED:
        base = ROOT / top
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.suffix not in (".py", ".js", ".css", ".html"):
                continue
            rel = str(path.relative_to(ROOT))
            if rel not in known:
                missing.append(rel)

    if missing:
        print("the shipped-completeness check failed: these files exist locally and "
              "not in git")
        for m in sorted(missing):
            reason = subprocess.run(["git", "check-ignore", "-v", m],
                                    cwd=ROOT, capture_output=True, text=True).stdout.strip()
            print(f"  - {m}" + (f"\n      ignored by this rule: "
                                 f"{reason.split(':')[0]}:{reason.split(':')[1]}"
                                 if reason else "  (not yet git added)"))
        print("\nA fresh clone will be missing these. Check whether a .gitignore "
              "pattern is too broad — one without a leading slash matches a directory "
              "of that name at any depth.")
        return 1

    print("the shipped-completeness check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
