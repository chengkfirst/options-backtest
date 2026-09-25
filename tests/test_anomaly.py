"""异常扫描。

两个方向都要测：**该触发的要触发**，**不该触发的绝不能触发**。
后者更重要——硬凑出来的"洞察"是噪音，而噪音会让真的警告也被跳过。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.research.anomaly import Finding, scan


def trades(n=100, seed=0, **over) -> pd.DataFrame:
    """一份平平无奇的交易明细：没有任何一条规则该触发。"""
    rng = np.random.default_rng(seed)
    entry = pd.date_range("2018-01-03", periods=n, freq="9D")
    # 盈亏按顺序交替，保证各年贡献接近——随机正态在 100 笔上很容易让某一年
    # 占掉绝大部分，那样夹具本身就是"异常"的，测不出"正常时保持沉默"。
    pnl = np.where(np.arange(n) % 3 == 0, 400.0, -150.0) + rng.normal(0, 20, n)
    frame = pd.DataFrame({
        "entry_date": entry,
        "exit_date": entry + pd.Timedelta(days=5),      # 互不重叠
        "net_pnl": pnl,
        "exit_reason": np.resize(["take_profit", "stop_loss", "time_exit"], n),
    })
    for k, v in over.items():
        frame[k] = v
    return frame


class TestQuietWhenNormal:
    def test_normal_result_finds_nothing(self):
        """**最重要的一条**：正常结果必须一条都不报。"""
        assert scan(trades()) == []

    def test_empty_input(self):
        assert scan(pd.DataFrame()) == []

    def test_small_sample_does_not_trigger_count_rules(self):
        """样本太少时规则应当沉默，而不是拿 5 笔得出结论。"""
        assert scan(trades(n=8)) == []

    def test_a_broken_rule_does_not_kill_the_scan(self):
        """某条规则算不了，其余的还要照常跑。"""
        t = trades()
        t["exit_date"] = "不是日期"          # 让重叠那条算不出来
        scan(t)                              # 不抛异常即可


class TestRulesFire:
    def kinds(self, t, **kw) -> set[str]:
        return {f.kind for f in scan(t, **kw)}

    def test_single_trade_dominates(self):
        t = trades(n=40, seed=1)
        t.loc[0, "net_pnl"] = t["net_pnl"].clip(lower=0).sum() * 3 + 5000
        assert "single_trade" in self.kinds(t)

    def test_one_year_carries(self):
        t = trades(n=90, seed=2)
        t["net_pnl"] = -50.0
        mask = pd.to_datetime(t["exit_date"]).dt.year == 2019
        t.loc[mask, "net_pnl"] = 900.0
        assert "one_year" in self.kinds(t)

    def test_edge_decay(self):
        t = trades(n=120, seed=3)
        t["net_pnl"] = [300.0] * 60 + [-300.0] * 60
        assert "decay" in self.kinds(t)

    def test_dead_exit_when_reason_missing(self):
        """止盈一次都没触发——那个档位是装饰品。"""
        t = trades(n=60, seed=4)
        t["exit_reason"] = ["stop_loss"] * 30 + ["time_exit"] * 30
        assert "dead_exit" in self.kinds(t)

    def test_dead_exit_when_reason_is_rare(self):
        t = trades(n=100, seed=5)
        t["exit_reason"] = (["take_profit"] * 2 + ["stop_loss"] * 49
                            + ["time_exit"] * 49)
        assert "dead_exit" in self.kinds(t)

    def test_costs_eat_edge(self):
        found = self.kinds(trades(), costs={"fees_pct_of_gross": 0.55, "total": 9000})
        assert "costs" in found

    def test_costs_quiet_when_reasonable(self):
        assert "costs" not in self.kinds(trades(), costs={"fees_pct_of_gross": 0.12})

    def test_overlap(self):
        t = trades(n=60, seed=6)
        t["exit_date"] = pd.to_datetime(t["entry_date"]) + pd.Timedelta(days=200)
        assert "overlap" in self.kinds(t)

    def test_too_good_for_the_sample(self):
        assert "too_good" in self.kinds(trades(n=50), metrics={"夏普": 3.4})

    def test_too_good_quiet_with_enough_trades(self):
        assert "too_good" not in self.kinds(trades(n=400), metrics={"夏普": 3.4})

    def test_loss_concentration(self):
        t = trades(n=60, seed=7)
        t["net_pnl"] = [-100.0] * 40 + [100.0] * 20
        t["exit_reason"] = ["stop_loss"] * 40 + ["take_profit"] * 20
        assert "loss_concentration" in self.kinds(t)


class TestOutput:
    def test_high_severity_comes_first(self):
        t = trades(n=120, seed=8)
        t["net_pnl"] = [300.0] * 60 + [-300.0] * 60          # decay → high
        t["exit_date"] = pd.to_datetime(t["entry_date"]) + pd.Timedelta(days=300)  # overlap → note
        found = scan(t)
        assert found and found[0].severity == "high"

    def test_every_finding_says_what_and_why(self):
        t = trades(n=120, seed=9)
        t["net_pnl"] = [300.0] * 60 + [-300.0] * 60
        for f in scan(t):
            assert f.headline and len(f.detail) > 20
            assert f.severity in ("high", "note")
