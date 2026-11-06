---
name: audit-docs
description: Sweep the project's documents for statements the code contradicts, with the code that proves each one, then correct the documents. Runs doc-keeper across README.md, requirements.md, conventions.md, research-questions.md, the CLAUDE.md files and docs/. For a whole-repository sweep it may use a workflow with a second agent verifying each finding. Use after a refactor or a rename, when a document names something that moved, or on a schedule.
argument-hint: "[a document, or nothing for all of them]"
disable-model-invocation: true
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash, Edit, Write, Agent(doc-keeper), Workflow, TodoWrite
---

# /audit-docs

Find what the documents claim and the code denies, and correct the documents. Named only:
it edits documents.

**One direction, always: the document is wrong and the code is right.** A document is a
claim about the code, never evidence about it. If the document turns out to be right and the
code wrong, that is a bug report — see "The finding that matters most" below.

## Why this is worth running

"A document rotted" is one of the eight silent-failure shapes in `conventions.md`, and its
"why it goes unnoticed" column is the whole problem: *nobody runs the code in a document.*
It was true when written, something moved, and it now reads authoritatively and wrongly —
which is worse than no document, because a reader believes it and stops looking.

Two guards cover what a tool can: `check_docs` runs the README's Python blocks, and
`check_doc_claims` checks specific stated claims. Neither reads prose. This is the rest.

## Start with the cheap sweep, before any agent

Three greps that find most of it in under a minute, and finding it this way costs nothing:

```bash
# 1. Every `path.ext` in every document, tested against the filesystem.
grep -rhoE '`(backtest|app|tools|tests|docs)/[A-Za-z0-9_/.-]+\.(py|js|css|html|md)`' \
  --include=*.md . | tr -d '`' | sort -u | while read -r f; do [ -e "$f" ] || echo "MISSING: $f"; done

# 2. The same in code comments and docstrings, which rot the same way.
grep -rhoE '\b(backtest|app|tools|tests)/[A-Za-z0-9_/.-]+\.(py|js|css|html)\b' \
  --include=*.py --include=*.js . | sort -u | while read -r f; do [ -e "$f" ] || echo "MISSING: $f"; done

# 3. Counts and numbers that are hand-written where something computes them.
/opt/anaconda3/bin/python -m tools.status
```

The first of these found five stale pointers in one pass, including the rule telling a
reader where to declare a field — pointing at a file that had moved.

## Then `doc-keeper`, per document

One agent per document, not one for all of them: a single agent over six long documents
reads the first two carefully.

Give each the document and this order of attack, most-likely-rotted first — **a path, a
symbol, a number, a behavioural statement, a contradiction between two documents**. The
fourth is the one that rots most completely, because the feature arrives and nobody searches
the documents for the sentence saying it does not exist. `requirements.md` said "no resume
from a state snapshot" while resume was built and reachable from `run()`.

**Every correction cites the code that proves it** — file and line, or name and module.
A correction with no citation is one more unchecked claim, and the next reader cannot tell
it from the claim it replaced.

`doc-keeper` is blocked by a hook from editing code, from the three generated
transcriptions, and from `docs/feedback/`.

## For a whole-repository sweep, a workflow

When auditing everything rather than one document, use a workflow: one agent per document
finding candidate contradictions, and **a second agent verifying each finding against the
code it cites** before anything is corrected. `pipeline()`, so each document's findings are
verified while the others are still being read.

The second agent is the point. A finding is a claim about a claim, and a document "corrected"
on a misread is worse than the rot: it now carries a confident wrong statement with a
citation attached.

## The finding that matters most

**The code is wrong and the document is right.**

When that happens: **do not correct the document.** Correcting it would bury the only
evidence anybody noticed. Report it with file and line, for `engine-dev` or `interface-dev`,
and leave the document alone with the discrepancy named.

Changing code to match a document is the move this skill most has to avoid — it turns a
documentation task into a behaviour change nobody reviewed, and the baseline guard would
then go red for a reason nobody could explain.

## What is not rot, and must not be "fixed"

- **A disagreement with `docs/architecture.md`** or the other two transcriptions. Those are
  faithful transcriptions of the PDFs in `docs/originals/`, which are the originals. A disagreement is
  either work not yet done — which belongs in `requirements.md` with its reason — or a
  deliberate deviation, which belongs in `conventions.md`, and three already are. An edit to
  a transcription would also be undone by the next `tools/pdf_to_md.py` run, silently.
- **"Not built yet" recorded with its reason.** That is `requirements.md` doing its job. A
  missing feature recorded with its reason is worth more than a document that does not
  mention it.
- **A claim no code can settle** — "the interface is clear", "the statistics are honest".
  `requirements.md` already marks such criteria hand-written rather than pretending they
  were computed. That is the right answer, not a gap.
- **`docs/feedback/`**, ever. It is a person's account of their own experience.
- **A decision record.** It records what was known at the time. Superseded, never edited.

## Finish

```bash
/opt/anaconda3/bin/python -m tools.check --fast     # language, doc claims, words
/opt/anaconda3/bin/python tools/check_docs.py        # runs the README's Python
```

Report three tables: **corrected** (document, line, what it claimed, what is true, the code
that proves it) · **not corrected, and why** · **code bugs found**, which is the output a
person should read first.
