# `tests/` — what the guards rest on

[docs/conventions.md](../docs/conventions.md) is the rulebook; this is what applies here in
particular. Read the rulebook first — this file does not repeat it.

## Three markers on every test

```python
from tests.kinds import labels
pytestmark = labels("guard", "cross")       # kind, layer
...
@pytest.mark.req("0.4.cash")                 # the requirement it claims
```

- **`kind`** — what a failure *means*. `unit`, `guard`, `consistency`, `known_answer`,
  `research`, `architecture`, `limit`, `baseline`. See `kinds.py` for each.
- **`layer`** — where it is, by the architecture document's own numbering: `0`–`7`, or
  `cross` for the few that span layers by nature.
- **`req`** — the requirement in `docs/requirements.md` it claims to cover.

`test_labels.py` requires every test to resolve to **exactly one** kind and one layer. A
module declares its own with `pytestmark`; a class or a test overrides where it differs, and
**a module needing many overrides is a module holding several subjects** — that count is the
measurement for splitting a file, which is evidence rather than taste.

### Why `kind` separates `guard` from `unit`

A unit failure is a bug: the code computes the wrong number. **A guard failure is a bug
detector going dark** — the thing it guards may be perfectly fine, and the next real error
will now pass unseen. They call for different responses, and the layer cannot tell you
which you have.

`limit` is the other one worth knowing: a known gap, asserted as still being there. When
the gap closes the test fails and tells you to delete it, which is the only way a recorded
limitation stops being a comment nobody re-reads.

### What a `req` claim is, and is not

It is **not** evidence that the requirement holds — nothing automatic can be. It is
evidence that somebody looked. The failure it prevents is not "the test was wrong", it is
"there was no test and nobody knew". `test_coverage_map.py` reads the markers out of the
source rather than the running session, because an answer that depends on which subset is
running is not an answer.

A requirement the system does not meet carries `not_built` with the reason, so "uncovered
because it is absent" and "uncovered because nobody looked" are different answers rather
than the same silence.

## Everything passes offline

Through the fixtures in `conftest.py`. A test that reaches the network is a test that fails
on a train and in CI, and a suite that fails for reasons unrelated to the code is a suite
people stop reading. The synthetic chain is the default fixture.

**It is a fixture, not a data source.** Constant IV, no skew, no term structure — so a test
asserting an option-side number on it (which delta is best, where to take profit) is
asserting the fixture's own arithmetic, not a property of anything. Underlying-side
assertions are fine.

## The baseline is never updated to make a change pass

`python -m tools.baseline update` exists for the case where results are *meant* to change,
and that case is a deliberate, separately reasoned act. If the baseline goes red and you
did not intend to change the results, **you changed behaviour**: find it and revert it.
Updating the baseline to clear a red guard is the one move this project refuses outright,
because it destroys the only record that the results used to be different.

`--fast` records itself as *not* passing, for the same reason: it skipped the slow guards,
so it cannot say the code is green, and saying so would be the quietest way to lose the
distinction. `backtest.core.run_id.guards_were_green` reports three states — green, red,
and **no record** — and no record is the ordinary state on a machine where nothing has run.

## Writing one

- **The failing test first.** For a bug, the test that reproduces it; for a feature, the
  test that would prove it. A test written after the code passes tends to assert what the
  code does rather than what was wanted.
- **Verify a guard in reverse.** Plant the error, confirm it fails, remove it, confirm it
  passes — and where the forms it must catch are a list, keep the list as a test rather
  than as a paragraph in a commit message. `test_git_add_hook.py` is the pattern: it drives
  the hook's real interface, 69 cases in both directions.
- **Assert the message, not only the raise.** A refusal that does not say what to do
  instead gets the same command retried.
- Run `python -m pytest tests -q` (about 13 minutes), or one file while working.
