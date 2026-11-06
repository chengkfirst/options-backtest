# Agent architecture setup — report

> For a human reading this in the morning. The plan is
> [agent-architecture-setup.md](agent-architecture-setup.md); the item-by-item record,
> with every command and its output, is [agent-setup-progress.md](agent-setup-progress.md).
> Branch: `setup/agent-architecture`, pushed after every commit.

---

## 1 · Summary

All eight phases are done; nothing is blocked. The branch is 22 commits, 58 files,
+6,800 lines, one per plan item so that an interruption could never cost more than one.

Two things were repaired that the plan did not know about, three guards now hold rules that
were previously left to memory, and ten agents, six skills and a workflow are in place —
each verified rather than asserted, including two hooks whose verification is kept as 148
test cases.

**Eight defects were found.** In order of seriousness: component state leaks between runs;
the trailing stop's high-water mark starts below entry; a CI test fails on wall-clock time;
a docstring denies an event bus the engine emits into at 17 sites; a guard that caught a
failure could not say what it was; `requirements.md`'s layer tables are unguarded and that
is where its rot was; a research question prescribes a simulator that does not exist; and
one agent cannot fix the test that guards its own area. **One is fixed** (the fifth, which
had to be, to identify the third). The engine ones are reported and not fixed, as hard rule
5 required. Three need a decision rather than a repair.

Separately, **about twenty-four statements in `requirements.md` and `research-questions.md`
were corrected** — almost all of them saying a thing was absent long after it was built.

The most useful thing in the night was that the new guards caught the night's own work:
`check_shipped` twice, the broad-`git add` hook once (wrongly, which was its own bug), the
language guard once, and the reviewer nine times.

---

## 2 · Before and after

| | Phase 0, before anything | Final full run |
|---|---|---|
| dependencies | OK | OK |
| shipped | OK | OK |
| language | OK | OK |
| defaults | OK | OK |
| plan | OK | OK |
| coverage map | OK | OK |
| labels | OK | OK |
| components | OK | OK |
| wiring | OK | OK |
| contrast | OK | OK |
| words | OK | OK |
| evidence | OK | OK |
| self-test | OK | OK |
| modules | OK | OK |
| baseline | OK · 9.4s | OK |
| test suite | OK · 802.9s · 1410 tests | OK · 1489 tests |
| plan parts | OK | OK |
| plan details | OK | OK |
| doc claims | OK | OK |
| doc examples | OK · 19.1s · 4 blocks | OK · **5 blocks** |
| **total** | **all 20 passed** | **all 20 passed** |

**The plan's premise about the starting state was wrong, and it mattered.** It expected the
`components` guard to be red on a clean clone; all 20 were green. That is why every item was
checked against the code before being acted on rather than followed — 1.1 turned out to be
already fixed, and the rest were confirmed first.

Two numbers moved for a reason: the doc-examples guard now runs **5** blocks rather than 4,
the fifth being the one whose import was wrong and which the loophole had always skipped;
and the suite grew by 79 tests, all of them reverse verification for the two new hooks.

`tools.status`: unchanged, as it should be — 2 of 7 components done and 5 partial, 123
acceptance criteria (67 done, 28 partial, 28 missing). **No engine behaviour changed.**

---

## 3 · What was built

### Phase 1 — what was red, and the documents that rotted

- `tests/test_components.py` — **nothing to fix.** Already repaired; verified in reverse.
- `tools/check_docs.py` — a skip marker now has to be on a block's **first** line.
- `README.md` — the adapter example's import corrected, and the block split.
- `conventions.md`, `tools/status.py`, `app/static/js/views/bench.js` — five pointers to
  two files that do not exist.
- `requirements.md` — the resume contradiction, resolved by reading the code.
- `README.md` — the two directories named `research/`, which are opposites.
- `app/static/css/bench.css` — deleted; nothing ever linked it.
- `pyproject.toml`, `README.md` — `requires-python = ">=3.11,<3.13"`.

### Phase 2 — guardrails

