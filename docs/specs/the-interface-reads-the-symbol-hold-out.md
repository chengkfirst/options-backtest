# The interface can declare and read a hold-out by symbol

**Status:** awaiting owner approval
**Approved:** not yet
**Raised by:** `reviewer`, on the `engine/gaps` item 2a diff, 2026-11-08
**Belongs to:** `interface-dev` (`app/`). Written by the engine side because the engine side
created the gap and may not close it.

## Goal

Item 2a of `engine/gaps` added a hold-out by **symbol**:
`backtest.research.holdout.ReservedSymbol`, a `hold_out_symbols` parameter on `run()`, and
three new provenance keys — `symbols`, `symbol_touched`, `symbol_note` — beside the period
half's existing `hold_out`, `touched`, `note`.

**Nothing in `app/` can declare one, and nothing in `app/` reads one.** So the mechanism
exists only to a caller writing Python against the SDK, and the only path a person actually
uses cannot reach it. `docs/decisions/0001-the-hold-out-part-b-and-the-option-data.md`
reserves SPY; after this spec is built, the interface is the place that reservation can be
stated and seen.

**This is not a contract violation today, and that is worth stating precisely so the next
reader does not go looking for one.** `manifest.provenance.hold_out` is declared in
`app/static/js/contract.js` at object depth, and `app/` reads only `.hold_out`, `.touched`
and `.note` — all three unchanged. `tools/check_wiring.py` passes, correctly. The defect is
not an undeclared read; it is three fields that exist and are never read.

## Non-goals

- **Not a change to the engine.** `ReservedSymbol`, `check()` and `run()` are built and are
  out of scope here. If this spec needs an engine change to be buildable, that is a finding
  against this spec, not work to do inside it.
- **Not a redesign of the hold-out row.** The period half's wording and verdict stay as they
  are; this adds the symbol half beside it.
- **Not a decision about whether one `unlock=` may spend both halves.** That is a separate
  open question on the engine side and is recorded there.
- **Not a backfill.** Runs stored before item 2a carry no `symbols` key. They must read as
  "nothing was reserved by symbol", which is true of them, and must not be rewritten.

## Acceptance criteria, each falsifiable

1. **A reservation can be declared from the interface.** The composer can state one or more
   reserved symbols for a run, and `app/runner.py` passes them to `run()` as
   `hold_out_symbols`. **Test:** a run declared with SPY reserved, launched from the API
   handler, produces a manifest whose `provenance.hold_out.symbols` is `["SPY"]`.
   **Falsified by:** the field arriving absent or empty after a declaration was made.

2. **The three fields are declared in `CONTRACT` before they are read**, at the depth they
   are read: `manifest.provenance.hold_out.symbols` (array of strings, or `null`),
   `.symbol_touched` (boolean), `.symbol_note` (string).
   **Test:** `python tools/check_wiring.py` passes against a real response from a run that
   declared a reserved symbol. **Falsified by:** `check_wiring` reporting an undeclared read,
   which is the guard working.

3. **A run that spent a reserved underlying does not read as clean.**
   `app/static/js/views/run.js:664-675` currently returns `"none"` and the verdict
   `bad("no period was reserved, so every number here was available while the strategy was
   being chosen")` for a run that reserved **and revealed** SPY. After this, such a run says
   so on the page. **Test:** in `app/static/states.html`, the hold-out row in all four
   states — nothing reserved · period reserved and respected · symbol reserved and
   respected · symbol reserved and spent, with its reason.
   **Falsified by:** any of the four rendering as any other.

4. **The experiment log's rollup counts a symbol touch.** `app/runner.py:766-774` gates every
   hold-out field on `held.get("hold_out")` — the period — so a symbol-only reservation
   contributes nothing and a spent symbol never sets `hold_out_touched`.
   **Test:** an idea whose only reservation is by symbol shows a reservation in the rollup,
   and one whose reserved symbol was spent shows as touched.
   **Falsified by:** the rollup reading "no period reserved" for an idea whose reserved
   underlying is gone.

5. **`touches()` is reachable from the interface.** `backtest.research.holdout.touches` is
   "the number a person needs when deciding what a final figure is worth" — a hold-out
   touched once is the protocol working and touched five times is a validation period that
   has quietly become a training one. Nothing in `app/` calls it, for either half.
   **Test:** the count, and each touch's reason, appear where a person comparing results
   will see them. **Falsified by:** the number existing only to the SDK, which is today.

## The guards that would go red

- **`tools/check_wiring.py`** — if a new sub-field is read before it is declared in
  `CONTRACT`, which is criterion 2's own test run in reverse.
- **`tools/check_contrast.py`** — if the new row's warning state is given a colour that
  fails contrast.
- **`tools/check_language.py`** — on any non-English identifier or string added.
- **`tools/check_labels.py`** — if a new interface label is added without its entry.
- The **front-and-back rule** in `docs/conventions.md`: this spec exists because the back
  moved alone, and building it is what makes the pair whole again.

## Open questions, for the owner rather than for an interview

Nobody was awake to interview when this was written, so these are listed rather than asked.

1. **Should the composer offer a symbol reservation at all, or only display one?** Declaring
   a hold-out is a group-4 research-method choice, and a form field invites it to be made
   casually. A display-only treatment, with the reservation declared in a decision document
   and passed by configuration, may be the more honest shape. **This spec does not decide
   it** — criterion 1 assumes declarable, and if the answer is display-only, criterion 1 is
   struck and the rest stands.
2. **Should a spent symbol block or only warn on the page?** The engine refuses without
   `unlock=`; the interface cannot refuse a stored result that already exists.
3. **Does the period half's verdict wording need to change** now that "no period reserved"
   no longer means "nothing reserved"? Criterion 3 requires the four states be
   distinguishable and does not fix the words.
