# Working conventions

> This file is independent of `Plan/backtest-system-architecture.pdf` (the architecture)
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
| **wiring** | the interface declares `CONTRACT` in `bench.js`, checked against a real response | fast |
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
   `app/static/bench.js`
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
| **The platform never defaults a strategy choice** | the document permits optimising a contract delta or an exit level under its protocol; it does not permit the platform picking one. A default decides the search and then reports its winner as a finding | `strategy_parameters_are_never_defaulted()`, asserted by a test |
| **A slot's logic never calls another slot** | the architecture requires every influence to pass through the trade context and the action vocabulary; direct calls are what make per-slot ablation impossible | a person, until the component protocol exists |

---

## Defined once, generated elsewhere

Already done:

| The fact | Where it is defined | What is generated from it |
|---|---|---|
| What the seven components are and what they ask for | `backtest/components.py` | the interface's form, the gap list, `tools/status.py` |
| The dependency layers | `LAYERS` in `tools/check_deps.py` | the check itself |
| Each signal's meaning and unit | `SIGNAL_META` in `signals/registry.py` | the interface reference, `describe_signals()` |
| Which columns wiring in data needs | `SCHEMA` in `data/adapters.py` | the health check, the documentation table |

**Not yet done, recorded honestly**: the statuses of the acceptance criteria in
`requirements.md` are hand-written. Criteria like "the interface is clear" or "the
statistics are honest" cannot be judged automatically — so `tools/status.py` only counts
them and **says they are hand-written**, rather than pretending they were computed.

---

## Before committing

```bash
python -m tools.check      # the guards
python -m tools.status     # a look at the project's state (computed, so it cannot go stale)
git add -A && git commit
git push
```

Write **why** in the commit message, not only what changed. What you will want to know in
six months is why it was decided this way, not what the diff already shows.
