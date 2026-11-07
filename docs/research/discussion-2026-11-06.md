# Discussion brief · 2026-11-06

> **Written by `research-lead` running non-interactively.** The person this brief is for has
> not argued back yet, which is the input it most needs. Every recommendation below is marked
> as a recommendation; none of them is a finding, and four of the seven decisions are not
> mine to make at all.
>
> **Two phases of today's plan did not run**, and this brief is weaker for both: the
> benchmark's **deep-dive phase (3B) was not run**, so section 2 is benchmark-depth reading
> rather than deep-dive depth; and the **`/explore` debate (phase 4) was not run**, so
> `docs/ideas/backlog.md` is empty and section 5's directions have **not been argued against
> by a sceptic**. They are derived from `docs/research/benchmark-2026-11-06.md`,
> `docs/radar/2026-W45.md` and `docs/research/plan.md` by one agent.

---

## 1 · The answer in five lines

1. **The platform is more trustworthy than any of the six repositories benchmarked, and has
   answered nothing.** `docs/findings/` holds two audits (`audit-adf0495.md`,
   `baseline-change-2026-11-06.md`) and **zero research results**; all 20 plan entries are
   unrun.
2. **The option side is unanswerable, and that stopped being a data problem this week.**
   `docs/radar/2026-W45.md` item 1: a real EOD chain with bid, ask and open interest is a
   one-time purchase, and the cheapest honest first step is **$199**, not $799.
