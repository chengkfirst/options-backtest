"""The cleaning stage — the Layer 1 pipeline step that was on no list of mine.

## Why it was missing and why it matters

The plan's Layer 1 is a ten-stage pipeline and `clean` is the third. I had transcribed the
document's **conformance table** — one row per section — and not its own submodule
tables, so four stages including this one were never on any list. See
`tests/plan_components.py`.

`check_frame` **reports**: it finds the wide spreads, the locked markets, the asks below
intrinsic. Nothing acted on it, so between "this data has 2,000 wide-spread rows" and
"the run used them" there was nothing at all — a dataset was refused whole or used whole.

## What the register says about it

Data-cleaning rules are a **group-1 choice**: sensitivity tests only, never optimised.
The row's own warning is that cleaner data "may remove stressed periods and bias results
toward calm markets", and the test it prescribes is "compare results with and without,
focusing on stress periods".

That comparison needs cleaning to be a thing that can be **switched off**, which is why
no rule is on by default and why `Cleaning()` with no rules is asserted below to change
nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.core.errors import StartupError
from backtest.data.options.clean import (
    UNTRADABLE_FLAG,
    Cleaning,
    Rule,
    below_intrinsic,
    crossed_quote,
    no_quote,
    wide_spread,
)

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "1")


def _frame(**over) -> pd.DataFrame:
    """Five rows: healthy, no quote, crossed, below intrinsic, wide spread."""
    base = pd.DataFrame({
        "bid": [1.00, 0.00, 2.00, 5.00, 1.00],
        "ask": [1.05, 0.00, 1.50, 5.20, 3.00],
        "strike": [400.0, 400.0, 400.0, 380.0, 400.0],
        "underlying_price": [400.0] * 5,
        "right": ["C"] * 5,
    })
    for key, value in over.items():
        base[key] = value
    return base


ALL_RULES = (crossed_quote(), below_intrinsic(), no_quote(),
             wide_spread(max_share_of_mid=0.20))


class TestEachRuleFindsItsOwnCase:
    def test_a_crossed_quote_is_dropped_and_not_marked(self):
        """The one case where dropping is right: a bid above an ask is not a worse
        price, it is **arithmetically impossible**. Kept, it leaves a negative spread in
        every statistic computed over the dataset."""
        kept, report = Cleaning(rules=(crossed_quote(),)).apply(_frame())
        assert report.dropped == 1 and report.marked == 0
        assert (kept["ask"].astype(float) >= kept["bid"].astype(float)).all()

    def test_a_row_with_no_two_sided_quote_is_marked_and_never_filled_forward(self):
        """The document's own instruction. Forward-filling is what makes a halted
        contract look tradable at yesterday's price."""
        kept, report = Cleaning(rules=(no_quote(),)).apply(_frame())
        assert report.marked == 1 and report.dropped == 0
        marked = kept.loc[kept["quality_flags"].str.contains(UNTRADABLE_FLAG)]
        assert len(marked) == 1
        # The row keeps its own values -- nothing was written into it.
        assert float(marked["bid"].iloc[0]) == 0.0

    def test_a_price_below_intrinsic_is_dropped(self):
        """Against the **ask**: a price you could have paid below intrinsic is free
        money, so the row is bad data. A bid below intrinsic is ordinary and must not
        match -- nobody is obliged to bid fair value."""
        kept, report = Cleaning(rules=(below_intrinsic(),)).apply(_frame())
        assert report.dropped == 1

        # A deep in-the-money row with a wide spread: ask above intrinsic, bid below.
        wide = pd.DataFrame({"bid": [18.0], "ask": [21.0], "strike": [380.0],
                             "underlying_price": [400.0], "right": ["C"]})
        _, ordinary = Cleaning(rules=(below_intrinsic(),)).apply(wide)
        assert ordinary.dropped == 0, (
            "the rule is reading the bid, so it drops every wide spread on a deep "
            "in-the-money contract"
        )

    def test_a_wide_spread_is_marked_rather_than_dropped(self):
        """Marked is the weaker action and the better default: a dropped row is
        invisible downstream, including to the count of what was dropped, while a marked
        one still carries its prices so research can see what was excluded."""
        kept, report = Cleaning(rules=(wide_spread(max_share_of_mid=0.20),)).apply(
            _frame())
        assert report.marked >= 1 and report.dropped == 0
        assert len(kept) == 5