- `tools/hooks/refuse_broad_git_add.py` + `.claude/settings.json` — refuses `git add -A`,
  `--all`, `-u`, `.`, `:/` and any directory.
- `tests/test_git_add_hook.py` — 69 cases, both directions.
- `tools/githooks/pre-commit` — runs `tools.check --fast`, refuses on failure. **Not
  enabled**; see §7.
- `.github/workflows/check.yml` — the **full** check on a clean clone, every push.
- `conventions.md`, `CLAUDE.md` — "no tool checks this" replaced with what now does, and
  what still does not.

### Phase 3 — documents

- `docs/` with nine `README.md` files, divided by **what makes each kind of document wrong**.
- `docs/architecture.md` — the existing transcription, checked against the PDF, moved, and
  given stable per-section anchors emitted by the generator.
- `tools/pdf_to_md.py` — anchors, and its destination defaulted to `docs/`.
- `backtest/CLAUDE.md`, `app/CLAUDE.md`, `tests/CLAUDE.md` — area rules where the area is.
- `CLAUDE.md` — rewritten to 100 lines with the agent roster.

### Phase 4 — builders and checkers

- `tools/hooks/restrict_paths.py` + `tests/test_restrict_paths_hook.py` — 79 cases.
- `.claude/agents/`: `engine-dev`, `interface-dev`, `reviewer`, `quant-auditor`,
  `doc-keeper`, `research-analyst`.

### Phase 5 — research and exploration

- `.claude/agents/`: `research-lead` (opus), `explorer-proposer`,
  `explorer-practitioner`, `explorer-skeptic` (all opus).
- `.claude/workflows/benchmark.js` — validated, **not run**.
- `.claude/scheduled/scout.md` — standalone; the task is **not created**.

### Phase 6 — skills

`.claude/skills/`: `spec`, `ship`, `new-guard`, `audit-docs`, `explore`. The last four are
`disable-model-invocation: true`.

### Phase 7 — the outputs of running them

- `docs/findings/audit-adf0495.md` — the audit.
- `docs/research/plan.md` — the draft plan, 20 entries.
- `tools/check.py` — a failed guard's full output where it cannot be re-run by hand.

---

## 4 · Verified, and how

Every reverse test was run twice: once when built, once again in 7.1. All are in the
progress file with their output.

| What | Planted | Failed with | Restored |
|---|---|---|---|
| components guard | `target_vol` renamed on one side | the field named in the assertion | 18 passed |
| doc-examples loophole | wrong module **plus** an inner-line `# skip` — the exact combination the old rule allowed | `ModuleNotFoundError: backtest.data.adapters` | 5 blocks pass |
| broad `git add` hook | 69 cases | 17 refused forms, each explaining itself | 15 allowed, 9 malformed inputs stand aside |
| path-restriction hook | 79 cases, arguments read from each agent's **own** frontmatter | 25 boundaries, both directions | — |
| pre-commit hook | a non-English line | exit 1 with the refusal text | exit 0 |
| CI verbose output | a non-English line | the guard's full output under its command | a green run unchanged |

Beyond the reverse tests:

- **The CI workflow ran green on a clean clone** — all 20 guards from a checkout into an
  empty directory, `pip install -e ".[dev]"` on Python 3.11, no cache, on two of the
  branch's runs. That answers the question the file could not answer locally: the suite does
  run offline on a fresh machine, with no `.cache/` and no `research/`. **Other runs of the
  same code went red on the wall-clock test in §6.3**, which is a property of the runner
  rather than of the code, and is the open item in §7.
- **The architecture transcription was checked against the PDF** by extracting its text
  independently with `pypdf` — a different code path from the generator's, so it is a
  comparison and not the generator agreeing with itself. 38 pages; all 42 numbered headings
  present; **7,979 words against the PDF's 7,936, a ratio of 1.005** — a summary does not
  come out at 100.5%.
- **Regeneration reproduces the committed files**, verified by generating into a scratch
  directory and diffing: the only difference is the 31 anchor lines, 0 removals.
