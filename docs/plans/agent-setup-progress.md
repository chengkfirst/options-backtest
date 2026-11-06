# Agent architecture setup — progress

Tracks [agent-architecture-setup.md](agent-architecture-setup.md), item by item. Written as
the work happens so that a stop — a usage limit, a crash, a compaction — loses at most one
item. Branch: `setup/agent-architecture`.

**How to read a row.** Each item records its status, the files it touched, the command that
verified it and that command's result, and the commit. `DONE (already fixed)` means the plan
described a failure that a later commit had already repaired, confirmed here rather than
taken on faith.

**Conservative readings taken.** Where the plan was ambiguous or its premise had moved on,
the reading taken is written down under the item, as section 0.1 requires.

---

## Phase 0 — Starting state

### 0.1 Uncommitted changes, and the branch — DONE

`git status` on arrival, on branch `backend` (2 commits ahead of `origin/backend`):

```
?? Plan/agent-architecture-setup.md
```

**One untracked file, and it is this plan.** Nothing else was uncommitted, so there is no
other session's work in the tree to leave alone. The architecture transcription named in
3.2 was already committed, in `c2f1d1f` — see 3.2.

Branch created: `git checkout -b setup/agent-architecture` from `c2f1d1f`.

### 0.2 Guard results before any change — DONE

`/opt/anaconda3/bin/python -m tools.check` (full, 14 min wall clock):

```
  OK   dependencies    0.1s        OK   self-test       0.0s
  OK   shipped         0.0s        OK   modules         0.3s
  OK   language        0.4s        OK   baseline        9.4s
  OK   defaults        0.6s        OK   test suite    802.9s
  OK   plan            0.0s        OK   plan parts      1.3s
  OK   coverage map    1.5s        OK   plan details    1.9s
  OK   labels          2.3s        OK   doc claims      0.0s
  OK   components      1.3s        OK   doc examples   19.1s
  OK   wiring          2.2s
  OK   contrast        0.0s
  OK   words           0.0s
  OK   evidence        0.0s

OK  all 20 guards passed
```

**All 20 green at the start.** This contradicts the plan's premise for 1.1 — it expected the
`components` guard to be red on a clean clone. It was fixed before tonight; confirmed by
reading the test, not by trusting the green. See 1.1.

`/opt/anaconda3/bin/python -m tools.status`: seven components — 2 done, 5 partial;
123 acceptance criteria (hand-written) — 67 done, 28 partial, 28 missing; 62 test files.

**Note for every later item: the full check costs ~14 minutes**, 803s of it the test suite.
So `--fast` runs during the work and the full run at the end, as the plan's 0.3 requires.

### 0.3 Plan and progress file committed — PENDING

---

## Phase 1 — What was red, and the documents that rotted

### 1.1 `components` guard on a clean clone — DONE (already fixed)

The plan expected `tests/test_components.py::TestDescribe::test_fields_match_real_run_parameters`
to read `app/static/bench.js` and raise `FileNotFoundError`. It does not: the test already
reads **the whole `app/static/js/` tree** (`js.rglob("*.js")`, line 180), and the comment at
line 176 records the move it was repaired for. Confirmed by reading the test, and by the
guard passing in 0.2. Nothing to fix.

**Verified in reverse anyway**, because a green guard proves nothing about what it catches.
Three interface fields are cleared only by the JS-tree read — `risk_pct`, `notional` and
`target_vol`, which reach `run()` neither directly nor through `app/runner.py`. `target_vol`
appears in exactly one file, so it is the field whose wiring can be removed cleanly:

- `app/static/js/views/bench.js:893`, `target_vol: +o.target_vol` renamed to
  `vol_target: +o.vol_target` — the shape of the real failure, a field renamed on one side.
- `pytest tests/test_components.py -q` → `1 failed, 17 passed`:
  `AssertionError: these interface fields reach neither run() nor the runner that assembles
  its arguments ... ['target_vol']`
- Restored, `git diff` empty, `pytest tests/test_components.py -q` → `18 passed`.

So the guard does catch a de-wired field, and it is the field names it checks, not the file
it reads.

### 1.2 The doc-examples loophole — DONE

Confirmed before fixing. `tools/check_docs.py` skipped a block on `SKIP_MARK in code` — the
marker **anywhere** in it. Of the README's 20 Python blocks, exactly one was exempted by
accident: the data-adapter block, whose `# skip` sat on a commented-out fourth line, and
whose first line imported `backtest.data.adapters` — a module that does not exist. The one
block with a wrong import was the one block never run.

Files: `tools/check_docs.py`, `README.md`.

- The rule is now `is_skipped()`: the **first non-empty line** must start with `# skip`.
  The module docstring says so, and records what the old rule cost.
- `README.md`: the import corrected to `backtest.data.options.adapters`, and the block split
  — `describe_schema()` runs; `check_file("your/path")` moved to its own block whose first
  line is `# skip (needs your own data file)`.
- Every other block checked under both rules: the runnable set is identical
  (`[1, 3, 4, 7, 10]` of 20 either way), so no other block changes behaviour. The
  `# skip (…)` markers already sat on first lines.
- `/opt/anaconda3/bin/python tools/check_docs.py` → `the doc-code check passed (5 blocks)`.
  It was 4 before: the corrected adapter block is the fifth, now actually run.

*Verified in reverse* — planted the exact failure the old rule allowed: the wrong module
back on the first line **and** a `# skip` on an inner line, which previously exempted the
whole block.

- `tools/check_docs.py` → `the doc-code check failed (1/5 blocks did not run)` /
  `README.md, python block 7: ModuleNotFoundError: No module named 'backtest.data.adapters'`
- Restored; guard passes again (5 blocks).

So the inner-line marker no longer buys an exemption, and the guard catches the import that
hid behind it.

