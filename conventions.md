# Working conventions

> This file is independent of [docs/architecture.md](docs/architecture.md) (the architecture,
> transcribed from the PDF in `Plan/`)
> and [requirements.md](requirements.md) (what is built against it). It covers one thing
> only: **how to make sure what gets built is right, and does not break quietly.**

---

## The one principle

**A convention a tool can check is a convention. One that only lives in a document is a
wish.**

What is dangerous in this project is not the errors that crash. It is the **silent
failures** — it ran, there is a result, nothing raised, and the result is wrong. They come
in a fixed set of shapes:

| The shape | An example | Why it goes unnoticed |
|---|---|---|
| **Code not published** | a whole package excluded by a broad `.gitignore` rule | `git status` is clean and `git add -A` raises nothing |
| **An input with no effect** | a form field that is not a `run()` parameter | it was filled in, nothing happened, nothing raised |
| **A number short by a piece** | a partial close's P&L left out of the final record | the numbers look normal |
| **An unparseable response** | `NaN` in JSON | the server returns 200 and the front end says only "not JSON" |
| **Control flow through prose** | free text deciding entry from exit | reword it and an entry executes as an exit |
| **Front and back out of step** | the server sends `id` and the interface reads `run_id` | both are undefined, so `find()` "matches" |
| **A document rotted** | a description of a state that stopped being true | nobody runs the code in a document |
| **Mixed-language identifiers** | a key spelled one way on each side | the lookup silently yields nothing |

Each of these has a guard below. **The guards were not imagined** — there is one for each
of these shapes.

## The fixed check

```bash
python -m tools.check           # before committing
python -m tools.check --fast    # for a small change, skipping the slow ones
```

| Guard | What it catches | Time |
|---|---|---|
| **dependencies** | a reverse dependency; pricing escaping the data layer; **an unregistered module fails outright** | fast |
| **shipped** | files that exist locally and not in git | fast |
| **language** | anything in the project that is not English | fast |
| **components** | the seven components' definitions being self-consistent; **a field must be a real `run()` parameter** | fast |
| **wiring** | the interface declares `CONTRACT` in `app/static/js/contract.js`, checked against a real response — **at the depth it is read**, so a renamed nested field fails too | fast |
| **contrast** | a colour pair too close to read, in either theme. Verified in reverse by the palette it was written for: the design plan's proposed values failed its own 4.5:1 rule in **twenty text pairs** across the two themes, and one slot colour was invisible on the dark ground | fast |
| **modules** | an interface module importing a name nothing exports, or using one it did not import. There is no bundler, so that raises only when the code path runs | fast |
| **evidence** | the standing-assumptions sheet going stale or going quiet: a row that writes its value instead of reading the record, one that cannot report a good state, or a permanent limitation of the platform left undeclared. Checking is the backend's; this holds the sheet that says what the machinery assumed | fast |
| **selftest** | the browser self-test losing a module. It runs every frontend module against the cases that have broken them — absence printed as a number, a chart given no rows, an escape safe in text and not in an attribute — at `/selftest.html`; this holds that it still covers the tree | fast |
| **words** | two words for one concept on screen, a button that names a verb and no object, an exclamation mark or a "please". The design plan's terms table exists because a reader who meets both "component" and "slot" has to work out whether they are the same thing | fast |
| **baseline** | an unintended change in results | 8 s |
| **test suite** | behavioural regression | 2 min |
| **doc examples** | the snippets in the README not running | 1 min |

**Any failure means no commit.** There is no such thing as a failure that can be ignored —
if one really could be, the guard itself should be changed.

---

## The hard rule: front and back move together

**Any change changes both sides, and the `CONTRACT` declaration pins it down.**

The two sides changing separately raises nothing; it just makes the feature fail quietly.
This class of error has occurred three times in this project: the run history showing
`undefined`, every group label coming out empty, and the interface still sending a sizing
method's old name after it was renamed.

How it works:

1. What the interface needs from the backend is written in `CONTRACT` in
   `app/static/js/contract.js`
2. **The interface checks it at runtime** — a missing field is reported at once, and
   `undefined` never seeps into rendering
