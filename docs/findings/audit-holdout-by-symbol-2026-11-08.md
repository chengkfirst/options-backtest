# Audit of the hold-out by symbol, before it was committed

**This is an audit of a change to the engine, not a research result.** No number here says
what a strategy earns. It says what `quant-auditor` and `reviewer` found in the change that
adds a hold-out by symbol, what was decided about each finding, and by whom — which is what
a person needs in order to approve, or refuse, the mechanism that now carries
`docs/decisions/0001-the-hold-out-part-b-and-the-option-data.md`.

**Date:** 2026-11-08 · **Branch:** `engine/gaps`, cut from `main` at `c4f003a`
**Audited:** the uncommitted diff of item 2a — `backtest/research/holdout.py`,
`backtest/api/sdk.py`, `tests/test_holdout.py`
**Verdicts:** `quant-auditor` **BLOCKED** · `reviewer` **CHANGES REQUIRED** (no blocker)
**State of the fixes at the time of writing:** every finding below was answered before item
2a was committed, and the sections below are written in the past tense for that reason. The
answers were verified in this order: the three affected test files (106 passed, 18 of them
red before the fixes), the full suite, both mutation entries run in reverse, and the full
`tools.check`. Two of the fixes could not be applied by `engine-dev` at all -- see the next
section, which is the most transferable thing this audit produced.

The two agents ran in parallel on the same diff and did not see each other's output. They
found finding 4 below independently of one another, which is the only overlap.

---

## The thing this audit found that was not about the code

**`backtest/research/` is owned by no agent.** `engine-dev`'s hook is registered as
`--allow backtest --allow tests --block backtest/research`, and no other agent's allow-list
covers it: `research-analyst` owns `docs/findings`, `docs/specs` and `research/`, and
`research-lead` owns `docs/research`, `docs/specs` and `docs/ideas`. So the eight findings
living in `backtest/research/holdout.py` and `backtest/research/prelaunch.py` could not be
fixed by the agent that wrote them, and `engine-dev` was right to refuse to route around the
hook with `Bash` -- `CLAUDE.md` names that path explicitly and calls it not an obstacle to
route around.

What happened instead is the behaviour the architecture should want: `engine-dev` landed what
it owned (`backtest/api/config_file.py`, and the failing-first tests for all ten findings),
and produced the rest as a patch, verified by copying the tree to a sandbox, applying it
there, and running the suite and the guards against it. The main session applied the patch
and re-verified. **Nothing was edited outside its owner.**

This is a gap in the agent roster rather than in this change, and it will recur on the next
change to `backtest/research/`. It is recorded in the handoff as still open. The two
candidate answers -- give `engine-dev` `backtest/research/` as well, or give the research
modules an owner of their own -- are an architecture decision, which `CLAUDE.md` reserves for
a person.

---

## Why this change was audited at all

`HoldOut` took a date range and nothing else, so the sentence "SPY is reserved" in decision
0001 was held by that document and nothing else — the exact kind of rule
`backtest/research/holdout.py`'s own docstring says does not survive: "a rule that rests on
remembering is a rule that holds until the week it matters."

So the change moves a research-integrity rule from prose into code. The question put to the
auditor was therefore not about arithmetic but about the record: **after this change, can a
result be produced on reserved data while the provenance still reads as clean?**

The answer was **yes, by two doors**, both now closed.

---

## The two blockers

### 1 · An empty reservation collection refused nothing and claimed one

`backtest/api/sdk.py` did `tuple(hold_out_symbols or ())` and `holdout.py` did
`if not reservations: return {"symbols": None, ...}`. So `hold_out_symbols=[]` reserved
nothing, refused nothing, and wrote provenance **identical to omitting the parameter**.

The auditor ran it rather than reasoning about it. A full backtest on the reserved
underlying completed, and the stored provenance asserted:

```
run(symbol="SPY", ..., hold_out_symbols=[])
-> {'symbols': None, 'symbol_touched': False,
    'symbol_note': 'no symbol was reserved, ...'}
```

