"""``backtest.check_data``: the entry point for "is my data usable here".

Until this existed the data layer's checks ran only inside a backtest, so the only way to
ask about a new data file was to start a run and read its failure -- and a failure part
way through a run does not distinguish "your data is wrong" from "your strategy is wrong".

The checks themselves are the data layer's own (``data/pipeline.py``) and are tested
there. What is tested here is the entry point: that it reports rather than raises, that it
takes the same arguments a run does, and that it reaches the chain.
"""

from __future__ import annotations

import pytest

from tests.kinds import kind, labels

pytestmark = labels("unit", "1")

RANGE = dict(symbol="QQQ", start="2023-01-01", end="2023-06-30")


class TestWhatItReports:
    @kind("limit")
    def test_it_reports_every_step_without_running_a_backtest(self):
        import backtest

        report = backtest.check_data(**RANGE)
        summary = report.as_dict()["summary"]
        assert summary["total"] >= 9, (
            f"only {summary['total']} checks ran; the pipeline has more steps than that, "
            f"so the entry point is not reaching all of them"
        )
        assert summary["passed"] + summary["warned"] + summary["failed"] == summary["total"]

    @kind("limit")
    def test_every_step_says_what_it_measured_and_where_to_change_it(self):
        """The whole value of a report over a boolean. A step that passes silently is
        indistinguishable from one that did not run."""
        import backtest

        report = backtest.check_data(**RANGE)
        for step in report.steps:
            assert step.result.detail, f"{step.step.name} reported no measurement"
            assert step.step.why, f"{step.step.name} does not say why it is checked"
            assert step.step.where, f"{step.step.name} does not say where to change it"

    @kind("limit")
    def test_the_text_form_is_readable_and_names_the_counts(self):
        import backtest

        text = backtest.check_data(**RANGE).text()
        assert "Data pipeline" in text
        assert "passed" in text and "failed" in text

    @kind("limit")
    def test_it_reaches_the_chain_rather_than_only_the_underlying(self):
        """A chain step that never ran would make a file of option data look checked
        when only the bars had been."""
        import backtest

        report = backtest.check_data(**RANGE)
        names = " ".join(s.step.name for s in report.steps)
        assert "chain" in names.lower()


class TestThePriceJumpCheck:
    """The one quality check the document lists that was missing.

    An unadjusted split arrives as a one-session move of tens of percent and passes every
    other check: the prices are positive, the high is above the low, no date repeats and
    nothing is missing.
    """

    @kind("guard")
    def test_an_unapplied_split_fails_rather_than_being_called_a_large_move(self):
        """**The claim is "this is a 2:1 split", not "this is big".** A threshold on size
        would also catch March 2020, which is real data and the most informative in the
        sample."""
        import pandas as pd

        from backtest.data.pipeline import _check_quality

        class _Bars:
            def __init__(self, frame):
                self.frame = frame

        class _Market:
            def __init__(self, frame):
                self.bars = _Bars(frame)

        prices = [400.0] * 10 + [200.0] * 10        # a 2:1 split, unapplied
        frame = pd.DataFrame(
            {"open": prices, "high": prices, "low": prices, "close": prices},
            index=pd.date_range("2024-01-02", periods=len(prices), freq="B"),
        )
        result = _check_quality(_Market(frame))
        assert result.status == "fail"
        assert "2:1 split" in result.detail
        assert result.numbers["largest_move_pct"] == 50.0

    @kind("guard")
    def test_a_large_but_not_split_like_move_is_reported_and_passes(self):
        """A 20% session is real and must not be deleted. It is named, with its date, and
        left to a person."""
        import pandas as pd

        from backtest.data.pipeline import _check_quality

        class _Bars:
            def __init__(self, frame):
                self.frame = frame

        class _Market:
            def __init__(self, frame):
                self.bars = _Bars(frame)

        prices = [400.0] * 10 + [320.0] * 10        # -20%, not a split ratio
        frame = pd.DataFrame(
            {"open": prices, "high": prices, "low": prices, "close": prices},
            index=pd.date_range("2024-01-02", periods=len(prices), freq="B"),
        )
        result = _check_quality(_Market(frame))
        assert result.status == "pass"
        assert result.numbers["largest_move_pct"] == 20.0
        assert "2024-01-16" in result.detail or "reported rather than judged" in result.detail

    @kind("limit")
    def test_a_real_run_reports_its_largest_move_with_a_date(self):
        import backtest

        step = [s for s in backtest.check_data(**RANGE).steps
                if "quality" in s.step.name][0]
        assert step.result.numbers["largest_move_pct"] > 0
        assert step.result.numbers["largest_move_date"]


class TestWhatItSaysAboutDataItDoesNotHave:
    """**Three real defects this test found**, all the same shape: a report confident
    about data it does not have.

    A 2030 range against data ending in 2023 used to report "the requested range is
    covered", pass the calendar check with "0 sessions in the backtest range" printed
    beside the pass, and crash the chain check with an `IndexError` reported as "the
    check itself failed". Seven of nine steps passed on data that cannot run.
    """

    FUTURE = dict(symbol="QQQ", start="2030-01-01", end="2030-06-30")

    @kind("guard")
    def test_a_range_the_data_does_not_reach_fails_rather_than_reading_as_covered(self):
        import backtest

        report = backtest.check_data(**self.FUTURE)
        source = report.steps[0]
        assert source.result.status == "fail", (
            f"the source step reported {source.result.status!r} on a range the data "
            f"does not reach: {source.result.detail}"
        )
        assert "do not overlap" in source.result.detail
        assert source.result.numbers["overlaps"] is False

    @kind("guard")
    def test_an_empty_range_fails_the_calendar_check(self):
        """It used to pass, with the count of zero sessions in its own message."""
        import backtest

        report = backtest.check_data(**self.FUTURE)
        calendar = [s for s in report.steps if "calendar" in s.step.name
                    and "expiry" not in s.step.name][0]
        assert calendar.result.status == "fail"
        assert calendar.result.numbers["sessions_in_range"] == 0

    @kind("guard")
    def test_the_chain_check_names_the_cause_rather_than_an_index_error(self):
        """A step that reports `IndexError: tuple index out of range` has told the reader
        about its own implementation instead of about their data."""
        import backtest

        report = backtest.check_data(**self.FUTURE)
        chain = [s for s in report.steps if "chain" in s.step.name][0]
        assert chain.result.status == "fail"
        assert "IndexError" not in chain.result.detail
        assert "no trading days" in chain.result.detail

    def test_a_range_the_data_stops_short_of_warns(self):
        """Half-covered is its own state: the run will work and will cover less than was
        asked for, which a result read later cannot recover."""
        import backtest

        report = backtest.check_data(symbol="QQQ", start="2023-01-01", end="2029-12-31")
        source = report.steps[0]
        assert source.result.status == "warn"
        assert source.result.numbers["covers_start"] is True
        assert source.result.numbers["covers_end"] is False


class TestWhatItRefuses:

    @kind("guard")
    def test_it_is_exported_from_the_package(self):
        """A data entry point nobody can reach is not an entry point. Checked explicitly
        because the module-level function existed for a while and nothing exported it."""
        import backtest

        assert "check_data" in backtest.__all__
        assert callable(backtest.check_data)
