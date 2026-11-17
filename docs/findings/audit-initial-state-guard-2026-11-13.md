# Audit of the starting-state guard, before it was committed

**This is an audit of a change to the engine, not a research result.** No number here says
what a strategy earns. It says what `quant-auditor` and `reviewer` found in the change that
makes a changed `initial_state` require a version bump, what was decided about each finding,
and by whom.

**Date:** 2026-11-13 · **Branch:** `engine/gaps`, item 2c, on top of `0792581`
**Audited:** the uncommitted diff — `backtest/core/component.py`,
`tests/test_component_state.py`, and `tools/check_mutations.py` (the mutation entry, added by
the main session because neither builder may edit `tools/`)
**Verdicts:** `quant-auditor` **CHANGES REQUIRED** · `reviewer` **CHANGES REQUIRED**
(no blocker from either)

The two agents ran in parallel on the same diff and did not see each other's output. They
reached the same conclusion about `params`/`describes` by different routes, and each corrected
a different claim the builder had made.

---

## The headline: the guard buys less than its own docstring claimed

`register`'s re-registration guard compared only `decide`, so a changed `initial_state` at an
unchanged version was accepted silently and two different rules could share one `run_id`. The
change adds that comparison. **It works, and it does not reach the case that motivated it.**

`quant-auditor` found that the comparison only happens when
`existing = _REGISTRY.get(key)` is not `None` — that is, when a name is registered **twice in
one process.** There are 11 `register(...)` call sites in the tree and no `importlib.reload`
anywhere. So in the ordinary workflow — edit `_trailing_initial`, re-run a fresh process —
`existing is None`, the comparison never runs, and **no version bump is forced.**

The builder's docstring had stated the opposite about the real case: that the hand-made bump to
version 2 on `fix/engine-state` was unforced, implying this change would have forced it. It
would not have.

**Why it matters, traced rather than asserted.** `initial_state` reaches the fingerprint by
exactly one route — `version`, inside `ref` (`backtest/api/sdk.py:864-867`). Nothing in `api/`,
`run_id.py` or the provenance reads `initial_state` itself. The fingerprint's other defence,
`"code": code_version()` (`backtest/core/run_id.py:38-56`), is `commit+dirty` — identical for
every working-tree state at one commit. So two runs whose starting states differ still produce
**one `run_id` and one `ref`**, and the trade log has no component column to tell them apart.
On the event-driven baseline that difference is worth 12 of 228 trades.

**Decided — by the main session, not by either agent:** narrow the claim to the truth and spec
the rest. The docstring now says what the guard is (a collision guard for a name registered
twice in one process — a reload, a notebook, two importing modules), says plainly that it does
not force a bump across separate processes, and names `code_version()`'s `commit+dirty` as the
reason. The real fix — carrying the starting state into the fingerprint — is
`docs/specs/the-starting-state-reaches-the-fingerprint.md`, **not built**, because changing
what composes `run_id` alters the fingerprint for every component run and `CLAUDE.md` reserves
an architecture-adjacent decision for a person.

---

## The live defect the audit demonstrated

The builder had argued that a closure-captured value being invisible is "the pre-existing reach
of this guard, same as `decide`". `quant-auditor` showed that is **true of the mechanism and
false of the exposure**, and the asymmetry is the whole point:

- `decide`'s intended route for variation is `params`, and `params` **is** fingerprinted
  (`sdk.py:866`).
