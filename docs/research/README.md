# `docs/research/` — what to answer next, and the benchmarks

Two kinds of file, and one of them is singular.

**[`plan.md`](plan.md)** — the research plan. One file, maintained by `research-lead`
(`.claude/agents/research-lead.md`), which is the only agent that turns a question into
development work, and only by writing a spec the human approves. It says which question is
next, the hypothesis, the test, the data required, whether the platform and the data can
support it, the smallest credible experiment, and **the result that would make us abandon
it** — fixed before any result is seen.

**`benchmark-<YYYY-MM-DD>.md`** — a comparison against other tools, written by the
`/benchmark` workflow (`.claude/workflows/benchmark.js`). Dated, never overwritten: what
another project does changes, and the value is in the comparison at a point in time. Three
sections, always: **worth learning** (where they do it, what we would change, rough cost),
**we do better** (strengths to keep), **do not copy** (and why). Every claim points at a
file in the repository it is about.

## The two standing constraints on any plan here

**The synthetic chain cannot answer an option-side question.** Its IV is constant, with no
skew and no term structure. So "which delta", "where to take profit", and anything about
the volatility surface are unanswerable on it — not imprecise, unanswerable. Conclusions
about the underlying (when to enter, how long to hold) can be trusted. A plan entry whose
question is option-side says what real data it needs, and stays blocked until there is
some.

**The criteria and the hold-out are fixed before the result is seen.** Not as a
formality: choosing the test after seeing the numbers is how a search of forty
configurations reports the best one as though it were the first. The plan file is where
that commitment is on record, with a date.

## Where the questions come from

[`research-questions.md`](../research-questions.md), one level up in `docs/`, is the
standing list — what the platform exists to answer. `plan.md` is the ordering of it:
which one now, and why that one.