**Why it would have mattered.** The realistic route is not a literal `[]`. It is
`hold_out_symbols=[s for s in reserved if ...]` that filtered to empty, or a configuration
key read as absent. The module **already refuses** `ReservedSymbol("   ")` for precisely
this reason — "it reads in a configuration as something held back, and silently reserves
nothing" — and did not apply the same rule one level up, to the collection.

This project had already written the general form of the rule elsewhere:
`backtest/engine/runner.py:193-197`, "an empty batch is almost always a list comprehension
that filtered everything out", which raises.

**Decided:** distinguish "not passed" (`None`) from "passed and empty", and raise
`StartupError` on the second. `symbols: None` versus `symbols: []` in the provenance is not
sufficient, because nothing downstream reads the difference.

### 2 · The configuration file round trip re-ran reserved data with clean provenance

`backtest/api/config_file.py`'s `to_mapping` writes `strategy`, `data` and `run`, and
**neither `hold_out` nor `hold_out_symbols` appeared in `DATA_KEYS`, `RUN_KEYS`,
`STRATEGY_KEYS` or `NOT_WRITABLE`.** The reservation was dropped in silence.

The sequence that matters: the final SPY evaluation is run once with
`unlock="final evaluation"`; `result.save_config(p)` writes a file that reproduces the run
with **no reservation and no unlock reason**; `run_from(p)` then re-runs the reserved
underlying with no refusal, and its provenance reads `symbols: None, symbol_touched: False`.

**Why it would have mattered.** The second, third and fourth use of a hold-out would be
invisible, and making them visible is the entire purpose of `touches()` and of both refusal
messages. A hold-out touched once is the protocol working; touched five times is a
validation period that has quietly become a training one, and this path erased the
difference.

**This is pre-existing for the period half**, and is not a defect this change introduced. It
became blocking here because this change is the one that moves a reservation from a document
into code, and the configuration file is the documented way a settled strategy is re-run.

**Decided, and this was the main session's call rather than either agent's:** add both
halves to `NOT_WRITABLE`, so the drop **refuses** instead of happening. `to_mapping` is
already built to refuse what it cannot carry — `NOT_WRITABLE`, the `<callable>` select,
`_plain` refusing an unknown type — each with the stated reason that "the file would then
look complete and reproduce nothing". Refusing is that existing design applied consistently.
Carrying both halves in the file would be a new file schema; it is a feature rather than a
repair, and it is recorded as a candidate for a later spec instead.

---

## The findings below blocker level

| # | Where | What | Decided |
|---|---|---|---|
| 3 | `holdout.py` `touches()` | Reported **every** reservation as spent, not the one that matched. With SPY and IWM reserved and a SPY run unlocked, `touches` returned `reserved: "IWM, SPY"` — IWM was never run | Return `symbol_spent` as a structured field; keep `symbols` as the declaration. The matched symbol was reachable only inside prose, and `docs/conventions.md` forbids facts living there |
| 4 | `holdout.py`, `sdk.py` | **One `unlock=` spent both halves.** Under decision 0001's own configuration an unlock given for the period also spent SPY, with no refusal naming the symbol and a recorded reason about dates. Two comments asserted the opposite — "unexpressible", "spent separately" | Add a separate `unlock_symbols=`, making the code true rather than the comments weaker. **Found independently by both agents** |
| 5 | `holdout.py` | A provenance note told a reader their QQQ result was "out of sample in the underlying" — false, and false in the flattering direction | Name the subject explicitly: SPY remains out of sample, this result does not |
| 6 | `prelaunch.py` `_out_of_sample` | Asserted "no hold-out was reserved" to a researcher who had reserved SPY and respected it. No path to a false `PASS`, so not a blocker | Read both halves. The danger was indirect and real: a researcher reading it would add a period hold-out and unlock it to turn the criterion green, **spending a second reservation to satisfy a report about the first** |
| 7 | `app/`, and `touches()` having no caller | The interface offers SPY as a selectable symbol, passes no reservation, and reads none of the three new provenance fields. `touches()` has no caller outside tests, while both refusal messages promise a count | Engine side: correct the module's "required" claim, which was untrue. Interface side: `docs/specs/the-interface-reads-the-symbol-hold-out.md`, written because the engine may not edit `app/` |
| 8 | `tools/check_mutations.py` | **No entry for the new symbol refusal.** The guard's own rule: "Entries are added when a mechanism is built, not afterwards" | Added by the main session, since neither builder may edit `tools/`. It is the one line whose silent removal produces a result on reserved data with everything green |
| 9 | `sdk.py` `check_data` | Loads and reports on the reserved underlying's data with no hold-out check. Low severity — data health, not performance | Recorded, not fixed. Pre-existing and outside this diff |
| 10 | `holdout.py` | One unannotated parameter; plain strings in `reserved` died with `AttributeError` rather than a reason | Annotated and refused with a reason |

