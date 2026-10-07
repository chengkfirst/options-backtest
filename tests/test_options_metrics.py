"""The document's Options metric category: the spread paid, and implied against realised.

## Why these two and not the rest of the category

Theta paid and vega P&L are already produced by the greeks attribution, which splits each
day's P&L into exactly those terms -- computing them again here would be a second answer
to one question. The other two were absent, and one of them was absent for a reason worth
recording: **the fill price was kept and the quote it came from was thrown away**, so no
finished run could say how far from the mid it had filled. That makes the spread
uncomputable and λ -- the realism register's most impactful assumption -- unauditable per
trade.

So ``trade_context`` now records the mid and the spread beside the fill, and both engines
get it from the one shared function.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.research.metrics import iv_versus_realized, spread_paid
from tests.kinds import kind, labels

pytestmark = labels("unit", "6")


def _trade(**over) -> dict:
    base = dict(
        trade_id=1, quantity=1, multiplier=100,
        entry_price=5.00, entry_mid=4.90, entry_spread=0.20,
        exit_price=7.00, exit_mid=7.20, exit_spread=0.40,
        gross_pnl=200.0, entry_iv=0.30,
        entry_date=date(2024, 1, 2), exit_date=date(2024, 1, 31),
    )
    return base | over


class TestTheSpreadPaid:
    @kind("known_answer")
    def test_it_is_the_distance_from_the_mid_on_both_legs(self):
        """Hand-computable, and that is the point of the case.

        One contract: bought at 5.00 against a 4.90 mid is $10 of adverse fill; sold at
        7.00 against a 7.20 mid is $20. Round trip $30 on a $500 premium is 6.0%.
        """
        out = spread_paid(pd.DataFrame([_trade()]))
        assert out["count"] == 1
        assert out["spread_dollars"] == pytest.approx(30.0)
        assert out["spread_pct_of_premium_mean"] == pytest.approx(0.06)
        assert out["entry_pct_of_premium"] == pytest.approx(0.02)
        assert out["exit_pct_of_premium"] == pytest.approx(0.04)

    def test_a_fill_at_the_mid_costs_nothing(self):
        """The λ = 0 case. If this reported a cost, the metric would be measuring the
        quote's width rather than what the fill paid for it."""
        out = spread_paid(pd.DataFrame([
            _trade(entry_price=4.90, exit_price=7.20)]))
        assert out["spread_dollars"] == pytest.approx(0.0)
        assert out["spread_pct_of_premium_mean"] == pytest.approx(0.0)

    @kind("guard")
    def test_a_favourable_fill_is_negative_rather_than_absolute(self):
        """**Signed, deliberately.** A fill better than the mid is not a cost, and taking
        the absolute value would report one -- turning a fill model that is too generous
        into a cost figure that looks conservative."""
        out = spread_paid(pd.DataFrame([
            _trade(entry_price=4.80, exit_price=7.30)]))
        assert out["spread_dollars"] < 0

    def test_size_separates_the_mean_from_the_weighted_figure(self):
        """Why both are reported: a small trade paying a large share and a large trade
        paying a small one give the typical trade and the book different answers."""
        frame = pd.DataFrame([
            _trade(trade_id=1, quantity=1, entry_price=1.00, entry_mid=0.95,
                   exit_price=1.10, exit_mid=1.15, gross_pnl=10.0),
            _trade(trade_id=2, quantity=10, entry_price=20.00, entry_mid=19.95,
                   exit_price=21.00, exit_mid=21.05, gross_pnl=1000.0),
        ])
        out = spread_paid(frame)
        assert out["spread_pct_of_premium_mean"] > out["spread_pct_of_premium_weighted"]

    def test_a_table_without_the_quote_says_so_rather_than_returning_zero(self):
        """Runs recorded before the mid was kept cannot answer this, and a 0 would read
        as "the fills were free"."""
        frame = pd.DataFrame([_trade()]).drop(columns=["entry_mid"])
        out = spread_paid(frame)
        assert out["count"] == 0 and "entry_mid" in out["note"]

    def test_a_missing_quote_on_one_leg_excludes_the_trade_and_counts_it(self):
        frame = pd.DataFrame([_trade(trade_id=1),
                              _trade(trade_id=2, exit_mid=np.nan)])
        out = spread_paid(frame)
        assert out["count"] == 1 and out["excluded"] == 1

    def test_no_trades_is_a_note_not_a_number(self):
        assert spread_paid(pd.DataFrame())["count"] == 0


