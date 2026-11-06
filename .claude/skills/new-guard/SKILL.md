---
name: new-guard
description: Add a guard, following the five steps docs/conventions.md requires — write down exactly how the failure happened and why it went unnoticed, write the check, verify it in reverse by planting the error, wire it into tools/check.py, and add a row to the table in docs/conventions.md. Use when a silent failure has actually occurred and should be caught by a tool from now on.
argument-hint: "[the failure the guard would catch]"
disable-model-invocation: true
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash, Write, Edit, TodoWrite
---

# /new-guard

Add a guard. Named only: it writes a check, edits `tools/check.py` and edits
`docs/conventions.md`.

The five steps are `docs/conventions.md`'s, not this file's. **None may be skipped**, and step 0
is the one most often skipped by being assumed.

## 0. First: has the failure actually happened?

**The bar is a concrete failure, then the guard.** Not a risk, not something that could go
wrong — something that did.

> A pit nobody has fallen into gets no fence. An imagined risk becomes noise nobody reads,
> and once there is enough noise the real warnings get skipped too.

So before anything: what happened, when, and how was it found? Point at the commit, the
bug, the wrong number.

**If it has not happened, stop here.** Say what it would take to make it worth guarding —
usually "this once, for real". That is the correct outcome of this skill a fair share of the
time, and a guard added without it makes every other guard slightly less likely to be read.

Two exceptions, and only two: a near miss caught by hand that would not have been caught
next time, and a failure that happened in a comparable project in a way that clearly
applies here. Both get said explicitly as what they are.

## 1. Write down exactly how it happened, and why it went unnoticed

In the check's **module docstring**, before writing the check. Every guard in `tools/` does
this, and it is the most valuable text in each of them, because it is what tells the next
reader whether the guard still answers to anything.

Three parts:

- **What happened**, concretely. "`check_docs` skipped a block when `# skip` appeared
  anywhere in it, so the README's data-adapter block was skipped by a comment four lines
  down — and its first line imported a module that does not exist."
- **Why nothing raised.** This is the part that makes it a guard rather than a test. Which
  thing looked fine: `git status` was clean; the server returned 200; both sides were
  `undefined` and so matched.
- **Why it is a tool and not a line in a document.** If a document could hold it, it should
  — one more guard costs every future run. "A document cannot stop an import."

## 2. Write the check

- Standard library where it can be. A guard that needs a dependency can fail for a reason
  unrelated to what it checks.
- **It fails for one reason and names it.** A guard whose output requires investigation
  gets run less each time.
- **The message says what to do.** A refusal that does not gets the same command retried.
- Where it goes: `tools/check_<thing>.py` for a check; `tools/hooks/` for a Claude Code
  hook; `tests/test_<thing>.py` for a check expressed as a test. `tools/` files must be
  tracked in git — `check_shipped` will say so.

## 3. Verify it in reverse. This cannot be skipped

> A check that has never been verified in reverse may never have worked at all.

Plant the error deliberately. Confirm the check fails, **and that its message names the
right thing**. Remove the error. Confirm it passes. Record both runs with their actual
output.

Two things learned here tonight, both the hard way:

- **Plant the real shape of the failure, not a convenient one.** A typo that any check would
  catch proves nothing. For the `# skip` loophole, the plant was the wrong module *and* a
  `# skip` on an inner line — the exact combination the old rule allowed.
- **Verify the allowed cases too, and more of them than the refused ones.** A guard that
  refuses what it should not is worse than no guard, because the way out of it is to turn it
  off. The `git add` hook refused its own first real use — it read a `git add -A` quoted
  inside a commit message's here-document as a command, and the documents here quote that
  form on nearly every page about committing.

**Where the forms it must catch are a list, keep the list as a test**, not as a paragraph in
a commit message. `tests/test_git_add_hook.py` and `tests/test_restrict_paths_hook.py` are
the pattern: they drive the real interface, in both directions, and they are re-run.

A test needs its three markers — `kind` (`guard`), `layer`, and a `req` where one applies.
See `tests/CLAUDE.md`.

## 4. Wire it into `tools/check.py`

Add it to the `GUARDS` tuple with its name and whether it is slow:

```python
("<name>", [sys.executable, "tools/check_<thing>.py"], False),
```

**An unregistered guard is the failure this project has already had**: three guards were
written and never registered, so a convention it holds to was checked only by whoever
remembered. That is the state the first line of `docs/conventions.md` calls a wish.

Then run `/opt/anaconda3/bin/python -m tools.check` — the **full** run — and confirm the
new guard appears in the list and passes. If it belongs outside `tools.check` because of
*when* it must run — before a command, at the commit, on a clean clone — put it in the
separate table in `docs/conventions.md` and say which of the three it is and why.

## 5. Add the row to `docs/conventions.md`

In "The fixed check", or in "The guards that are not part of `tools.check`" if it is one of
those. Columns: the guard, what it catches, the time it costs.

**The "what it catches" column is the failure, not the mechanism.** Compare: "checks skip
markers" against "a block exempted from the doc check by a comment deep inside it". The
second tells a reader whether it still matters.

## Done when

The docstring says how the failure happened and why nothing raised; the reverse
verification is recorded with both runs; it is registered; the full `tools.check` passes
with it in the list; and `docs/conventions.md` has its row.