3. **`check_wiring` checks the same declaration against a real response** before committing
4. The backend renames a field and the contract check fails on the spot; the interface
   reads a new field by adding it to `CONTRACT` first

One declaration, used in two places. Nothing in the interface may read a response field
that is not declared in `CONTRACT`.

## The hard rule: the project is written in English

**Every file: code, comments, docstrings, interface text, documents and file names.**

This is not a stylistic preference. A Chinese dict key is a **functional identifier** — the
front end reads `m["sharpe"]` and a test asserts on `out["trades"]`. When one side is
renamed and the other is not, nothing raises: the lookup returns `undefined` deep inside
rendering, or `.get()` quietly yields `None` and a metric reads as zero. Encoding,
normalisation (NFC against NFD) and full-width punctuation each add another way for two
spellings of "the same" key to stop matching.

One language means one spelling for every identifier, and `grep` finds every use of it.

Held by `tools/check_language.py`, registered in `tools.check`.

## The bar for a new convention

**A concrete failure first, then the guard.**

A pit nobody has fallen into gets no fence. An imagined risk becomes noise nobody reads,
and once there is enough noise the real warnings get skipped too.

The steps for a new guard:

1. Write down **exactly how that error happened, and why it went unnoticed**
2. Write the check
3. **Verify it in reverse**: plant the error deliberately and confirm the check fails
4. Wire it into `tools/check.py`
5. Add a row to the table above

Step 3 cannot be skipped. A check that has never been verified in reverse may never have
worked at all.

---

## The hard lines in the code

No tool checks these yet, so they rest on a person. **Saying so is itself part of the
convention** — pretending they are guarded is more dangerous than admitting they are not.

| The line | Why | Who holds it |
|---|---|---|
| Absence is explicit; no bare `None`/`NaN`/`0` | a delta read as 0 selects the wrong contract and the run finishes normally | `Maybe[T]` plus an architecture test |
| When unsure, refuse to give a number rather than approximate | beyond the second-order expansion's range, a stress grid's numbers have the wrong sign | a person |
| The unit of statistics is the **logical trade**, not the position | counted by position, three rolls become four trades and both the win rate and the holding period are distorted | partly tested |
| Two price sets, two time conventions | forcing either into one makes one of them wrong | a person |
| Detect events on the close, measure the pain intraday | closes throughout understate both the drawdown and the probability of a further fall | tested |
| A conditional probability always comes with its unconditional baseline | reporting only the former is self-deception | tested |
| **One fact is defined in exactly one place** | two definitions always drift | a person |
| **A realism assumption is never optimised** | picking the λ, the latency or the cleaning rule that maximises backtest returns optimises an illusion. These are set conservatively and only tested for sensitivity | a person |
| **No threshold is written as a literal** | a cut inside a module turns a judgement ("a plateau") into something that reads as a measurement, and nobody can then answer "what was decided for me". Every one lives in `core/thresholds.py`, in the architecture document's four groups, and every verdict names the cut behind it | a test asserts the groups and that an override reaches the tool |
| **The platform never defaults a strategy choice** | the document permits optimising a contract delta or an exit level under its protocol; it does not permit the platform picking one. A default also hides itself: it never appears in the configuration a person reads back when asking what the run actually did | `tools/check_defaults.py`, which reads the dataclass field defaults. The earlier test checked `thresholds.py` and found its strategy group empty — true, and beside the point: the choices lived as field defaults, where `ExitRule` supplied +100% / -50% to every run that omitted them |
| **A slot's logic never calls another slot** | the architecture requires every influence to pass through the trade context and the action vocabulary; direct calls are what make per-slot ablation impossible | a person, until the component protocol exists |
| **Anything that changes the trades is in the run fingerprint** | two configurations sharing one id make a search look smaller than it was, so the deflated result comes out better than it earned. The error flatters, which is the kind nobody reports | a test over six configurations, and `run_id` refusing to hash without the execution settings |
| **A visible default is allowed; a hidden one is not** | the interface's form ships starting values for every field. That is the right place for one: it is on screen, in the saved configuration, and one edit away. What is forbidden is a value that fills itself in when a field is *omitted* and then appears nowhere | the same guard, whose `ALLOWED` list carries a reason per exemption |
| **A stored number carries its basis in its name** | a column called `sharpe` let an annualised figure be passed where a per-period one was wanted. It is `sharpe_annualised` | a person |

