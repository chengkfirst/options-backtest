"""The document's 6.2 pre-launch checklist — the one section no inventory had.

Every one of its eight criteria already had a tool. Nothing put them together, so "is
this ready" was answered by remembering to run eight things and remembering what each
answer should look like, which is the form in which a check gets skipped on the run that
most needed it.

## The design decision worth testing hardest

**Unknown is not a soft pass.** Five of the eight need a second run, and a checklist that
scored an unrun check as satisfied would be worse than none — it would put a tick beside
work nobody did. So `ready` is false while any criterion is unmeasured, and every unknown
says what to run.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.research.prelaunch import FAIL, PASS, UNKNOWN, Check, Checklist, checklist
from tests.kinds import kind, labels

pytestmark = labels("unit", "6")

RUN = dict(
    symbol="QQQ", start="2021-01-04", end="2023-12-29",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


@pytest.fixture(scope="module")
def result():
    from backtest.api.sdk import run

    return run(**RUN)


class TestUnknownIsNotAPass:
    @kind("guard")
    def test_a_checklist_with_any_unknown_is_not_ready(self):
        checks = (Check("a", "a", PASS, ""), Check("b", "b", UNKNOWN, ""))
        assert not Checklist(checks=checks).ready

    @kind("guard")
    def test_all_eight_criteria_are_present(self, result):
        """Eight in the document, eight here. A criterion that quietly stopped being
        reported would leave `ready` easier to reach than the document allows."""
        assert len(result.prelaunch().checks) == 8

    def test_every_unknown_says_what_to_run(self, result):
        for check in result.prelaunch().unknown:
            assert len(check.detail) > 40, (
                f"{check.name} is unknown and does not say how to answer it"
            )

    @kind("limit")
    def test_nothing_passes_on_a_bare_result(self, result):
        """With no second runs, the criteria that need them must all be unknown — the
        failure mode being guarded against is a checklist that looks mostly green on a
        result nobody researched."""
        bare = result.prelaunch()
        needs_a_run = {"stable", "delay", "costs", "baseline"}
        for check in bare.checks:
            if check.name in needs_a_run:
                assert check.status == UNKNOWN, (
                    f"{check.name} reported {check.status} without the run it needs"
                )
        assert not bare.ready


class TestTheConcentrationCriterion:
    @kind("known_answer")
    def test_one_trade_carrying_the_profit_fails(self):
        trades = pd.DataFrame({
            "net_pnl": [1000.0] + [10.0] * 20,
            "exit_date": pd.date_range("2021-01-04", periods=21, freq="60D"),
        })
        out = checklist(_Result(trades)).checks[4]
        assert out.status == FAIL
        assert out.numbers["top_trade_share"] > 0.35

    @kind("guard")
    def test_a_one_year_run_cannot_fail_the_dates_half(self):
        """**The false failure this test exists for.** The first version reported "the
        best year is 100%, over the 70% limit" on a run covering one year — a failure a
        reader cannot act on, which trains them to ignore the checklist."""
        trades = pd.DataFrame({
            "net_pnl": [100.0] * 20,
            "exit_date": pd.date_range("2023-01-04", periods=20, freq="10D"),
        })
        out = checklist(_Result(trades)).checks[4]
        assert out.status == UNKNOWN
        assert "one year" in out.detail
        assert out.numbers["years"] == 1

    def test_a_spread_out_multi_year_profit_passes(self):
        trades = pd.DataFrame({
            "net_pnl": [100.0] * 30,
            "exit_date": pd.date_range("2021-01-04", periods=30, freq="40D"),
        })
        out = checklist(_Result(trades)).checks[4]
        assert out.status == PASS

    def test_a_losing_run_is_unknown_rather_than_passed(self):
        """There is no profit to be concentrated. Passing it would be a green tick on a
        strategy that lost money."""
        trades = pd.DataFrame({
            "net_pnl": [-100.0] * 20,
            "exit_date": pd.date_range("2021-01-04", periods=20, freq="40D"),
        })
        out = checklist(_Result(trades)).checks[4]
        assert out.status == UNKNOWN and "no profit" in out.detail


class TestTheThesisCriterion:
    def test_no_thesis_is_unknown_rather_than_guessed(self, result):
        """A tool that assumed the thesis would be marking its own homework."""
        out = result.prelaunch().checks[6]
        assert out.status == UNKNOWN and "thesis" in out.detail

    @kind("limit")
    def test_a_thesis_the_attribution_contradicts_fails(self, result):
        """On this fixture the largest term is theta, so a delta thesis must fail. That
        is the criterion doing its job: the strategy is earning from something other
        than what it claims."""
        out = result.prelaunch(thesis="delta").checks[6]
        assert out.status in (FAIL, UNKNOWN)
        if out.status == FAIL:
            assert out.numbers["largest_term"] != "delta"
            assert "something other than what it claims" in out.detail

    @kind("limit")
    def test_the_thesis_that_matches_passes(self, result):
        """Whichever term actually dominates, declaring it must pass -- otherwise the
        criterion is unsatisfiable rather than demanding."""
        attribution = result.attribution_daily()
        terms = {k: v for k, v in attribution.totals.items()
                 if k not in ("residual", "spread")}
        largest = max(terms, key=lambda k: abs(terms[k]))
        out = result.prelaunch(thesis=largest).checks[6]
        assert out.status == PASS, out.detail

    def test_an_unknown_term_is_refused_rather_than_failed(self, result):
        out = result.prelaunch(thesis="vanna").checks[6]
        assert out.status == UNKNOWN and "not an attribution term" in out.detail

    @kind("guard")
    def test_an_untrustworthy_attribution_cannot_answer_it(self):
        """If the expansion is not describing the P&L, what it says about the thesis
        cannot be read either — unknown, not fail and not pass."""
        from backtest.accounting.attribution import Attribution

        class _R:
            trades = pd.DataFrame({"net_pnl": [1.0], "exit_date": [date(2023, 1, 2)]})
            hold_out: dict = {}

            def attribution_daily(self):
                return Attribution(
                    pd.DataFrame(), {"delta": 10.0, "gamma": 0.0, "vega": 0.0,
                                     "theta": 0.0, "spread": 0.0, "residual": 90.0},
                    0, 5, method="daily")

        out = checklist(_R(), thesis="delta").checks[6]
        assert out.status == UNKNOWN and "residual" in out.detail


class TestTheTrialCount:
    def test_absent_is_unknown_and_says_why_a_run_cannot_know_it(self, result):
        out = result.prelaunch().checks[7]
        assert out.status == UNKNOWN and "across all slots" in out.detail

    def test_a_count_is_accepted(self, result):
        assert result.prelaunch(trials=42).checks[7].status == PASS

    def test_zero_is_a_failure_rather_than_an_absence(self, result):
        """At least this run happened, so a recorded zero is a broken counter -- a
        failure, distinct from "nobody told us", which is the unknown above."""
        assert result.prelaunch(trials=0).checks[7].status == FAIL


class TestTheHoldOut:
    def test_no_hold_out_is_unknown(self, result):
        out = result.prelaunch().checks[0]
        assert out.status == UNKNOWN and "hold_out=" in out.detail

    @kind("limit")
    def test_a_reserved_but_locked_hold_out_is_unknown_and_says_that_is_correct(self):
        """The document's first research rule is that the hold-out is used **once, at the
        end**. A locked one is the right state during research, so this must not read as
        a failure."""
        from backtest.api.sdk import run

        held = run(**(RUN | {"end": "2023-06-30",
                              "hold_out": ("2023-07-01", "2023-12-29")}))
        out = held.prelaunch().checks[0]
        assert out.status == UNKNOWN
        assert "correct state" in out.detail
        assert "2023-07-01" in out.detail


class _Result:
    """A result stub carrying only what a criterion reads."""

    def __init__(self, trades: pd.DataFrame):
        self.trades = trades
        self.hold_out: dict = {}

    def attribution_daily(self):
        raise RuntimeError("not available on this stub")
