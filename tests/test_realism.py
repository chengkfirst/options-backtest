"""Lambda sensitivity: the run the document asks for, and the choice it forbids.

The document's first research rule has two clauses pulling against each other -- a
realism assumption is tested for sensitivity, and never optimised. The run has to exist
and must not be readable as a recommendation.

So the tests here are of two kinds: the arithmetic of the shape, and the **absences** --
no best fraction, no sweep that omits the honest one, no verdict invented where the honest
run was not made. An absence is the harder thing to test and the easier thing to lose.
"""

from __future__ import annotations

import pytest

from backtest.core.errors import StartupError
from backtest.research.realism import (
    DOCUMENT_FRACTIONS,
    HONEST,
    Point,
    Sensitivity,
    sensitivity,
)

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("research", "6")


def _runner(table):
    """fraction -> (P&L, trades), from a table, so the shape under test is written here
    rather than produced by a run that could change underneath it."""
    return lambda f: table[f]


def _from(pairs) -> Sensitivity:
    return Sensitivity(points=tuple(Point(fraction=f, net_pnl=p, trades=10)
                                    for f, p in pairs))


class TestTheShape:
    def test_each_fraction_is_run_once_and_in_order(self):
        seen = []

        def runner(f):
            seen.append(f)
            return (100.0, 1)

        got = sensitivity(runner, fractions=(1.5, 0.5, 1.0))
        assert seen == [0.5, 1.0, 1.5], "the table is read in the order it is printed in"
        assert [p.fraction for p in got.points] == [0.5, 1.0, 1.5]

    def test_per_trade_is_the_total_over_the_count(self):
        got = sensitivity(_runner({1.0: (900.0, 9)}), fractions=(1.0,))
        assert got.points[0].per_trade == pytest.approx(100.0)

    def test_no_trades_is_not_a_division(self):
        """Zero trades at some fraction is a real outcome -- a rule that stops trading
        when costs rise -- and must not raise."""
        got = sensitivity(_runner({1.0: (0.0, 0)}), fractions=(1.0,))
        assert got.points[0].per_trade != got.points[0].per_trade  # nan


class TestTheVerdictIsTheDocuments:
    def test_it_holds_when_every_run_at_or_above_one_makes_money(self):
        got = _from([(0.5, 1500.0), (1.0, 1000.0), (1.5, 500.0)])
        assert got.holds() is True
        assert "holds at lambda = 1" in got.read()

    def test_it_does_not_hold_when_a_run_at_or_above_one_loses(self):
        """The case the whole rule exists for: good at the mid, gone at the quote."""
        got = _from([(0.5, 1500.0), (1.0, 200.0), (1.5, -400.0)])
        assert got.holds() is False
        verdict = got.read()
        assert "does not hold" in verdict and "1.5" in verdict
        assert "-400" in verdict, "the figure that fails is not in the sentence"

    def test_a_run_at_one_alone_is_enough_to_answer(self):
        got = _from([(1.0, 1000.0)])
        assert got.holds() is True

    def test_profit_below_one_does_not_count_towards_holding(self):
        """0.5 making money is not evidence of anything: it is a fill between the bid
        and the ask, which no order gets."""
        got = _from([(0.5, 5000.0), (1.0, -10.0)])
        assert got.holds() is False


class TestWhereItRefusesToSpeak:
    """The absences. Each of these would be a plausible-looking field or sentence, and
    each would turn a sensitivity run into the optimisation the rule forbids."""

    @pytest.mark.req("R1.never_optimised")
    def test_no_sweep_without_the_honest_fraction(self):
        """0.3, 0.5, 0.7 is not a sensitivity run. It is a search for a figure that makes
        the strategy work, and it is what someone rescuing a result would write."""
        with pytest.raises(StartupError) as caught:
            sensitivity(_runner({}), fractions=(0.3, 0.5, 0.7))
        assert "1" in str(caught.value) and "full adverse side" in str(caught.value)

    def test_no_empty_sweep(self):
        with pytest.raises(StartupError):
            sensitivity(_runner({}), fractions=())

    def test_no_negative_fraction(self):
        with pytest.raises(StartupError):
            sensitivity(_runner({}), fractions=(-0.5, 1.0))

    def test_an_unanswered_question_is_not_a_failure(self):
        """A sweep that skipped 1.0 has not answered the question. Rendering that as
        "does not hold" would be inventing a result, pessimistic direction or not."""
        got = _from([(0.5, 1500.0), (0.8, 900.0)])
        assert got.holds() is None
        assert "unanswered" in got.read()

    @pytest.mark.req("R1.never_optimised")
    def test_nothing_in_the_result_names_a_best_fraction(self):
        """The field is the thing to leave out, not the recommendation.

        A ``best_lambda`` -- or an ``optimal``, or a ``recommended`` -- would be the
        optimisation the document forbids wearing the clothes of a measurement. Checked
        against the real attribute names rather than trusted to review.
        """
        got = _from([(0.5, 1500.0), (1.0, 1000.0), (1.5, 500.0)])
        names = set(dir(got)) | set(dir(got.points[0]))
        forbidden = {n for n in names
                     if any(word in n.lower()
                            for word in ("best", "optimal", "recommend", "preferred",
                                         "choose", "chosen", "pick"))}
        assert not forbidden, (
            f"these read as a choice of realism assumption: {sorted(forbidden)}"
        )

    def test_the_text_does_not_recommend_one_either(self):
        got = _from([(0.5, 1500.0), (1.0, 1000.0), (1.5, 500.0)])
        text = got.text(synthetic=False).lower()
        for word in ("best", "optimal", "recommend", "should use"):
            assert word not in text, f"the report says {word!r} about a lambda"


class TestTheReport:
    def test_it_marks_the_honest_fraction(self):
        got = _from([(0.5, 1500.0), (1.0, 1000.0), (1.5, 500.0)])
        text = got.text(synthetic=False)
        honest_line = next(l for l in text.splitlines() if "1.00" in l)
        assert "the honest one" in honest_line

    def test_it_says_which_of_the_documents_fractions_were_skipped(self):
        got = _from([(1.0, 1000.0)])
        text = got.text(synthetic=False)
        assert "0.5" in text and "1.5" in text, (
            "the report does not say the document's other two fractions were not run, so "
            "a partial sweep reads as a complete one"
        )

    def test_a_complete_sweep_says_nothing_was_skipped(self):
        got = _from([(f, 1000.0) for f in DOCUMENT_FRACTIONS])
        assert "was not run" not in got.text(synthetic=False)

    def test_the_synthetic_boundary_comes_before_the_numbers(self):
        """The rule for every report in this layer: a reader who stops after the first
        screen must already know what the figures do not cover."""
        text = _from([(f, 1000.0) for f in DOCUMENT_FRACTIONS]).text(synthetic=True)
        mark = text.index("synthetic")
        first_number = text.index("1,000")
        assert mark < first_number, (
            "the synthetic-chain limit is printed after the figures it limits"
        )
