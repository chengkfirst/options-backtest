# Working in this repository

An options backtesting and research platform — US equity options, QQQ and SPY,
mid-frequency, days to months. It exists to **settle a strategy's seven components**, then
to **validate the result** over the long run.

**Read [conventions.md](conventions.md) before changing anything** — the rulebook: what the
guards are, which failures this project refuses, and why. Area rules are in
[backtest/](backtest/CLAUDE.md), [app/](app/CLAUDE.md) and [tests/](tests/CLAUDE.md).

## The agents

| Agent | Use it for |
|---|---|
| `engine-dev` | any change to `backtest/` and the tests for it. Writes the failing test first. Never edits `app/` |
| `interface-dev` | any change to `app/`. Declares in `CONTRACT` before reading a field; reviews in `states.html`. Never edits `backtest/` |
| `reviewer` | a diff, before a commit or a merge. Read-only. First question: does every staged file belong to this change |
| `quant-auditor` | any change under `backtest/`, for the domain errors that leave the result wrong and looking normal — look-ahead, timing, an optimised realism assumption. Read-only |
| `doc-keeper` | statements the code contradicts. Corrects the document, never the code |
| `research-analyst` | one question from `docs/research/plan.md`, through the public `run()`. Writes findings only |
| `research-lead` | which question is next and whether it can be answered at all. Run it as its own session: `claude --agent research-lead` |
| `explorer-proposer` · `explorer-practitioner` · `explorer-skeptic` | the three roles of the `/explore` debate about what this project should become |

The two builders own disjoint paths and a hook enforces it. A change either needs from the
other travels as a spec in `docs/specs/`, not as an edit.

Skills: `/spec` before building · `/ship` to commit · `/new-guard` to add a guard ·
`/audit-docs` for rotted statements · `/explore` for the debate · `/benchmark` to compare
other tools.

## More than one session runs here at once

One window is usually on the engine, another on the interface. **Commit what you changed.
Nothing else.**

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

Both forms are now **refused**, by `tools/hooks/refuse_broad_git_add.py`. What no tool can
check is whether the files you named are yours — so reading the staged list is still the
rule, and costs one command.

Better: remove the shared tree. Give each builder its own —
`git worktree add ../bench-engine -b engine/<topic>` — so neither can see the other's
half-finished files, and each runs its own guards in its own tree. **The main session
merges, and only after the full `tools.check` passes on the merge result**, not on either
branch alone: the front/back contract is what a merge breaks and what neither side can see
alone.

## Where a document goes

`specs/` before it is built · `decisions/` never edited once made · `findings/` a result
with its caveats · `research/plan.md` what to answer next · `ideas/backlog.md` candidates ·
`feedback/` real use, **written by the human** · `radar/` the scout digest · `plans/` work
with an end — all under `docs/`. [docs/README.md](docs/README.md) says what makes each kind
wrong, which is what decides who may change it.

## Before committing

```bash
python -m tools.check          # all the guards; --fast skips the slow ones
python -m tools.status         # the project's state, computed rather than written down
```

**Any failure means no commit.** If a guard really could be ignored, change the guard.
Enable the pre-commit hook once per clone, so that rule is held by the commit rather than
by memory: `git config core.hooksPath tools/githooks`. It runs `--fast`. `.github/workflows/check.yml` runs the **full** check on a clean clone on
every push — the only place a missing-from-git failure can show up at all.

Use a Python with the dependencies: `/opt/anaconda3/bin/python`; the `.venv` has no pandas.
Write **why** in the commit message, not only what changed.

## The two rules that catch the most

- **Front and back move together.** Anything the interface reads is declared in `CONTRACT`
  (`app/static/js/contract.js`), at the depth it is read, and `tools/check_wiring.py`
  checks the declaration against a real response. The interface may not read an undeclared
  field.
- **Everything is in English** — code, comments, docstrings, interface text, documents,
  file names. `tools/check_language.py` enforces it. A Chinese dict key is a functional
  identifier, and two spellings of "the same" key stop matching with nothing raising.

## Running the interface

```bash
/opt/anaconda3/bin/python app/server.py                    # http://localhost:8765
BENCH_PORT=8766 /opt/anaconda3/bin/python app/server.py   # a second instance, beside it
```

`app/static/states.html` is the component gallery: every component in every state, from the
real modules. Review changes there as well as on the page.
