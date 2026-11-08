# Benchmark · 2026-11-06

Six repositories, read against this platform on eight fixed dimensions. Clones under
`../benchmark-clones/`, read-only, **none of their code was executed**.

| Repository | What it is | Clone |
|:---|:---|:---|
| [optopsy](https://github.com/goldspanlabs/optopsy) | an options backtester — the only direct comparable | `../benchmark-clones/optopsy` |
| [nautilus_trader](https://github.com/nautechsystems/nautilus_trader) | event-driven trading platform, Rust core | `../benchmark-clones/nautilus_trader` |
| [qlib](https://github.com/microsoft/qlib) | quantitative research platform for equities | `../benchmark-clones/qlib` |
| [RD-Agent](https://github.com/microsoft/RD-Agent) | an LLM research loop driving qlib | `../benchmark-clones/RD-Agent` |
| [gs-quant](https://github.com/goldmansachs/gs-quant) | client for Goldman's Marquee platform | `../benchmark-clones/gs-quant` |
| [ArcticDB](https://github.com/man-group/ArcticDB) | versioned columnar time-series store | `../benchmark-clones/ArcticDB` |

The run was executed in two batches because of a session limit, and the two batch syntheses
were merged into this file. The per-repository records are `docs/research/benchmarks/`.

---

## Read this before anything else: this run's cross-check did not happen

**No claim in either batch carried a `verdict` field. The count of cross-check-`confirmed`
claims across all six repositories is zero.** The rule for a benchmark report in this project
is that only a confirmed claim may carry a conclusion, and under the letter of that rule this
report would be empty.

Two causes, both in the workflow rather than in the repositories:

1. **Several cross-check agents died on a session limit.** The batch logs read
   `[cross-check optopsy] failed`, `[cross-check nautilus_trader] failed`,
   `[cross-check RD-Agent] failed`, `[cross-check ArcticDB] failed`, and
   `0 claims confirmed, 0 overstated or wrong`.
2. **The synthesis payload was truncated by character count**, as
   `JSON.stringify(kept).slice(0, 60000)` in `.claude/workflows/benchmark.js`, with `claims`
   serialised **before** `checked`. A long first repository therefore starves every
   repository of its verdicts — which is exactly what happened in both batches.

What exists instead is weaker, and is named as such on every claim below.

| Grade | What it means |
|:---|:---|
| **[re-opened]** | An analyst claim arrived; a report writer opened the cited file in the clone. One agent read it and a second re-opened it — the standard the workflow asks for, except that the second agent is the report writer rather than an independent verifier. |
| **[record]** | Carried from `docs/research/benchmarks/gs-quant.md` or `docs/research/benchmarks/ArcticDB.md`. Each of those records declares in its own header that no verdict reached it either, and that its claims were re-opened by its own writer. Same standard as [re-opened], one hop further away. |
| **[one reader]** | No analyst claim existed; one agent read the clone. **No second reading of any kind.** Every nautilus_trader, qlib and RD-Agent finding is of this kind, as are gs-quant dimensions 4–8 and ArcticDB dimensions 4, 5, 6 and 8. |

Where the two batch reports graded the same claim differently, **the weaker grade is kept
here**. A `[one reader]` entry should be verified before anyone acts on it.

### The arithmetic of what was verified

| | Count |
|:---|---:|
| Claims that reached a synthesis writer | **139** — 74 in batch 1 (optopsy only, the 74th truncated mid-sentence) and 65 in batch 2 (gs-quant only) |
| …repositories those claims covered | **2** of 6 |
| …dimensions they covered | optopsy 4 of 8 (data model, fill and cost, look-ahead, validation); gs-quant 3 of 8 (data model 25, fill and cost 20, look-ahead 20) |
| Claims carrying a cross-check verdict | **0** |
| Verdict `confirmed` from the designated verifier | **0** |
| Verdict `overstated` | **0** |
| Verdict `wrong` | **0** |
| Verdict `file_not_found` | **0** |
| `could_not_determine` entries received, any repository | **0** |
| Claims re-opened in a clone **by a report writer** | **80** — 15 optopsy, 65 gs-quant |
| …held exactly as cited | **72** — 14 optopsy, 58 gs-quant |
| …held with a line drift | **1** optopsy; gs-quant accurate to a line or two throughout except one item |
| …did not survive as stated | **15** — 7 gs-quant claims, 8 ArcticDB items |
| Cited a file that does not exist, any repository | **0** |
| Conclusions overturned by a defect in a claim | **0** — but see the correction below, which overturned a comparison rather than a conclusion |
| Claims reaching a report with no verification of any kind | **59** (optopsy) — not used below; some are interesting, none is evidence |
| Dimensions twice-verified by an independent verifier | **0 of 48** |

**No dimension in this benchmark is twice-verified. Nothing in this report should be read as
though it were.**

### The most instructive failure: a "better than ours" claim that was false about us

gs-quant claim 24 said a named P&L residual "is the discipline our two equity curves do not
impose". The half about gs-quant held exactly. The half about **us is false**:
`backtest/accounting/attribution.py:47` already is
`COMPONENTS = ("delta", "gamma", "vega", "theta", "residual")`; the residual is documented as
"not a waste bin" but a diagnostic (`:12-17`); a threshold past which the attribution is
declared untrustworthy is a named realism assumption (`:36-45`); `high_residual` counts the
trades that cross it (`:57`); and the daily method carries a `spread` term as well
(`:168`). On that dimension we were **ahead** of what was being recommended to us.

The claim's own citations into their clone were accurate. What it got wrong was the
comparison, and no file in their clone could have settled it.

> **A "better than ours" judgement is a claim about two codebases, and only one of them was
> being read.** No cross-check stage as the workflow currently defines it would have caught
> this: the verifier opens the cited file in *their* clone. The next run needs a verifier that
> also opens the file in ours.

What survives of that claim is narrow and real, and it is entry 8 of "Worth learning".

### Three faults to fix in the workflow before the next run

They cost this report more than any repository did.

1. **`could_not_determine` is discarded between stages.** `.claude/workflows/benchmark.js`
   stage 2 builds its payload as `(r.claims || []).map(...)` and never carries
   `r.could_not_determine` forward, so the closing section the script asks for has nothing to
   draw on by construction.
2. **The synthesis payload is truncated by character count**, and `claims` is serialised
   before `checked`, so the verdicts of every repository are starved by a long first one.
3. **Claims can arrive mislabelled by dimension.** Every ArcticDB claim did: licence and
   sizing claims arrived tagged `"data model and option chains"` or `"fill and cost
   realism"`, and the look-ahead claims arrived tagged `"fill and cost realism"`. The
   `dimension` field was unreliable and was ignored by that record's writer.

And a fourth, from the correction above: a cross-check verdict should be a statement about
both codebases whenever the claim is comparative.

---

## Worth learning

Fifteen entries, ordered by what we would do first. **Twenty candidate entries came out of
the two batches; five were duplicates across them, and one was reduced in scope by the
correction above.** Each entry names the repository, the file, the change here, a rough cost,
and — where it can be told — the question in `docs/research/plan.md` it would unblock.

### 1 · An error-message table as a document type — an afternoon
**ArcticDB · `docs/mkdocs/docs/error_messages.md:129, 141, 143-144, 150`** [record]
*(the "document type" framing comes from that record's dimension 6, which had no claim behind
it — that half is* [one reader]*)*

A table of *message · cause · resolution*. The thing worth copying is not the format but what
writing it forced: this is the file in which they document their own two worst boundary
failures — "**ArcticDB does not check this condition at write time**" (`:129`, about index
sortedness, which surfaces much later at read time as "Non-contiguous rows, range search on
unsorted data?", `:136`) and "we do not know: * Whether a specified column exists * What the
type of the data held in a specified column is" (`:141`, `:143-144`), with `get_description`
offered as the user's own discipline.

**Here:** `backtest/core/errors.py` declares the three kinds of failure and our errors carry
context, but nothing maps a message to a remedy and `docs/` has no such file. Our refusals do
carry reasons — `check_tradable`, `effective_lambda`'s absent-input rule — documented where
they are raised. A single table from message to cause to remedy is the artefact a researcher
at 11pm actually needs, and assembling it is the cheapest audit of our own messages we could
run. Write `docs/errors.md` as that table, one row per `StartupError` and per guard refusal.

**Cost:** an afternoon for the table; a few days if it is generated from the raise sites and
guarded, so it cannot drift — which is the failure this project refuses everywhere else.
**Unblocks:** no plan entry directly.

### 2 · One skill file, read by CI and by a local command from the same path — an afternoon
**ArcticDB · `.github/workflows/claude-code-review.yml` and `.claude/skills/review-pr/SKILL.md`** [one reader]

Their review workflow runs on every non-draft pull request (`opened, synchronize,
ready_for_review, reopened`) and literally reads the skill file into the prompt (`- name: Read
review skill` … `cat .claude/skills/review-pr/SKILL.md`), and the same file is the local slash
command. The skill is written to detect which mode it is in: "When invoked from a GitHub
Actions workflow the following variables are injected before this prompt: `PR_NUMBER`, `REPO`,
`EVENT_ACTION`, `BEFORE_SHA`, `AFTER_SHA`. When invoked locally as a slash command the PR
number is `$ARGUMENTS`."

A related idea from gs-quant, same cost [record]: `python -m gs_quant.skills install --global`
**symlinks** `.claude/skills/<name>` to the directory inside the installed package, so
"Updating gs_quant updates the skill automatically" (`gs_quant/skills/README.md:9-12, 62`,
copying instead on Windows at `:63`). The skill cannot drift from the installed version.

**Here:** `.github/workflows/check.yml` and our agent definitions currently describe the
review rules twice. Point CI at the same file a human invokes. The failure this prevents is
ours already in another form — the three guards that were written and never registered, which
is why `tools/githooks/pre-commit` exists.

**Cost:** an afternoon. **Unblocks:** no plan entry directly.

### 3 · A per-branch work log, committed — an afternoon
**ArcticDB · `docs/claude/plans/<branch-name>/branch-work-log.md`, declared in `CLAUDE.md` (271 lines) alongside 25 files under `docs/claude/`** [one reader]

A committed, per-branch record of what was done on that branch and why, written to be read by
whoever picks it up next.

**Here:** this repository has two sessions in one working tree, and `CLAUDE.md`'s rule — write
**why** in the commit message — only tells the next reader about work that has already landed.
A per-branch log says what the other window is in the middle of, and it survives a squash.

**Cost:** an afternoon. **Unblocks:** no plan entry directly.

### 4 · An expiry-cycle field on the chain row — an afternoon
**optopsy · `optopsy/data/providers/eodhd.py:61-66`** [re-opened]

Their vendor row carries `expiration_type` (monthly / weekly) and `rho`; `expiration_type` is
part of the dedup key and of the Postgres unique index
(`optopsy/data/providers/pg_store.py:69-78`). Ours has neither.

**Here:** add `expiration_cycle` to `CHAIN_COLUMNS` (`backtest/core/types.py:202-230`) with
the missing-not-dropped rule the comment at `:200-201` already states. A weekly and a monthly
contract at the same strike and expiry are different instruments to trade, and today we cannot
ask a question that separates them.

A caution from the same repository, also [re-opened]: their *default* silently keeps monthly
only (`optopsy/data/providers/eodhd.py:732`,
`_fetch_options(..., expiration_type: str = "monthly")`). Add the field; do not add a default
that filters the universe.

**Cost:** an afternoon. **Unblocks:** plan entry 12 (`5.6 · selection perturbation`), whose
"adjacent expiry" arm cannot be meaningful while a weekly and a monthly expiry are
indistinguishable on the row.

### 5 · Composable transaction-cost models — an afternoon to a week
**gs-quant · `gs_quant/backtests/backtest_objects.py:542-595` and `gs_quant/backtests/equity_vol_engine.py:414-431`** [record]

Three primitives: `ConstantTransactionModel(cost)`; `ScaledTransactionModel(scaling_type,
scaling_level)` where `scaling_type` may be `'notional_amount'` *or a risk measure* (for a
risk measure it does `with PricingContext(state): risk = instrument.calc(self.scaling_type)`,
so a cost can be a fraction of notional, a percentage of premium, or **a cent per vega**); and
`AggregateTransactionModel(transaction_models, aggregate_type=SUM|MAX|MIN)`. `MAX` of a fixed
and a scaled model is a per-order minimum commission, expressed rather than coded. Their
server-side engine has the options unit as a first-class enum member —
`if model.scaling_type == EqVega: scaling_quantity_type = TransactionCostScalingType.Vega`,
over `class TransactionCostScalingType(Enum): Quantity / Notional / Delta / Vega`.

**Here:** `order_fees` / `exercise_fees` (`backtest/execution/costs.py:160-180`) express a fee
schedule as fields on one config, and `:169-171` hard-codes
`max(quantity * costs.commission_per_contract, costs.min_commission_per_order)`. Rebuild
`FeeBreakdown`'s inputs as composed units so the minimum is a declared `MAX`, and a cost per
unit of vega — the natural unit for an options cost model, which we cannot write down at all
today — becomes expressible. The per-trade sum check stays exactly as it is.

**Cost:** an afternoon for the composition; a week with the config surface and the form.
**Unblocks:** plan entry 6 (`does the result survive realistic costs and one more day of
delay?`), which cannot vary a cost shape it cannot express.

### 6 · Every refusal at once, and the engine chosen by capability — a week
**gs-quant · `gs_quant/backtests/equity_vol_engine.py:145-290` and `gs_quant/backtests/strategy.py:30-34, 66`** [one reader]

Two things. The engine's pre-flight **accumulates** readable refusals and reports them
together — "Error: Maximum of 3 triggers", "Error: Only AggregateTrigger and PeriodTrigger
supported", "Error: Only EqOption or EqVarianceSwap supported", "Error: HedgeAction: Hedge
instrument must be a synthetic forward …" — rather than failing on the first. And `Strategy`
asks every engine whether it can run this strategy:
`[engine for engine in _backtest_engines() if engine.supports_strategy(self)]`, so the engine
is a consequence of the configuration rather than a choice the user must make correctly.

**Here:** `backtest/engine/vectorized.py:93-106` already declares `UNSUPPORTED` as data, which
is the better half, but `_guard` (`:108-140`) raises on the **first** mismatch, so a user
fixing three things learns about them one run at a time. Turn `_guard` into
`refusals(strategy) -> list[str]`, raise once with all of them, and let the runner pick the
engine that can run the configuration — or name every reason neither can.

**Cost:** a week. **Unblocks:** no plan entry directly; it is a usability change.

### 7 · The unresolved specification travels with the resolved object — an afternoon to a week
**gs-quant · `gs_quant/base.py:623-629` and `gs_quant/base.py:128`** [record]

`new_instrument.__unresolved = copy.copy(self)` and
`new_instrument.__resolution_key = resolution_key`, where `RiskKey` is a namedtuple of
`(provider, date, market, params, scenario, risk_measure)`. A resolved instrument keeps both
the relative specification it came from and the conditions it was resolved under.

**Here:** a chain row carries `quality_flags` and `greeks_model_derived`
(`backtest/core/types.py:229-230`), but a trade record does not carry the selector that chose
that contract. `r.why()` reconstructs it; storing it means the reconstruction cannot disagree
with what happened.

**Cost:** an afternoon to a week. **Unblocks:** plan entry 12 (`5.6 · selection
perturbation`) — perturbing the selector is easier to trust when the trade records which
selector produced it.

### 8 · Higher-order terms broken out of the residual, not folded into it — an afternoon to a week
**gs-quant · `gs_quant/target/backtests.py:80-100` (`FlowVolBacktestMeasure`) and `gs_quant/skills/gs-quant-overview/backtesting.md:541-546`** [record]

**This is what survives of the claim corrected at the top of this report.** The
"name a residual" recommendation does not apply: we already do, and we additionally refuse to
trust the decomposition when the residual is too large, which they do not.

What is left to take is narrow and real. `FlowVolBacktestMeasure` enumerates `PNL_spot`,
`PNL_vol`, `PNL_carry`, `PNL_delta`, `PNL_gamma`, `PNL_higher_order_spot`,
`PNL_higher_order_vol`, `PNL_theta`, `PNL_transactions`, `PNL_vega`, `PNL_unexplained`,
`PNL`, `delta`, `gamma`, `vega`, `NAV`. Three things in that list are not in ours: the
**higher-order spot and vol terms are their own named terms**, so the unexplained part is what
survives after the higher orders are accounted for rather than what survives a second-order
expansion; `PNL_transactions` sits inside the same decomposition in both their methods, not
only the daily one; and three grouped aggregates are defined once rather than recomputed by
each reader — "`PNL_spot` is delta plus gamma plus the higher-order spot part, `PNL_vol` is
vega plus the higher-order vol part, and `PNL_carry` is `PNL_spot` plus `PNL_theta`", with
the residual arithmetic given as "`PNL` minus `PNL_spot`, `PNL_vol` and `PNL_theta` is the
unexplained part".

**Here:** break the third-order spot and the vol-of-vol terms out of `residual` in
`backtest/accounting/attribution.py`, so `high_residual` (`:57`) fires when something is
genuinely wrong rather than when the underlying simply moved a long way — which the module's
own docstring names as the usual cause (`:13-15`).

Their numbers are computed server-side and **cannot be checked from the artifact**, so what
would be copied is the term list, not an implementation.

**Cost:** an afternoon to a week — smaller than its position suggests, and eighth only because
it touches a number a reader acts on. **Unblocks:** no plan entry directly.

### 9 · A volume term in the fill widening — a week
**optopsy · `optopsy/pricing.py:51-59` and `optopsy/types.py:159-160`** [re-opened]

```python
liquidity_score = (volume.fillna(0) / reference_volume).clip(upper=1.0)
ratio = fill_ratio + (1 - fill_ratio) * (1 - liquidity_score)
```

with `reference_volume = 1000`: a liquid contract fills near the mid, a zero-volume contract
at the touch. They also charge by leg count —
`ratio = min(fill_ratio + per_leg_slippage * (num_legs - 1), 1.0)` with
`per_leg_slippage = 0.073`.

**Here:** `effective_lambda` (`backtest/execution/costs.py:133-158`) widens by days-to-expiry
and by delta, both defaulting to zero, and reads neither volume nor open interest. A volume
term is the one widening the chain row can already support.

The week, not the afternoon, is because the λ our document allows runs to 3 and theirs is
capped at 1.0; a widening added without first measuring what it does to the baseline fixture
is a number we would then have to defend. Measure on `tests/baselines/baseline_trades.csv`
first, then land it, then `tools.baseline update` with the diff printed.

**Cost:** a week. **Unblocks:** plan entry 6 (`realistic costs and one more day of delay`),
and the liquidity-filter half of entries 12 and 19.

### 10 · The benchmark reported beside the strategy in the default result — a week
**qlib · `qlib/workflow/record_temp.py:505-536`** [one reader]

Their default portfolio record computes three things and prints all three, always:

```python
analysis["excess_return_without_cost"] = risk_analysis(report_normal["return"] - report_normal["bench"], ...)
analysis["excess_return_with_cost"]    = risk_analysis(report_normal["return"] - report_normal["bench"] - report_normal["cost"], ...)
...
pprint("The following are analysis results of benchmark return(...).")
pprint(risk_analysis(report_normal["bench"], freq=_analysis_freq))
```

We have better baselines than they do — `backtest/research/baselines.py` has
`delta_equivalent` and a stratified `random_entry`, which is a harder benchmark than an index.
The difference is where they live: ours is a research module a person chooses to call; theirs
is in the record that every run writes. A researcher who forgets is the common case.

**Here:** put the baseline comparison in the default result header, so a run that has no
baseline says so rather than being silent about it.

**Cost:** a week. **Unblocks:** plan entry 5 (`can random entries at the same frequency do as
well?`) — the comparison exists, what is missing is that every run makes it.

### 11 · The fit window as a constructor argument on anything fitted — a week
**qlib · `qlib/data/dataset/processor.py:197-205, 239, 282` and `qlib/data/dataset/handler.py:535`** [one reader]

Every fitted processor takes `fit_start_time` / `fit_end_time` explicitly, with the rule in the
code beside it:

```python
# NOTE: correctly set the `fit_start_time` and `fit_end_time` is very important !!!
# `fit_end_time` **must not** include any information from the test data!!!
```

and the fit slice is applied mechanically:
`fetch_df_by_index(df, slice(self.fit_start_time, self.fit_end_time), ...)`. The separation is
typed, not advisory — `handler.py:535` raises
`TypeError("Only processors usable for inference can be used in `infer_processors` ")`.

**Here:** anything in `backtest/research/` that estimates a parameter from data — a
normalisation, a threshold, a fitted rule — should take its fit window as an argument it
cannot omit, and a guard should refuse a fit window that overlaps the hold-out that
`backtest/research/holdout.py` is protecting. We have the hold-out; we do not have a mechanical
statement of which rows a fit was allowed to see.

**Cost:** a week. **Unblocks:** nothing by itself; it protects section 6 of the plan, the
pre-registration record, and every Part B and Part C entry that fits a parameter before
testing it.

### 12 · A named fill-model family behind one interface — a week
**nautilus_trader · `crates/execution/src/models/fill.rs:51-90`** [one reader]

One `FillModel` trait (`is_limit_filled`, `is_slipped`, `fill_limit_inside_spread`, plus an
optional synthetic `OrderBook` the matching engine consumes as available liquidity), and
**eight** implementations of it in the same file: `DefaultFillModel` (`:327`),
`BestPriceFillModel` (`:384`), `OneTickSlippageFillModel` (`:489`), `ProbabilisticFillModel`
(`:570`), `TwoTierFillModel` (`:657`), `ThreeTierFillModel` (`:754`),
`LimitOrderPartialFillModel` (`:868`), `SizeAwareFillModel` (`:966`).

This is the dimension the architecture document calls comparing two *forms* of a component
rather than two parameter values of one form. Our fill rule is a single function —
`fill_price` / `effective_lambda` in `backtest/execution/costs.py:80-158` — parameterised by
λ. Two candidate fill rules cannot be run against each other today; two λ values can.

**Here:** lift the fill rule to a named component the way `backtest/research/forms.py` already
treats candidate component forms, so a trade log can record which fill model produced it. The
one-module cost rule that both engines share (`backtest/execution/costs.py`, whose docstring
forbids the trade table from simplifying to the mid) stays — the point is one interface,
several named rules, not several cost modules.

One thing **not** to copy: `random_seed: Option<u64>` (`:172`, and in all eight constructors)
is optional, so a stochastic fill model can run unseeded and irreproducibly. Ours would
require the seed.

**Cost:** a week. **Unblocks:** plan entry 6, in its stronger form — whether the result
survives a *different fill rule*, not only a worse λ.

### 13 · Provenance refused at the point of aggregation, not merely recorded — a week
**gs-quant · `gs_quant/api/gs/backtests_xasset/response_datatypes/risk_result_datatypes.py:38-40` and `gs_quant/risk/results.py:191, 326-329`** [record]

`if self.unit != other.unit: raise ValueError(...)`, and
`raise ValueError('Results must have matching scenario and location')`. Arithmetic on risk
results refuses to combine values whose units, market, scenario or location differ.

**Here:** `r.manifest()` records the run's commit, data version and provenance for a reader to
inspect, and a reader may skip it. Make the comparisons that already exist — the pinned
baseline, `Result.compare_forms` (`backtest/api/sdk.py:1537`, over
`backtest/research/forms.py`) — **refuse** two runs whose data version, cost configuration or
synthetic flag differ, rather than printing a difference that is partly an artefact of the
difference in assumptions.

**Cost:** a week. **Unblocks:** plan entry 6 and entry 12, both of which are comparisons
between runs and both of which are wrong if the runs differ in more than the thing being
varied.

### 14 · Snapshots as a named, deletion-proof pinned set — a week
**ArcticDB · `docs/mkdocs/docs/tutorials/snapshots.md:3, 25-26` and `docs/mkdocs/docs/index.md:390`** [record]

"Snapshots enable multiple symbols to be versioned together via a human readable string name" —
`library.snapshot(f"SNAP_{i}")`, after which the name "will forever point to all symbols that
exist at this time at their current latest version", and "certain versions [are] protected
from deletion" (`library.py:2736-2738` spells out that snapshotted data survives a delete).

Our point-in-time answer is **computational**: `available_at` on the row
(`backtest/core/types.py:212`), cut at `backtest/data/options/adapters.py:435-441`, which is
auditable — you can ask *why* a row was visible. Theirs is **referential**: a name you can hand
to someone else. These are not the same guarantee and we should have both. A run's manifest
names a data version, not an immutable pinned set.

**Here:** add a named data pin, recorded in the manifest, so a run can be re-run against the
same bytes.

**Cost:** a week. **Unblocks:** nothing by itself; it is what a reader of
`docs/research/plan.md` would need in order to reproduce a published result a year later, and
it is the missing half of section 6's pre-registration record.

### 15 · A revision chain on the row — a month
**qlib · `qlib/data/pit.py:30-37` and `qlib/data/data.py:750-765`** [one reader]

Their point-in-time record is four columns per statement — `date` (publication), `period` (what
it describes), `value`, `_next` (byte index of the next occurrence of the same field) — so a
restated figure does not overwrite its predecessor; the chain is walked to find the version
that was current at `cur_time`. And the forward read is **refused, not discouraged**, in two
places:

```python
# qlib/data/pit.py:34-37
raise ValueError("PIT database does not support referring to future period "
                 "(e.g. expressions like `Ref('$$roewa_q', -1)` are not supported")
# qlib/data/data.py:756
assert end_index <= 0  # PIT don't support querying future data
```

**Here:** `available_at` handles *late arrival* — a row that arrived two days after the session
it describes is not visible on that session. It does not handle *restatement*: `CHAIN_KEY` is
`("asof", "symbol", "expiry", "strike", "right")` (`backtest/core/types.py:234`), so a
corrected quote replaces the original and the original is gone.

Every other repository read here has the same gap, two of them explicitly: ArcticDB's
`update()` "replaces the whole index range it spans"
(`python/arcticdb/version_store/library.py:1597-1599`) [record], and optopsy's cache keeps
"only the **last** occurrence of each key … so that newer data wins"
(`optopsy/data/providers/cache.py:151-164`) [re-opened].

**Cost:** a month — it touches the chain key, every adapter, the read cut, the baseline fixture
and `tools/check_wiring.py`. Worth it only when a vendor who revises is actually in use, but
worth writing down now, because the cheap version (keep `available_at`, add a `superseded_at`)
is a decision made once. **Unblocks:** section 4.2 of the plan names `available_at` as "the
only route to the point-in-time alignment `requirements.md` marks absent"; restatement is the
half of that route we do not have.

---

## We do better

Each with what would be lost if it were traded away. This section exists because several of the
gaps above look like missing features and are refusals.

### The one-day execution lag
`backtest/engine/loop.py:13-29` — intents created on T-1 are filled on T, as step 3 of a fixed
eight-step day; `backtest/core/config.py:177` (`execution_lag_days: int = 1`), required `>= 1`
at `:191`, with `min_days_to_expiry` constrained against it at `:201-203`.

**Not one of the six has it.** optopsy joins signal dates to entry rows on the same
`quote_date` and prices the entry from that row's own bid/ask —
`optopsy/filters.py:76` is
`return data.merge(valid_dates, on=["underlying_symbol", date_col], how="inner")`, and
`optopsy/evaluation.py:84-86` is `entry=lambda r: (r["bid_entry"] + r["ask_entry"]) / 2`
[re-opened]. gs-quant has **no lag in any engine and none can be configured**: `AddTradeAction`
and `HedgeAction` both document "The trades are resolved on the trigger date (state)"
(`gs_quant/backtests/actions.py:119-120`, `:387-389`), and the only lag-shaped parameter in the
package (`EventTriggerRequirements.offset_days`, `gs_quant/backtests/triggers.py:408`) shifts an
economic-calendar date [record]. ArcticDB's query language cannot express a lag at all — no
`shift`, `lag`, `lead`, cumulative or window clause exists in
`cpp/arcticdb/processing/clause.hpp` [record]. nautilus_trader has a real latency model —
separate insert, update and delete latencies queued as inflight commands
(`crates/backtest/src/exchange.rs:968-1000`) [one reader] — at nanosecond grain for an intraday
venue, which is the right answer to a different question.

**What would be lost:** every result on this platform becomes a number that assumed a fill at a
price the decision could see. There is no way to recover the honest version afterwards, and no
guard can detect it, because nothing is wrong with the arithmetic. This is the single difference
most likely to be mistaken for a missing feature.

### Look-ahead protection that is not optional per engine
`backtest/core/dataview.py` guards every read in both engines, with the truncate-and-recompute
property test at `tests/test_signals.py:101` and the injected-leak check at `:119`.

gs-quant's `Clock` (`gs_quant/backtests/data_handler.py:22-52`) is a real mechanism and raises
in both directions — `:35-36` refuses running backwards, `:51-52`
`if lookahead: raise RuntimeError(...)`, and every read goes through it (`:74-81`) — but it is
constructed in exactly one place (`gs_quant/backtests/predefined_asset_engine.py:129`), the
flagship `GenericEngine` never builds one, triggers hold their own `DataSource` and bypass it
entirely (`gs_quant/backtests/triggers.py:173-179`), **nothing in 3,256 lines of backtest tests
asserts that a future read raises**, and the phrase "look-ahead" appears nowhere in the
project's prose — including the 603-line skill that teaches a model to write their backtests
[record]. In ArcticDB the vocabulary is absent from the whole tree: a case-insensitive grep for
`look.?ahead|lookahead|survivorship` over `*.py`/`*.md`/`*.cpp`/`*.hpp`/`*.ipynb` returns zero
hits, and the ~20 registered exception types
(`cpp/arcticdb/python/python_module.cpp:120-320`) are storage- and schema-shaped throughout
[record].

**What would be lost:** the only defence that keeps working when the person writing the strategy
has not thought about it.

### `available_at` as a column, with the two questions kept apart
`snapshot(self, day: date, *, known_by: date | None = None)`
(`backtest/data/options/chain.py:46`, `backtest/data/options/adapters.py:448`) separates "what
could be traded now" from "what is known now about then".

ArcticDB's `as_of` is **write time**, selected on creation timestamp alone
(`cpp/arcticdb/version/version_functions.hpp:304-307`, reached from
`local_versioned_engine.cpp:379-401`), which cuts the version chain and never the rows — a
ten-year backfill in one write leaves it nothing to cut — and that timestamp is the writing
client's own wall clock (`cpp/arcticdb/async/async_store.hpp:53, 249`; the engine itself
subtracts a `clock_unsync_tolerance` at `version_map.hpp:951-953`). "Bitemporal" in their
documentation means version time only (`index.md:21, 366`, `faq.md:33`, `history.md:30`,
`fundamentals.md:141`) — framing statements, no column and no API parameter. Their own options
notebook calls a **data date** `as_of` and passes it as a degenerate `date_range`
(`ArcticDB_demo_equity_options.ipynb:200-203`), which is the clearest single illustration of why
the distinction needs two names [record]. gs-quant's only availability rule is wall-clock ("is
today's close out yet", `gs_quant/markets/markets.py:60-89`), and its genuinely bitemporal
`as_of` on `Dataset.get_data` (`gs_quant/data/dataset.py:171-211`) is never passed by the
backtest's own data source (`gs_quant/backtests/data_sources.py:76, 90`) [record].

**What would be lost:** the distinction, and with it any honest answer to "was this knowable
then".

### A row is never dropped; an absence is reported with a reason
`backtest/core/types.py:200-201` — "A field one implementation cannot fill is marked missing; it
MUST NOT be dropped from the interface" — with `CHAIN_COLUMNS` (`:202-230`) defined after what
vendors supply, so a field an implementation cannot produce is NaN and stays.

optopsy's trades are built by an inner merge of an entry row with its own contract's exit row:
`entries.merge(right=exits, on=merge_cols, suffixes=("_entry", "_exit"))`
(`optopsy/evaluation.py:82-88`) — pandas' default `how="inner"`, no `indicator=`, no log line
[re-opened]. A contract with no quote at the exit DTE disappears, and the result set keeps only
the trades the data could price at exit. gs-quant's `PositionSet` keeps only
`p.asset_id is not None`, stashes the rest in `__unresolved_positions`, and logs at INFO
("Sifting them out and resolving the rest…") — `gs_quant/markets/position_set.py:684, 692-694,
1079-1083`; nothing raises and the caller must know to ask `get_unresolved_positions()`
[record].

**What would be lost:** the survivorship is in the direction of the result. Our own baseline file
records exactly this failure happening here once — 223 → 230 trades, net −8,612 → −11,822,
"because unresolvable trades were being dropped AFTER their outcome was known, flattering the
baseline by 3,210 of losses". That is the number this rule is worth.

### A tradability gate instead of a crash or a substitution
`check_tradable` (`backtest/execution/costs.py:40-78`) refuses an absent, NaN, non-positive or
crossed quote, a relative spread over the configured maximum, and a row a cleaning rule
flagged — and never substitutes an estimated price.

gs-quant has nothing to inspect: its whole backtest valuation vocabulary is one member,
`class ValuationFixingType(EnumBase, Enum): PRICE = 'price'` (`gs_quant/backtests/core.py:48-49`),
there is no bid, ask or spread anywhere in the fill path, and an unavailable price is
`raise RuntimeError('can not compute the execution price')` (`gs_quant/backtests/order.py:48-53`,
with the GenericEngine's equivalent at `generic_engine.py:700-713`) [record]. ArcticDB applies
no tradability test in its only option workflow — across the 22 cells of
`ArcticDB_demo_equity_options.ipynb` the words bid, ask, mid and spread do not appear once — and
its shipped sample is full of rows nothing could trade: measured directly from
`docs/mkdocs/docs/notebooks/data/2013-06-03tech-options.csv`, 10.5% of rows have `bid == 0`,
63.1% have zero volume, 23.1% zero open interest, median relative spread 0.041 with **p90 of
2.00**, 24.8% above a 20% spread, and the first data row is a zero-volume deep-ITM quote. Their
notebook charts it without comment [record].

**What would be lost:** fills on quotes that never existed, and a backtest whose best trades are
its worst data.

### Costs default pessimistic, and no strategy choice is defaulted at all
`adverse_fraction` defaults to 1.0 — the full spread paid — and may be raised to 3. And
`tools/check_defaults.py` reads dataclass **field** defaults over 12 parameters in slots 2b/3/4/6.
That guard exists because `ExitRule` once supplied +100%/−50% to every run that omitted them, so
the MAE distribution a researcher reads *in order to choose a stop* came back censored at exactly
the stop the platform had chosen.

**Every** gs-quant cost default is zero: `gs_quant/backtests/actions.py:50-51`
(`def default_transaction_cost(): return ConstantTransactionModel(0)`), `:66`, `:161-164`, across
all ten action classes; and the one local backtest in that repository that charges turnover also
defaults to zero (`gs_quant/timeseries/backtesting.py:56-63`) and is never given costs by its
public wrapper (`:259`) [record]. optopsy's defaults are a live example of the other half: its
slippage default is `"spread"` in the code (`optopsy/types.py:157`) and `'mid'` in the
documentation (`docs/parameters.md:318`) [both re-opened] — two different fill assumptions in one
shipped product, and the result registry records the documented one
(`optopsy/ui/tools/_helpers.py:596`). No repository read here has anything resembling a guard that
reads its own defaults.

**What would be lost:** a frictionless result that nobody labelled frictionless; and the platform
quietly answering the question instead of the researcher, in the one place it is hardest to notice
— a distribution that looks clean because it was clipped.

### Trials counted, the Sharpe deflated by the count, and the rest of the validation suite
`backtest/research/tracker.py:42-128` — `Trials` counts distinct configurations by `run_id` so a
rerun is not a new trial, refuses a record with no `run_id` ("a rerun cannot be told from a new
trial"), and `deflate()` uses the spread of the trial Sharpes when there are at least two. Around
it, `backtest/research/` holds baselines, walk-forward and purged k-fold with an embargo, a
hold-out that raises on an overlapping run, trade and block bootstrap, conditional statistics that
are refused without their unconditional baseline, and a pre-launch gate where `unknown` is not a
soft pass.

**This is the widest gap in our favour, and no repository read here has any of it.** optopsy's
`metrics.py:43-51` returns `0.0` when there are fewer than two returns and `0.0` again when the
standard deviation is near zero, so a one-trade bucket reports "Sharpe 0.00" rather than "not
computable"; its whole statistical surface is eleven point metrics with no interval, standard
error, p-value or trial count [re-opened]. gs-quant's `backtests/` package has no module for
baselines, walk-forward, hold-out, bootstrap, deflated Sharpe or multiple-testing correction, and
nothing records how many configurations were tried; its statistics live one series at a time
outside the backtester (`gs_quant/timeseries/econometrics.py:219-245`) and are not reachable from a
`Strategy` result [record]. ArcticDB's equivalent slot holds airspeed-velocity *performance*
benchmarks (`asv.conf.json`, `python/benchmarks/`, `.github/workflows/benchmark_commits.yml`) —
performance regression control, not statistical control [record]. RD-Agent's loop *does* count its
iterations — `loop_n` as a remaining budget (`rdagent/utils/workflow/loop.py:133-134, 315-324`) and
`idx2loop_id` over the full experiment/feedback history (`rdagent/core/proposal.py:283-284`) — and
corrects nothing by it; a repository-wide search for
`deflated|bonferroni|multiple.?test|p.?value` returns only training-epoch advice in LLM prompts
[one reader].

**What would be lost:** the single most likely way a result from this platform is wrong. Counting
trials is cheap; the number is useless to anyone who did not record it at the time.

### Two engines held to agreeing row for row
`tests/test_event_driven.py:84` — `TestDualEngine::test_row_for_row_identical`: both engines run
the same `Strategy` and share one cost module, and with `max_concurrent=None,
enforce_capital=False` the two trade logs must be equal under `assert_frame_equal`.

optopsy is the proof of what this catches. It has two result paths, and they disagree about when
P&L is realised [re-opened]:

```python
# optopsy/simulator.py:731, 616-617, 741  — the single-strategy path
trades = trades.sort_values("entry_date").reset_index(drop=True)
trade_log["cumulative_pnl"] = trade_log["realized_pnl"].cumsum()
equity_curve = trade_log.set_index("exit_date")["equity"]

# optopsy/simulator.py:879-881  — the portfolio path
# Sort by exit_date (when P&L is realized) for correct cumulative equity,
combined = combined.sort_values(["exit_date", "entry_date", "leg"])
```

The rule is written out in the comment on one path and violated on the other, so the equity value
stamped on a date in the single-strategy path embeds profit from trades that have not closed — and
Sharpe, Sortino, Calmar and max drawdown are all derived from that curve
(`optopsy/simulator.py:470, 503-507`). An equality test between the two paths would have failed on
the first run. Ours did: it caught a real semantic divergence the first time it ran.

**What would be lost:** the vectorised engine stops being an answer to "is there edge" and becomes a
second opinion nobody checks.

### The guard suite, and a result that says what it assumed
`tools/check.py:49-74` holds twenty guards behind one command, plus five in `docs/conventions.md`
that cannot live there because of when they must run — the `git add -A` refusal hook, the pre-commit
`--fast` run, the full check on a clean clone in `.github/workflows/check.yml`, and the per-agent
path restriction. Beside them, `r.manifest()` with `guards_were_green()` in three states, and
`r.report()` refusing to print an option-side number without a `synthetic` label.

ArcticDB is the closest comparison and the contrast is instructive [record]: 30 GitHub Actions
workflows, 166 Python and 114 C++ test files, a dedicated flaky-test summary job — and nothing that
checks a *domain* invariant, because it is a store. Its own documentation concedes the limit: it
**does not check** index sortedness at write time (`error_messages.md:129`) and cannot know whether
a column exists before touching storage (`:141-144`). gs-quant's public CI contains exactly one
workflow and it is a PyPI publish; the real pipeline is `.gitlab-ci.yml` against GS-internal
infrastructure an outside contributor cannot run, and its 112 test files mock one network call
(`gs_quant/test/utils/mock_calc.py:61-64`: `api = GsRiskApi; method = '_exec'`) and replay 155
recorded cassettes, so they verify request construction and cannot verify a price, a greek or a fill
[record]. optopsy's CI is `ruff format --check`, `ruff check`, `ty check`, `pytest`
(`.github/workflows/ci.yml:25-32`) — formatting, linting, typing, tests, and nothing that could fail
a build on a look-ahead regression [re-opened].

**What would be lost:** `check_shipped.py` alone — the guard written because `.gitignore` held
`research/` without a leading slash, so all six modules of `backtest/research/` were never pushed
while their tests were, with a clean `git status` and green local tests. Formatting and typing cannot
see that class of failure, and the only thing that did was a full check on a clean clone. And with
`r.manifest()`, the ability to tell a green run from an unexamined one.

### The front/back contract
`tools/check_wiring.py` against `CONTRACT` in `app/static/js/contract.js`, checked at the depth the
field is read, against a real response. Written for three failures that had already happened: the
server sending `id` while the UI read `run_id` (both `undefined`, so `find()` "matched"); a `tag`
column not passed through, so every group came out empty; a renamed sizing method still sent under
its old name.

Nothing comparable exists in any of the six. The nearest thing is a *mismatch* of exactly this kind
in optopsy, left standing: the Postgres upsert passes
`_DEDUP_COLS = ["quote_date", "expiration", "strike", "option_type", "expiration_type"]`
(`optopsy/data/providers/eodhd.py:511-517`) as `index_elements` against a unique index that includes
`underlying_symbol` (`optopsy/data/providers/pg_store.py:69-78`) [re-opened].

**What would be lost:** the failure mode is silence. A UI that reads a field nobody sends renders an
empty panel, and an empty panel looks like a result.

### An option pricing model that can be read, tested and argued with
`backtest/data/options/pricing.py:134-171` (`implied_vol` returns an explicit absence with a reason
rather than a silent zero) and `backtest/data/options/american.py`.

optopsy has no pricing model at all — its dependency set contains no scipy and no pricing library, a
repository-wide search for `black.scholes|norm.cdf|implied_vol|brentq` returns nothing inside the
package, and consequently delta is a hard input requirement: without a delta column no strategy can
run. Its unknown IV rank is fabricated as the neutral value: `return rank.fillna(0.5)`
(`optopsy/signals/iv.py:79-84`) [re-opened], compared straight against the entry threshold.
gs-quant ships **no option pricing model at all**: `gs_quant/models/` holds `epidemiology.py`,
`risk_model.py` and `risk_model_utils.py`, and the only Black-Scholes expression in the tree is
inside a tutorial notebook written to check the service's number [record]. ArcticDB reads `delta`,
`gamma`, `theta`, `vega` and `implied_volatility` straight from the vendor CSV and derives nothing
— there is no solver, no Black-Scholes and no tree anywhere in the repository [record].

**What would be lost:** the ability to answer an option-side question at all when the vendor column
is missing, and the ability to say *why* a number is what it is. A fabricated 0.5 is a signal firing
on a row whose implied volatility is `NaN`.

### It runs on a laptop with no credentials
gs-quant's first line of README after the title is that a client id and secret are "available to
institutional clients of Goldman Sachs" (`README.md:7`), and all three of its engines price through
the service. For a reader without Marquee access that repository **is documentation** [record].

**What would be lost:** the ability of anyone to check any number in any result.

---

## Do not copy

### Worse than ours

All [re-opened] in optopsy, which is the only direct comparable and therefore the only repository
where "worse" is a meaningful verdict:

- **Commission in dollars subtracted from a P&L in option points.** `optopsy/pricing.py:204-212`
  does `net_pnl = data["total_exit_proceeds"] - data["total_entry_cost"]` — sums of per-share quotes
  — then `net_pnl = net_pnl - data["total_commission"]`, where `_calculate_commission` returns
  dollars (`:173-181`). The ×100 multiplier is applied later, in the simulator
  (`optopsy/simulator.py:607`). An enabled $1.30 round turn is charged as if it were $130.
  `tests/test_commission.py:228-236` asserts only the direction.
- **And then the commission never reaches dollar P&L anyway.** `optopsy/simulator.py:607-617`
  builds `realized_pnl = (exit_proceeds - entry_cost) * lot_size` from prices that were never
  reduced by it; the commission exists only inside a local `net_pnl` used for `pct_change`.
- **The exit rule is stated on one path and violated on the other.** `optopsy/simulator.py:731`
  sorts the single-strategy path by `entry_date`; `:879-881` sorts the portfolio path by
  `exit_date` with the comment "when P&L is realized". See "Two engines held to agreeing row for
  row" above for why this is the entry in this list that most earns its place.
- **A stop-loss both fires and fills at the midpoint**, discarding the configured slippage mode:
  `optopsy/exits.py:462` is `lookup["_new_exit"] = (lookup["bid"] + lookup["ask"]) / 2`, and
  `_calculate_fill_price` is never called in that file.
- **The early-exit scan window ends at the contract's last quote in the loaded file**, not at the
  trade's planned exit. The comment concedes it: "The planned exit is the quote date at exit_dte —
  we can get it from the original result's exit info. But we don't have that here directly.
  Instead, get max quote_date per contract from the data as the 'end' boundary"
  (`optopsy/exits.py:328-339`). Extending the input data forward changes which trades get a stop
  and at what price.
- **An uncomputable statistic is reported as 0.0** (`optopsy/metrics.py:43-51`), so an
  under-powered result is indistinguishable from a flat one.
- **An unknown IV rank is fabricated as 0.5** (`optopsy/signals/iv.py:79-84`).
- **The documentation contradicts the code on the fill assumption** — `'mid'` in
  `docs/parameters.md:318`, `"spread"` in `optopsy/types.py:157`.

From gs-quant [record] — not "worse at being an options backtester", which it is not trying to be,
but worse as a design decision in a shared problem:

- **`MissingDataStrategy.interpolate` — a working look-ahead leak.**
  `gs_quant/backtests/data_sources.py:143-159`: a read of a missing past date inserts NaN, sorts,
  then `self.data_set = self.data_set.interpolate()` — linear interpolation reads the **following**
  observation, and the result is written back into the series the trigger keeps for the rest of the
  run. The sibling `fill_forward` branch is safe. A per-read strategy enum with one leaking member
  is worse than no option at all.
- **A risk-scaled cost that becomes NaN for any future date and is then erased:**
  `if state > dt.date.today(): return np.nan` (`gs_quant/backtests/backtest_objects.py:569-571`)
  followed by `cumsum()` and `.fillna(0)` (`:246-248`). A cost model can stop charging rather than
  raise. Read, not run.
- **Per-trade PnL gross of cost.** `gs_quant/backtests/backtest_objects.py:265-302` computes
  `Trade PnL` from entry and exit model prices with no cost term; cost appears only as a cumulative
  column on the equity curve (`:246-251`). Our `FeeBreakdown` is checked to sum on every trade
  record, and that is the right way round.
- **One word for two axes.** `as_of` is knowledge time on `Dataset.get_data`
  (`gs_quant/data/dataset.py:171-211`) and observation time on `get_data_last`, where it is passed
  as the query's `end` (`:391`, docstring at `:374`). A caller reasoning about look-ahead has to
  know which function they are in.

From ArcticDB [record]:

- **`update()` replacing the whole index range it spans.**
  `python/arcticdb/version_store/library.py:1597-1599`, with their own worked example admitting
  `# Note that 2018-01-02 is gone despite not being in update_df` (`:1669`). Exactly right for
  writing one whole day's chain; on a **partial redelivery** it deletes contracts rather than
  erroring.
- **Shipping an unfiltered vendor sample as the worked example.** Runnable data beside a notebook
  is a good idea; shipping a 63%-zero-volume, p90-spread-2.00 chain and charting it without a word
  teaches the pipeline and mis-teaches the data.

The pattern across most of this list is one failure: **an absence given a plausible value instead
of being refused.** It is the failure our defaults guard, our tradability gate and our
missing-not-dropped rule all exist to prevent, and it is worth re-reading this list the next time
one of those guards looks like friction.

### Right for a different problem

- **nautilus_trader's latency and matching machinery** [one reader]. Separate insert, update and
  delete latencies per command, queued as inflight commands against a nanosecond clock
  (`crates/backtest/src/exchange.rs:968-1000`), over L1/L2/L3 order books. This is the correct model
  for intraday and crypto execution, where the question is whether your order reached the book
  before the other one. Our question is whether a mid-frequency options decision made on T's close
  could have been filled on T+1 — for which a one-day lag is not an approximation of their model but
  a different and better answer. Taking the shape would mean maintaining a sub-second clock for a
  daily chain.
- **ArcticDB's storage layer, entire** [record]. Immutable version chains, a four-layer format, a
  B-tree index, filter and projection pushdown, billions of rows (`README.md:33`; a 20-year history
  of 400,000 securities in one symbol at `README.md:40`). Their own defaults put us two to three
  orders of magnitude below the scale it is built for (`rows_per_segment = 100_000`,
  `python/arcticdb/options.py:47`, against a headline demonstration of 1,000,000,000 rows), and a
  daily chain ingest is their documented **worst case**: one version per underlying per day is
  ~2,520 versions over ten years, and "reading old versions (using the `as_of` argument) can become
  slower as lots of tiny object reads are required"
  (`docs/mkdocs/docs/technical/on_disk_storage.md:37`) — with the promised compaction absent from
  the public `Library` API (private at `cpp/arcticdb/version/version_store_api.cpp:1482`; the three
  documented methods `compact_symbol_list`, `compact_data` and `defragment_symbol_data` all compact
  something else). Query pushdown buys nothing when the frame fits in memory, and their language has
  no row offset, so our lag could not live there even if we moved. Take the *snapshot* idea (entry
  14); do not take the store.
- **ArcticDB's recursive normalizers for a chain object per date** [record]. Expressible and
  unusable: their own notebook states that recursively normalized data cannot be filtered and raises
  `SchemaException`.
- **gs-quant's server-resolved relative strikes** [record].
  `EqOption(expiration_date='3m', strike_price='ATMS+10%')` then `.resolve()` is elegant, and it is
  elegant because a Marquee server holding a vol surface does the work: resolution is an HTTP call
  to `/risk/calculate` (`gs_quant/api/gs/risk.py:89`), available to institutional clients
  (`README.md:7`). The syntax runs to `'25d'`, `'p=10%'` and even `'Price=1000'` — a strike solved
  for a target premium (`gs_quant/skills/gs-quant-overview/instruments.md:12`). This is correct for
  OTC derivatives struck off a curve on the day, where there is no listing to point at. For listed
  options the contract **is** the identity, and a formula that resolves differently per date is the
  thing our chain exists to prevent. The absolute strike it produced is not even kept in the result
  — you ask the server for it as a separate measure (`gs_quant/target/backtests.py:26-44`).
  Adopting the relative-spec idea without a server means writing the solver, which is a different
  project.
- **gs-quant's same-day resolution with no lag, read in its own context** [record]. These are OTC
  trades struck off date *d*'s curve, not an equity order filled at the close that produced the
  signal. The mitigation is real for them and does not transfer to us.
- **gs-quant's order-type vocabulary** [record]. `OrderTWAP`, `OrderMarketOnClose`, `OrderAtMarket`,
  `OrderCost`, `OrderTwapBTIC` (`gs_quant/backtests/order.py:73-219`) belong to intraday equity and
  futures execution. At mid-frequency daily options with a one-day lag, a TWAP window has nothing to
  average, and all five price off the same single `ValuationFixingType.PRICE` with no spread anyway.
- **gs-quant's APEX pre-trade impact model** [record] — spread plus temporary plus permanent impact,
  ADV percentage, participation cap, an urgency parameter (`gs_quant/target/risk.py:59-66, 240-249,
  408-414`). A basket-equity execution problem, reached only through `/risk/execution/pretrade`
  (`gs_quant/api/gs/risk.py:336-338`), and `grep` for it across `gs_quant/backtests/` returns
  nothing: no backtest in that repository is affected by it either.
- **qlib's PIT file format, as a format** [one reader]. The record is
  `(date, period, value, _next)` with `period` an integer like `200704` — quarterly financial
  statements. The *shape* is entry 15 above; the format assumes a small number of restatements per
  field per year, and an options chain is several thousand rows per day.
- **RD-Agent's loop, as it stands** [one reader]. Not because the loop is badly built, but because
  this instance of it judges on its test period — see the next section, which is where that finding
  belongs.

### Cannot be evaluated, because their code was never run

The workflow's rule is that a benchmark is a reading exercise. Everything below is therefore
**unassessed, not assessed and found wanting** — and the distinction matters most where the reading
looks damning.

- **Every optopsy finding in this report is a reading of its source.** No test was run, no example
  executed, no number reproduced. The commission unit error, the entry-ordered equity curve and the
  stop-at-the-mid are all visible in the code and none of them was observed happening. A reading can
  be wrong about reachability: a path that looks live may be unreachable in practice.
- **nautilus_trader's fill behaviour.** Its core is Rust; nothing was compiled. The eight fill
  models were read as declarations. Whether `ProbabilisticFillModel` behaves as its name suggests is
  not established here.
- **gs-quant's prices, greeks, implied volatilities, fills and P&L attribution.** These are not
  merely unrun — they are **absent from the artifact**. They execute on Goldman's servers behind
  institutional OAuth (`README.md:7`; `gs_quant/api/gs/risk.py:89`;
  `gs_quant/api/gs/backtests_xasset/`). Its 112 test files verify request construction and response
  handling against 155 recorded cassettes with the API method patched
  (`gs_quant/test/utils/mock_calc.py:61-64`); they cannot verify a price. Treat every
  numerical-quality question about gs-quant as unanswered [record]. In particular, the size of their
  `PNL_unexplained` — the only thing that would say whether breaking out the higher-order terms
  (entry 8) actually shrinks a residual in practice — is unknowable from the clone.
- **Two gs-quant runtime claims in this report are read-only inferences and are labelled as such**
  [record]: the `execution_quantity` `TypeError` in `get_costs`
  (`gs_quant/backtests/backtest_objects.py:899-904` calls
  `order.execution_quantity(self.data_handler)` while every definition is
  `def execution_quantity(self) -> float` at `gs_quant/backtests/order.py:55, 95, 122, 146, 183`),
  and the NaN-then-zero cost path.
- **gs-quant's listed-versus-OTC distinction** [record] is a two-member enum forwarded to the
  service (`RollDateMode`, `ExpiryDateMode` in
  `gs_quant/api/gs/backtests_xasset/response_datatypes/backtest_datatypes.py:61-78`); its behaviour
  is not in the repository.
- **RD-Agent's loop quality.** Running it needs an LLM API key, a qlib data bundle and hours. What
  *was* established is narrower and sharper, and it is the dimension-8 question the workflow asks:
  **which split does the loop read when it judges a result?** [one reader]

  ```yaml
  # rdagent/scenarios/qlib/experiment/factor_template/conf_combined_factors.yaml:43-45, 77-80
  backtest:
      start_time: {{ test_start | default("2017-01-01", true) }}
  ...
  segments:
      train: [2008-01-01, 2014-12-31]
      valid: [2015-01-01, 2016-12-31]
      test:  [2017-01-01, null]
  ```

  The backtest runs on the **test** segment. Its metrics are exactly the three handed to the model —
  `IMPORTANT_METRICS = ["IC", "1day.excess_return_with_cost.annualized_return", "1day.excess_return_with_cost.max_drawdown"]`
  (`rdagent/scenarios/qlib/developer/feedback.py:17-21`) — and the model returns
  `"Replace Best Result"` and a `"New Hypothesis"` from them (`feedback.py:107-111`), with
  `trace.sota_experiment` then returning the last experiment whose `decision` was true
  (`rdagent/core/proposal.py:310-317`). **So the loop reads its test period to choose its next
  hypothesis, and every reported result is in-sample with respect to the search, however the code is
  arranged.** The loop count is tracked (`rdagent/utils/workflow/loop.py:133-134`) and nothing is
  deflated by it.

  That finding is from reading the files that decide it, which is enough to say it. Whether the loop
  finds anything is not.
- **ArcticDB's compression ratio and read performance at our volume** [record]. No parquet file was
  written and no columnar encoding was measured. The "0.15–0.35 GB parquet" figure in that record is
  an **extrapolation with no file behind it**, carries no conclusion under this project's own rule,
  and is not used anywhere in this report. What *was* measured from the clone holds exactly: 803,920
  bytes for 6,792 data rows over 19 columns = 118 bytes/row as CSV text, `gzip -9` = 29.4 bytes/row,
  AAPL = 2,370 contract-rows on one date. The LZ4-codec argument rests on ArcticDB's own call sites;
  the library itself was not read, because all seven declared submodules are empty in the clone.

---

## What the comparison could not establish

No `could_not_determine` entry reached either writer, for any repository — not because the analysts
answered everything, but because `.claude/workflows/benchmark.js` drops the field between stages
(see the faults at the top). What follows is therefore assembled from what is demonstrably missing
from this run, not from the analysts' own account of their limits. That is a weaker list than the one
the script intended, and the gap should be closed in the script before the next run.

1. **Four of eight dimensions for optopsy, five of eight for gs-quant, four for ArcticDB, and all
   eight for nautilus_trader, qlib and RD-Agent arrived with no analyst claim at all.** No claim
   arrived for extensibility, usability, licence-and-health or AI-integration on optopsy; none for
   gs-quant's dimensions 4–8; none for ArcticDB's 4, 5, 6 and 8; and none at all for
   nautilus_trader, qlib or RD-Agent. Those dimensions were answered by one agent reading a clone,
   and are marked [one reader] above. *What it would take:* re-run Analyse and Cross-check with the
   session limit lifted, and fix the payload truncation so `checked` survives.
2. **No claim in this report was verified by an independent agent.** Eighty were re-opened by a
   report writer; fifty-nine were not verified at all and carry no conclusion here. **No dimension
   here should be treated as twice-verified.** *What it would take:* the Cross-check stage running to
   completion — and, for any comparative claim, a verifier that reads *our* file too.
3. **Health — cadence, contributors, open issues, release rhythm — for all six.** Every clone is
   `--depth 1` and the workflow makes no network call. gs-quant's single commit is
   `8725ddf Chore: Make release 2.2.5`, with the version computed by `versioneer` rather than written
   down; ArcticDB's HEAD is `b14a8f0` dated 2026-11-06 (PR #3492), which is one data point and not a
   cadence. The substitutes used — test-file counts, 30 workflows for ArcticDB against one for
   gs-quant, a same-day HEAD — are weaker and are labelled in each record. **"Worth learning from" is
   established; "worth depending on" is unmeasured for all six.** *What it would take:* a non-shallow
   clone, or the GitHub API for commits, releases, issues and contributors.
4. **Any runtime behaviour, anywhere.** By rule. The four places this bites hardest:
   nautilus_trader's fill models (Rust, uncompiled), gs-quant's numerical quality (server-side,
   absent from the artifact), RD-Agent's loop (needs keys and hours), and ArcticDB's compression and
   read performance at our volume. *What it would take:* a sandbox outside this tree with an explicit
   decision to execute third-party code — which is a different exercise with a different risk, and
   should be proposed as one rather than smuggled into a benchmark.
5. **Whether qlib's own backtest is as careful as its data layer.** Only its PIT mechanism, its
   processor fit windows and its portfolio record were read. The execution model, the fill rule and
   the cost treatment in `qlib/backtest/` were not. *What it would take:* an afternoon of reading, on
   the dimension that matters most to us.
6. **Whether optopsy's defects are reachable in a default run.** Every one is read from source.
   *What it would take:* either executing it — refused — or tracing each path to a public entry point
   by reading, which was done for none of them.
7. **Whether the hosted Marquee backtest service offers validation facilities the client does not
   expose.** *What it would take:* the Marquee product documentation, which is not in the repository.
   From the clone, the client offers none.
8. **How gs-quant's backtests would handle splits and dividends.** Not answerable: no corporate-action
   concept exists in `gs_quant/backtests/`, and adjustment is vendor-side. The wire protocol has
   `adjust_as_of`, `id_as_of_date` and `use_temporal_x_ref` (`gs_quant/target/data.py:501-511`) and
   **nothing in the repository sets any of them**. The adjustment vocabulary exists as dataset fields
   only (`gs_quant/data/fields.py:42-49`).
9. **Whether ArcticDB's licence conversion table still reads as this report says.** *What it would
   take:* re-reading `README.md:152-196` at the time of use. The table is maintained per release, and
   5.1 converts on **2026-11-15**, nine days after this reading.

---

## Licences, read from each repository's own licence file

"Worth learning from" and "safe to borrow code from" are different questions. These are the answers
to the second.

| Repository | Licence file | Licence | Borrowing code |
|:---|:---|:---|:---|
| optopsy | `LICENSE:1-2` | **GNU Affero General Public License v3** | No. AGPL-3.0 is copyleft with a network clause; borrowing any of it would place this platform under AGPL. Read it, cite it, reimplement from the idea. |
| nautilus_trader | `LICENSE:1-2` | **GNU Lesser General Public License v3** | Linking is permitted; copying source into our modules is not, without taking LGPL obligations on the result. The `FillModel` *shape* is an idea, not code. |
| qlib | `LICENSE:1-3` | **MIT** (Copyright Microsoft Corporation) | Yes, with the notice retained. |
| RD-Agent | `LICENSE:1-3` | **MIT** (Copyright Microsoft Corporation) | Yes, with the notice retained. |
| gs-quant | `LICENSE:2-3` ("Apache License / Version 2.0, January 2004"); `NOTICE.txt` adds third-party Apache-2.0 code, and every source file carries the header (e.g. `gs_quant/backtests/backtest_engine.py:1-15`) | **Apache License 2.0** | Yes, with attribution and notice. |
| ArcticDB | `LICENSE.txt:1` (Licensor "Man Group Operations Limited" at `:3`; Change License "Apache License, version 2.0" at `:25`) | **Business Source License 1.1** | Not from the current tree. Conversion is per version, two years from that version's release (`:17-23`), capped at four years (`:33`); every release up to 5.0 has converted, master has not. Its own README is stricter than the licence — "Use of ArcticDB in production … requires a paid for license" (`README.md:26`, and `:142-143`) — and `docs/mkdocs/docs/licensing.md:9` goes further still, naming research and dev environments. Borrow only from a converted tag, and re-read the version table first. |

This platform has **no LICENSE file of its own**, as of this date. That is a separate decision and it
is not this document's to make, but it is worth noting here, since two of the six repositories above
would require one before any of their code could be used in a distributed artifact.
