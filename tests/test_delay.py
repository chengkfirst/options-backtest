"""The delay test: the second of the document's two defences against look-ahead.

The data view's cut is structural -- it stops a read that should not happen. This is
empirical, and it catches what a cut cannot: a signal that is legitimately readable and
whose edge lives entirely in acting on it immediately. Every read is in range, and the
result is still not reachable.

It is deliberately not a verdict. A real short-horizon effect decays fast and so does an
artefact; the shape cannot separate them, so the tool returns the shape.
"""

from __future__ import annotations

import pytest

from backtest.core.errors import StartupError
from backtest.research.delay import Decay, Step, decay

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("research", "6")


def _runner(table):
    """lag -> (P&L, trades), from a table, so the shape under test is the one written."""
    return lambda lag: table[lag]


class TestTheShape:
    @pytest.mark.req("0.4.delay")
    def test_a_result_that_survives_delay_is_read_as_such(self):
        got = decay(_runner({1: (1000.0, 50), 2: (900.0, 50), 3: (850.0, 50)}),
                    base_lag=1, extra=(1, 2))
        assert got.retained(got.steps[-1]) == pytest.approx(0.85)
        assert "does not depend much" in got.read()

    def test_a_result_that_vanishes_says_it_rested_on_being_quick(self):
        got = decay(_runner({1: (1000.0, 50), 2: (100.0, 50), 3: (0.0, 50)}),
                    base_lag=1, extra=(1, 2))
        assert "rested on being quick" in got.read()

    def test_a_partial_decline_refuses_to_choose_between_the_two_explanations(self):
        """A real short-horizon effect looks exactly like an artefact here, and saying
        otherwise would be inventing a verdict the data cannot support."""
        got = decay(_runner({1: (1000.0, 50), 2: (600.0, 50), 3: (300.0, 50)}),
                    base_lag=1, extra=(1, 2))
        read = got.read()
        assert "artefact" in read and "short-horizon effect" in read

    def test_the_survivor_sentence_does_not_claim_the_edge_is_real(self):
        """Surviving a delay is necessary and nowhere near sufficient."""
        got = decay(_runner({1: (1000.0, 50), 2: (950.0, 50)}), base_lag=1, extra=(1,))
        assert "is not evidence that it is one" in got.read()


class TestWhereItRefusesToSpeak:
    def test_a_losing_base_run_is_not_read_as_robust(self):
        """A loss that shrinks with delay has 'retained' below 100%, which reads as decay
        and is the opposite of what happened. Caught, because this is the ordinary state
        of a strategy under research."""
        got = decay(_runner({1: (-1000.0, 50), 2: (-500.0, 50)}), base_lag=1, extra=(1,))
        assert "reads backwards" in got.read()

    def test_a_base_run_that_made_nothing_has_no_share_to_report(self):
        """A share of nothing is not a share, and reporting one makes a strategy that
        never worked look robust."""
        got = decay(_runner({1: (0.0, 50), 2: (100.0, 50)}), base_lag=1, extra=(1,))
        assert got.retained(got.steps[-1]) != got.retained(got.steps[-1])  # NaN
        assert "no result for a delay to take away" in got.read()

    def test_one_lag_alone_has_no_shape(self):
        got = Decay(steps=(Step(1, 100.0, 10),))
        assert "no shape to read" in got.read()

    def test_the_lags_to_try_are_required(self):
        """How much delay is worth testing depends on how long the strategy holds, which
        the platform does not know."""
        with pytest.raises(StartupError, match="needs the extra lags"):
            decay(_runner({}), base_lag=1, extra=())


class TestThroughTheEngine:
    """The tool is only worth anything if the lag it passes actually reaches the fills."""

    BASE = dict(
        symbol="QQQ", start="2022-06-01", end="2023-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    def test_each_lag_produces_a_different_run(self):
        from backtest import run

        got = run(**self.BASE).delay_test(extra=(1, 2))
        assert [s.lag_days for s in got.steps] == [1, 2, 3]
        assert len({s.net_pnl for s in got.steps}) > 1, (
            "every lag produced the same P&L, so the lag is not reaching the fills"
        )

    def test_the_boundary_comes_before_the_numbers(self):
        from backtest import run

        result = run(**self.BASE)
        text = result.delay_test(extra=(1,)).text(synthetic=result.synthetic)
        assert text.index("!!") < text.index("net P&L")