@kind("guard")
class TestWhatTheStageRefusesToDo:
    def test_no_rule_is_on_by_default(self):
        """**The register's requirement, as a mechanism.**

        A platform-chosen cleaning rule is the platform deciding which market conditions
        a strategy is tested through, and the register's point about this group is that
        the choice biases the result in a direction nobody notices. So no rules means no
        change, and that is the state every existing run is in.
        """
        frame = _frame()
        kept, report = Cleaning().apply(frame)
        assert kept.equals(frame), "cleaning with no rules changed the data"
        assert report.dropped == 0 and report.marked == 0
        assert not report.outcomes
        assert "no cleaning rules were given" in report.text()

    def test_nothing_writes_a_price(self):
        """A rule drops a row or marks it; a cleaned price is a price somebody made up,
        and the one thing worse than bad data is bad data that looks fine."""
        frame = _frame()
        kept, _ = Cleaning(rules=ALL_RULES).apply(frame)
        for _, row in kept.iterrows():
            original = frame.loc[(frame["bid"] == row["bid"])
                                 & (frame["ask"] == row["ask"])]
            assert len(original), (
                f"a kept row has bid {row['bid']} / ask {row['ask']}, which is not any "
                f"row the source supplied"
            )

    def test_a_rule_with_no_reason_is_refused(self):
        """A dataset that lost rows to a rule nobody can name has a hole in its
        provenance, and the with-and-without comparison cannot be read without it."""
        with pytest.raises(StartupError) as caught:
            Rule(name="mystery", matches=lambda f: np.zeros(len(f), bool),
                 action="drop", reason="")
        assert "has no reason" in str(caught.value)

    def test_an_action_outside_the_two_is_refused(self):
        with pytest.raises(StartupError):
            Rule(name="x", matches=lambda f: np.zeros(len(f), bool),
                 action="fix_the_price", reason="because")

    def test_a_matcher_returning_the_wrong_shape_is_refused(self):
        """A matcher returning one value for a frame would silently mark everything or
        nothing, depending on how numpy broadcast it."""
        with pytest.raises(StartupError) as caught:
            Cleaning(rules=(Rule(name="bad", matches=lambda f: np.array([True]),
                                  action="drop", reason="because"),)).apply(_frame())
        assert "one boolean per row" in str(caught.value)


class TestTheReport:
    def test_a_row_two_rules_match_is_attributed_to_the_first(self):
        """So the figures sum to the total rather than double-counting. The second rule
        says how many it matched **and** how many were already handled, because a reader
        comparing rule sets needs both."""
        kept, report = Cleaning(rules=ALL_RULES).apply(_frame())
        acted = sum(o.acted for o in report.outcomes)
        assert acted == report.dropped + report.marked
        later = [o for o in report.outcomes if o.matched > o.acted]
        assert later, "no rule overlapped another, so this test checks nothing"
        assert "already handled by an earlier rule" in later[0].describe()

    def test_it_says_cleaning_is_a_sensitivity_question(self):
        """The register's instruction travels with the number. A share of rows touched,
        with no statement of what it costs, reads as a quality score."""
        _, report = Cleaning(rules=ALL_RULES).apply(_frame())
        text = report.text()
        assert "never optimise" in text
        assert "stressed periods" in text
        assert "with these rules off" in text

    def test_it_is_carried_as_data_too(self):
        _, report = Cleaning(rules=ALL_RULES).apply(_frame())
        out = report.as_dict()
        assert out["rows_in"] == 5
        assert {r["name"] for r in out["rules"]} == {o.name for o in report.outcomes}
        assert all(r["reason"] for r in out["rules"])


@kind("guard")
class TestTheMarkRefusesAFill:
    """A flag nothing reads is a cleaning rule that reports and does not clean.

    The stage writes `untradable:<rule>` into `quality_flags`; `check_tradable` is the one
    place an order is refused, and it had never read the column. So before this, a marked
    row filled exactly as an unmarked one did -- the rule ran, the report said rows were
    marked, and the backtest traded them anyway.
    """

    def test_a_marked_row_is_not_tradable(self):
        from backtest.core.config import CostConfig
        from backtest.execution.costs import check_tradable

        costs = CostConfig()
        clean = check_tradable(1.00, 1.05, costs, flags="")
        marked = check_tradable(1.00, 1.05, costs,
                                 flags=f"{UNTRADABLE_FLAG}:wide_spread>20%")
        assert clean.ok, "a healthy quote was refused, so this test proves nothing"
        assert not marked.ok
        assert "cleaning rule" in (marked.reason or "")
        assert "wide_spread" in (marked.reason or ""), (
            "the refusal does not name the rule, so a reader cannot tell which rule "
            "removed the trade"
        )

    def test_an_unrelated_flag_does_not_refuse_a_fill(self):
        """`quality_flags` carries other things -- `iv_unsolved`, `early_exercise_value`
        -- and none of them means untradable. Matching the column rather than the word
        would stop trading on every row whose IV failed to solve."""
        from backtest.core.config import CostConfig
        from backtest.execution.costs import check_tradable

        for flag in ("iv_unsolved", "early_exercise_value", "stale"):
            assert check_tradable(1.00, 1.05, CostConfig(), flags=flag).ok, flag

    def test_both_engines_read_it(self):
        """The two engines being row-identical is the cross-check this project rests on.
        Reading the flag in one and not the other would diverge on any cleaned dataset,
        and the divergence would look like a cleaning effect."""
        import pathlib

        for name in ("backtest/engine/loop.py", "backtest/engine/vectorized.py"):
            source = pathlib.Path(name).read_text(encoding="utf-8")
            calls = source.count("check_tradable(")
            passes = source.count("flags=str(")
            assert calls == passes, (
                f"{name} calls check_tradable {calls} time(s) and passes the flags "
                f"{passes} time(s). A call without them fills a row a rule removed"
            )
