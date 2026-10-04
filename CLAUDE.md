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

No tool can check this. It costs one command to read the staged list.

## Before committing

```bash
python -m tools.check          # all the guards; --fast skips the slow ones
python -m tools.status         # the project's state, computed rather than written down
```

**Any failure means no commit.** If a guard really could be ignored, change the guard.

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
