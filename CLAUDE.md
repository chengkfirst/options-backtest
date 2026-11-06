# Working in this repository

Read [conventions.md](conventions.md) before changing anything. It is short, and it is the
rulebook: what the guards are, which failures this project refuses, and why.

## More than one session runs here at once

One window is usually on the engine (`backtest/`, `tests/`), another on the interface
(`app/`). They share one working tree.

**Commit what you changed. Nothing else.**

```bash
git add path/to/each/file      # explicit paths. Never `git add -A`, never a bare directory
git diff --cached --name-only  # read it: is every one of these yours?
git commit
```

`git add -A` and `git add tools/` have each already swept another session's half-finished
work into a commit whose message was about something else. Nothing was lost — it is all in
git — but the history then says things that are not true, and the history is the only
account of why anything was done. If a file you did not write is staged,
`git restore --staged <path>` and leave it for whoever wrote it.

**Those two commands are now refused**, by `tools/hooks/refuse_broad_git_add.py` — a
`PreToolUse` hook on Bash, registered in `.claude/settings.json`. It blocks `-A`, `--all`,
`-u`, `.`, `:/` and any pathspec that is a directory, and allows explicit paths,
`--dry-run` and `-p`. What it cannot check is whether the files you named are yours: no
tool can. **Reading the staged list still costs one command, and is still the rule.**

## Each builder in its own worktree

A shared working tree is what makes the mistake above possible at all. When two agents
work at once, give each its own worktree and branch, so neither can see the other's
half-finished files:

```bash
git worktree add ../bench-engine    -b engine/<topic>     # the engine session
git worktree add ../bench-interface -b interface/<topic>   # the interface session
```

Each runs its own guards in its own tree. **The main session merges, and only after the
full `tools.check` passes** on the merge result — not on either branch separately, because
the front/back contract is the thing a merge can break and neither side can see alone.
`git worktree remove ../bench-engine` when it is done.

## Before committing

```bash
python -m tools.check          # all the guards; --fast skips the slow ones
python -m tools.status         # the project's state, computed rather than written down
```

**Any failure means no commit.** If a guard really could be ignored, change the guard.

Enable the pre-commit hook once, per clone, so that rule is held by the commit rather than
by memory:

```bash
git config core.hooksPath tools/githooks
```

It runs `tools.check --fast` and refuses on failure. The full check still has to be run
before pushing; `.github/workflows/check.yml` runs it on a clean clone on every push, which
is the only place a missing-from-git failure can show up at all.

Use a Python with the dependencies — `/opt/anaconda3/bin/python`. The `.venv` has no pandas.

Write **why** in the commit message, not only what changed.

## The two rules that catch the most

- **Front and back move together.** Anything the interface reads from the backend is
  declared in `CONTRACT` (`app/static/js/contract.js`), at the depth it is read, and
  `tools/check_wiring.py` checks the declaration against a real response. The interface
  may not read a field that is not declared.
- **Everything is in English** — code, comments, docstrings, interface text, documents,
  file names. `tools/check_language.py` enforces it. A Chinese dict key is a functional
  identifier, and two spellings of "the same" key stop matching with nothing raising.

## Running the interface

```bash
/opt/anaconda3/bin/python app/server.py          # http://localhost:8765
BENCH_PORT=8766 /opt/anaconda3/bin/python app/server.py   # a second instance, beside it
```

`app/static/states.html` is the component gallery: every component in every state, rendered
from the real modules. Review changes there as well as on the page.