- **The interface was driven in a browser.** The composer and the gallery render correctly,
  no console errors, and the variant picker is properly grouped by the stylesheet that *is*
  linked — which narrowed what item 1.4 had reported.
- **The workflow script was validated** 21 ways, syntax checked as the runtime wraps it.
- **The full suite passes locally**: 1489 passed in 822s.

### What was *not* verified, and should be

**The ten agent definitions were never loaded by Claude Code as agents.** A session loads
`.claude/agents/` at start, and these were written mid-session, so each was exercised by a
general-purpose agent **told to read and follow its own definition file**. That tests the
instructions — which is the part written tonight, and they worked: the reviewer found nine
real defects, the auditor two, the research lead reorganised its own source. It does **not**
test that the frontmatter parses in the loader, that `tools` restricts as intended, or that
the per-agent hooks fire. The hooks were verified directly instead, 148 cases. **Start a
fresh session and check that `/agents` lists all ten.**

---

## 5 · Blocked or skipped

**Nothing was blocked.** Three things were deliberately not done, each as the plan directed:

| Not done | Why |
|---|---|
| `git config core.hooksPath tools/githooks` | the plan says the human enables it. It changes where git looks for every hook |
| `/benchmark` not run | the plan forbids it tonight, and rule 9 forbids cloning third-party repositories. The repository list is a judgement |
| the scout scheduled task not created | the plan says the human creates it |

Agent teams were **not** enabled in any settings file (rule 11). The optional second
transcription was done as a side effect, as `docs/frontend-web-plan.md`.

One ordering deviation: **3.4 was done after Phase 5**, because its content is the agent
roster and a roster written before the agents exist is a table of pointers to files that are
not there — which is what Phase 1.3 spent its time removing.

---

## 6 · Found, not fixed

### 6.1 Component state leaks between runs — the serious one

`backtest/core/component.py:210`, `backtest/engine/loop.py:2113`.

`Bound.forget()` is called on **one of the three close paths** — the exit-intent path. It is
not called from `_settle` (expiry) or `_early_exercise`, and `run()` never clears `_state`.
`_state.clear()` appears exactly once in the class, inside `set_state`. Trade ids restart at
1 every run.

**So a second run of the same strategy object inherits a closed trade's high-water mark.**
`research/positioning.compare` with the *identical* sizing method three times returns
27 / **29** / 27 trades, reproducibly. Seeding one state entry moved a trade's exit by two
days and $84. The exposed callers include `sdk.delay_test` and `sdk.realism_test` — **the
look-ahead and realism tests themselves**.

**How a result is biased:** silently, and in no fixed direction. `run_id` is identical
either way, so nothing in the output distinguishes a first run from a later one, and the
guard record cannot tell you which figures were produced second. Already inherited by the
`ratchet` variant.

I verified this myself rather than taking the auditor's word: parsing `loop.py` confirms
neither close path calls `forget` and that `run` does not clear.

**Not fixed** because hard rule 5 forbids engine behaviour changes tonight, and this needs a
failing test first and someone watching the baseline. The audit suggests the general guard:
*no state held for any trade that has a record*.

### 6.2 The trailing stop's high-water mark starts below entry

`backtest/signals/stop_loss/__init__.py:60-73`.

`initial_state` is called lazily on the first decide call — the fill day — with an
**exit-side** `premium_ratio`. Measured negative on **33 of 33** trades (≈ -0.0198), so the
mark starts below entry by the round-trip cost and the stop is looser than its stated rule.
At `fraction=0.5`, trade 24 stops under `premium` and does not under `trailing`.

The docstring, the parameter description and the test all describe the opposite; the test
feeds `0.0` by hand. Whether the rule ("give-back from the first sellable price") was
intended is a judgement — it is defensible, but it is not what anything says.

### 6.3 The CI workflow is flaky, on two wall-clock assertions

`tests/test_adapters.py:334` (`assert took < 20.0`) and `:363` (`assert took < 5.0`), in
`TestTheFileSourceIsUsableOnRealVolumes`.

