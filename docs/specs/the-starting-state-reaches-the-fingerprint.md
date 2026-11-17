# A form's starting state reaches the run fingerprint

**Status:** awaiting owner approval
**Approved:** not yet
**Raised by:** `quant-auditor`, on `engine/gaps` item 2c, 2026-11-13. **The finding is not about
the change it was found in** — item 2c closed the in-process half and this is the half it
cannot reach.

## Goal

`backtest/core/component.py`'s `register` now refuses a changed `initial_state` at an
unchanged version, as it already did for `decide`. **That guard only fires when a name is
registered twice in one process.** There are 11 `register(...)` call sites in the tree and no
`importlib.reload` anywhere, so in the ordinary workflow — edit `_trailing_initial`, re-run a
fresh process — `existing is None`, the comparison never runs, and no version bump is forced.

`initial_state` reaches the fingerprint by exactly one route: `version`, inside `ref`
(`backtest/api/sdk.py:864-867` hashes `{"ref": c.ref, "params": dict(c.params)}`). Nothing in
`api/`, `run_id.py` or the provenance reads `initial_state` itself. The fingerprint's other
defence is `"code": code_version()` (`backtest/core/run_id.py:38-56`), which is
`commit+dirty` — **identical for every working-tree state at one commit.**

So **two runs whose starting states differ can still produce one `run_id` and one `ref`**, and
the trade log has no component column to tell them apart. On the event-driven baseline
configuration that difference is worth **12 of 228 trades**
(`docs/findings/baseline-second-configuration-2026-11-08.md`) — measured on the synthetic
fixture, so that magnitude is the fixture's and not a general one.

Per `run_id.py:130-137` the consequence is the one this project most wants to avoid: the
search looks smaller than it was, and a deflated result reads better than it earned.

**The asymmetry that makes this worth fixing rather than documenting.** `decide`'s intended
route for variation is `params`, and `params` **is** fingerprinted. `initial_state` has no such
route — `Bound.state_for` (`component.py:235`) calls `self.variant.initial_state(ctx)` with no
parameters, and the shipped ratchet says so itself at
`backtest/signals/stop_loss/__init__.py:154-156` ("The initial-state hook cannot see the
parameters"). So a closure or a module global is the **only** way to parameterise a starting
state, and both are invisible to a source or bytecode comparison.

Demonstrated by the auditor rather than argued: two variants built from a factory with
`mark=0.0` and `mark=0.5`, both registered at version 1, produced **no refusal**, and
`register` returned the existing variant — so the engine keeps the old mark while the code and
the configuration both say the new one. A researcher sweeping a starting high-water offset
that way gets N runs that **all report the first run's rule, under one `ref`.**

## Non-goals

- **Not a change to any form's rule.** No recorded number may move. That is acceptance
  criterion 2.
- **Not a redesign of `run_id`.** One field is added to what the `components` entry carries.
  The hash function, the `code_version()` half and the provenance shape stay as they are.
- **Not a fix for the module-global case.** `lambda ctx: {"high_water": MARK}` with `MARK`
  changing cannot be closed by comparing source or bytecode: it compares equal compiled, and
  `getsource` of a lambda returns only its own line, so the constant's definition is never in
  the compared text. It belongs in the recorded reach, and an attempt to close it here would
  be a second, much larger change.
- **Not `params` or `describes`.** Both are still uncompared by `register`, and both are
  separate and smaller: chosen parameter values already reach the fingerprint post-validation,
  and `describes` is prose. Recorded in the handoff, not here.

## The decision this spec turns on, which is the owner's

**Adding anything to the `components` entry changes `run_id` for every run that uses a
component.** Every stored run's fingerprint stops matching a re-run of the same
configuration — not because the result changed, but because the fingerprint's composition did.

Two ways to take that, and they are not equivalent:

1. **Accept the break.** Simpler, honest, and the fingerprint then means what it says. Every
   stored component run's `run_id` becomes unreproducible, and `out/baseline/manifest.json`
   carries no component today, so the measurable blast radius may be zero — **that must be
   checked rather than assumed.**
2. **Version the fingerprint.** Carry a fingerprint-version field so old runs stay
   reproducible under the old composition. Correct, and more machinery than the problem may
   deserve.

`docs/conventions.md` makes a change to the fingerprint's composition an architecture-adjacent
decision, and `CLAUDE.md` reserves those for a person. **This spec does not choose.**

## Acceptance criteria, each falsifiable

1. **Two runs differing only in a form's starting state carry different `run_id`s.**
   **Test:** two variants from a factory with different marks, each run through `run()`,
   asserting the `run_id`s differ. **Falsified by:** equal `run_id`s — today's behaviour.

2. **No recorded number moves.** Both baselines compare clean and stay byte-identical:
   `tests/baselines/baseline_trades.csv` at `sha256 ba4b37021fcd22910dfff02c5307d0c719a016b51a639118a239d01ea1222064`
   and `baseline_event_driven_trades.csv` at `sha256 3ce65dae7e33baabb4a8bf198fe551d5e1e6d1891de3de50da3371c6070f3289`.
   **Test:** `python -m tools.baseline check`. **Falsified by:** either moving.

3. **The componentless path is unchanged.** A run with no components has the same `run_id` as
   before this change. **Test:** a stored componentless fingerprint, asserted equal.
   **Falsified by:** any difference — that would mean the change reached runs it has no
   business touching.

4. **The blast radius is measured, not assumed.** A written count of stored runs whose
   `run_id` stops reproducing, read from the store rather than reasoned about.
   **Falsified by:** the claim being made without the count.

5. **The guard and the fingerprint agree.** Whatever `_starting_state` can distinguish is what
   the fingerprint distinguishes. Any shape the guard cannot see — the module global above — is
   named in both places. **Falsified by:** a shape the fingerprint distinguishes and the guard
   does not, or the reverse, with neither saying so.

## The guards that would go red

- **`baseline`**, on both configurations, if any recorded trade moves — criterion 2 in reverse.
- **`components`**, if a registration or a version changes as a side effect.
- **`wiring`** and **`CONTRACT`**, if the provenance shape the interface reads changes.
- **`tools/check_mutations.py`**, if the new behaviour is a mechanism with no entry — that
  file's rule is that entries are added when a mechanism is built, not afterwards.
- **`test suite`**, on `tests/test_run_id.py` and `tests/test_component_state.py`.

## Open questions, for the owner rather than for an interview

1. **Accept the fingerprint break, or version the fingerprint?** The question above. Nothing
   else in this spec can be decided first.
2. **Should a closure-parameterised `initial_state` be refused outright** rather than
   fingerprinted? Item 2c takes the conservative half — a non-empty `co_freevars` is treated
   as "cannot tell" and raises — which closes the silent-wrong-rule path without touching the
   fingerprint. If that refusal is the permanent answer, this spec shrinks to the module-global
   case and the docstring, and may not be worth building at all.
3. **Does the trade log need a component column?** The auditor's route to the defect ran through
   the log being unable to tell two runs apart. A column naming the form and version per trade
   would make the difference visible after the fact, which is a different and possibly cheaper
   answer than fingerprinting.
