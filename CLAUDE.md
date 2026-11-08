# Working in this repository

An options backtesting and research platform — US equity options, QQQ and SPY,
mid-frequency, days to months. It exists to **settle a strategy's seven components**, then
to **validate the result** over the long run.

**Read [conventions.md](docs/conventions.md) before changing anything** — the rulebook: what the
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

The two builders own disjoint paths, and a hook refuses a `Write` or `Edit` outside them.
**It does not watch `Bash`**, so a `sed -i` across the line is not stopped — the hook
removes the easy way, not every way. A change either side needs from the other travels
as a spec in `docs/specs/`, not as an edit.

## The pipeline

**This session does not edit `backtest/` or `app/` itself. It delegates.** Not a matter of
capability — of review. Work that goes through a builder gets read by someone who did not
write it, and the two defects fixed on `fix/engine-state` were both found that way. Work
done here gets read by nobody.

| When | Then |
|---|---|
| a new feature | `/spec` first, and **wait for approval**. No code before it |
| after `engine-dev` | `quant-auditor` **and** `reviewer`, in parallel. **No `/ship` until both pass** |
| after `interface-dev` | `reviewer`, and look at it in `states.html` |
| after a merge | `doc-keeper`, because a merge is when documents go stale |

The engine pair is not two reviews of the same thing. `reviewer` asks whether the change
is sound; `quant-auditor` asks whether the **result** is wrong while everything still
runs. On `fix/engine-state` they found different things, and the auditor's — that the old
trailing stop made its own level a function of λ, so the λ-sensitivity test was moving a
strategy rule — was not visible as a code defect at all.

A **Stop hook** holds the engine half of this: with uncommitted changes under `backtest/`
and no newer report in `docs/findings/`, the session does not finish. Write the report,
commit the change, or say plainly in your final message that it is unaudited and why.

## Stop and ask, rather than deciding

Three things are the human's, always:

- **A baseline update.** `tools.baseline update` is never run to make a guard green. Where
  results are *meant* to move, the change is explained trade by trade in `docs/findings/`
  and committed **before** the baseline command, in its own commit.
- **An architecture change.** The architecture is transcribed, not authored here. A
  deviation is recorded in `docs/conventions.md` with its reason, and a change to the
  intent is a conversation.
- **A merge.** Branches are stacked and PRs opened; nothing is merged, and `main` is never
  pushed to directly.

Skills: `/spec` before building · `/ship` to commit · `/new-guard` to add a guard ·
`/audit-docs` for rotted statements · `/explore` for the debate. And one workflow,
`/benchmark`, to compare other tools.

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

Everything under `docs/`, and [docs/README.md](docs/README.md) is the index — it says what
makes each kind of document wrong, which is what decides who may change it.

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
