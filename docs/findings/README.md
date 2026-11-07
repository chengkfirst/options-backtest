# `docs/findings/` — a research result, with what would make it wrong

A finding is a number plus everything needed to distrust it. The number on its own is the
part that travels; the caveats are the part that stops it being quoted into a decision it
cannot support.

**File name:** `<question>.md`, or `<YYYY-MM-DD>-<question>.md` when the same question is
asked again on better data. An audit is `audit-<commit>.md` and says in its first line
that it is an audit, not a result.

**Who writes one.** `research-analyst`, and only there — it may not edit platform code.
`quant-auditor` writes audits. Neither fixes what it finds.

## The shape of an entry

```markdown
# <the question, as a question>

**Date:** YYYY-MM-DD
**Answers:** docs/research/plan.md § <n>   (or: not from the plan — say where it came from)
**Verdict:** one sentence, including "no answer" where that is the answer

## Data
The source, the symbol, the date range. **Whether the option chain is the synthetic one**,
which has constant IV, no skew and no term structure — so an option-side conclusion
(which delta, where to take profit) does not hold on it, while a conclusion about the
underlying can. If it is synthetic, this section says what the finding therefore cannot
claim.

## Method
The configuration, run as `run(...)`, copyable. The guard record state at the time
(`backtest.core.run_id.guards_were_green` — green, red, or no record), because a figure
produced while the guards were red has no backing from the suite.

## Baselines
What it was compared against. A conditional probability **always** with its unconditional
baseline; a strategy result against buy-and-hold, random entry, and the structure without
the signal. A result with no baseline is not a finding.

## Result
The numbers, with their units and their basis in their names (`sharpe_annualised`, not
`sharpe`). Intervals where the sample supports one, and the sample size either way.

## Caveats
What would make this wrong. The sample, the period, the data's limits, how many
configurations were tried before this one. If one trade or one year carries most of the
P&L, that goes here and not in a footnote.

## What it changes
What should now be done differently — or nothing, which is a real and common answer.
```

## The rules behind the sections

- **A conditional probability with no unconditional baseline is self-deception**
  (`conventions.md`, the hard lines).
- **The unit is the logical trade, not the position.** Counted by position, three rolls
  become four trades, which distorts both the win rate and the holding period.
- **A realism assumption is never optimised** — λ, latency and the cleaning rules are set
  conservatively and only tested for sensitivity. A finding that varied one to improve a
  result is reporting an illusion, and says so.
- **How many configurations were tried** belongs in the caveats, because a search's size is
  what the deflated figure corrects for, and two configurations sharing one fingerprint
  make the search look smaller than it was.