- `initial_state` has no such route. `Bound.state_for` (`component.py:235`) calls
  `self.variant.initial_state(ctx)` **with no parameters**, and the shipped ratchet says so
  itself (`backtest/signals/stop_loss/__init__.py:154-156`: "The initial-state hook cannot see
  the parameters").

So **a closure or a module global is the only way to parameterise a starting state**, and both
are invisible to a source or bytecode comparison.

Demonstrated rather than argued: two variants built from a factory with `mark=0.0` and
`mark=0.5`, both registered at version 1, produced **no refusal**, and `register` returned the
existing variant — so the engine keeps the old mark while the code and the configuration both
say the new one. **A researcher sweeping a starting high-water offset that way gets N runs that
all report the first run's rule, under one `ref`.**

`__defaults__` is invisible for the same reason: `lambda ctx, mark=0.0:` → `mark=0.5` compares
equal while returning `{"high_water": 0.0}` versus `{"high_water": 0.5}`.

**Decided:** close both in the conservative direction — a non-empty `co_freevars` is treated as
"cannot tell" and **raises**, as a sourceless callable already does, and `__defaults__` /
`__kwdefaults__` join the comparison. Refusing is right here: the alternative is silently
keeping the old rule. The **module-global** case cannot be closed by comparing source or
bytecode — it compares equal compiled, and `getsource` of a lambda returns only its own line,
so the constant's definition is never in the compared text — and is recorded as reach rather
than fixed.

---

## What was verified rather than taken on trust

| Claim | Result |
|---|---|
| Both baselines unmoved | **verified twice.** The main session hashed both; `quant-auditor` re-ran `compare()`, which checks every row of `COMPARED` rather than counts, and got `event_driven: unchanged / trade_table: unchanged` |
| The refusal message for a changed `decide` is byte-identical | **verified three times, two ways.** The main session rendered the exception and compared to the literal (the source now uses `' and '.join(changed)`, so reading the source would have been the wrong test); `quant-auditor` reproduced it under the new code and got `BYTE IDENTICAL: True`; `reviewer` reconstructed both f-strings |
| The refactor preserves `decide`'s behaviour | **verified.** The old `same_code` short-circuited on `version ==`, so all three branches are reproduced exactly: equal + same → return existing; equal + differs → raise; differs → overwrite |
| `stateful` is not a third uncompared half | **verified by both.** `component.py:299-313` refuses `stateful` without `initial_state` and the reverse, so the flag is derivable from the `None`-ness already compared |
| The tests fail for the right reason | **verified by analysis.** Under the old code `_decide` is shared by every probe, so `same_code` is True and the four raising tests cannot raise — 4 fail / 5 pass, and the 5 that passed were deliberately the ones that must keep passing |
| The mutation entry kills its mutant | **verified by the main session.** `caught by tests/test_component_state.py::TestAChangedStartingStateNeedsAVersion`. All three entries added across this branch kill their mutants; 100 entries, every `what` unique |

## The findings below blocker level

| Where | What | Decided |
|---|---|---|
| `tests/test_component_state.py` | The probe's registry key is written twice — once as `_PROBE`, again as `slot=`/`name=` literals in four places. If they drift the fixture stops cleaning and the probe **leaks into every later test with no test failing**: `test_variants.py:62` asserts a subset, and the unknown-variant test loops over whatever is registered. The fixture is also not `autouse` | Derive the key from the variant, and make the fixture `autouse` on the class |
| `tools/check_mutations.py` | The main session's new entry was inserted **between an existing comment and the entry that comment documents**, so hold-out reasoning read as a comment on a component guard | Fixed by the main session — the entry moved below the hold-out entry. Nothing checks comment ownership, so this would have failed silently |
| `component.py` `_STARTING_STATE_NOTE` | Restates "12 of the 228 trades" inside a user-facing error, so the figure now appears four times. A permitted baseline update makes the refusal assert a stale number | Cite the finding without restating the figure, and add that 12/228 is the **synthetic fixture's** magnitude, not a general one |
| `tests/test_component_state.py` | `ctx.get("premium_ratio", 0.0)` — absence read as a value, in the file a reader copies from | Use `ctx["premium_ratio"]` |
| `component.py` `("opaque", id(fn))` | Sound where it is called (both operands alive), but lifetime-dependent — two distinct temporaries compared equal after CPython reused a freed address. It is also the one place the guard **refuses correct code**: an unchanged re-import of a `functools.partial` compares unequal, which is the version-inflation failure the docstring itself argues against | Comment the lifetime constraint; special-case a partial |
| `component.py` `co_consts` | Conflates types: `0` vs `0.0`, `False` vs `0`, `True` vs `1` compare equal. Latent — the shipped forms take the source path | Recorded |
| `component.py` source path | Docstring-sensitive, so a comment-only edit to `_trailing_initial`'s 20-line docstring demands a version bump on a reload. Same kind as `decide`'s existing behaviour | Recorded |

## One claim of the builder's that both agents corrected

The builder described `params`/`describes` being uncompared as "the same class of defect as the
one just closed". **It is not**, and the wrong framing would have misled whoever picked it up.
`sdk.py:864-867` hashes `{"ref": c.ref, "params": dict(c.params)}` where `c.params` is
`bind()`'s **validated output**, so a changed `Param.lo/hi` cannot give two different results
one `run_id` — the value newly admitted by the wider range is itself in the hash. A renamed
parameter fails loudly in `bind()`. `describes` is prose and is not in the fingerprint at all.

What really changes silently is **the legal configuration set under one unchanged `ref`** — a
real defect, and a different and much less dangerous one. One sharper point from
`quant-auditor`: `describes` is the field that was **right while the trailing stop's code was
wrong**, so two results under one `ref` can carry different descriptions of the rule.

## Checks, as run

**Passed:** timing (nothing in the diff sits in the decide or fill path; `register` runs at
import) · look-ahead (no data read, no `shift`, no series indexing added) · realism assumptions
never optimised (no λ, latency or cleaning rule; nothing enters a sweep) · the statistical unit
(the cited finding counts by `trade_id`, and the message quotes 12 trades, not 23 rows) ·
absence (`("stateless",)` is a named sentinel and no path's tuple can collide with another's) ·
no threshold or realism literal in the new code · no hidden default · test labels
(`@kind("guard")`, `req("0.2.registry")`, whose recorded failure mode is literally "two
different forms resolving to the same reference") · non-English identifiers ·
`tests/test_component_state.py` + `tests/test_variants.py` 63 passed · `tools.check --fast`
18/18 including the baseline guard.

**Failed:** the scope check, in the accounting sense only — all three files belong to item 2c,
but the brief named two, so the commit message must name all three and say the mutation entry
was added with the mechanism rather than hand-applied.

**Not run:** the full `tools.check` slow guards, by both agents, because a `quant-auditor` and
a `reviewer` were reading the same files concurrently and `run_one` rewrites
`backtest/core/component.py` in place for about two minutes — a mutated read would have
corrupted the other's audit. The main session ran the mutation entry separately, and runs the
full check on a quiet machine before committing.

## What the fixes landed, verified after the audit

This section was added after the findings above were answered, so that the report describes
the code as it was committed rather than as it was audited.

**The closure refusal is live, and was demonstrated firing** rather than asserted. The check
on `co_freevars` runs **before** the source path, which matters: two factory-built lambdas have
**identical source**, so a source-first order would have missed exactly the case this closes.
Two closures over the same value are refused as well — the conservative direction, pinned by
its own test. Before the fix the same script printed no refusal at all and the registry kept
`{"high_water": 0.0}` while the code said `0.5`.

**The guard no longer refuses correct code.** A `functools.partial` is now compared by what it
bound, recursing into `fn.func`, so an unchanged re-import of `partial(base, mark=0.0)` is
**allowed**. That was the one place the guard refused correct code, and it fell on precisely
the parameterisable shape — a refusal there would have pushed a researcher into bumping the
version to silence it, which breaks replay (`component.py` refuses a configuration asking for
a version that no longer exists) and makes the version stop meaning "the rule changed". It has
a both-directions test.

**`0`, `0.0` and `False` are three starting states now**, through a type-tagging helper, where
`co_consts` equality had conflated them.

**The claim was narrowed rather than the code oversold.** `_what_changed`'s docstring now
states outright that the comparison runs only where a name is registered twice in one process,
that the ordinary edit-then-rerun workflow forces no bump — **including the hand-made bump to
`trailing@2`** — why nothing else catches it, and where the real fix would go. The test class
docstring carries the same paragraph, so the tests are not read as establishing more than they
do. It is a **collision guard, not an edit guard**, and it now says so.

**Five more tests, written first and failing first:** three `DID NOT RAISE` (closure-
parameterised, changed default argument, int-versus-float mark), and the `partial` case failing
in the opposite direction — a `StartupError` on an **unchanged** re-import, which is the false
refusal. Fourteen in the class after.

**Re-verified after the fixes:** the full suite (1,595 passed) · `tools.baseline check` clean
on both configurations · both digests unchanged, `ba4b3702…1222064` and `3ce65dae…0f3289` ·
`tools.check --fast` 18/18 · the mutation entry run in reverse against the final tree, where 9
of the 14 tests fail under the mutation and 14 pass restored.

One consequence of the fixes reached this document's own siblings: the figure "12 of 228" was
removed from the error message, the test docstring and the mutation comment, each of which now
cites `docs/findings/baseline-second-configuration-2026-11-08.md` without restating it — so a
permitted baseline update cannot leave any of them asserting a number that is no longer true.

---

## What it changes

The in-process half is closed and the silent-wrong-rule path for a closure-parameterised
starting state is closed by refusing it. **What is not closed, and is now written down in two
places rather than implied:** across separate processes nothing forces a version bump, because
`code_version()` cannot tell two dirty working trees apart. Until
`docs/specs/the-starting-state-reaches-the-fingerprint.md` is approved and built, a changed
starting state between two fresh runs at one commit still produces one `run_id`.