def _path(trade_id: int, prices: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "trade_id": trade_id,
        "date": pd.date_range("2024-01-02", periods=len(prices), freq="B"),
        "underlying": prices,
    })


class TestImpliedAgainstRealized:
    @kind("known_answer")
    def test_the_realised_figure_is_the_annualised_sample_deviation(self):
        """Computed independently of the function under test, from the same path."""
        prices = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0]
        out = iv_versus_realized(pd.DataFrame([_trade(entry_iv=0.30)]),
                                 _path(1, prices))
        returns = np.diff(np.log(prices))
        expected = returns.std(ddof=1) * np.sqrt(252)
        assert out["mean_realized_vol"] == pytest.approx(expected)
        assert out["mean_gap"] == pytest.approx(0.30 - expected)

    def test_the_gap_sign_says_who_came_out_ahead(self):
        """Negative gap: the underlying moved more than the option was priced for, which
        is the buyer's case. A flat path is the seller's."""
        moving = iv_versus_realized(
            pd.DataFrame([_trade(entry_iv=0.05)]),
            _path(1, [100.0, 104.0, 97.0, 105.0, 96.0, 106.0]))
        assert moving["mean_gap"] < 0
        assert moving["share_realized_above_implied"] == 1.0

        quiet = iv_versus_realized(
            pd.DataFrame([_trade(entry_iv=0.80)]),
            _path(1, [100.0, 100.05, 100.0, 100.02, 100.0, 100.03]))
        assert quiet["mean_gap"] > 0
        assert quiet["share_realized_above_implied"] == 0.0

    @kind("guard")
    def test_a_window_too_short_to_measure_is_excluded_not_zeroed(self):
        """**A realised vol of 0 would read as the best possible trade for a buyer**,
        which is the exact opposite of what an unmeasurable window means."""
        out = iv_versus_realized(pd.DataFrame([_trade()]), _path(1, [100.0, 101.0]))
        assert out["count"] == 0 and out["excluded"] == 1
        assert "usable returns" in out["note"]

    def test_an_unsolved_entry_iv_is_excluded_not_treated_as_zero(self):
        out = iv_versus_realized(
            pd.DataFrame([_trade(entry_iv=np.nan)]),
            _path(1, [100.0, 101.0, 100.5, 102.0, 101.0]))
        assert out["count"] == 0 and out["excluded"] == 1

    def test_each_trade_reads_only_its_own_path(self):
        """Two trades over different windows must not share a volatility. Pooling them
        would make every trade in a run report the same realised figure."""
        trades = pd.DataFrame([_trade(trade_id=1, entry_iv=0.30),
                               _trade(trade_id=2, entry_iv=0.30)])
        paths = pd.concat([
            _path(1, [100.0, 100.1, 100.0, 100.1, 100.0]),
            _path(2, [100.0, 105.0, 95.0, 106.0, 94.0]),
        ])
        out = iv_versus_realized(trades, paths)
        per_trade = out["per_trade"].set_index("trade_id")["realized_vol"]
        assert per_trade[2] > per_trade[1] * 10, (
            "a quiet path and a violent one reported comparable volatility, so the "
            "grouping by trade is not happening"
        )

    def test_no_path_says_which_engine_produces_one(self):
        out = iv_versus_realized(pd.DataFrame([_trade()]), None)
        assert out["count"] == 0 and "event_driven" in out["note"]


class TestOnARealRun:
    @kind("limit")
    def test_both_metrics_come_out_of_a_run_and_are_mechanically_sound(self):
        """Not a claim about the market -- a claim that the figures are internally
        consistent: a spread paid between zero and the whole premium, and a realised
        volatility that is positive and finite on every trade it reports.
        """
        from backtest.api.sdk import run

        result = run(
            symbol="QQQ", start="2023-01-01", end="2023-06-30",
            entry="close < sma_20 * 0.98",
            select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                    "delta_lo": 0.25, "delta_hi": 0.45},
            exit={"hold_days": 20, "dte_floor": 7},
            engine="event_driven", sizing={"method": "fixed"}, quantity=1,
        )
        spread = result.spread()
        assert spread["count"] == len(result.trades)
        assert 0.0 < spread["spread_pct_of_premium_mean"] < 1.0, (
            f"a spread of {spread['spread_pct_of_premium_mean']:.1%} of the premium is "
            f"either free or more than the position cost"
        )

        vol = result.vol_premium()
        assert vol["count"] > 0
        per_trade = vol["per_trade"]
        assert (per_trade["realized_vol"] > 0).all()
        assert np.isfinite(per_trade["realized_vol"]).all()
        assert (per_trade["n_returns"] >= 3).all()
