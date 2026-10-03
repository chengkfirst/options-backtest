"""The investor's metrics, all of them off the daily mark-to-market of the whole book.

## Why this file exists

``backtest/research/investor.py`` had no tests. It computes Sortino, Calmar, time
underwater, concentration and beta -- the figures a result is judged on -- and nothing
held any of them to an arithmetic a person can check.

## The requirement it owns

The document asks for return and risk metrics **from the daily mark-to-market of the
whole book**, and the thing that falsifies it is a drawdown computed from closed trades.
That is not a pedantic distinction: a book can be 30% underwater on open positions that
all close green, and a drawdown taken from the trade table would report nothing at all.
The first class below is that difference, in numbers.

Every curve here is written out by hand for that reason. A metric checked against a curve
produced by a run is checked against whatever the run happens to do.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.research import investor

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "6")


def _equity(values) -> pd.DataFrame:
    """A daily book. The index is dates because the real one is."""
    days = pd.bdate_range("2021-01-04", periods=len(values))
    return pd.DataFrame({"equity_mid": [float(v) for v in values]}, index=days)


def _trades(pnls, *, exit_years=None) -> pd.DataFrame:
    out = pd.DataFrame({"net_pnl": [float(p) for p in pnls]})
    if exit_years is not None:
        out["exit_date"] = [pd.Timestamp(f"{y}-06-01") for y in exit_years]
    return out


class TestTheMetricsComeFromTheDailyBook:
    """The requirement, stated as the difference between two numbers."""

    #: A book that falls 20% while it is open and closes every trade at a profit. The
    #: trade table shows three wins; the daily curve shows a fifth of the capital gone
    #: at the trough.
    CURVE = (100_000.0, 95_000.0, 80_000.0, 90_000.0, 104_000.0)
    CLOSED = (1_000.0, 1_500.0, 1_500.0)

    @pytest.mark.req("6.4.daily_pnl")
    def test_the_drawdown_is_the_books_and_not_the_trade_tables(self):
        """80,000 against a peak of 100,000 is -20%, and the trades alone say zero.

        Calmar is return over that drawdown, so this is checkable without a drawdown
        field: the annualised return divided by 0.20 is what comes back, and dividing by
        anything the trade table could produce gives a different number or a division by
        zero.
        """
        equity = _equity(self.CURVE)
        got = investor.summary(equity, _trades(self.CLOSED), synthetic=True)

        # Every closed trade was a win, so a drawdown taken from them is zero and Calmar
        # would be undefined. It is not undefined here.
        assert all(p > 0 for p in self.CLOSED)
        assert np.isfinite(got["Calmar"]), (
            "Calmar is not a number on a book that fell 20%, which is what happens when "
            "the drawdown is taken from closed trades -- every one of them was a win"
        )

        years = len(self.CURVE) / investor.YEAR
        total = self.CURVE[-1] / self.CURVE[0] - 1.0
        annual = (1.0 + total) ** (1 / years) - 1.0
        assert got["Calmar"] == pytest.approx(annual / 0.20, rel=1e-9), (
            "Calmar is not return over the book's worst drawdown of 20%"
        )

    def test_the_time_underwater_is_the_books_too(self):
        """Three of the five days sit below the running peak, and no trade was ever
        held at a loss at its close."""
        got = investor.summary(_equity(self.CURVE), _trades(self.CLOSED))
        assert got["share_underwater"] == pytest.approx(3 / 5)
        assert got["longest_underwater_days"] == 3


class TestUnderwater:
    def test_the_longest_stretch_is_the_longest_one_not_the_last(self):
        """Two separate stretches, the earlier one longer. Taking the most recent is the
        natural off-by-one here and it reads plausibly in a report."""
        curve = _equity([100, 95, 94, 93, 101, 99, 102])
        got = investor._underwater(curve["equity_mid"])
        assert got["longest_underwater_days"] == 3

    def test_a_curve_that_only_rises_is_never_underwater(self):
        got = investor._underwater(_equity([100, 101, 102])["equity_mid"])
        assert got["share_underwater"] == 0.0
        assert got["longest_underwater_days"] == 0

    def test_the_deepest_drawdown_reports_how_long_it_took_to_recover(self):
        """Trough at index 2, back to the prior peak of 100 at index 5: three days."""
        curve = _equity([100, 90, 80, 85, 95, 100])
        got = investor._underwater(curve["equity_mid"])
        assert got["deepest_recovered"] is True
        assert got["deepest_recovery_days"] == 3

    def test_still_underwater_at_the_end_is_said_so_and_not_zero(self):
        """The absence that matters: a drawdown not yet recovered reported as 0 days
        reads as recovered immediately, which is the opposite of the truth."""
        curve = _equity([100, 90, 80, 85, 90])
        got = investor._underwater(curve["equity_mid"])
        assert got["deepest_recovered"] is False
        assert got["deepest_recovery_days"] != got["deepest_recovery_days"]  # nan


class TestRiskAdjusted:
    def test_sortino_counts_only_the_downside(self):
        """Two books with **identical** down days and different up days must give the
        same downside volatility -- that is the whole reason this is not Sharpe.

        An option buyer's upside volatility is the payoff, not the risk.

        Built from returns rather than from levels, which is the trap this test fell into
        on the first attempt: writing out two level series with the same-looking dips
        gives different *percentage* down moves once the levels diverge, and the test then
        fails for arithmetic reasons rather than because upside is being counted.
        """
        def curve(up):
            # Two different down days, so the downside has a spread to measure -- four
            # identical ones have a standard deviation of zero and Sortino is then
            # undefined for a reason that has nothing to do with this test.
            rets = []
            for down in (-0.01, -0.02, -0.01, -0.02):
                rets += [down, up]
            return _equity(100.0 * np.cumprod(np.r_[1.0, [1 + r for r in rets]]))[
                "equity_mid"]

        mild = investor._risk_adjusted(curve(0.012))
        wild = investor._risk_adjusted(curve(0.30))
        assert wild["downside_vol"] == pytest.approx(mild["downside_vol"], rel=1e-9), (
            "a big up move changed the downside volatility, so upside is being counted "
            "as risk and this is Sharpe wearing another name"
        )
        # Same risk by this measure, far more return: the ranking has to follow.
        assert wild["Sortino"] > mild["Sortino"]

    def test_calmar_is_undefined_rather_than_infinite_with_no_drawdown(self):
        """A curve that never fell has no drawdown to divide by. Returning infinity
        would make the best possible strategy and a two-day curve look alike."""
        got = investor._risk_adjusted(_equity([100, 101, 102, 103])["equity_mid"])
        assert got["Calmar"] != got["Calmar"]  # nan

    def test_too_short_a_curve_is_undefined_rather_than_computed(self):
        got = investor._risk_adjusted(_equity([100])["equity_mid"])
        assert got["Sortino"] != got["Sortino"]
        assert got["Calmar"] != got["Calmar"]


class TestConcentration:
    def test_the_top_five_share_is_of_the_total(self):
        got = investor._concentration(_trades([100, 100, 100, 100, 100, 500]))
        assert got["top5_share"] == pytest.approx(900 / 1000)
        assert got["without_top5"] == pytest.approx(100.0)

    def test_a_losing_book_has_no_share_rather_than_a_negative_one(self):
        """A share of a loss is not a share, and a negative one would sort as low
        concentration -- which reads as the safe end."""
        got = investor._concentration(_trades([-100, -200, 50]))
        assert got["top5_share"] != got["top5_share"]  # nan

    def test_the_worst_year_is_a_year_and_not_the_whole_range(self):
        got = investor._concentration(
            _trades([300, -100, -50, 400], exit_years=[2021, 2022, 2022, 2023]))
        assert got["worst_year"] == pytest.approx(-150.0)
        assert got["profitable_years_share"] == pytest.approx(2 / 3)

    def test_no_trades_gives_nothing_rather_than_zeros(self):
        assert investor._concentration(pd.DataFrame()) == {}


class TestTheBenchmark:
    def test_without_one_nothing_is_invented_and_it_says_why(self):
        """The question the whole module opens with -- alpha or leveraged beta -- cannot
        be answered without the underlying, and a beta of 0 would answer it wrongly in
        the flattering direction."""
        got = investor.summary(_equity([100, 101, 102]), _trades([1.0]))
        assert got["beta_vs_underlying"] != got["beta_vs_underlying"]  # nan
        assert "no benchmark" in got["beta_note"]

    def test_too_little_overlap_is_refused_with_the_reason(self):
        equity = _equity(np.linspace(100, 120, 10))
        bench = pd.Series(np.linspace(300, 320, 10), index=equity.index)
        got = investor.summary(equity, _trades([1.0]), bench)
        assert got["beta_vs_underlying"] != got["beta_vs_underlying"]
        assert "20 trading days" in got["beta_note"]

    def test_a_book_that_is_twice_the_underlying_has_a_beta_of_two(self):
        """Known answer, hand-constructed: each daily return on the book is twice the
        benchmark's, so the regression slope is 2."""
        days = pd.bdate_range("2021-01-04", periods=60)
        bench_ret = np.resize([0.01, -0.005, 0.002, -0.001], 59)
        bench = pd.Series(300.0 * np.cumprod(np.r_[1.0, 1 + bench_ret]), index=days)
        book = pd.Series(100.0 * np.cumprod(np.r_[1.0, 1 + 2 * bench_ret]), index=days)
        got = investor.summary(pd.DataFrame({"equity_mid": book}), _trades([1.0]), bench)
        assert got["beta_vs_underlying"] == pytest.approx(2.0, rel=0.02)


class TestTheBoundary:
    def test_a_synthetic_chain_is_stated_and_a_real_one_is_too(self):
        """Both states, because "no note" and "not asked" are different things and a
        reader cannot tell them apart from an absent key."""
        synthetic = investor.summary(_equity([100, 99, 101]), _trades([1.0]),
                                     synthetic=True)
        real = investor.summary(_equity([100, 99, 101]), _trades([1.0]), synthetic=False)
        assert "synthetic" in synthetic["boundary"]
        assert "real option data" in real["boundary"]

    def test_not_saying_leaves_the_key_out_rather_than_claiming_either(self):
        got = investor.summary(_equity([100, 99, 101]), _trades([1.0]))
        assert "boundary" not in got