Also fixed, from the reviewer: the normalisation rule was written in **three** places
(`__post_init__`, `matches()`, and again for the note text) — one fact in three places, whose
drift fails silently in the worst direction, since changing one makes a reservation stop
matching. Extracted to a single `_canonical`.

---

## What was verified rather than taken on trust

The builder claimed the period half was "moved without an edit". That claim carries every
existing result's provenance semantics, so it was checked three times independently — by the
main session before either agent ran, and by both agents:

- **`HoldOut` the class: byte-identical** to `c4f003a`.
- **The period refusal block: byte-identical**, 21 lines, extracted from
  `git show c4f003a:backtest/research/holdout.py` and from the working tree and diffed.
- One qualification the auditor added: the `hold_out is None` branch gained a preceding
  branch, but it is unreachable without a symbol reserved, so **every existing caller's note
  string is byte-identical**.

The auditor also verified the reverse direction on the claim that the conjunction is
unexpressible — no object carries both a symbol and a date — and cleared the bypass paths
that would have mattered: `sweep`, `ablate`, `delay_test`, `realism_test`, `compare_forms`
and `baselines` all reuse `self.raw.config`, so the symbol cannot change inside a `Result`,
and no checkpoint-resume entry point exists outside `run()`.

The reviewer reverse-verified the new refusal by forcing `ReservedSymbol.matches` to return
`False` in memory: **11 of the 17 new cases fail**, and the 6 that stay green are the ones
that legitimately do not exercise matching.

## Checks, as run

**Passed:** timing (nothing in the diff touches signals, selection, fills or the daily loop)
· look-ahead (`holdout_check` is called before `MarketData` is constructed, so a refusal
happens before any reserved data is read) · realism assumptions never optimised (no λ,
latency or cleaning rule in the diff; `SWEEPABLE` unchanged) · no threshold or realism
literal in the new code · symbol normalisation cannot be bypassed by case or whitespace ·
scope, every file belongs to item 2a and nothing from 2b or 2c leaked in · the existing
mutation anchor still occurs exactly once, and that entry run in isolation still reports
`caught by tests/test_holdout.py::TestTheRefusal` · `tests/test_holdout.py` 42 passed · the
full suite 1,541 passed · `tools.check --fast` 18/18.

**Failed:** absence read as a value — blockers 1 and 2. Bypass paths to a scorable number —
the configuration file round trip, the interface path, and `check_data`.

**Not run:** the statistical-unit check, because nothing in the diff counts trades, rolls or
conditional probabilities — there was nothing to measure. The full `tools.check` including
the mutation suite and the doc examples, by the auditor: it ran the one mutation entry this
diff could have broken instead. Synthetic-chain dependence: not applicable, the diff reads
no chain, quote or greek.

## What it changes

The mechanism is sound and the two doors are closed, but **the reservation is still not
enforced on the path a person actually uses.** The interface offers SPY, passes no
reservation, and reads none of the new fields; `touches()` has no caller. Until
`docs/specs/the-interface-reads-the-symbol-hold-out.md` is approved and built, "SPY is
reserved" is enforced for callers that repeat the declaration and for nobody else — which is
better than a document alone, and is not yet the guard decision 0001 assumed it would get.