---

## The timing rule, and how it differs from the document

**T's close decides, T+1's close fills.** A signal is computed from the bar that has
closed; the order is filled on the next session's close; the contract chosen is the
execution day's, while everything the decision reads is cut at the decision day.

The document asks for something finer: a signal at an intraday snapshot (15:45 ET) and a
fill at a later quote (15:50), with the decision latency between them. It calls filling on
the same quote the signal was computed from "a subtle form of look-ahead", and the daily
rule here avoids that -- a whole session apart is more conservative than five minutes, not
less.

What it costs, stated rather than left to be discovered:

- **a realised holding is `hold_days` plus the lag.** A rule written as "hold 20 days"
  holds 21. The cap is tested on the close and the fill is the next one. Anyone computing
  theta over the stated window is a day out, and the configuration does not say so.
- **no intraday stop can be modelled.** A stop is tested on the close and filled on the
  next, so a trade that fell through the stop and recovered within a session is recorded
  as having been stopped out at a price it reached the following day. Whether that is
  conservative depends on the path and is not knowable from daily data.
- **the gap through a stop is real and is not what the sizer assumed.** `risk_pct` divides
  the risk budget by the stop distance, which assumes the loss equals that distance. With
  a close-to-close fill it routinely exceeds it, so the position taken is larger than the
  budget intended. Tested in `TestFirstTrigger`.

Changing this needs intraday option quotes, which is a data problem rather than a code
one. Recorded here because the difference is invisible in a result: a backtest run this
way looks exactly like one run the document's way.

---

## One known deviation from the architecture document

The document's daily order is: corporate actions → market data → signals → portfolio →
orders → fills → expiry → **accounting**. `engine/loop.py` marks the book to market at
step 5, *before* the strategy runs, rather than last, and has **no corporate-action step
at all**.

Under the current one-day execution lag these are equivalent: nothing changes the
portfolio after step 5, so marking there and marking last give the same equity. It stops
being equivalent the moment an intraday fill lands on the same day as its signal — then
the mark is taken before a fill that belongs in it, and the equity curve is wrong by that
fill with nothing raising.

Recorded rather than fixed because fixing it means moving accounting after expiry, which
changes the daily snapshot order that the invariant checks and the shadow tracking both
hang off. It is criterion 1.32, and the condition that makes it urgent is intraday
execution, which does not exist yet.

---

## Defined once, generated elsewhere

Already done:

| The fact | Where it is defined | What is generated from it |
|---|---|---|
| What the seven components are and what they ask for | `COMPONENTS` in `backtest/core/slots.py` | the interface's form, the gap list, `tools/status.py` |
| The dependency layers | `LAYERS` in `tools/check_deps.py` | the check itself |
| Each signal's meaning and unit | `SIGNAL_META` in `signals/registry.py` | the interface reference, `describe_signals()` |
| Which columns wiring in data needs | `SCHEMA` in `data/adapters.py` | the health check, the documentation table |

**Not yet done, recorded honestly**: the statuses of the acceptance criteria in
`requirements.md` are hand-written. Criteria like "the interface is clear" or "the
statistics are honest" cannot be judged automatically — so `tools/status.py` only counts
them and **says they are hand-written**, rather than pretending they were computed.

---

## More than one session at a time

**Commit what you changed. Nothing else. Ever.**

More than one session works in this repository at once — one on the engine, one on the
interface, sometimes a third on the tests. They share a working tree, so every file either
of them has touched is visible to both, and `git add -A` does not know which is whose.

It has gone wrong in both directions, within one afternoon:

- a session finishing engine work ran `git add -A && git commit` and swept a half-finished
  interface rebuild into a commit whose message was about component state
- the session doing the interface ran `git add tools/` and committed the other's
  469-line mutation-testing guard under a message about CSS tokens

