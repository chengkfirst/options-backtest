# `docs/research/benchmarks/`

One file per repository the `/benchmark` workflow has read, written by the workflow itself.

**File name:** the repository's own name, kebab-cased — `qlib.md`, `nautilus-trader.md`.
Not dated: the file is replaced when the repository is read again, and the date it was read
is inside it.

## Why these exist separately from the synthesis

`docs/research/benchmark-<date>.md` is a **comparison**, and a comparison is rewritten
whenever the set of repositories changes. These are the **record of one project**, and they
let a later run compare against what was found before rather than reading the repository
again from scratch.

## The section that matters most

**"Claims that did not survive the cross-check."** Every claim an analyst made that the
verifier could not confirm — overstated, wrong, or citing a file that does not exist — with
what the file actually shows.

That section is why these files are worth keeping. It is the record of what *looked* true
about another project and was not, and it is the only thing that stops the next run
believing the same thing again. A benchmark that keeps only its confirmed claims teaches
nothing about how it was wrong.

## What a reader should distrust

- **A dimension answered from a partial checkout.** Large repositories are cloned sparsely,
  and the file says which directories were left out. An answer drawn from a tree missing the
  relevant package is an answer about the part that was there.
- **Anything with no file behind it.** The workflow drops uncited claims, but a reader
  should check: a claim about another project with no path in it was not verified.
- **The licence line**, unless it was read from that repository's own `LICENSE`. "Worth
  learning from" and "safe to borrow code from" are different questions, and only the second
  one turns on the licence.
