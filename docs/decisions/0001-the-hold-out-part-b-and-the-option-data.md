# 0001 · The hold-out is SPY plus everything after 2026-11-07; Part B is a method test; no option data is bought

**Date:** 2026-11-07
**Status:** accepted
**Supersedes:** nothing

## Context

`docs/research/plan.md` § 6 recorded the pre-registration as complete in every part but
one: **the hold-out was `NOT FIXED`**, and § 7 named that as the first of four things the
plan hands back to a person. Until it was fixed, entries 5 to 9 and 16 to 20 had no
out-of-sample stage and every one of their results was in sample.

Two further choices blocked work in the same way. Part B (entries 5 to 9) is gated on a
person naming a strategy, because pre-registering a test against a configuration nobody
intends to trade spends the one thing pre-registration provides. And every option-side
question is blocked on data that has not been bought
(`docs/research/discussion-2026-11-06.md` § 6, decision 3).

`docs/research/discussion-2026-11-06.md` § 6 set out all three with options, consequences
and a recommendation marked as a recommendation.

**What was not known at the time of this decision.** Nothing had been run. `docs/findings/`
held only its README and an audit; `docs/feedback/` held only its README, so there was no
evidence about which question actually matters in use. Every ordering judgement behind these
three choices therefore rested on the documents and the code, which the plan itself
(§ 2) calls the weakest basis this role has.

## Options

Each of the three was a real choice with a real cost.

**The hold-out.** (a) Reserve **SPY** as an untouched underlying — buys independence in the
underlying, and costs the per-underlying check `research-questions.md` 9.5 wants.
(b) Reserve **2025-01-01 to the present** — about 23 months, which can reject gross failure
and nothing finer, and which is the longest period never looked at. (c) Both — costs both
and buys the most. Both (a) and (b) are bad, which the plan says plainly rather than
hiding.

**Part B.** (a) Name a thesis now — spends the project's first pre-registered test on
whatever gets named under time pressure. (b) Declare Part B a **method test** against the
demo — gets the battery exercised and records that no strategy conclusion was drawn.
(c) Leave Part B gated and run Part A only — delays falsification but costs nothing.

**Option data.** (a) HistoricalData.net, $199, one year — maps to `SCHEMA` by rename alone
and is a $199 test of the whole adapter path. (b) ORATS, $599, 2007 to present — needs a
melt, and its underlying is reverse-engineered from put-call parity rather than a traded
close. (c) DeltaNeutral, $1,150 / $1,495 — states it is OPRA-licensed; Level 1 ships no
greeks or IV. (d) Cboe, two snapshots per contract per day, $1,500-$2,100 for one year.
(e) Buy nothing yet.

## The choice, and why

**On 2026-11-07 the owner accepted the brief's recommendations without stating a preference
of their own.** That is recorded here exactly as it happened, because a decision adopted
from a recommendation and a decision argued to independently are worth different amounts
later, and the difference is not recoverable afterwards.

### 1 · The hold-out: SPY, plus everything after 2026-11-07

**SPY is reserved. No run may use SPY until the final check.** It is untouched by every run
in this repository, which is what makes it a hold-out at all; 2015-2024 has already been run
(`out/baseline/`) and 1999-2026 has already been studied (the table in
`docs/research-questions.md` § 2.1). Reserving SPY buys independence in the dimension a
QQQ-first data purchase cannot give us.

**All data after 2026-11-07 is also held out**, declared with
`backtest/research/holdout.py`'s `HoldOut` as a period: `HoldOut(date(2026, 11, 8),
date(2099, 12, 31))`, passed to `run()` as `hold_out=("2026-11-08", "2099-12-31")`. A run
whose range reaches into it raises unless `unlock="<why>"` is passed, and the reason is
recorded in the run's provenance (`backtest/api/sdk.py:546-548`). This half is
out-of-sample that accrues: it costs nothing today and is worth more every month.

**The SPY reservation cannot be enforced by that class today, and this is the gap in this
decision.** `HoldOut` takes `start: date` and `end: date` and nothing else
(`backtest/research/holdout.py:44-50`); it has no concept of a symbol. So "SPY is reserved"
is held tonight by **this document and nothing else** — exactly the kind of rule the module's
own docstring says does not survive: "a rule that rests on remembering is a rule that holds
until the week it matters."

**Stage 2 of the 2026-11-07 run adds it**: a way to declare a reserved symbol, so a run on
it raises unless passed `unlock=` with a reason, recorded the same way the period form
records it. Until that is merged, every SPY run is prevented by discipline only.

### 2 · Part B is a method test, and is not run tonight

**Part B (plan entries 5 to 9) is a method test only.** It may later run against the demo
strategy in `run_backtest.py` (`sma_20 > sma_50`, delta 0.30, 30 days, +100% / -50% /
10 days) **to prove the battery works, and no conclusion about any strategy may be drawn
from it.** The demo was never chosen as a thesis and is an interface demonstration.

**Part B was not run on 2026-11-07.** Recommendation (b) plus (c) was taken: Part A now,
Part B declared a method test in writing and left for later.

### 3 · No real option data is bought

**Not bought. Nothing in the 2026-11-07 run uses the option chain** — every entry run was an
underlying-scope event study on QQQ daily OHLC. The recommendation to buy
HistoricalData.net's $199 one-year window as a test of the adapter path stands, unspent;
it is a purchase, which is the owner's and not an agent's.

## Consequences

**What this makes easy.** Part A can run, and did. Every Part A entry is `UNDERLYING` scope
and needs neither a strategy nor an option price, so the hold-out choice unblocks the plan's
whole first group without a purchase.

**What this makes hard.** `research-questions.md` 9.5 wants the effect to hold on each
underlying separately, and that check is now unavailable until the final evaluation —
by design, and it is the price of reserving SPY. Entries 5 to 9 stay unrun, so the
falsification battery is still unexercised.

**What becomes wrong if a condition changes:**

- **If anything runs on SPY before the final check, the hold-out is gone and cannot be
  restored.** There is no mechanism tonight; see the gap above.
- The period half of the hold-out starts empty. **A result scored on it in, say, three
  months is being scored on three months**, which can reject gross failure and nothing
  finer. It is worth quoting only with its length beside it.
- **If Part B is ever run and its numbers are quoted as a result about a strategy, this
  decision has been broken**, whatever the run's own labels say.
- If option data is bought, every option-side entry's blocked status is re-decided, and the
  `available_at` problem named in the brief (`adapters.py:510-516`'s assumed path runs on
  **every** real backtest) arrives with it.

## What would reopen this

- **The hold-out:** the final evaluation being run. That is the one use it exists for, and
  after it the hold-out is spent — not reopened, finished. A second use is a new decision.
- **Part B:** a person writing a strategy thesis down. Then entries 5 to 9 stop being a
  method test and become a pre-registered test, with their own date.
- **The option data:** a purchase. Any of the four vendors arriving changes which entries
  are blocked, and the plan's section 4 constraint ("what the synthetic chain cannot
  answer") is re-read against the real chain's actual columns.
