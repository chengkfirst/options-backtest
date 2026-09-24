"""事件研究：跌到某个程度之后通常会怎样。

这块最危险的不是算错，是**算出一个看起来很精确的错数**：把一次熊市按天数成
几百个观测、只报条件概率不给基准、窗口不够却按已有天数凑合算。
所以测试重点是这几件事会不会发生。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.research.events import (
    baseline,
    effective_sample,
    find_corrections,
    measure,
    study,
)


def series(values, start="2000-01-03") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def ramp(up_days, down_days, peak=100.0, trough=80.0):
    """先涨到 peak，再跌到 trough，再涨回去。

    ``down_days`` 要足够短——滚动参考高点会跟着阴跌下移，慢慢跌的行情
    在这个口径下根本不算回调（见 test_slow_grind_is_not_a_correction）。
    """
    return ([*np.linspace(peak * 0.8, peak, up_days),
             *np.linspace(peak, trough, down_days),
             *np.linspace(trough, peak * 1.1, up_days)])


class TestFindCorrections:
    def test_counts_episodes_not_days(self):
        """一次回调持续几十天，是 1 段，不是几十个观测。"""
        s = series(ramp(300, 25))
        eps = find_corrections(s, 0.10, reference_window=60)
        assert len(eps) == 1

    def test_slow_grind_is_not_a_correction(self):
        """**方法的固有性质**：滚动参考高点会跟着阴跌下移。

        两百天从 100 缓慢跌到 80，在 60 日滚动口径下最深只有约 -7%，
        根本不触发阈值。这不是 bug——"回调"的定义本来就是相对近期高点的
        快速下跌。但用这个工具的人必须知道：慢熊不会出现在结果里。
        """
        s = series(ramp(300, 200))
        assert find_corrections(s, 0.10, reference_window=60) == []

    def test_new_episode_needs_a_new_peak(self):
        """同一个前高下的两次触及是一次事件。"""
        # 涨 → 跌破 → 小反弹（不创新高）→ 再跌破
        vals = [*np.linspace(80, 100, 120), *np.linspace(100, 88, 20),
                *np.linspace(88, 95, 10), *np.linspace(95, 87, 15)]
        eps = find_corrections(series(vals), 0.10, reference_window=60)
        assert len(eps) == 1, f"同一个前高下应当只算一段，实际 {len(eps)}"

    def test_threshold_must_be_positive_fraction(self):
        with pytest.raises(ValueError, match="threshold"):
            find_corrections(series([1, 2, 3]), -0.1)

    def test_records_reference_high(self):
        eps = find_corrections(series(ramp(200, 120)), 0.10, reference_window=60)
        assert eps and eps[0].ref_high > eps[0].price
        assert eps[0].drawdown <= -0.10

    def test_no_episode_when_never_falls(self):
        assert find_corrections(series(np.linspace(100, 200, 300)), 0.10) == []


class TestMeasure:
    def test_excludes_windows_past_data_end(self):
        """数据不够的窗口**不出现**，而不是按已有天数凑合算。"""
        s = series(ramp(200, 100))
        eps = find_corrections(s, 0.10, reference_window=60)
        out = measure(s, eps, horizons_months=(1, 120))
        assert (out["horizon_months"] == 1).any()
        assert not (out["horizon_months"] == 120).any()

    def test_path_measures_are_not_endpoint_measures(self):
        """"中途再跌 10%" 要看路径；只看终点会漏掉。"""
        # 跌破后先深跌再涨回起点：终点持平，但路径上跌过
        vals = [*np.linspace(80, 100, 120), *np.linspace(100, 88, 20),
                *np.linspace(88, 70, 20), *np.linspace(70, 88, 20)]
        s = series(vals)
        eps = find_corrections(s, 0.10, reference_window=60)
        out = measure(s, eps, horizons_months=(2,))
        assert out["touched_minus_10"].iloc[0]
        # 终点离起点不远，但路径上跌得很深——两者必须分开测量
        assert abs(out["end_return"].iloc[0]) < 0.05
        assert out["max_drawdown_after"].iloc[0] < -0.15


class TestIntradayConvention:
    """判定事件用收盘，衡量痛苦用日内。两者混用会让结论系统性偏好。"""

    def bars(self):
        """收盘温和、日内插针的行情：收盘从不跌破 5%，日内跌破 12%。"""
        # 插针段要够长，否则被「末尾截断」规则正确地排除掉（这条规则本身另有测试）
        close = [*np.linspace(80, 100, 120), *[97.0] * 40]
        low = [*np.linspace(80, 100, 120), *[88.0] * 40]     # 日内插针
        idx = pd.bdate_range("2000-01-03", periods=len(close))
        return pd.DataFrame({"close": close, "high": close, "low": low}, index=idx)

    def test_event_detection_ignores_intraday_spikes(self):
        """日内插一下又收回来，不算一次回调。"""
        assert find_corrections(self.bars(), 0.10, reference_window=60) == []

    def test_pain_measures_use_intraday_low(self):
        """止损是被日内打掉的。只看收盘会说"从没跌过 10%"。"""
        from backtest.research.events import Episode

        b = self.bars()
        ep = Episode(b.index[120], 97.0, 100.0, b.index[119], -0.03, None)
        with_low = measure(b, [ep], horizons_months=(1,))
        close_only = measure(b["close"], [ep], horizons_months=(1,))
        assert with_low["touched_minus_5"].iloc[0]
        assert not close_only["touched_minus_5"].iloc[0]
        assert with_low["max_drawdown_after"].iloc[0] < close_only["max_drawdown_after"].iloc[0]

    def test_close_only_input_is_flagged(self):
        """只给收盘时必须标明口径，不能让人以为拿到的是完整数据。"""
        s = series(ramp(300, 25))
        assert study(s, 0.10)["intraday"] is False
        assert "低估" in study(s, 0.10)["口径"]

    def test_ohlc_input_is_flagged_too(self):
        vals = ramp(300, 25)
        idx = pd.bdate_range("2000-01-03", periods=len(vals))
        b = pd.DataFrame({"close": vals, "high": vals, "low": vals}, index=idx)
        assert study(b, 0.10)["intraday"] is True

    def test_missing_close_column_raises(self):
        with pytest.raises(ValueError, match="close"):
            find_corrections(pd.DataFrame({"high": [1.0], "low": [1.0]}), 0.10)


class TestBaseline:
    def test_baseline_exists_for_each_horizon(self):
        b = baseline(series(np.linspace(100, 200, 600)), horizons_months=(1, 3))
        assert set(b["horizon_months"]) == {1, 3}

    def test_rising_market_has_high_unconditional_rate(self):
        """这正是需要基准的理由：单边上涨里无条件概率本来就接近 1。"""
        b = baseline(series(np.linspace(100, 300, 600)), horizons_months=(3,))
        assert b["上涨"].iloc[0] > 0.95


class TestEffectiveSample:
    def test_overlapping_episodes_collapse(self):
        """十二个月窗口高度重叠时，有效样本远小于名义样本。"""
        from backtest.research.events import Episode

        eps = [Episode(pd.Timestamp(f"2020-0{m}-01"), 100.0, 120.0,
                       pd.Timestamp("2019-12-01"), -0.17, None) for m in (1, 2, 3, 4)]
        assert effective_sample(eps, 12) == 1
        # 一个月窗按 30 天算：1/1 → 1/31，2/1 过关；2/1+30 = 3/2，3/1 落在窗内被吞
        assert effective_sample(eps, 1) == 3

    def test_empty(self):
        assert effective_sample([], 12) == 0


class TestStudy:
    @pytest.fixture(scope="class")
    def result(self):
        rng = np.random.default_rng(7)
        # 带漂移的随机游走，足够长以产生若干回调
        steps = rng.normal(0.0004, 0.014, 4000)
        return study(series(100 * np.exp(np.cumsum(steps))), 0.10)

    def test_table_pairs_conditional_with_baseline(self):
        """**条件概率必须和基准并排**，只报前者是在骗自己。"""
        rng = np.random.default_rng(7)
        s = series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, 4000))))
        t = study(s, 0.10)["table"]
        assert {"上涨", "基准上涨", "超额"} <= set(t.columns)

    def test_excess_is_the_difference(self, result):
        t = result["table"]
        usable = t[t["上涨"].notna()]
        for _, r in usable.iterrows():
            assert r["超额"] == pytest.approx(r["上涨"] - r["基准上涨"])

    def test_reports_effective_sample(self, result):
        assert (result["table"]["有效样本"] <= result["table"]["样本"]).all()

    def test_tiny_sample_says_so_bluntly(self):
        """三个观测就该说"这不是统计，是三个故事"。"""
        from backtest.research.events import _sample_warning

        assert "三个故事" in _sample_warning(3, 3)
        assert _sample_warning(40, 30) == ""

    def test_reports_what_was_excluded(self, result):
        assert set(result["excluded_by_data_end"]) == {1, 3, 6, 12}
