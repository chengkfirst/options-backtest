---
name: ship
description: Commit a finished change safely — runs the full tools.check, refuses to continue on any failure, shows git status, stages only the files belonging to this change by explicit path, has the reviewer subagent confirm the staged set matches the change's purpose, and writes a commit message that says why. Use when work is finished and ready to commit.
disable-model-invocation: true
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash, Agent(reviewer), TodoWrite
---

# /ship

Commit a finished change. **Named only** — it has side effects, and a commit is the one
action here that writes history.

Each step gates the next. Do not run them out of order, and do not continue past a failure.

## 1. The full check. Not `--fast`

```bash
/opt/anaconda3/bin/python -m tools.check
```

About **fourteen minutes**, 800 seconds of it the test suite. Say so before starting so the
wait is expected rather than alarming.

**Any failure stops this.** Not "note it and continue", not "it is unrelated" — there is no
such thing as a failure that can be ignored here, and if one really could be, the guard
itself should change (`/new-guard`). Report which guard failed and its output, and stop.

The baseline is the one that needs naming when it goes red: **you changed behaviour.** Find
it and revert it, or stop and report that the results move and why, so a person decides.
**Never run `python -m tools.baseline update` to get past it.**

## 2. What is in the tree

```bash
git status
git diff --stat
```

Read it, and say out loud what you see. More than one session works in this tree at once,
so some of what is here may not be yours. If something is modified that you did not touch,
**say so by name and leave it alone.**

## 3. Stage by explicit path

```bash
git add path/to/one/file  path/to/another
```

One path per file. **Never `git add -A`, never `git add .`, never `git add <directory>`.** A
directory looks specific and is not: `tools/` once committed another session's 469-line
guard under a message about CSS tokens.

A hook refuses those forms outright, so this is not a reminder — it is what will happen. If
you find yourself wanting the shorthand, that is the signal to list the files.

## 4. Read the staged list

```bash
git diff --cached --name-only
```

**Read it.** Is every one of these yours, and does every one belong to *this* change? If a
file you did not write is staged: `git restore --staged <path>` and leave it for whoever
wrote it.

## 5. The reviewer confirms the staged set

Give the `reviewer` subagent the staged diff and the message you intend to write, and ask
one thing first: **does every staged file belong to the purpose this message states?**

This is a second reader on the question a single session is worst at, because the files all
look familiar — you touched them, so they feel like they belong. The reviewer has not
touched them.

If it says a file does not belong, that is usually right. Split the commit.

Then its ordinary findings: literal thresholds, hidden defaults, one fact in two places,
front and back moving separately, a new guard never verified in reverse. **Fix what it
finds before committing**, or say why not — a finding waved past at commit time is a
finding nobody ever returns to.

## 6. The message says why

```
<type>: <the point, in one line>

<Why. What was true that made this necessary, what the failure was, what was
considered and not done, and what is now true that was not.>
```

What a reader wants in six months is the reasoning. The diff already shows what changed.

Three things that make a message worth reading here:

- **Name the failure.** Not "improve the guard" but "a skip marker anywhere exempted the
  block, so the one wrong import was never run".
- **Say what you did not do, and why.** A reader who sees the obvious thing missing and no
  explanation assumes it was missed.
- **Record a reverse verification** if the change adds or alters a guard: what was planted,
  what failed, what passed after. A check never made to fail may never have worked.

Then commit, then push:

```bash
git commit
git push
```

**Never to `main`. Never force. Never merge** without the full check passing on the merge
result — that is the only run that sees the front/back contract across both branches.

## When this stops

- a guard fails — report and stop
- the baseline moves — report and stop
- the reviewer says a staged file does not belong — split, then start again at step 2
- the staged set holds work you cannot account for — stop; it is probably another session's
- you are on `main` — stop and say so. Branch first