Nothing was lost either time — it is all in git — but the history now says things that are
not true, and the history is the only account of why anything was done. A commit message
that describes a third of its own diff is worse than no message: the next person reads it,
believes it, and stops looking.

So:

```bash
python -m tools.check          # the guards
python -m tools.status         # a look at the project's state (computed, so it cannot go stale)

git add path/to/each/file      # explicit paths, never -A and never a bare directory
git diff --cached --name-only  # read it. Is every one of these yours?
git commit
git push
```

`git add -A` and `git add <directory>` are the two that cause this, and a directory is the
sneakier of them: `tools/` looks specific and is not. If a file you did not write is already
staged, `git restore --staged <path>` puts it back; leave it for whoever wrote it.

**The broad forms are now refused rather than remembered.** A guard still cannot know
which session wrote which line — nothing can — so the part that rests on a person is
unchanged: **read the staged list before committing.** What a tool *can* do is refuse the
two commands that make the mistake possible, and
[`tools/hooks/refuse_broad_git_add.py`](tools/hooks/refuse_broad_git_add.py) does:
`git add -A`, `--all`, `-u`, `.`, `:/`, and any pathspec that is a directory. It allows
explicit file paths, `--dry-run`, `-p`, and a pathspec that does not exist on disk, and it
fails open on its own bugs — a hook that blocked every command in the repository because
of a bug in itself would be worse than the mistake it guards.

Each builder working in **its own worktree and branch** removes the shared tree that makes
this possible at all; see the worktree workflow in [CLAUDE.md](CLAUDE.md). The hook is for
the case where sessions do share a tree, which is still the ordinary case.

Write **why** in the commit message, not only what changed. What you will want to know in
six months is why it was decided this way, not what the diff already shows.

---

## The guards that are not part of `tools.check`

Three of them, each outside the fixed check because of *when* it has to run rather than
what it reads. Same bar as any other guard: a concrete failure first, then the check,
verified in reverse.

| Guard | What it catches | The failure it answers to |
|---|---|---|
| [`tools/hooks/refuse_broad_git_add.py`](tools/hooks/refuse_broad_git_add.py) · a Claude Code `PreToolUse` hook on Bash, registered in `.claude/settings.json` | a `git add` whose pathspec names more than the caller chose: `-A`, `--all`, `-u`, `.`, `:/`, a directory | `git add -A` swept a half-finished interface rebuild into a commit about component state; `git add tools/` committed another session's 469-line guard under a message about CSS tokens. The history then says things that are not true, and the history is the only account of why anything was done |
| [`tools/githooks/pre-commit`](tools/githooks/pre-commit) · enabled per clone with `git config core.hooksPath tools/githooks` | a commit made while a guard is red. Runs `tools.check --fast` and refuses on failure | "any failure means no commit" was held by whoever remembered to run the guards. That is the state the first line of this file calls a wish — and this project has the evidence: three guards in `tools/check.py` were written and never registered there, so they ran only when someone thought of them |
| [`.github/workflows/check.yml`](.github/workflows/check.yml) · the **full** check on every push and pull request | anything that only fails on a clean clone | `backtest/research/` was excluded by a `.gitignore` rule missing its leading slash: six modules unpublished, their tests pushed, every local guard green and `git status` clean. Only a clone on another machine shows that class of failure |

Why each is outside `tools.check` rather than in it: the first has to run **before** a
command, which no checker invoked afterwards can do; the second has to run **at** the
commit; the third has to run **somewhere else**, on a checkout into an empty directory.
`tools.check` itself cannot do any of the three, and the two that wrap it call it rather
than duplicate it.

Both hooks are verified in reverse. The `git add` one keeps its verification as a test —
`tests/test_git_add_hook.py`, 69 cases driven through the real interface, a JSON payload on
stdin and an exit code out — because the forms it must refuse are a list, and a list
belongs where it is re-run. It earned that: it refused its own first real use, reading a
`git add -A` quoted inside a commit message's here-document as a command. A here-document
body is data, and the documents here quote that form on nearly every page about committing.
