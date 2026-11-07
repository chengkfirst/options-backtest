#!/usr/bin/env python3
"""Refuse an edit outside the paths an agent owns.

## The failure this answers to

`engine-dev` owns `backtest/` and the tests for what it changes; `interface-dev` owns
`app/`. Written as instructions, that holds right up to the moment it is inconvenient --
an engine change needs one field in the composer, the agent is right there, and it reaches
across. Nothing raises. The two sides then move in one commit by one agent, which is the
thing `CONTRACT` and `check_wiring` exist to prevent from happening *silently*: the guards
would still catch a contract mismatch, but they cannot catch the organisational failure of
one agent owning both sides, which is what makes the mismatch likely in the first place.

So the ownership is enforced where the edit happens, not only described in the agent's
instructions. The point is not that the agent is untrustworthy; it is that "I will not edit
that directory" is a promise about every future turn, and a hook is a fact about each one.

## Usage

Registered as a `PreToolUse` hook in an agent definition's frontmatter, so it applies only
while that agent is active:

    hooks:
      PreToolUse:
        - matcher: "Write|Edit|NotebookEdit"
          hooks:
            - type: command
              command: "$CLAUDE_PROJECT_DIR/tools/hooks/restrict_paths.py --allow backtest --allow tests --block backtest/research"

- `--allow PREFIX` -- a path the agent owns. Repeatable. With none given, nothing is
  checked and the hook stands aside, because an agent with no declared ownership is not
  one this is meant to constrain.
- `--block PREFIX` -- carved out of an allowed path. Repeatable. Takes precedence, so
  `--allow backtest --block backtest/research` means the package except that subpackage.

Paths are matched by **path components**, not by string prefix, so `--allow app` does not
allow `application/x.py`. Outside the project directory is always refused: an agent told to
work in this repository has no business writing to a home directory.

## How it reports

Exit 2, with the reason on stderr, which is what the caller reads. Anything it cannot read
-- malformed input, no `file_path`, an unparseable path -- exits 0. A hook that failed
closed on its own bug would stop the agent working at all, which is worse than the mistake
it guards.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

#: Tools that write a file. `matcher` in the frontmatter should name these, but the hook
#: checks too: a matcher widened later should not silently start refusing `Bash`.
WRITING_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def within(path: Path, prefix: str) -> bool:
    """Is `path` inside `prefix`, matching whole path components.

    `Path.is_relative_to` rather than `str.startswith`, so that `app` does not match
    `application/` -- a string prefix would, and the mistake is invisible until something
    is written to the wrong tree.
    """
    try:
        return path == Path(prefix) or path.is_relative_to(Path(prefix))
    except (TypeError, ValueError):
        return False


def verdict(relative: Path, allow: list[str], block: list[str]) -> str | None:
    """The reason to refuse this edit, or None."""
    for prefix in block:
        if within(relative, prefix):
            return (f"`{relative}` is inside `{prefix}`, which this agent does not own "
                    f"even though it owns the directory around it.")
    if not any(within(relative, prefix) for prefix in allow):
        owned = ", ".join(f"`{p}/`" for p in allow)
        return (f"`{relative}` is outside the paths this agent owns ({owned}). "
                f"Editing it would move two sides of the system in one change, by one "
                f"agent, which is what the contract between them exists to prevent.")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="refuse an edit outside the agent's paths")
    ap.add_argument("--allow", action="append", default=[])
    ap.add_argument("--block", action="append", default=[])
    args, _unknown = ap.parse_known_args()
    if not args.allow:
        return 0                 # no declared ownership: nothing to enforce

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") not in WRITING_TOOLS:
        return 0

    tool_input = payload.get("tool_input")
    raw = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return 0

    root = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    try:
        target = Path(raw)
        absolute = target if target.is_absolute() else (root / target)
        relative = absolute.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        # Outside the project directory. An agent told to work in this repository has no
        # business writing elsewhere, so this one is refused rather than waved through.
        print(f"Refused: `{raw}` is outside the project directory.\n\n"
              f"This agent works in this repository only.", file=sys.stderr)
        return 2

    reason = verdict(relative, args.allow, args.block)
    if reason is None:
        return 0
    print(f"Refused: {reason}\n\n"
          f"If the change really is needed there, it belongs to the agent that owns that "
          f"path. Write it as a request in `docs/specs/` and say so in your report — see "
          f"CLAUDE.md for the roster.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                                  # noqa: BLE001
        print(f"the path-restriction hook could not run ({exc!r}); allowing the edit",
              file=sys.stderr)
        raise SystemExit(0) from exc
