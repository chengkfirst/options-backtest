"""事件驱动引擎。

最重要的一条是 :class:`TestDualEngine` —— 同一策略在无资金约束、无并发上限时，
两个引擎必须产出逐行相同的交易明细。它们的实现方式完全不同（一个查表、一个逐日推进），
所以任何一边算错都会被对方照出来。这条在开发中第一次跑就抓到了一个语义分歧：
成交表允许对同一张合约重复建仓，事件驱动不允许。
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.engine.event_driven import EventDrivenEngine
from backtest.engine.expression import parse_condition
from backtest.engine.strategy import EntryRule, ExitRule, Strategy
from backtest.engine.trade_table import TradeTableEngine
from backtest.errors import StartupError
from backtest.selection.rules import SelectionRule
from backtest.types import Right


def build_strategy(signals, **overrides):
    condition, requires = parse_condition("sma_20 > sma_50", signals.names)
    defaults = dict(
        name="baseline",
        entry=EntryRule("sma_20 > sma_50", condition, requires),
        select=SelectionRule(right=Right.CALL, target_dte=30, dte_window=(21, 45), target_delta=0.30),
        exit=ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5, dte_floor=5),
        quantity=1,
    )
    return Strategy(**(defaults | overrides))


def comparable(records) -> pd.DataFrame:
    """两个引擎的记录顺序不同（一个按入场、一个按了结），所以先排序再比。"""
    return (
        pd.DataFrame([
            {
                "entry": r.entry_date, "exit": r.exit_date, "contract": str(r.contract),
                "entry_price": round(r.entry_price, 8), "exit_price": round(r.exit_price, 8),
                "net_pnl": round(r.net_pnl, 6), "reason": r.exit_reason.value,
                "holding": r.holding_days,
            }
            for r in records
        ])
        .sort_values(["entry", "contract"])
        .reset_index(drop=True)
    )


@pytest.fixture(scope="module")
def both(market, chain, signals, config):
    """同一策略跑两个引擎。事件驱动关掉资金约束与并发上限以求可比。"""
    strategy = build_strategy(signals)
    table = TradeTableEngine(market, chain, signals, config).run(strategy)
    driven = EventDrivenEngine(
        market, chain, signals, config, max_concurrent=None, enforce_capital=False
    ).run(strategy)
    return table, driven


class TestDualEngine:
    def test_same_trade_count(self, both):
        table, driven = both
        assert len(table.records) == len(driven.records) > 0

    def test_row_for_row_identical(self, both):
        """逐行一致。不一致说明两边有一个算错了，或者语义悄悄分歧了。"""
        table, driven = both
        pd.testing.assert_frame_equal(comparable(table.records), comparable(driven.records))

    def test_event_driven_rejects_stacking(self, market, chain, signals, config):
        """事件驱动拒绝 allow_stacking，而不是静默丢掉合并后拆不回来的那些交易。

        账本按合约净额记仓（真实券商就是这样），引擎按 trade_id 追踪逻辑交易，
        两者在同一张合约上建两次仓时对不上——第二笔会永远挂着。宁可报错。
        """
        stacked = build_strategy(signals, allow_stacking=True)
        with pytest.raises(StartupError, match="allow_stacking"):
            EventDrivenEngine(market, chain, signals, config).run(stacked)

    def test_trade_table_still_allows_stacking(self, market, chain, signals, config):
        """成交表各笔独立计算，开这个开关是成立的——只是结果不再与事件驱动可比。"""
        plain = TradeTableEngine(market, chain, signals, config).run(build_strategy(signals))
        stacked = TradeTableEngine(market, chain, signals, config).run(
            build_strategy(signals, allow_stacking=True))
        assert len(stacked.records) > len(plain.records)


class TestPortfolioState:
    @pytest.fixture(scope="class")
    def result(self, market, chain, signals, config):
        return EventDrivenEngine(
            market, chain, signals, config, max_concurrent=3, enforce_capital=True
        ).run(build_strategy(signals))

    def test_equity_curve_covers_every_session(self, result, market):
        assert len(result.snapshots) == len(market.sessions())

    def test_liquidation_equity_never_above_mid(self, result):
        """多头按买价、空头按卖价估值，清算权益必然不高于中价权益。"""
        equity = result.equity
        assert (equity["equity_liq"] <= equity["equity_mid"] + 1e-9).all()

    def test_concurrency_limit_respected(self, result):
        assert result.equity["positions"].max() <= 3

    def test_no_position_left_open(self, result):
        """区间结束时不应留着未了结的持仓——那说明了结路径有漏。"""
        assert len(result.ledger) == 0

    def test_every_trade_has_both_contexts(self, result):
        for record in result.records[:20]:
            assert "iv" in record.entry_context and "iv" in record.exit_context
            assert any(k.startswith("sig_") for k in record.entry_context)

    def test_accounting_conserved_throughout(self, result):
        """主循环每天都会校验守恒；能跑完就说明 753 天全部通过。"""
        assert result.snapshots

    def test_cash_never_negative_when_enforced(self, result):
        assert (result.equity["cash"] >= -1e-9).all()


class TestCapitalConstraint:
    def test_small_capital_reduces_trades(self, market, chain, signals, config):
        """资金不足时开不出仓——这正是成交表引擎看不见的那部分。"""
        import dataclasses

        strategy = build_strategy(signals)
        rich = EventDrivenEngine(
            market, chain, signals,
            dataclasses.replace(config, notional_capital=10_000_000),
            max_concurrent=None, enforce_capital=True,
        ).run(strategy)
        poor = EventDrivenEngine(
            market, chain, signals,
            dataclasses.replace(config, notional_capital=800.0),
            max_concurrent=None, enforce_capital=True,
        ).run(strategy)
        assert len(poor.records) < len(rich.records)


class TestGuards:
    def test_rejects_unknown_signal(self, market, chain, signals, config):
        bad = build_strategy(signals, entry=EntryRule("bad", lambda v: True, ("nope",)))
        with pytest.raises(StartupError, match="未注册的信号"):
            EventDrivenEngine(market, chain, signals, config).run(bad)

    def test_rejects_dte_floor_below_lag(self, market, chain, signals, config):
        bad = build_strategy(
            signals, exit=ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5, dte_floor=1)
        )
        with pytest.raises(StartupError, match="剩余到期下限"):
            EventDrivenEngine(market, chain, signals, config).run(bad)


class TestDeterminism:
    def test_two_runs_identical(self, market, chain, signals, config):
        strategy = build_strategy(signals)
        a = EventDrivenEngine(market, chain, signals, config, max_concurrent=3).run(strategy)
        b = EventDrivenEngine(market, chain, signals, config, max_concurrent=3).run(strategy)
        pd.testing.assert_frame_equal(comparable(a.records), comparable(b.records))
        pd.testing.assert_frame_equal(a.equity, b.equity)