Named by CI, once CI could name things (§6.5):

```
FAILED tests/test_adapters.py::TestTheFileSourceIsUsableOnRealVolumes::
       test_construction_is_seconds_and_not_minutes
E  AssertionError: 208,520 rows took 40.9s to prepare.
E  assert 40.921020731 < 20.0
```

**40.9s against a 20s budget, on a two-core runner.** The suite passes locally every time
(1489 passed), where the whole test including its fixture takes 16.3s. That also explains
why the branch's runs failed **non-monotonically** — runs on later commits passed after
earlier ones failed, because the margin depends on which runner the job landed on.

**I did not touch them.** Loosening a threshold to make CI green is exactly what hard rule 7
forbids, and these tests are valuable: the first records that the operation once took 101s.
The decision is yours, and the options are not equal:

- **Raise the budget** — simplest, and weakens a real regression guard.
- **Measure work, not time** — count the derivations rather than the seconds. Strongest, and
  real work.
- **Mark them to run only locally** — honest, and loses them exactly where a regression
  would first show up.

Until this is settled the CI badge cannot be trusted, which matters because §7 asks you to
check it.

### 6.4 A docstring says this system has no event bus. It has one

`backtest/core/component.py:27-28` states: "there is **no event bus in this system** —
layers call each other directly". `backtest/core/events.py:281` is `class EventBus`, with
`emit`, `trace`, a `PHASE` order and 15 named types, and `engine/loop.py:291` instantiates
it and emits at **17 sites**. `requirements.md` 1.15 already recorded the bus as built.

The narrower half of the sentence is still true and worth keeping: the document's
`on_event(event, ctx)` protocol has nothing to receive while the loop acts inline, so
`decide` is what the engine calls.

Found by `doc-keeper`, which correctly **refused to fix it** — a docstring is code, and its
one rule is that the document is wrong and the code is right. For `engine-dev`.

Worth knowing how it surfaced: I wrote `docs/decisions/README.md` last night listing "no
event bus" as a recorded deviation, taking it from this docstring. A stale sentence in a
docstring propagated into a brand-new document within hours. That is this project's
"document rotted" failure shape, observed live, and the only reason it was caught is that
a second pass read the code instead of the document.

### 6.5 A guard found a failure and could not say what it was — fixed

`tools/check.py` captured each guard's output and printed its **last line**, then said to
run that guard alone for the rest. At a terminal that is right. In CI there is nothing you
can do with it, and twenty-five minutes of suite produced
`FAIL test suite 1472.4s  1 failed, 1408 passed` and nothing else.

Fixed: a failed guard's whole output is printed when `CI` is set, or with `--verbose`.
Nothing newly passes; only how much of a failure's own output survives changes. The next run
named the test in §6.3 — the guard that found the problem was the one that had to be
repaired to report it.

### 6.6 `requirements.md`'s layer tables are unguarded, and that is where the rot was

`tools/check_doc_claims.py` checks the README's `[absent: …]` brackets and
`requirements.md`'s **section** statuses. The Layer tables at `requirements.md:70-155` carry
a per-piece status word that **nothing checks** — and six of the eight stale "absent" claims
`doc-keeper` found lived there. A candidate for `/new-guard`, with a real failure already
behind it.

### 6.7 No path simulator exists, and a research question prescribes one

`research-questions.md` 1.1 prescribes "run Monte Carlo under different drift assumptions"
and marks the question **can do**, which the status table defines as runnable now. There is
no path simulator in `backtest/`: the only RNG uses are random entry dates
(`research/baselines.py:196`) and bootstrap resampling
(`research/validation/intervals.py:99,139`) — resampling observed data, not simulating paths.

`doc-keeper` stopped rather than correcting it, and was right to: "can do (underlying side)"
may be a claim about *data* rather than tooling, and marking it "needs tooling" implies a
`requirements.md` row for a simulator. Yours.

### 6.8 `interface-dev` cannot fix the test that guards its own area

