"""Trade-level reporting. There is no equity curve at this level, so no drawdown and
no Sharpe."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.report import stats as R
from backtest.types import TRADE_COLUMNS


def make_trades(pnl: list[float], reasons: list[str] | None = None) -> pd.DataFrame:
    n = len(pnl)
    reasons = reasons or ["time_exit"] * n
    return pd.DataFrame(
        {
            "trade_id": range(1, n + 1),
            "engine": "trade_table",
            "symbol": "QQQ",
            "entry_date": [date(2023, 1, 3)] * n,
            "exit_date": [date(2023, 1, 13)] * n,
            "contract": "QQQ_20230217_C_00300000",
            "quantity": 1,
            "entry_price": 5.0,
            "exit_price": [5.0 + p / 100 for p in pnl],
            "multiplier": 100,
            "gross_pnl": [p + 2.0 for p in pnl],
            "fees": 2.0,
            "fee_commission": 1.8,
            "fee_regulatory": 0.2,
            "fee_slippage": 0.0,
            "net_pnl": pnl,
            "exit_reason": reasons,
            "suppressed_reasons": "",
            "holding_days": 7,
            "source": "synthetic",
            "quote_gaps": 0,
            "deferred_days": 0,
        }
    )


class TestTradeStats:
    def test_zero_trades_is_not_an_error(self):
        """With no trades this returns an empty summary rather than raising or
        reporting a misleading 0% win rate."""
        got = R.trade_stats(pd.DataFrame(columns=list(TRADE_COLUMNS)))
        assert got["count"] == 0 and "no trades" in got["note"]

    def test_win_rate_and_totals(self):
        got = R.trade_stats(make_trades([100.0, -50.0, 200.0, -30.0]))
        assert got["count"] == 4
        assert got["win_rate"] == pytest.approx(0.5)
        assert got["net_pnl"] == pytest.approx(220.0)

    def test_profit_factor(self):
        got = R.trade_stats(make_trades([100.0, -50.0]))
        assert got["profit_factor"] == pytest.approx(2.0)

    def test_group_by_exit_reason(self):
        trades = make_trades([100.0, -50.0, 60.0], ["take_profit", "stop_loss", "take_profit"])
        grouped = R.by_exit_reason(trades)
        assert grouped.loc["take_profit", "count"] == 2
        assert grouped.loc["stop_loss", "net_pnl"] == pytest.approx(-50.0)


class TestDistribution:
    def test_quantiles_and_shape(self):
        pnl = [-200.0, -150.0, -100.0, -80.0, 500.0, 900.0]
        got = R.pnl_distribution(make_trades(pnl), 100_000.0)
        assert got["max_win"] == 900.0 and got["max_loss"] == -200.0
        assert set(got["quantiles_abs"]) == {"q5", "q25", "q50", "q75", "q95"}
        assert got["skew"] > 0  # a buyer's positive skew

    def test_two_denominations(self):
        got = R.pnl_distribution(make_trades([100.0, -50.0]), 10_000.0)
        assert got["quantiles_pct"]["q50"] == pytest.approx(got["quantiles_abs"]["q50"] / 10_000.0)

    def test_flags_outlier_driven_mean(self):
        """A negative median with a positive mean: a few extremes drive the average,
        which does not generalise."""
        got = R.pnl_distribution(make_trades([-100.0, -100.0, -100.0, 1000.0]), 100_000.0)
        assert got["driven_by_outliers"]

    def test_histogram_bins(self):
        hist = R.histogram(make_trades([1.0, 2.0, 3.0, 40.0]), bins=4)
        assert len(hist) == 4 and hist["count"].sum() == 4


class TestCosts:
    def test_breakdown_sums(self):
        got = R.cost_breakdown(make_trades([100.0, 100.0]))
        assert got["total"] == pytest.approx(got["commission"] + got["regulatory"] + got["slippage"])

    def test_flags_costs_eating_profit(self):
        """Gross positive and net negative: the edge does not cover the costs."""
        trades = make_trades([-1.0, -1.0])
        trades["gross_pnl"] = 1.0
        assert R.cost_breakdown(trades)["eaten_by_costs"]


class TestDisclosure:
    def test_all_synthetic_flagged(self):
        got = R.data_provenance(make_trades([1.0, 2.0]))
        assert got["all_synthetic"] and got["synthetic_share"] == 1.0

    def test_mixed_sources(self):
        trades = make_trades([1.0, 2.0])
        trades.loc[0, "source"] = "vendor"
        got = R.data_provenance(trades)
        assert got["mixed"] and got["synthetic_share"] == pytest.approx(0.5)

    def test_itm_passive_expiry_flags_rule_failure(self):
        """An in-the-money passive expiry means the exit rules failed at least
        once."""
        trades = make_trades([1.0, -2.0], ["time_exit", "expiry_itm"])
        assert R.passive_expiry_check(trades)["rule_failed"]

    def test_otm_expiry_alone_is_not_failure(self):
        trades = make_trades([1.0, -2.0], ["time_exit", "expiry_otm"])
        assert not R.passive_expiry_check(trades)["rule_failed"]

    def test_environment_records_numeric_versions(self):
        env = R.environment()
        assert {"python", "numpy", "pandas", "scipy"} <= set(env)
