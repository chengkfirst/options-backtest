"""逐日复查：动态止盈止损、分批加减仓。

这块最危险的不是"跑不起来"，是**盈亏悄悄算错**。减仓之后最终记录只按剩余张数
算进出价差，先减掉的那几张的盈亏会凭空消失——回测照常跑完，数字看着正常。
所以测试重点是**交易明细与账本必须分毫不差**。
"""

from __future__ import annotations

import pytest

from backtest.engine.review import Add, Close, PositionView, Reduce, Roll
from backtest.errors import StartupError


class TestActions:
    def test_close_kind_validated(self):
        with pytest.raises(StartupError, match="未知的了结类别"):
            Close("x", kind="随便写")

    @pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.2])
    def test_reduce_fraction_bounds(self, bad):
        with pytest.raises(StartupError, match="减仓比例"):
            Reduce(bad)

    def test_add_at_least_one(self):
        with pytest.raises(StartupError, match="至少 1 张"):
            Add(0)

    def test_roll_refuses_loudly(self):
        """展期没实现。悄悄当成平仓会让胜率与持有期失真，所以直接拒绝。"""
        with pytest.raises(StartupError, match="展期还没实现"):
            Roll(to_contract=None)


class TestEngineIntegration:
    BASE = dict(
        symbol="QQQ", start="2022-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        exit={"hold_days": 30, "take_profit": 1.0, "stop_loss": 0.5, "dte_floor": 7},
        engine="event_driven", sizing={"method": "risk_pct", "risk_pct": 0.03},
    )

    def _run(self, **extra):
        from backtest import run

        return run(**self.BASE, **extra)

    def test_reduce_keeps_one_logical_trade(self):
        """减仓不得让一笔变成两笔——展期三次按持仓统计会变成四笔，胜率全失真。

        判据是 **trade_id 不重复**，不是"总笔数与不减仓时相同"：仓位大小依赖权益，
        减仓会改变权益路径，后续某些交易的张数随之变化（算出 0 张就不开仓），
        所以两个变体的总笔数本来就会不同。
        """
        def review(ctx, pos):
            return Reduce(0.5, "减半") if (pos.days_held == 5 and pos.quantity >= 2) else None

        r = self._run(review=review)
        t = r.trades
        assert len(t) == t.trade_id.nunique(), "减仓把一笔逻辑交易拆成了多行"
        events = r.events
        assert events.detail.astype(str).str.contains("减仓").any(), "这次没有真的发生减仓"

    def test_partial_close_pnl_is_not_lost(self):
        """最终记录按剩余张数算，天然漏掉先减掉的那几张——不报错，盈亏少一块。"""
        def review(ctx, pos):
            return Reduce(0.5, "减半") if (pos.days_held == 5 and pos.quantity >= 2) else None

        r = self._run(review=review)
        fees = sum(x.fees for x in r.raw.records)
        assert r.trades.net_pnl.sum() == pytest.approx(r.raw.ledger.realised - fees, abs=0.01)

    def test_dynamic_exit_changes_outcome(self):
        """复查真的改变了结果，而不是被静默忽略。"""
        def review(ctx, pos):
            return Close("跟踪止盈", kind="take_profit") if pos.ratio > 0.2 else None

        plain, dynamic = self._run(), self._run(review=review)
        assert dynamic.trades.holding_days.mean() < plain.trades.holding_days.mean()

    def test_static_rules_still_apply(self):
        """静态规则是安全网，复查绕不过去——否则写错的复查会把仓位拖到到期。"""
        r = self._run(review=lambda ctx, pos: None)
        assert (r.trades.holding_days <= 30).all()

    def test_bad_return_is_rejected(self):
        with pytest.raises(StartupError, match="应当返回 Close"):
            self._run(review=lambda ctx, pos: "平掉")

    def test_exception_carries_the_day(self):
        def review(ctx, pos):
            raise ValueError("我坏了")

        with pytest.raises(StartupError, match="复查函数在"):
            self._run(review=review)

    def test_trade_table_refuses_review(self):
        """成交表向量化算出场，逐日复查它做不到——拒绝，不给近似。"""
        from backtest import run

        opts = {**self.BASE, "engine": "trade_table"}
        with pytest.raises(StartupError):
            run(**opts, review=lambda ctx, pos: Close("x"))