`engine-dev` owns `backtest/` and `tests/`; `interface-dev` owns `app/` only. But
`tests/test_components.py` is the guard over interface field wiring — so the agent that can
break it cannot repair it. Raised by the reviewer as a judgement rather than a defect: the
alternative, giving `interface-dev` `tests/`, lets both builders edit one directory.
Related: `tools/` is owned by no agent, which is fine only while `/new-guard` is run from
the main session, as it is written to be.

---

## 7 · For the human to do, in order

1. **Review the branch and open a pull request.**
   `setup/agent-architecture` is pushed. 22 commits, one per plan item.
   `gh pr create --base backend --head setup/agent-architecture`
   **Base it on `backend`, not `main`** — `main` is a month behind, so a diff against it is
   77 commits of already-reviewed work.
2. **Enable the pre-commit hook**, once per clone:
   `git config core.hooksPath tools/githooks`
   It runs `tools.check --fast` and refuses the commit on failure. It was deliberately not
   enabled by this session, and four commits were made after building it that it would have
   checked — one of them introduced a guard failure that the reviewer, not a tool, caught.
3. **Decide what to do about §6.3 before trusting the CI badge**, then confirm the latest run
   on the branch: `gh run list --branch setup/agent-architecture`. A failing run now prints
   the failing guard's whole output, so it will name the test.
4. **Create the scout scheduled task** with `/schedule`, weekly, Monday morning.
   Give it **the contents of `.claude/scheduled/scout.md`** as its prompt — the whole file,
   not a pointer to it. A cloud task has no checkout until it clones, so "follow
   `.claude/scheduled/scout.md`" fails before it can read the file. It needs the repository,
   permission to push a branch and open a pull request, and web access; it needs no Python.
5. **Supply GitHub links and run `/benchmark`:**
   `/benchmark` with `{ "repos": ["https://github.com/owner/name", …], "date": "YYYY-MM-DD" }`
   The date is an argument because a workflow script may not read the clock. It clones
   read-only outside this repository and never executes anything it clones.
6. **Read and argue with `docs/research/plan.md`**, which is marked `DRAFT — for discussion`:
   `claude --agent research-lead`
   It asks you to argue with one decision in particular — demoting the whole falsification
   group below the event studies, against `research-questions.md`'s own priority — and it
   left **the hold-out unfixed on purpose**, because no uncontaminated period of usable
   length exists and both remaining options are bad. That one is yours.
7. **Start a fresh session and confirm the ten agents load** (see §4). Nothing tonight could
   test that.

---

## 8 · Recommended next

1. **Fix 6.1 before any result is trusted.** It is the only finding here that can have
   already moved a published figure, and nothing distinguishes an affected run from an
   unaffected one. Write the failing test first — two runs of one strategy object, asserted
   identical — then clear state at the start of `run()` and call `forget` from all three
   close paths.
2. **Split `EventDrivenEngine.run` before any parallel-engine work.** It is **727 lines**,
   not the 573 the plan said — a number that was stale in the plan and that I copied into two
   documents before the reviewer caught it. It holds the daily order, the exit precedence,
   the review hook, the risk brake, the checkpoint and the shadow tracking, and it is the
   most-changed method in the project's history.
3. **Finish the stylesheet migration.** `app.css` and `bench.css` sit at the static root
   beside a `css/` directory. That arrangement is how a second, unlinked `bench.css` came to
   exist in `css/` and be forgotten for long enough that three of its five rules died. One
   name, one place.
4. **Give `docs/decisions/` its first three entries**, which already exist as prose in
   `conventions.md`: the one-day execution lag against the document's intraday design;
   marking to market at step 5 rather than last; and no event bus. Each has a condition that
   would reopen it, which is exactly what a decision record is for and what prose cannot
   carry.
5. **Write the first `docs/feedback/` entry.** It is the one directory agents may not write
   to, and it is empty — so `research-lead` and `/explore` are both currently reasoning with
   the one input that cannot be derived missing. One real session of using the platform,
   written up as *what you were trying to do and where it went wrong*, is worth more to them
   than anything else you could add.
