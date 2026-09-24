"""归因、压力测试、MFE/MAE、regime、仓位对照。

这些模块的共同风险是**给出一个看似合理但其实错误的数字**——归因少算一项、压力测试
在大冲击下方向反了、MFE 分布被止损截断。所以测试重点不是"能不能跑"，是"错的时候
会不会说出来"。
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.risk.attribution import RESIDUAL_WARN, attribute, attribute_by
from backtest.risk.hedge import DrawdownBrake, ExposureLimits, hedge_value, without_hedges
from backtest.risk.stress import (
    LARGE_SHOCK,
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
        """四个分量加残差必须严格等于实际盈亏——残差就是为了让它成立。"""
        trades = make_trades()
        result = attribute(trades)
        assert sum(result.totals.values()) == pytest.approx(trades["gross_pnl"].sum())

    def test_missing_greeks_excluded_not_zeroed(self):
        """缺希腊字母的交易被排除并计数，不是填 0 混进去。"""
        trades = make_trades()
        trades.loc[0:4, "entry_vega"] = np.nan
        result = attribute(trades)
        assert result.unusable == 5
        assert len(result.per_trade) == len(trades) - 5

    def test_flags_high_residual(self):
        """残差过大说明一阶近似失效，必须标出来而不是当作归因成功。"""
        trades = make_trades()
        trades["gross_pnl"] = trades["gross_pnl"] * 50      # 人为制造巨大残差
        assert attribute(trades).high_residual > 0

    def test_empty_input(self):
        result = attribute(make_trades().iloc[0:0])
        assert result.totals == dict.fromkeys(result.totals, 0.0)

    def test_missing_columns_raise(self):
        with pytest.raises(KeyError, match="缺少归因所需的列"):
            attribute(pd.DataFrame({"net_pnl": [1.0]}))

    def test_group_attribution(self):
        grouped = attribute_by(make_trades(60), "exit_reason")
        assert set(grouped.columns) >= {"delta", "gamma", "vega", "theta", "residual"}
        assert grouped["笔数"].sum() == 60


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
        """大冲击下二阶展开会算出方向都错的结果——宁可不给数字。"""
        grid = stress_grid(greeks, spot_shocks=(-0.20, 0.0), vol_shocks=(0.0,))
        assert np.isnan(grid.loc["-20%", "IV+0%"])
        assert "超出" in grid.loc["-20%", "说明"]

    def test_threshold_is_explicit(self, greeks):
        just_inside = stress_grid(greeks, spot_shocks=(LARGE_SHOCK,), vol_shocks=(0.0,))
        assert just_inside[f"IV+0%"].notna().all()

    def test_direction_makes_sense(self, greeks):
        """正 delta 组合：标的涨应当赚钱。"""
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
        assert np.isnan(result["var"]) and "不可信" in result["note"]

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
        out = scenario_replay(equity, {"远古": ("1999-01-01", "1999-12-31")})
        assert out.loc["远古", "备注"] == "回测区间未覆盖"
        assert np.isnan(out.loc["远古", "区间收益"])

    def test_computes_return_and_drawdown(self):
        idx = pd.date_range("2023-01-03", periods=40, freq="B")
        curve = pd.Series(np.linspace(100, 90, 40), index=idx)
        equity = pd.DataFrame({"equity_mid": curve})
        out = scenario_replay(equity, {"下跌": ("2023-01-03", "2023-03-01")})
        assert out.loc["下跌", "区间收益"] < 0 and out.loc["下跌", "最大回撤"] < 0


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

    @pytest.mark.parametrize("dd,state", [(-0.02, "normal"), (-0.12, "reduced"), (-0.25, "halted")])
    def test_brake_states(self, dd, state):
        assert DrawdownBrake().state(dd) == state

    def test_brake_scale_zero_when_halted(self):
        assert DrawdownBrake().scale(-0.30) == 0.0

    def test_hedge_columns_required(self):
        with pytest.raises(KeyError, match="is_hedge"):
            without_hedges(make_trades())

    def test_hedge_value_separates_core_and_hedge(self):
        trades = make_trades(40)
        trades["is_hedge"] = [i % 5 == 0 for i in range(40)]
        out = hedge_value(trades)
        assert set(out.index) == {"含对冲", "剔除对冲", "对冲本身"}
        assert out.loc["剔除对冲", "笔数"] + out.loc["对冲本身", "笔数"] == 40


class TestExcursion:
    def test_requires_event_driven_columns(self):
        from backtest.research.excursion import study

        trades = make_trades().drop(columns=["mfe"])
        with pytest.raises(KeyError, match="event_driven"):
            study(trades)

    def test_censoring_detected(self):
        from backtest.engine.strategy import ExitRule
        from backtest.research.excursion import study

        result = study(make_trades(), ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5))
        assert any("止损" in c for c in result.censoring)
        assert any("止盈" in c for c in result.censoring)

    def test_no_censoring_when_rules_off(self):
        from backtest.engine.strategy import ExitRule
        from backtest.research.excursion import study

        rule = ExitRule(hold_days=20, take_profit=None, stop_loss=None, dte_floor=5)
        result = study(make_trades(), rule)
        assert not any("止损" in c for c in result.censoring)

    def test_stop_table_counts_killed_winners(self):
        from backtest.research.excursion import study

        trades = make_trades(50)
        table = study(trades).stop_loss_table(levels=(0.3,))
        assert table.loc["-30%", "其中最终本会盈利"] >= 0
        assert table.loc["-30%", "触及笔数"] >= table.loc["-30%", "其中最终本会盈利"]


class TestRegime:
    def test_dimension_label_count_validated(self):
        from backtest.research.regime import Dimension

        with pytest.raises(ValueError, match="标签"):
            Dimension("x", "vix", (0.2, 0.3), ("低", "高"), "两个边界要三个标签")

    def test_missing_source_marked_unavailable(self):
        from backtest.research.regime import DEFAULT_DIMENSIONS, classify

        panel = pd.DataFrame({"vxn": [0.2] * 5},
                             index=pd.date_range("2023-01-03", periods=5, freq="B"))
        regimes = classify(panel, None, DEFAULT_DIMENSIONS)
        assert "波动率期限结构" in regimes.unavailable and "趋势" in regimes.unavailable

    def test_partial_dimensions_yield_unknown(self):
        """任一维度缺失则整体未知——用剩下的凑合会让状态的含义悄悄改变。"""
        from backtest.research.regime import classify

        panel = pd.DataFrame(
            {"vxn": [0.2, 0.35], "term_structure": [1.1, np.nan]},
            index=pd.to_datetime(["2023-01-03", "2023-01-04"]),
        )
        regimes = classify(panel, None)
        assert regimes.frame.loc[pd.Timestamp("2023-01-04"), "combined"] == "未知"

    def test_stratify_flags_thin_samples(self):
        from backtest.research.regime import classify, stratify

        panel = pd.DataFrame({"vxn": [0.15] * 40},
                             index=pd.date_range("2023-01-03", periods=40, freq="B"))
        regimes = classify(panel, None)
        trades = make_trades(5)
        out = stratify(trades, regimes, min_trades=15)
        assert (out["样本"].str.contains("不足")).any()
