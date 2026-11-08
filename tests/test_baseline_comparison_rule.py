"""One comparison rule, written twice — requirement 0.4.regression.

`tools/baseline.py`'s ``compare`` and `tests/baseline_configurations.py`'s ``difference``
implement the same rule for "did the result move": **a trade count first, then every
compared column with the numbers rounded to 8 places.** Four expressions each, differing
only in a variable name.

## Why they are separate, and why that needs a test rather than a comment

The duplication is deliberate and argued where it lives: the first configuration's reader
has read `baseline_trades.csv` since it was written, and factoring the two together would
put a file that must not move behind code that had just changed. That argument protects
the recorded file from a refactor. **It does nothing about drift.**

Drift here fails in the quietest way available. Change the rounding place in one, or add a
tolerance to one, and the two recorded baselines are thereafter compared under different
rules — with both guards still green, because each still agrees with its own file. Nothing
in the suite would notice until a real difference fell between the two rules, and then only
one of the two configurations would report it.

So the rule is held by a test instead: both comparators are driven over the **same
constructed pair of frames**, and required to give the **same answer**. Each case is placed
to sit on one clause of the rule, so a one-sided edit to that clause makes the two
disagree:

- a difference below the eighth decimal place, and one above it — the rounding place, and
  any tolerance wider than it
- a difference in a column with no numbers in it — the non-numeric half of the comparison
- one trade fewer — the count check, and that it comes first

## How the pair reaches the real comparators

Neither ``compare`` takes a pair of frames; each reads its recorded file and runs its own
build. So the constructed pair is put where each of them looks: written to a file both are
pointed at, and handed back by the builder each of them calls. **Their bodies are driven
unchanged** — that is the point, since the bodies are what must not drift. Nothing here
touches either recorded baseline, and no backtest runs.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

import tools.baseline as tool
from tests import baseline_configurations as event_driven
from tests.kinds import labels

#: A failure here means the two implementations of one rule have forked -- which is
#: ``consistency``, not ``baseline``: no recorded result has moved, and the thing that
#: would notice if one did has quietly changed shape.
pytestmark = labels("consistency", "cross")

#: Two rows with every compared column filled, shaped like the recorded files: dates as
#: `date` so they survive the write-and-read both comparators do, and the numeric columns
#: numeric so `select_dtypes("number")` picks out the same set on either side.
TWO_TRADES = [
    dict(trade_id=1, entry_date=date(2023, 1, 3), exit_date=date(2023, 1, 13),
         contract="QQQ_20230217C00300000", quantity=1,
         entry_price=5.0, exit_price=6.0, gross_pnl=100.0, fees=1.30, net_pnl=98.70,
         exit_reason="take_profit", holding_days=10),
    dict(trade_id=2, entry_date=date(2023, 2, 1), exit_date=date(2023, 2, 8),
         contract="QQQ_20230317C00310000", quantity=2,
         entry_price=4.0, exit_price=2.0, gross_pnl=-400.0, fees=2.60, net_pnl=-402.60,
         exit_reason="stop_loss", holding_days=7),
]

#: Where the rounding sits. The rule rounds to 8 places, so a difference of 1e-9 is one
#: the rule is meant to swallow and a difference of 1e-7 is one it is meant to report.
#: Written from the place itself rather than as two literals, so a reader can see that the
#: two cases straddle it.
ROUNDED_TO = 8
BELOW_THE_ROUNDING = 10.0 ** -(ROUNDED_TO + 1)
ABOVE_THE_ROUNDING = 10.0 ** -(ROUNDED_TO - 1)


def _frame(rows: list[dict]) -> pd.DataFrame:
    """A trade log holding exactly the compared columns, in the recorded order."""
    return pd.DataFrame(rows)[tool.COMPARED]


def _with(changes: dict[str, object]) -> pd.DataFrame:
    """`TWO_TRADES` with the second trade's fields changed -- copied, so the case table
    cannot alter the pair every other case is built from."""
    altered = [dict(row) for row in TWO_TRADES]
    altered[-1].update(changes)
    return _frame(altered)


#: ``(what differs, the current run, the answer both comparators must give)``.
#:
#: The answer is either ``None`` for "nothing moved", a tuple of the positions that moved,
#: or the text of the count issue. Spelled out rather than computed from one of the
#: comparators, so a case cannot agree with a rule that has changed.
CASES = [
    pytest.param("nothing differs at all", _frame(TWO_TRADES), None,
                 id="unmoved"),
    pytest.param(f"net_pnl differs by {BELOW_THE_ROUNDING:g}, under the rounding",
                 _with({"net_pnl": -402.60 + BELOW_THE_ROUNDING}), None,
                 id="under-the-rounding"),
    pytest.param(f"net_pnl differs by {ABOVE_THE_ROUNDING:g}, over the rounding",
                 _with({"net_pnl": -402.60 + ABOVE_THE_ROUNDING}), (1,),
                 id="over-the-rounding"),
    pytest.param("exit_reason differs -- a column with no numbers in it",
                 _with({"exit_reason": "time_exit"}), (1,),
                 id="non-numeric-column"),
    pytest.param("the trade count differs, and one row's net_pnl with it",
                 _frame([dict(TWO_TRADES[0], net_pnl=0.0)]), "trade count 2 -> 1",
                 id="trade-count-first"),
]


def _both_answers(current: pd.DataFrame, stored: pd.DataFrame,
                  recorded_file, monkeypatch) -> tuple:
    """Each comparator's answer on the same pair, from its own unmodified body.

    The pair is installed the only way either comparator can receive one: ``stored`` is
    written to a file both are pointed at, and ``current`` is returned by the builder each
    of them calls. A copy per call, because a comparator returns a slice of what its
    builder gave it and two comparators must not be handed one object to slice.
    """
    stored.to_csv(recorded_file, index=False, date_format="%Y-%m-%d")

    monkeypatch.setattr(tool, "BASELINE_FILE", recorded_file)
    monkeypatch.setattr(tool, "build_trades", lambda: current.copy())
    monkeypatch.setattr(event_driven, "EVENT_DRIVEN_FILE", recorded_file)
    monkeypatch.setattr(event_driven, "build_event_driven_trades",
                        lambda: current.copy())

    return tool.compare(), event_driven.compare()


def _expected(answer, current: pd.DataFrame):
    """The case's answer as the frame a comparator would return, or ``None``."""
    if answer is None:
        return None
    if isinstance(answer, str):
        return pd.DataFrame({"issue": [answer]})
    return current.iloc[list(answer)]


