#!/usr/bin/env python3
"""Refuse a `git add` that stages more than the caller named.

## The failure this answers to

More than one session works in this repository at once -- one on the engine, one on
the interface, sometimes a third on the tests. They share one working tree, so every
file either of them has touched is visible to both, and a broad `git add` cannot know
which is whose. It has gone wrong in both directions within one afternoon:

- a session finishing engine work ran `git add -A && git commit` and swept a
  half-finished interface rebuild into a commit whose message was about component state
- the session doing the interface ran `git add tools/` and committed the other's
  469-line mutation-testing guard under a message about CSS tokens

Nothing was lost either time -- it is all in git -- but the history then says things
that are not true, and the history is the only account of why anything was done. A
commit message that describes a third of its own diff is worse than no message: the
next reader believes it and stops looking.

`CLAUDE.md` and `conventions.md` both said "no tool can check this". This is the tool.
It cannot know which session wrote which line, and it does not try. It holds the one
thing that makes the mistake possible: **a `git add` whose pathspec names something
other than the files the caller chose.** A directory is the sneakier of the two forms,
because `tools/` looks specific and is not.

## What is blocked, and what is not

Blocked:

- `git add -A`, `git add --all`, `git add .`, `git add -u`, `git add --update`
- `git add :/` and `git add :/path` -- the whole-repository pathspec magic
- `git add <dir>` where `<dir>` is a directory in the working tree
- `git add` with no pathspec at all but a `-A`/`-u` style flag anywhere in it

Allowed:

- `git add path/to/one/file.py other/file.js` -- explicit files, the point of the rule
- a pathspec that does not exist on disk (a glob the shell left unexpanded, a deleted
  file being staged); git decides, and git's own error is clearer than a guess here
- anything with `--dry-run` or `-n`, which stages nothing
- `git add -p` / `--patch` / `--interactive`, which stage only what a human picks

## How it reports

Exit code 2, with the reason on stderr, which is the contract for a `PreToolUse` hook
that blocks: Claude Code shows that text as why the call was refused. Every other
outcome -- a command this does not recognise, malformed input, an unreadable path --
exits 0 and lets the call through. **A hook that fails closed on its own bugs would
block all work in the repository**, which is a worse failure than the one it guards.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

#: Flags that stage everything the caller did not name. `--no-all` and `--no-ignore-removal`
#: turn `-A`/`-u` back off, so they are checked for before these are trusted.
BROAD_FLAGS = {"-A", "--all", "--no-ignore-removal", "-u", "--update"}
BROAD_FLAG_CANCELS = {"--no-all", "--ignore-removal"}
#: Pathspecs that mean "everything", whatever directory you are standing in.
BROAD_PATHS = {".", "./", "-", ":/", ":"}
#: Flags that mean nothing is staged without a human choosing it, so the breadth of the
#: pathspec does not matter.
HARMLESS_FLAGS = {"--dry-run", "-n", "-p", "--patch", "-i", "--interactive",
                  "--edit", "-e", "-h", "--help"}

#: `git add` takes these with a value in a separate argument, so the value is not a
#: pathspec and must not be mistaken for one.
VALUE_FLAGS = {"--chmod", "--pathspec-from-file"}

#: What separates one command from the next in a shell line. A `git add` chained after
#: something else is the common case -- `python -m tools.check && git add -A` -- so
#: splitting on these is the whole reason this is not a simple prefix test.
SEPARATORS = ("&&", "||", ";", "|", "\n")

#: The start of a here-document: `<<EOF`, `<<-EOF`, `<<'EOF'`, `<<"EOF"`. The body that
#: follows is **text being written, not commands being run**, and has to be dropped before
#: anything in it is read as a command. See :func:`without_heredocs`.
HEREDOC = re.compile(r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_][A-Za-z0-9_]*))")


def without_heredocs(command: str) -> str:
    """Drop here-document bodies, keeping the lines that are commands.

    **This exists because the hook refused its own first real use.** The command was

        git add tools/hooks/… tests/… && git commit -F - <<'EOF'
        … `git add -A` and `git add <dir>` have each already swept …
        EOF

    -- an explicit, correct `git add`, with a commit message *about* the mistake. The body
    of a here-document is data: it is being written to a file or piped to a program, and no
    part of it is executed. Reading it as commands made the hook block a sentence that
    quotes the thing it forbids, which is the failure mode its own docstring warns about:
    a hook that refuses what it should not is a hook that gets switched off, and then
    nothing is checked at all.

    Three incidental lessons, all of them the project's own conventions:
    documents in this repository quote `git add -A` constantly, so this was never going to
    be rare; a guard is verified by running it on the real thing, not only on its test
    cases; and the test cases were all single-line, which is why they missed it.
    """
    out: list[str] = []
    pending: list[str] = []          # delimiters whose bodies have not closed yet
    for line in command.splitlines():
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue                 # inside a body: text, not a command
        out.append(line)
        # A line may open more than one body, as in `cat <<A <<B`; they close in order.
        pending += [m.group(1) or m.group(2) or m.group(3)
                    for m in HEREDOC.finditer(line)]
    return "\n".join(out)


def segments(command: str) -> list[str]:
    """Split a shell line into the commands it runs.

    Here-document bodies are dropped first (:func:`without_heredocs`). Beyond that it is
    deliberately crude: it does not parse subshells or process substitution. A `git add`
    hidden inside `$(...)` is not caught, and that is stated rather than pretended away --
    this blocks the ordinary mistake, not a determined caller.
    """
    parts = [without_heredocs(command)]
    for sep in SEPARATORS:
        parts = [piece for part in parts for piece in part.split(sep)]
    return [p.strip() for p in parts if p.strip()]


def tokens(segment: str) -> list[str] | None:
    """Tokenise one command, or None if it cannot be read as a shell command."""
    try:
        return shlex.split(segment)
    except ValueError:
        return None


def is_git_add(words: list[str]) -> int | None:
    """Index of the token after `add`, if this command is a `git add`; else None.

    Handles the global options that may sit between `git` and the subcommand, so
    `git -C sub add -A` and `git --no-pager add .` are not missed.
    """
    if not words:
        return None
    # Leading `VAR=value` assignments, and `env`, before the program name.
    i = 0
    while i < len(words) and ("=" in words[i] and not words[i].startswith("-")
                             or words[i] == "env"):
        i += 1
    if i >= len(words) or Path(words[i]).name != "git":
        return None
    i += 1
    while i < len(words):
        word = words[i]
        if word == "add":
            return i + 1
        if word.startswith("-"):
            # `-C <path>`, `-c <name>=<value>`, `--git-dir=<path>` and friends.
            if word in {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}:
                i += 2
                continue
            i += 1
            continue
        return None          # some other subcommand
    return None


def verdict(words: list[str], after_add: int, cwd: Path) -> str | None:
    """The reason to refuse this `git add`, or None to let it through."""
    args = words[after_add:]
    if any(a in HARMLESS_FLAGS for a in args):
        return None

    flags: list[str] = []
    paths: list[str] = []
    saw_separator = False
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if saw_separator:
            paths.append(arg)
            continue
        if arg == "--":
            saw_separator = True
            continue
        if arg in VALUE_FLAGS:
            flags.append(arg)
            skip_next = True
            continue
        if arg.startswith("-") and arg != "-":
            flags.append(arg)
            continue
        paths.append(arg)

    if any(f in BROAD_FLAG_CANCELS for f in flags):
        broad_flags: list[str] = []
    else:
        broad_flags = [f for f in flags if f in BROAD_FLAGS]
        # Bundled short flags, as in `git add -vA`.
        for f in flags:
            if f.startswith("-") and not f.startswith("--") and len(f) > 2:
                broad_flags += [f"-{c}" for c in f[1:] if f"-{c}" in BROAD_FLAGS]

    if broad_flags:
        return (f"`git add {' '.join(broad_flags)}` stages every change in the working "
                f"tree, including the ones another session has not finished.")

    for path in paths:
        if path in BROAD_PATHS or path.startswith(":/"):
            return (f"`git add {path}` stages everything under it, including the changes "
                    f"another session has not finished.")
        try:
            target = (cwd / path).expanduser()
            if target.is_dir():
                return (f"`git add {path}` names a directory. A directory looks specific "
                        f"and is not: it stages every changed file under it, whoever "
                        f"wrote them.")
        except OSError:
            continue           # unreadable path: git's own error is clearer than a guess

    return None


def reason_for(command: str, cwd: Path) -> str | None:
    """The reason to refuse this shell command, or None."""
    for segment in segments(command):
        words = tokens(segment)
        if words is None:
            continue
        after_add = is_git_add(words)
        if after_add is None:
            continue
        reason = verdict(words, after_add, cwd)
        if reason is not None:
            return reason
    return None


ADVICE = """
Stage what you changed, by name:

    git add path/to/each/file      # explicit paths, one per file
    git diff --cached --name-only   # read it: is every one of these yours?
    git commit

More than one session works in this tree at once. `git add -A` and `git add <dir>` have
each already swept another session's half-finished work into a commit whose message was
about something else -- see conventions.md, "More than one session at a time".

If a file you did not write is already staged: `git restore --staged <path>`.
""".strip()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 0               # not something this hook can read; let the call through
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") not in (None, "Bash"):
        return 0

    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not command.strip():
        return 0

    raw_cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or "."
    try:
        cwd = Path(raw_cwd)
    except (TypeError, ValueError):
        cwd = Path(".")

    reason = reason_for(command, cwd)
    if reason is None:
        return 0
    print(f"Refused: {reason}\n\n{ADVICE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:                                  # noqa: BLE001
        # A hook that fails closed on its own bug blocks every commit in the repository,
        # which is worse than the mistake it guards. Say what happened and stand aside.
        print(f"the broad-git-add hook could not run ({exc!r}); allowing the command",
              file=sys.stderr)
        raise SystemExit(0) from exc