### 1.3 Stale references — DONE

Swept the whole repository, not only the listed places: every `` `path/to/file.ext` `` in
every `.md`, and every such path in `.py`/`.js` comments, tested against the filesystem.
**Exactly two paths in the project do not exist**: `app/static/bench.js` and
`backtest/components.py`. Both are the ones the plan named; there was no third.

| Where | Was | Is |
|---|---|---|
| `conventions.md:45` (guard table) | `CONTRACT` in `bench.js` | `CONTRACT` in `app/static/js/contract.js` |
| `conventions.md:72` (the front/back rule) | `app/static/bench.js` | `app/static/js/contract.js` |
| `conventions.md:199` (defined once) | `backtest/components.py` | `COMPONENTS` in `backtest/core/slots.py` |
| `tools/status.py:17` (docstring) | `backtest/components.py` | `COMPONENTS` in `backtest/core/slots.py` |
| `app/static/js/views/bench.js:221` (comment) | `backtest/components.py` | `backtest/core/slots.py`, as `COMPONENTS` |

**Not changed:** `tests/test_components.py:176` also names `app/static/bench.js`, and
correctly — it is the comment recording the move the test was repaired for. A history that
names the old path is not a stale reference.

Where the components really are, read rather than guessed: `COMPONENTS` is a
`tuple[Component, ...]` at `backtest/core/slots.py:123`, and that is what the interface's
form, the gap list and `tools/status.py` are generated from. `backtest/core/component.py` is
a different thing — the swappable-form protocol (`register`, `decide`, name and version into
the fingerprint) — so citing it here would have been a second wrong pointer.

**The `requirements.md` resume contradiction.** Read `engine/runner.py`, `engine/loop.py`
and `api/sdk.py` before deciding which side was wrong. `Checkpoint` is a frozen dataclass at
`engine/runner.py:52`; `until=` and `resume_from=` are parameters of
`EventDrivenEngine.run` at `engine/loop.py:361`, which takes a checkpoint and puts one on
the result; `api/sdk.py:596` passes both through from `run()`. So **resume is built**, and:

- The Layer 5 table row (line 119) said "No resume from a state snapshot" — flatly wrong,
  and replaced with what is built, where, and what is still absent (no scheduler; the batch
  runner is sequential).
- Criterion 5.1 (line 185) was right that resume is built but pointed only at
  `engine/runner.py`, which holds the `Checkpoint` and not the resuming. It now names both
  halves, so a reader looking for `resume_from=` finds it.

**The README `research/` note.** The ambiguity was real: the layout block above the note
lists the packages inside `backtest/`, `research/` among them, and the note then said
"`research/` ... is not version controlled". Replaced with a two-row table naming
`backtest/research/` (code, version controlled, guarded) against `/research/` at the root
(the local record, excluded), and the `.gitignore` leading slash that separates them —
which is the thing that once excluded the whole code package while `git status` stayed clean.

Verified: `tools.check --fast` → `OK  all 18 guards passed`. The `doc claims` guard, which
reads documents against code, is among them.

### 1.4 Orphaned stylesheet — DONE, with a finding the plan did not expect

Confirmed unreferenced by a repository-wide search: no `<link>` in `index.html`,
`states.html` or `selftest.html` resolves to it (they link `bench.css` at the static root,
which is a **different, live** 444-line file), there is no `@import` anywhere in
`app/static/`, and no module names it. `app/static/css/bench.css` was 42 lines and five
rules. Deleted.

**But it was not dead code, and that is worth recording.** Its five rules split in two:

- `.variant-lead`, `.variant-pick`, `.variant-why` — genuinely dead. They styled a
  `<select>`-based picker; `variantPicker()` (`app/static/js/views/bench.js:563`) now emits
  radio options with the classes `.rule-opt` / `.rule-name` / `.rule-why` / `.rule-tag`,
  which **are** styled, in the linked `app/static/bench.css`.
- `.variant` and `.variant .fields` — **live markup, never loaded.** `bench.js:576` emits
  `<div class="variant" data-slot=…>`, and `variantParams()` puts `<div class="fields">`
  inside it. No linked stylesheet styles either, here or anywhere.

So deleting is a **strict no-op on what renders**: the file was never served, so the two
live rules have never applied, and the variant picker has been rendering without its
container box (the `--surface-2` ground, padding and radius that visually group a slot's
candidate forms) for as long as the file has existed. Putting them back would be a visual
change, which tonight's scope excludes and which belongs in a review in `states.html`. It
goes to the report under "Found, not fixed"; the picker is looked at in the browser in 7.1.

The half-finished migration itself — `app.css` and `bench.css` at the static root beside a
`css/` directory, which is how a second `bench.css` came to exist and be forgotten — goes
to "Recommended next", as the plan directs. Not consolidated tonight.

Verified: `check_shipped`, `check_contrast`, `check_modules`, `check_selftest` all pass with
the file gone (contrast still reports its 130 pairs over two themes).

### 1.5 Python version — DONE

`pyproject.toml` declared `requires-python = ">=3.11"` and the README said "Python 3.11+",
while the pinned `numpy==1.26.4` and `pandas==2.1.4` publish no wheels for 3.13. An
open-ended declaration lets a 3.13 interpreter satisfy it and then fail at `pip install`,
which reads as a broken project rather than an unsupported interpreter.

- `pyproject.toml`: `requires-python = ">=3.11,<3.13"`, with the reason in a comment beside
  it so the cap is not later "tidied" back open. **No pin changed.**
- `README.md`: "Python 3.11 or 3.12. **Not 3.13**", naming which pins cause it.

Verified: `tomllib` parses the file and reports `>=3.11,<3.13` with the two pins unchanged;
`tools.check --fast` → `OK  all 18 guards passed`.