def _describe(answer) -> str:
    if answer is None:
        return "nothing moved"
    return "\n" + answer.to_string(index=False)


@pytest.mark.parametrize("differs, current, answer", CASES)
@pytest.mark.req("0.4.regression")
def test_the_two_comparators_give_one_answer(differs, current, answer,
                                             tmp_path, monkeypatch):
    """`tools.baseline.compare` and `tests.baseline_configurations.difference` are the
    same rule written twice. On one pair they answer the same thing, or the two recorded
    baselines are being compared under two rules.
    """
    from_tool, from_tests = _both_answers(
        current, _frame(TWO_TRADES), tmp_path / "constructed.csv", monkeypatch)

    assert (from_tool is None) == (from_tests is None), (
        f"the two baseline comparators disagree on whether anything moved when "
        f"{differs}.\n"
        f"  tools/baseline.py compare():               {_describe(from_tool)}\n"
        f"  tests/baseline_configurations.py difference(): {_describe(from_tests)}\n"
        f"They implement one rule -- a trade count first, then every compared column "
        f"rounded to {ROUNDED_TO} places -- and one of the two has changed without the "
        f"other. Until they agree, the two recorded baselines are compared under "
        f"different rules and both guards can still be green."
    )
    if from_tool is not None:
        pd.testing.assert_frame_equal(
            from_tool, from_tests, obj=f"the rows reported when {differs}")


@pytest.mark.parametrize("differs, current, answer", CASES)
@pytest.mark.req("0.4.regression")
def test_that_one_answer_is_the_rule_as_written_down(differs, current, answer,
                                                     tmp_path, monkeypatch):
    """And the agreed answer is the rule the modules document, not merely some shared
    answer. Without this, both could be edited the same way and the agreement above would
    still hold while the rule had moved under both files at once.
    """
    answers = _both_answers(
        current, _frame(TWO_TRADES), tmp_path / "constructed.csv", monkeypatch)
    wanted = _expected(answer, current)

    for name, got in zip(("tools/baseline.py compare",
                          "tests/baseline_configurations.py difference"), answers):
        assert (got is None) == (wanted is None), (
            f"{name} answers {_describe(got)} when {differs}, and the rule says "
            f"{_describe(wanted)}. The comparison rule is a trade count first, then "
            f"every compared column rounded to {ROUNDED_TO} places; if it is now "
            f"something else, say so in both modules and here, because a rounding place "
            f"decides which differences a baseline can report at all."
        )
        if wanted is not None:
            pd.testing.assert_frame_equal(got, wanted, obj=f"{name}, when {differs}")
