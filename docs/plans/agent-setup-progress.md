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

