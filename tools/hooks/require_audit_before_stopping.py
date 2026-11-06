#!/usr/bin/env python3
"""Refuse to finish while `backtest/` has uncommitted changes and nothing audited them.

## The failure this answers to

An engine change can be wrong in a way that raises nothing, finishes normally, and
reports a number somebody acts on. That is what `quant-auditor` is for, and in this
repository it has already earned its place twice in one night: it found that per-trade
component state outlived the run -- so a second run of one strategy object produced a
different trade count under an identical ``run_id`` -- and that a trailing stop's
high-water mark started below entry on every trade.

Neither was found by a guard. Both were found because somebody remembered to run the
audit. ``CLAUDE.md`` now says to run it after every ``engine-dev`` change, and a rule
that lives only in a document is the thing this project calls a wish.

So: while there are uncommitted changes under ``backtest/`` and no audit report exists
for them, the session does not finish.

## What counts as an audit report

A file under ``docs/findings/`` whose modification time is **newer than the newest
uncommitted file under ``backtest/``**. That is deliberately crude, and the crudeness is
the point: a stricter rule (parsing the report, matching it to a diff) would be a rule
that goes wrong in ways nobody can see, and this one fails in the safe direction -- it
asks for an audit that may already have happened rather than letting one be skipped.

Writing the report is how you satisfy it, which is the behaviour wanted anyway.

## What it does not do

It does not check the report says anything useful; nothing automatic can. It does not
fire for `app/`, `tools/`, `tests/` or documents -- only `backtest/`, where a wrong
number comes from. And it stands aside the moment it cannot be sure, for the reason
every hook in this repository does: a hook that blocks a session nobody can escape is
worse than the mistake it guards.

## Testing it

It reads JSON on stdin and answers with an exit code, so it is driven directly::

    echo '{"hook_event_name":"Stop","stop_hook_active":false,"cwd":"..."}' \\
        | tools/hooks/require_audit_before_stopping.py; echo $?

``tests/test_audit_hook.py`` holds that list.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

#: Where a wrong number comes from. A change under `app/` can make the interface wrong;
#: a change here can make the *result* wrong while everything still looks normal, which
#: is a different kind of problem and the only one this hook is about.
WATCHED = "backtest"

#: Where `quant-auditor` writes. Its definition says so, and so does
#: `docs/findings/README.md`.
REPORTS = Path("docs") / "findings"


def changed_under(root: Path, prefix: str) -> list[Path]:
    """Uncommitted files under `prefix` -- staged, unstaged or untracked."""
    out = subprocess.run(["git", "status", "--porcelain", "--", prefix],
                         cwd=root, capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        return []
    found = []
    for line in out.stdout.splitlines():
        # "XY path" and "XY old -> new" for a rename; the destination is what exists.
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            found.append(root / path)
    return found


def newest_mtime(paths: list[Path]) -> float:
    best = 0.0
    for p in paths:
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            continue        # deleted, or unreadable: it cannot date the comparison
    return best


def audited_since(root: Path, when: float) -> bool:
    """Is there a findings file written after `when`."""
    reports = root / REPORTS
    if not reports.is_dir():
        return False
    for report in reports.rglob("*.md"):
        if report.name == "README.md":
            continue        # the directory's own description is not an audit
        try:
            if report.stat().st_mtime >= when:
                return True
        except OSError:
            continue
    return False


MESSAGE = """\
There are uncommitted changes under `{watched}/` and no audit report newer than them.

A change here can make a backtest *result* wrong while everything still runs, raises
nothing and reports a number -- which is what `quant-auditor` exists to catch, and what
it caught twice on this repository already: component state outliving a run under an
identical run_id, and a trailing stop's mark starting below entry on every trade.

Do one of these before finishing:

  - run `quant-auditor` on the change and write its report to `docs/findings/`
  - commit the change, if it has already been audited
  - say plainly, in your final message, that the change is not audited and why

Changed: {files}
"""


def main() -> int:
    payload = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return 0
    if not isinstance(payload, dict):
        return 0

    # **The loop guard.** When a Stop hook has already blocked, this field is true on the
    # next stop. Blocking again would leave a session with no way out, which is a worse
    # failure than an unaudited change -- see the module docstring.
    if payload.get("stop_hook_active"):
        return 0

    raw_root = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        root = Path(raw_root).resolve()
    except (TypeError, ValueError, OSError):
        return 0
    if not (root / ".git").exists():
        return 0            # not this repository: nothing to say

    changed = changed_under(root, WATCHED)
    if not changed:
        return 0

    if audited_since(root, newest_mtime(changed) or time.time()):
        return 0

    shown = ", ".join(sorted(str(p.relative_to(root)) for p in changed)[:6])
    if len(changed) > 6:
        shown += f", and {len(changed) - 6} more"
    print(MESSAGE.format(watched=WATCHED, files=shown), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                                  # noqa: BLE001
        # A Stop hook that fails closed on its own bug is a session that cannot end.
        print(f"the audit-before-stopping hook could not run ({exc!r}); allowing the stop",
              file=sys.stderr)
        raise SystemExit(0) from exc
