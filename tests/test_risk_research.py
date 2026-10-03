"""Attribution, stress tests, MFE/MAE, regimes, sizing comparison.

What these modules share is the risk of **producing a plausible number that is
wrong** — attribution dropping a term, a stress test whose direction inverts under
a large shock, an MFE distribution censored by the stop. So these tests are not
about whether it runs, but about whether it says so when it is wrong.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.accounting.attribution import attribute, attribute_by
from backtest.core.thresholds import get
from backtest.portfolio.constraints import DrawdownBrake, ExposureLimits, hedge_value, without_hedges
from backtest.portfolio.scenarios import (
    PortfolioGreeks,
    scenario_replay,
    stress_grid,
    var_cvar,
)


def make_trades(n=30, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    spot0 = 300 + rng.normal(0, 5, n)
    spot1 = spot0 * (1 + rng.normal(0.01, 0.05, n))
    iv0 = rng.uniform(0.15, 0.30, n)
    return pd.DataFrame({
        "trade_id": range(1, n + 1),
        "entry_date": pd.date_range("2023-01-03", periods=n, freq="5D"),
        "exit_date": pd.date_range("2023-01-13", periods=n, freq="5D"),
        "entry_underlying_price": spot0, "exit_underlying_price": spot1,
        "entry_iv": iv0, "exit_iv": iv0 + rng.normal(0, 0.02, n),
        "entry_delta": rng.uniform(0.2, 0.5, n),
        "entry_gamma": rng.uniform(0.005, 0.02, n),
        "entry_vega": rng.uniform(0.1, 0.4, n),
        "entry_theta": -rng.uniform(5, 20, n),
        "gross_pnl": rng.normal(20, 200, n),
        "net_pnl": rng.normal(10, 200, n),
        "multiplier": 100, "quantity": 1,
        "exit_reason": rng.choice(["take_profit", "stop_loss", "time_exit"], n),
        "mfe": rng.uniform(0, 1.5, n), "mae": -rng.uniform(0, 0.9, n),
        "quote_gaps": 0, "deferred_days": 0,
    })


class TestAttribution:
    def test_components_sum_to_actual(self):
        """The four terms plus the residual must equal the actual P&L exactly —
        that is what the residual is for."""
        trades = make_trades()
        result = attribute(trades)
        assert sum(result.totals.values()) == pytest.approx(trades["gross_pnl"].sum())

    def test_missing_greeks_excluded_not_zeroed(self):
        """Trades missing greeks are excluded and counted, not filled with 0."""
        trades = make_trades()
        trades.loc[0:4, "entry_vega"] = np.nan
        result = attribute(trades)
        assert result.unusable == 5
        assert len(result.per_trade) == len(trades) - 5

    @pytest.mark.req("5.3.attribution")
    def test_flags_high_residual(self):
        """A large residual means the approximation failed, and has to be flagged
        rather than passed off as a successful attribution."""
        trades = make_trades()
        trades["gross_pnl"] = trades["gross_pnl"] * 50      # force a huge residual
        assert attribute(trades).high_residual > 0

    def test_empty_input(self):
        result = attribute(make_trades().iloc[0:0])
        assert result.totals == dict.fromkeys(result.totals, 0.0)

    def test_missing_columns_raise(self):
        with pytest.raises(KeyError, match="missing columns attribution needs"):
            attribute(pd.DataFrame({"net_pnl": [1.0]}))

    def test_group_attribution(self):
        grouped = attribute_by(make_trades(60), "exit_reason")
        assert set(grouped.columns) >= {"delta", "gamma", "vega", "theta", "residual"}
        assert grouped["trades"].sum() == 60


class TestStress:
    @pytest.fixture
    def greeks(self):
        return PortfolioGreeks(
            day=date(2024, 3, 1), spot=400.0, delta=100.0, gamma=5.0,
            vega=2000.0, theta=-8000.0, n_positions=4, equity=100_000.0, missing=0,
        )

    def test_small_shocks_have_numbers(self, greeks):
        grid = stress_grid(greeks, spot_shocks=(-0.05, 0.0, 0.05), vol_shocks=(0.0,))
        assert grid["IV+0%"].notna().all()

    def test_large_shocks_give_no_number(self, greeks):
        """Under a large shock the second-order expansion gets the direction wrong,
        so no number is better."""
        grid = stress_grid(greeks, spot_shocks=(-0.20, 0.0), vol_shocks=(0.0,))
        assert np.isnan(grid.loc["-20%", "IV+0%"])
        assert "outside" in grid.loc["-20%", "note"]

    def test_threshold_is_explicit(self, greeks):
        just_inside = stress_grid(greeks, spot_shocks=(get("large_shock"),), vol_shocks=(0.0,))
        assert just_inside[f"IV+0%"].notna().all()

    def test_direction_makes_sense(self, greeks):
        """A positive-delta portfolio should make money when spot rises."""
        grid = stress_grid(greeks, spot_shocks=(-0.05, 0.05), vol_shocks=(0.0,))
        assert grid.loc["+5%", "IV+0%"] > grid.loc["-5%", "IV+0%"]

    def test_theta_costs_money_over_time(self, greeks):
        flat = stress_grid(greeks, spot_shocks=(0.0,), vol_shocks=(0.0,), days_forward=0)
        later = stress_grid(greeks, spot_shocks=(0.0,), vol_shocks=(0.0,), days_forward=10)
        assert later.loc["+0%", "IV+0%"] < flat.loc["+0%", "IV+0%"]

    def test_greeks_as_dict_normalises_by_equity(self, greeks):
        d = greeks.as_dict()
        assert d["delta_pct_equity"] == pytest.approx(100.0 * 400.0 / 100_000.0)


class TestVaR:
    def test_small_sample_refuses(self):
        result = var_cvar(pd.Series([0.01] * 10))
        assert np.isnan(result["var"]) and "not trustworthy" in result["note"]

    def test_cvar_not_above_var(self):
        rng = np.random.default_rng(1)
        result = var_cvar(pd.Series(rng.normal(0, 0.01, 500)))
        assert result["cvar"] <= result["var"]

    def test_warns_on_thin_tail(self):
        rng = np.random.default_rng(2)
        result = var_cvar(pd.Series(rng.normal(0, 0.01, 100)))
        assert result["note"]


class TestScenarioReplay:
    def test_uncovered_window_is_flagged(self):
        equity = pd.DataFrame(
            {"equity_mid": [100.0, 101.0]},
            index=pd.to_datetime(["2023-01-03", "2023-01-04"]),
        )
        out = scenario_replay(equity, {"ancient": ("1999-01-01", "1999-12-31")})
        assert out.loc["ancient", "note"] == "not covered by the backtest range"
        assert np.isnan(out.loc["ancient", "return"])

    def test_computes_return_and_drawdown(self):
        idx = pd.date_range("2023-01-03", periods=40, freq="B")
        curve = pd.Series(np.linspace(100, 90, 40), index=idx)
        equity = pd.DataFrame({"equity_mid": curve})
        out = scenario_replay(equity, {"selloff": ("2023-01-03", "2023-03-01")})
        assert out.loc["selloff", "return"] < 0 and out.loc["selloff", "max drawdown"] < 0


class TestHedge:
    def test_limits_block_when_exceeded(self):
        greeks = PortfolioGreeks(date(2024, 1, 2), 400.0, 100.0, 1.0, 5000.0, -1.0,
                                 2, 100_000.0, 0)
        allowed, why = ExposureLimits(max_delta_pct=0.10).check(greeks, 100_000.0)
        assert not allowed and "delta" in why

    def test_limits_allow_when_within(self):
        greeks = PortfolioGreeks(date(2024, 1, 2), 400.0, 10.0, 1.0, 100.0, -1.0,
                                 1, 100_000.0, 0)
        allowed, _ = ExposureLimits(max_delta_pct=0.50, max_vega_pct=0.50).check(greeks, 100_000.0)
        assert allowed

    #: The stages this test uses. Stated rather than taken from a default, because the
    #: brake no longer has one: where to cut and how hard governs the whole book.
    BRAKE = dict(reduce_at=0.10, halt_at=0.20, resume_at=0.05, keep=0.5)

    @pytest.mark.parametrize("dd,state", [(-0.02, "normal"), (-0.12, "reduced"), (-0.25, "halted")])
    def test_brake_states(self, dd, state):
        assert DrawdownBrake(**self.BRAKE).state(dd) == state

    def test_brake_scale_zero_when_halted(self):
        assert DrawdownBrake(**self.BRAKE).scale(-0.30) == 0.0

    def test_hedge_columns_required(self):
        with pytest.raises(KeyError, match="is_hedge"):
            without_hedges(make_trades())

    def test_hedge_value_separates_core_and_hedge(self):
        trades = make_trades(40)
        trades["is_hedge"] = [i % 5 == 0 for i in range(40)]
        out = hedge_value(trades)
        assert set(out.index) == {"with hedges", "hedges removed",
                                  "the hedges themselves"}
        assert (out.loc["hedges removed", "trades"]
                + out.loc["the hedges themselves", "trades"]) == 40


class TestExcursion:
    def test_requires_event_driven_columns(self):
        from backtest.research.excursion import study

        trades = make_trades().drop(columns=["mfe"])
        with pytest.raises(KeyError, match="event_driven"):
            study(trades)

    def test_censoring_detected(self):
        from backtest.signals.strategy import ExitRule
        from backtest.research.excursion import study

        result = study(make_trades(), ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5))
        assert any("stop loss" in c for c in result.censoring)
        assert any("take profit" in c for c in result.censoring)

    def test_no_censoring_when_rules_off(self):
        from backtest.signals.strategy import ExitRule
        from backtest.research.excursion import study

        rule = ExitRule(hold_days=20, take_profit=None, stop_loss=None, dte_floor=5)
        result = study(make_trades(), rule)
        assert not any("stop loss" in c for c in result.censoring)

    def test_stop_table_counts_killed_winners(self):
        from backtest.research.excursion import study

        trades = make_trades(50)
        table = study(trades).stop_loss_table(levels=(0.3,))
        assert table.loc["-30%", "of which were winners"] >= 0
        assert table.loc["-30%", "reached"] >= table.loc["-30%", "of which were winners"]


class TestRegime:
    def test_dimension_label_count_validated(self):
        from backtest.signals.setup.regime import Dimension

        with pytest.raises(ValueError, match="labels"):
            Dimension("x", "vix", (0.2, 0.3), ("low", "high"),
                      "two boundaries need three labels")

    def test_missing_source_marked_unavailable(self):
        from backtest.signals.setup.regime import DEFAULT_DIMENSIONS, classify

        panel = pd.DataFrame({"vxn": [0.2] * 5},
                             index=pd.date_range("2023-01-03", periods=5, freq="B"))
        regimes = classify(panel, None, DEFAULT_DIMENSIONS)
        assert ("volatility term structure" in regimes.unavailable
                and "trend" in regimes.unavailable)

    def test_partial_dimensions_yield_unknown(self):
        """Any dimension absent makes the whole thing unknown — making do with the
        rest would quietly change what the regime means."""
        from backtest.signals.setup.regime import classify

        panel = pd.DataFrame(
            {"vxn": [0.2, 0.35], "term_structure": [1.1, np.nan]},
            index=pd.to_datetime(["2023-01-03", "2023-01-04"]),
        )
        regimes = classify(panel, None)
        assert regimes.frame.loc[pd.Timestamp("2023-01-04"), "combined"] == "unknown"

    def test_stratify_flags_thin_samples(self):
        from backtest.signals.setup.regime import classify, stratify

        panel = pd.DataFrame({"vxn": [0.15] * 40},
                             index=pd.date_range("2023-01-03", periods=40, freq="B"))
        regimes = classify(panel, None)
        trades = make_trades(5)
        out = stratify(trades, regimes, min_trades=15)
        assert (out["sample"].str.contains("too small")).any()