3. **Three gates hold everything else:** the strategy thesis (no strategy exists to
   falsify — plan Part B), the hold-out (plan § 6 says `NOT FIXED`, and it is the human's),
   and whether real option data is bought.
4. **The highest-value decisions are cheap.** $199 of data, one sentence naming a hold-out,
   and one sentence naming a thesis or declaring Part B a method test. None of them needs
   development work.
5. **`docs/feedback/` is empty.** Nothing in this brief is grounded in what happened when the
   platform was used, and the first feedback entry will probably reorder it more than any
   argument in it.

---

## 2 · What the benchmark taught us

**Grade warning, which governs this whole section.** The benchmark's own header declares that
this run's cross-check stage **did not happen**: 0 claims carried a verdict, 0 of 48
dimensions are twice-verified, and claims carry `[re-opened]` (one analyst + the report
writer), `[record]` (same, one hop further away) or `[one reader]` (**no second reading of any
kind**). The deep-dive phase was not run either. Nothing below is settled; the grade is
carried on every lesson, and a `[one reader]` lesson should be re-read before anyone builds
on it.

### The five lessons, in the order I would act on them

| # | Lesson | Grade | File here | What we would change | Cost |
|---|---|---|---|---|---|
| 1 | **A fit window is a constructor argument, not advice** (qlib `data/dataset/processor.py:197-205`, with `handler.py:535` raising on a non-inference processor) | [one reader] | anything in `backtest/research/` that estimates a parameter | make the fit window an argument that cannot be omitted, and refuse one that overlaps the declared hold-out | a week |
| 2 | **The baseline is in the default result, not in a module you remember to call** (qlib `workflow/record_temp.py:505-536`) | [one reader] | `backtest/research/baselines.py`, the result header | every run reports its baseline comparison or says it has none | a week |
| 3 | **Composable transaction-cost units, including a cost per unit of vega** (gs-quant `backtests/backtest_objects.py:542-595`) | [record] | `backtest/execution/costs.py:160-180`, where `max(quantity * commission_per_contract, min_commission_per_order)` is hard-coded at `:169-171` | express the minimum as a declared `MAX` of composed models; make a per-vega cost writable at all | an afternoon to a week |
| 4 | **An expiry-cycle field on the chain row** (optopsy `data/providers/eodhd.py:61-66`, in their dedup key) — *the strongest-graded of the five* | [re-opened] | `backtest/core/types.py:202-230` | add `expiration_cycle`, missing-not-dropped, **and no default that filters the universe** (theirs silently keeps monthly only, `eodhd.py:732`) | an afternoon |
| 5 | **One fill-model interface, several named rules** (nautilus_trader `crates/execution/src/models/fill.rs:51-90`, eight implementations) | [one reader] | `backtest/execution/costs.py:80-158`, one function parameterised by λ | lift the fill rule to a named component so two *forms* can be compared, not two λ values. **Require the seed** — theirs is optional | a week |

Lesson 4 is the one with a deadline attached: it should land **before** a real chain does, not
after, or the first real dataset gets loaded on a row that cannot tell a weekly from a monthly.

Three more worth reading before any build: refusing an aggregation whose provenance differs
(gs-quant, `risk/results.py:191` — our `Result.compare_forms` prints a difference it should
refuse) [record]; a named, deletion-proof data pin (ArcticDB snapshots) [record]; and an
error-message → cause → remedy table as a document type (ArcticDB `error_messages.md`)
[record / one reader].

### Two findings outside the lesson list that change a decision

**The vendor snapshot problem — and it is a decision, not a bug.** Both vendors that publish a
snapshot time take it about **14 minutes before the close** (ORATS: 15:46 ET, with the stated
reason that option quotes deteriorate at the close). The chain carries its own
`underlying_price` — the spot *at that moment* (`backtest/data/options/adapters.py:61`,
"spot at that moment (**the traded price, not the adjusted one**)"). So a signal computed from
T's equity close selects a contract against greeks and a spot that **predate the information
the decision used**. `docs/requirements.md:382` (criterion 1.20, "signal and fill never share a
quote") is satisfied — but the **reverse** defect, a quote strictly *earlier* than its signal,
has **no guard anywhere**. The bias is concentrated in exactly the sessions that move in the
last quarter hour, which are the sessions a regime signal fires on. Cboe is the only vendor
that sells both snapshots (`Bid 1545` / `Bid EOD`), which is the only way to turn this from an
argument into a sensitivity test. See decision 7.

**RD-Agent's loop judges on its test period.** `conf_combined_factors.yaml:43-45, 77-80` puts
`backtest.start_time` at the **test** segment start; the three metrics handed to the model are
`IMPORTANT_METRICS` in `developer/feedback.py:17-21`; the model returns `"Replace Best
Result"` and a `"New Hypothesis"` from them (`feedback.py:107-111`); the loop count is tracked
(`utils/workflow/loop.py:133-134`) and **nothing is deflated by it** [one reader]. That is the
clearest available statement of what an honest automated loop here would have to do
differently — and we already have `backtest/research/holdout.py` and
`backtest/research/tracker.py`. What we do **not** have is a hold-out that code can only open
once; see direction 3 and decision 6.

### The three things we already do better than every project compared

1. **The one-day execution lag.** `backtest/engine/loop.py:13-29` and
   `backtest/core/config.py:177` (`execution_lag_days: int = 1`, required `>= 1` at `:191`).
   **Not one of the six has it.** optopsy prices the entry from the signal day's own bid/ask
   (`evaluation.py:84-86`) [re-opened]; gs-quant has no lag in any engine and none can be
   configured (`backtests/actions.py:119-120`) [record]; ArcticDB's query language cannot
   express a lag at all [record]; nautilus_trader has a nanosecond latency model, which is the
   right answer to a different question [one reader]. **If this were traded away, no guard
   could detect it** — the arithmetic stays correct.
2. **Trials counted, and the rest of the validation suite.** `backtest/research/tracker.py:42-128`
   refuses a record with no `run_id` ("a rerun cannot be told from a new trial"), and around it
   sit baselines, walk-forward, purged k-fold with embargo, hold-out, two bootstraps and a
   pre-launch gate where `unknown` is not a soft pass. **No repository read here has any of
   it.** optopsy reports an uncomputable Sharpe as `0.00` (`metrics.py:43-51`) [re-opened];
   gs-quant has no module for any of it [record]; RD-Agent counts and corrects nothing [one
   reader].
3. **`available_at` as a column, with the two questions kept apart.**
   `snapshot(day, *, known_by=...)` (`backtest/data/options/chain.py:46`) separates "what could
   be traded now" from "what is known now about then". ArcticDB's `as_of` is write time and
   cuts the version chain, never the rows [record]; gs-quant uses one word for two axes
   (`data/dataset.py:171-211` vs `:391`) [record].

Behind those three, and nearly as wide: the tradability gate that never substitutes a price
(`backtest/execution/costs.py:40-78`), the missing-not-dropped rule whose value is recorded in
our own baseline file as 3,210 of hidden losses, pessimistic cost defaults with a guard that
reads them (`tools/check_defaults.py`), and the row-for-row dual-engine equality test
(`tests/test_event_driven.py:84`) — which optopsy is the live proof of, having two result paths
that disagree about when P&L is realised (`simulator.py:731` vs `:879-881`) [re-opened].

### And one lesson about the benchmark itself

gs-quant claim 24 told us to name an unexplained P&L residual, calling it "the discipline our
two equity curves do not impose". **It was false about us.**
`backtest/accounting/attribution.py:47` is already
`COMPONENTS = ("delta", "gamma", "vega", "theta", "residual")`, and we additionally refuse to
trust the decomposition when the residual is too large — which they do not. The claim's
citations into *their* clone were accurate; what it got wrong was the comparison, and no file
in their clone could have settled it. **A "better than ours" judgement is a claim about two
codebases and only one of them was being read.** Read every comparison in that benchmark with
that in mind, including the favourable ones.

What survived is narrow and real, and worth having: break the **higher-order spot and vol
terms** out of the residual, so `high_residual` fires when something is wrong rather than when
the underlying moved a long way.

---

## 3 · Scope options — what this project is for, for three months

**A · A personal research tool aimed at one tradable long-options edge.**
*Implies:* buy real option data; name a thesis; run Part A now and Part D the week the data
lands; adopt lessons 2 and 4 only. *Rules out:* multi-leg, selling, margin, live, MCP, any
automated loop. *Costs:* $199 now, up to $1,150 later; roughly two weeks of build (lessons 2
and 4, plus the vendor adapter), the rest research time.

**B · A vehicle for honest automated research.**
*Implies:* build the three mechanisms we lack and the radar's item 2 claims to have — a
write-once hold-out with a durable receipt, an append-only ledger of every formulation and
refusal, and "the model proposes, trusted code decides" as an enforced boundary. *Rules out:*
answering an option-side question this quarter — the machinery is built against a fixture
whose option side is arithmetic. *Costs:* four to six weeks of build, and it produces **no
finding**; it produces the right to believe a later one.

**C · A platform others or agents can use.**
*Implies:* a LICENSE file (we have none), `docs/errors.md`, one skill file read by CI and by a
human from the same path, MCP access, and health/usability work on the interface. *Rules out:*
most of the research calendar; usability work is unbounded and competes directly with
research. *Costs:* three to six weeks, and no finding. **The benchmark cannot tell you whether
this is worth it** — "worth learning from" is established for all six, "worth depending on" is
unmeasured for all six (their health was never read; every clone was `--depth 1`).

**D · Realism and plumbing hardening — adopt the benchmark.**
*Implies:* lessons 1, 3, 5 plus the provenance refusal and the data pin; pin pandas before 3.1
changes a number silently (radar item 5). *Rules out:* nothing permanently, delays everything
by a month. *Costs:* four weeks. **This is the option I would argue against hardest**: it is
the most comfortable one, it generates no finding, and lessons 1 and 5 protect research that
has not started.

---

## 4 · Goal candidates — one measurable goal per scope, and its failure

| Scope | Three-month goal | The result that says it failed |
|---|---|---|
| **A** | **Four findings in `docs/findings/`, one of them option-side on real quotes.** Entries 1–4 written up with intervals and effective sample size, plus entry 16 (the VRP term structure) on a purchased chain that passed `check_file()`. | No option-side finding by 2027-02-06 — either because the data was never bought, or because `check_file()` failed on the $199 sample and no second vendor was tried. A fifth entry run before the first four are written up is also a failure: it means the plan is being spent rather than followed. |
| **B** | **A hold-out that code opens once.** A declared hold-out cannot be read twice without a recorded unlock, and `tracker.py` reports a trial count that spans sessions rather than one run. Demonstrated by a test that fails when the second read is attempted. | The receipt exists and the trial count still resets per session — i.e. we built the ceremony and not the denominator. Equally a failure: the ledger exists and no question was asked against it, which makes it an empty audit trail. |
| **C** | **A second person, or an agent in a fresh clone, reproduces one recorded result from the documents alone**, with no question asked of the author. | They cannot, or they can only after being told something not written down. Watch for the cheap version of success: a reproduction that works because the author was in the room. |
| **D** | **The five lessons landed, `tools.check` green, and the baseline diff for each printed and approved.** | Any lesson lands without its baseline diff; or lesson 5 lands and no second fill rule is ever written, which means we built an interface for one implementation. |

---

## 5 · Directions — top five

> **Underived from a debate.** `/explore` did not run; `docs/ideas/backlog.md` is empty. These
> are mine, scored on the five filters in `docs/ideas/README.md`, and **no sceptic has argued
> against them**. Filter 1 ("whose real problem, with what evidence") is the weakest column
> throughout, because `docs/feedback/` is empty — so for every one of them the honest answer to
> filter 1 is "the project's own documents", which the filter explicitly says is not evidence.

**D1 · Buy the $199 one-year window and run `check_file()` on it.**
*Problem:* every option-side question in `plan.md` § 4.1 is recorded unanswerable. *Connects
to:* `backtest/data/options/adapters.py` — `SCHEMA` and `check_file()` already exist, so this
is a purchase and a `column_map`, not a build. *Data/time/skills:* sufficient; this **is** the
data. *Cheapest week test:* buy it, run `check_file()`, report column names, right aliases, IV
units, theta sign and convention. *Kills it:* `check_file()` fails on fields a rename cannot
fix, and the fix is not cheap on the $799 archive either — then try DeltaNeutral instead of
scaling up. **Score: strongest of the five, and the only one that converts "unanswerable" into
"answerable".**

**D2 · Decide which spot is authoritative, and guard it.**
*Problem:* the snapshot gap above — a selection made against a spot 14 minutes stale, biased
in the optimistic direction, in exactly the sessions that matter. *Connects to:*
`requirements.md:382` (1.20), `adapters.py`, the config surface. *Data/time:* answerable with
no purchase as a **spec**; testable only with Cboe's two snapshots. *Cheapest week test:* a
spec, plus a check that refuses a chain whose snapshot time and equity close disagree unless
declared. *Kills it:* measurement on real data shows the 15:45-vs-close choice moves no
conclusion — but that measurement needs Cboe, so **this is a spec now and a test later**.

**D3 · A write-once hold-out and a cross-session trial ledger.**
*Problem:* `plan.md` § 6 records the hold-out as `NOT FIXED`, and `tracker.py` counts trials
within what it is shown. *Connects to:* `holdout.py` and `tracker.py`, both built. *Data/time:*
no data needed. *Cheapest week test:* a durable receipt written before the hold-out can be
read, and a second read refused by code; count trials in a file, not a session. *Kills it:* if
the human intends to open the hold-out repeatedly anyway, then this makes the refusal visible
rather than real, and the honest move is to admit the hold-out is advisory. **Radar item 2's
single most useful datum belongs here: their best survivor went Sharpe ≈ 3 in validation to
0.26 on its one hold-out use.**

**D4 · Anytime-valid promotion (an e-process) instead of relying on a deflated Sharpe.**
*Problem:* a deflated Sharpe assumes a fixed trial count known in advance; this project's
process is adaptive by construction (`plan.md` gains entries, weak ones get dropped, good ones
get re-run with another year). PROMOTE reports 94.5% of null paths crossing under repeated
unadjusted testing versus 0.0% under its gate (radar item 3). *Connects to:* `tracker.py`, and
to how `research-lead` registers a question — **not** an engine change. *Data/time:* no data;
real statistical work. *Cheapest week test:* on the two findings we have, nothing — there is
no return stream yet, so **the cheapest version is to declare each entry's birth and its
wealth allocation now**, which costs an hour and is a prerequisite either way. *Kills it:* the
plan never re-tests a candidate on new data, in which case a fixed-count correction is enough.
**Caveat I want argued with: this makes every result this project ever produces look worse.
That is the point, and it is also why nobody adopts it.**

**D5 · Benchmark `autonomous-quant-researcher` (radar item 2).**
*Problem:* D3 and D4 are both designs we would be inventing; this repository claims to have
implemented both, and it is the only project found this week that ran options strategies on
**real traded option bars**. *Connects to:* the three places we are weakest — synthetic chain,
unrecorded trial count, hold-out protected by intention. *Data/time:* a reading exercise,
three to five days. *Cheapest week test:* read its hold-out receipt, its ledger and its
acceptance gates, and its option-bar loader. *Kills it:* the mechanisms turn out to be
conventions in prose rather than code — which is exactly what our own hold-out currently is,
and would be worth knowing. **Run `/benchmark` on it with the three workflow faults fixed
first** (`could_not_determine` dropped between stages; the 60,000-character truncation with
`claims` serialised before `checked`; a comparative verifier that also opens *our* file).

Hygiene, not a direction, and it has a clock on it: **pin pandas now.** 3.1 changes results
rather than raising — `merge` with `how="left"` keeping the source dtype,
`select_dtypes("datetime64[ns]")` matching exactly, timedelta from numerics defaulting to
microseconds. The guard most likely to catch the silent ones is the dual-engine equality test,
and only if both engines are exercised on the same upgrade.

---

## 6 · Decisions for the human

**1 · The strategy thesis.** Part B (entries 5–9) cannot start until a strategy is named, and
the demo in `run_backtest.py` (`sma_20 > sma_50`, delta 0.30, 30 days, +100%/−50%/10 days) was
never chosen as a thesis.
*Options:* (a) name a thesis now; (b) declare Part B a **method test** against the demo —
proving the battery works, no conclusion drawn about any strategy; (c) leave Part B gated and
run Part A only.
*Consequence:* (a) spends the project's first pre-registered test on whatever you name under
time pressure; (b) gets the battery exercised and records that no strategy conclusion was
drawn; (c) delays falsification but costs nothing.
**Recommendation (a recommendation, not a finding):** **(b) plus (c)** — run Part A now, and
declare Part B a method test in writing. Naming a thesis to unblock a battery is choosing the
question by convenience, which is the failure this plan's ordering exists to avoid.

**2 · The hold-out.** `plan.md` § 6: `NOT FIXED`. Both options are bad.
*Options:* (a) reserve **SPY** as an untouched underlying; (b) reserve **2025-01-01 → present**
(~23 months); (c) both.
*Consequence:* (a) costs the per-underlying check that `research-questions.md` 9.5 wants; (b)
23 months can reject gross failure and nothing finer; (c) costs both and buys the most.
**Recommendation:** **(a), plus declaring everything after today as accruing out-of-sample.**
Reserving SPY buys independence in the underlying, which is the dimension a QQQ-first data
purchase will not give us. Whatever you pick, **pick it before any entry runs** — a hold-out
chosen later is not one.

**3 · Buying real option data, and which first.**
*Options:* (a) **HistoricalData.net $199**, one-year window — 34 columns, one row per contract
per day, maps to `SCHEMA` by rename alone (`quote_date`→`asof`, `underlying`→`symbol`,
`expiration`→`expiry`, `type`→`right`, `underlying_close`→`underlying_price`); (b) ORATS $599
for 2007-to-present — cheaper per year and deeper, but puts a call **and** a put on one row
with **no `right` column**, so it needs a melt before `FileChainSource` sees it, and its
underlying is **reverse-engineered from put-call parity**, not a traded close; (c) DeltaNeutral
$1,150 (Level 1) / $1,495 (Level 2) — states an OPRA licence and names an operator; (d) Cboe —
the only vendor shipping **two snapshots per contract per day**, price unreadable behind a
calculator, third-party figures disagreeing between $2,100 and $8,500.
*Consequence:* (a) is a **$199 test of the whole adapter path** — column names, right aliases,
IV units, theta's sign and convention — before committing $799; (b) buys new code, not
configuration, and a `underlying_price` that is not what the schema asks for; (c) buys a named
counterparty and an OPRA licence for $696 more than (a)'s archive; (d) is the only one that
makes decision 7 testable.
**Recommendation:** **(a) now, this week.** The options-data path is a $199 decision, not a
data problem. Then choose between the $799 archive and DeltaNeutral's $1,150 on one question
only — whether an anonymous vendor's self-reported reconciliation is acceptable for a result
you would trade on. **Do not buy ORATS first**: a parity-implied spot makes every moneyness
subtly wrong in the one direction nothing here can detect. And note what no vendor sells:
**a historical `available_at`**. `quote_time` exists only from 2026-08-06 forward and is a
quote-staleness stamp, not a delivery time — so `adapters.py:510-516`'s assumed path runs on
**every** real backtest, and that "assumed" flag had better be visible in the interface and
not only in a log.

**4 · Single-leg or multi-leg.**
*Options:* (a) stay single-leg for three months; (b) start multi-leg.
*Consequence:* (b) means changing `TradeRecord`'s one-contract-per-row grain and building a
margin model with it (`requirements.md` names both, and names selling as the reason) — the
largest architectural change available, with no finding behind it.
**Recommendation:** **(a).** Every blocked question in `plan.md` is single-leg, and multi-leg
is extending the platform rather than using it. Revisit when a single-leg finding exists that
a spread would plausibly improve.

**5 · Is live trading in scope?**
*Options:* (a) no; (b) paper trading, manually, with a journal; (c) yes.
*Consequence:* (c) needs build stage 7, which `requirements.md` marks **absent**, plus live
drift monitoring, which is placeheld on live trading existing. (b) costs nothing and produces
the one input nothing in this repository can derive.
**Recommendation:** **(a), and (b) as the useful half.** Start writing `docs/feedback/` — a
journal of what you actually did and what the platform failed to tell you. It is empty today,
and it will reorder the research plan more than any argument in this brief.

**6 · Which AI direction, if any.**
*Options:* (a) the honesty machinery only — write-once hold-out receipt, cross-session trial
ledger, "the model proposes, trusted code decides" as an enforced boundary (D3); (b) a full
automated research loop; (c) agent/MCP access to the platform; (d) a copilot turning papers
into pre-registered hypotheses; (e) none.
*Consequence:* (b) is the direction RD-Agent demonstrates the failure of — it judges on its
test period, tracks its loop count and deflates nothing, so every result it reports is
in-sample with respect to its own search, however the code is arranged. Building that here
would produce numbers someone might trade on, wrong in a way nobody would notice. (c) is scope
C. (d) is cheap and low-value: the plan already has 20 entries and none has been run.
**Recommendation:** **(a) only.** It is the one AI-adjacent direction that makes a result
*harder* to believe rather than easier to produce, and we already have the two modules it
builds on. Refuse (b) until a human-run question has produced a finding — a loop that
automates an unexercised process automates nothing.

**7 · Which spot is authoritative.**
*Options:* (a) declare the chain's `underlying_price` authoritative and compute signals from
it; (b) declare the equity close authoritative and accept a stale chain; (c) carry both, make
the configuration say which it means, and refuse an undeclared mismatch.
*Consequence:* (a) changes what every existing signal reads; (b) keeps the optimistic bias and
hides it; (c) costs a config field and a guard, and makes the 15:45-vs-close choice a
sensitivity test rather than an argument.
**Recommendation:** **(c)**, specced before the first real chain is loaded. This is the only
place in this brief where I think a guard is missing rather than a question unasked: 1.20
catches a fill sharing its signal's quote, and nothing catches a quote strictly older than its
signal.

**8 · A LICENSE file.** This repository has none. Not a research decision, and not mine — but
two of the six benchmarked repositories (qlib, RD-Agent, both MIT; gs-quant, Apache-2.0) would
require one before any of their code could be used in a distributed artifact, and optopsy
(AGPL-3.0) and ArcticDB (BSL-1.1, converting per release) cannot be borrowed from at all.
**Recommendation:** decide it when scope C is decided; it is a consequence of that, not an
independent choice.

---

## 7 · What to run next, conditional on the answers

**Task 1 — `/goal`: write up what is already answered, then run Part A.**
*Condition:* decision 1 = (b) or (c). No data purchase needed.
*Scope:* first, write `research-questions.md` § 2.1's existing result (QQQ 1999–2026, 30
correction episodes, P(up) at 6 months 85.7% vs a 72.9% unconditional baseline) into
`docs/findings/` with its data range, configuration, guard state and caveats — it is quoted as
a conclusion today and has no finding behind it. Then run plan entries 1–4 as event studies.
*Must not:* touch the option chain; run any Part B entry; change a hypothesis or an
abandonment criterion after seeing a number — a revised criterion is a **new entry with its
own date**; report a point estimate without its interval and effective sample size. Entry 1
rests on **21 non-overlapping observations** and is the single most likely result in this plan
to be noise.

**Task 2 — `/goal`: buy the $199 sample and prove the adapter path.**
*Condition:* decision 3 = (a).
*Scope:* purchase, `column_map`, `check_file()`, and a written report of exactly what the file
does and does not supply — IV units, theta sign and convention, `right` aliases, whether
`volume` and `open_interest` are populated, and the `available_at` absence with the
`availability_assumed` flag it sets.
*Must not:* run a strategy on it; draw any research conclusion; report a number from it as a
finding. This task's output is a schema verdict, not a result. Land lesson 4
(`expiration_cycle`) **before** the archive is bought, not after.

**Task 3 — a spec, then stop.**
*Condition:* decision 6 = (a) and/or decision 7 = (c).
*Scope:* one spec per item in `docs/specs/` — (i) the write-once hold-out receipt plus a
cross-session trial ledger; (ii) the authoritative-spot declaration and the stale-quote guard.
Each says what becomes answerable that is not answerable now, the smallest version, and what
would make it not worth having built.
*Must not:* implement either; bundle them into one spec; or include anything that proposes a
hypothesis automatically. The boundary is "the model proposes, trusted code decides", and a
spec that blurs it is the thing to refuse.
