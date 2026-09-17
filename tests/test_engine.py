"""成交表引擎：执行延迟、首次触发、优先级、守卫、被动到期。

首次触发这类逻辑用**脚本化的报价**来测，而不是用合成链——只有当每日价格由测试
指定时，"应该在第几天以什么原因了结"才有唯一正确答案。
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd
import pytest

from backtest.engine.strategy import EntryRule, ExitRule, Strategy
from backtest.engine.trade_table import TradeTableEngine
from backtest.errors import StartupError
from backtest.selection.rules import SelectionRule
from backtest.types import CHAIN_COLUMNS, ContractId, ExitReason, Right


class ScriptedChain:
    """按脚本给价的链：``prices`` 是 {日期: 理论价}，价差固定 2%。"""

    def __init__(self, contract: ContractId, prices: dict[date, float], spot: dict[date, float] | None = None):
        self.contract = contract
        self.prices = prices
        self.spot = spot or {}

    def _row(self, day: date, price: float) -> dict:
        row = {c: np.nan for c in CHAIN_COLUMNS}
        half = max(price * 0.02, 0.01) / 2
        row.update(
            asof=day, symbol=self.contract.symbol, expiry=self.contract.expiry,
            strike=self.contract.strike, right=self.contract.right.value,
            bid=round(price - half, 4), ask=round(price + half, 4), last=price,
            iv=0.22, delta=0.3, gamma=0.01, vega=0.2, theta=-0.05,
            underlying_price=self.spot.get(day, 400.0), source="synthetic",
            quality_flags="", greeks_model_derived=True,
        )
        return row

    def snapshot(self, day: date) -> pd.DataFrame:
        if day not in self.prices:
            return pd.DataFrame(columns=list(CHAIN_COLUMNS))
        return pd.DataFrame([self._row(day, self.prices[day])])

    def series(self, contract: ContractId, days: Iterable[date]) -> pd.DataFrame:
        rows = [self._row(d, self.prices[d]) for d in days if d in self.prices]
        return pd.DataFrame(rows, columns=list(CHAIN_COLUMNS)) if rows else pd.DataFrame(columns=list(CHAIN_COLUMNS))


def always_enter(view) -> bool:
    return True


def enter_on(days: set[date]):
    def condition(view) -> bool:
        return view.day in days

    return condition


@pytest.fixture
def scripted(market, config):
    """构造一笔可控交易：信号日、入场日、以及其后的价格脚本。"""

    def build(path: list[float], *, exit_rule: ExitRule, expiry_offset: int = 40):
        sessions = market.sessions()
        signal_day = sessions[60]
        entry_day = market.calendar.shift(signal_day, config.execution_lag_days)
        expiry = market.calendar.shift(entry_day, expiry_offset)
        contract = ContractId("QQQ", expiry, Right.CALL, 400.0)
        window = [d for d in sessions if d >= entry_day][: len(path)]
        prices = dict(zip(window, path))
        chain = ScriptedChain(contract, prices)
        strategy = Strategy(
            entry=EntryRule("scripted", enter_on({signal_day})),
            select=SelectionRule(
                target_dte=(expiry - entry_day).days,
                dte_window=(1, 400),
                target_delta=0.30,
            ),
            exit=exit_rule,
            quantity=1,
        )
        return signal_day, entry_day, contract, chain, strategy

    return build


class TestExecutionLag:
    def test_entry_is_next_session_after_signal(self, market, chain, signals, config):
        """T 日收盘产生意图，T+1 收盘执行。用 T 日收盘价同时决策与成交是前视。"""
        sessions = market.sessions()
        signal_day = sessions[60]
        strategy = Strategy(
            entry=EntryRule("one_day", enter_on({signal_day})),
            select=SelectionRule(target_delta=0.30),
            exit=ExitRule(hold_days=5, take_profit=None, stop_loss=None, dte_floor=5),
        )
        result = TradeTableEngine(market, chain, signals, config).run(strategy)
        assert len(result.records) == 1
        assert result.records[0].entry_date == market.calendar.shift(signal_day, 1)

    def test_signal_on_last_session_is_dropped(self, market, chain, signals, config):
        last = market.sessions()[-1]
        strategy = Strategy(
            entry=EntryRule("last", enter_on({last})),
            select=SelectionRule(target_delta=0.30),
            exit=ExitRule(hold_days=5, take_profit=None, stop_loss=None, dte_floor=5),
        )
        result = TradeTableEngine(market, chain, signals, config).run(strategy)
        assert not result.records
        assert "candidate_dropped" in result.events.counts()


class TestFirstTrigger:
    def test_take_profit(self, market, signals, config, scripted):
        path = [5.0, 5.5, 11.0, 12.0, 6.0]  # 第 2 个交易日翻倍，次日成交
        _, entry, contract, chain, strategy = scripted(
            path, exit_rule=ExitRule(hold_days=20, take_profit=1.0, stop_loss=0.5, dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.TAKE_PROFIT

    def test_stop_loss(self, market, signals, config, scripted):
        path = [5.0, 4.5, 2.0, 1.9, 6.0]
        _, _, _, chain, strategy = scripted(
            path, exit_rule=ExitRule(hold_days=20, take_profit=1.0, stop_loss=0.5, dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.STOP_LOSS

    def test_time_exit_when_nothing_triggers(self, market, signals, config, scripted):
        path = [5.0, 5.1, 5.2, 5.1, 5.0, 5.05, 5.0, 5.0]
        _, _, _, chain, strategy = scripted(
            path, exit_rule=ExitRule(hold_days=4, take_profit=1.0, stop_loss=0.5, dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.TIME_EXIT

    def test_earliest_trigger_wins(self, market, signals, config, scripted):
        """先跌破止损再涨到止盈：应当记止损，而不是事后诸葛地取更好的那个。"""
        path = [5.0, 2.0, 2.1, 12.0, 12.0]
        _, _, _, chain, strategy = scripted(
            path, exit_rule=ExitRule(hold_days=20, take_profit=1.0, stop_loss=0.5, dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.STOP_LOSS

    def test_same_day_conflict_takes_priority_and_records_suppressed(
        self, market, signals, config, scripted
    ):
        """止盈与持有期满同日成立：按优先级取止盈，被压制的原因仍可查。

        止盈与止损不可能同日成立（价格无法既 +100% 又 -50%），真实的同日冲突发生在
        价格类与时间类条件之间。
        """
        path = [5.0, 5.2, 11.0, 10.8]  # 第 2 个交易日既翻倍、又正好是持有期上限；第 3 天成交
        _, _, _, chain, strategy = scripted(
            path, exit_rule=ExitRule(hold_days=2, take_profit=1.0, stop_loss=0.5, dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.TAKE_PROFIT
        assert ExitReason.TIME_EXIT in rec.suppressed_reasons

    def test_dte_floor_triggers(self, market, signals, config, scripted):
        path = [5.0] * 12
        _, _, _, chain, strategy = scripted(
            path,
            exit_rule=ExitRule(hold_days=None, take_profit=None, stop_loss=None, dte_floor=6),
            expiry_offset=8,
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.DTE_FLOOR


class TestGuards:
    def test_rejects_unknown_signal_at_startup(self, market, chain, signals, config):
        strategy = Strategy(
            entry=EntryRule("bad", always_enter, requires=("not_a_signal",)),
            select=SelectionRule(target_delta=0.30),
            exit=ExitRule(),
        )
        with pytest.raises(StartupError, match="未注册的信号"):
            TradeTableEngine(market, chain, signals, config).run(strategy)

    def test_exit_rule_requires_time_condition(self):
        """没有时间类了结条件，仓位会被拖到到期。"""
        with pytest.raises(StartupError, match="时间类了结条件"):
            ExitRule(hold_days=None, dte_floor=None, take_profit=1.0, stop_loss=0.5)

    def test_quantity_must_be_positive(self):
        with pytest.raises(StartupError):
            Strategy(
                entry=EntryRule("x", always_enter),
                select=SelectionRule(target_delta=0.3),
                exit=ExitRule(),
                quantity=0,
            )


class TestPnlIntegrity:
    def test_net_equals_gross_minus_fees(self, market, chain, signals, config):
        strategy = Strategy(
            entry=EntryRule("all", always_enter),
            select=SelectionRule(target_delta=0.30),
            exit=ExitRule(hold_days=8, take_profit=1.0, stop_loss=0.5, dte_floor=5),
        )
        result = TradeTableEngine(market, chain, signals, config).run(strategy)
        assert result.records
        for r in result.records:
            assert r.gross_pnl == pytest.approx(
                (r.exit_price - r.entry_price) * r.multiplier * r.quantity
            )
            assert r.net_pnl == pytest.approx(r.gross_pnl - r.fees)
            assert r.fees == pytest.approx(
                r.fee_commission + r.fee_regulatory + r.fee_slippage
            )

    def test_concurrency_and_capital_check(self, market, chain, signals, config):
        strategy = Strategy(
            entry=EntryRule("all", always_enter),
            select=SelectionRule(target_delta=0.30),
            exit=ExitRule(hold_days=8, take_profit=1.0, stop_loss=0.5, dte_floor=5),
        )
        result = TradeTableEngine(market, chain, signals, config).run(strategy)
        assert result.concurrency.max() >= 1
        check = result.capital_check()
        assert check["peak_capital"] > 0 and isinstance(check["feasible"], bool)


class TestSweep:
    """参数扫描：速度是它的价值，试验次数是它的刹车。"""

    @pytest.fixture
    def pieces(self, market, chain, signals, config):
        strategy = Strategy(
            entry=EntryRule("all", always_enter),
            select=SelectionRule(target_delta=0.30),
            exit=ExitRule(hold_days=8, take_profit=1.0, stop_loss=0.5, dte_floor=5),
        )
        return market, chain, signals, config, strategy

    def test_covers_full_grid(self, pieces):
        from backtest.engine.sweep import sweep

        res = sweep(*pieces, {"take_profit": [0.5, 1.0], "stop_loss": [0.3, 0.5]})
        assert res.trials == 4 and len(res.table) == 4

    def test_records_trial_count(self, pieces):
        """扫了多少组必须留档——阶段 2 的多重检验修正要用它，不让人假装只试了一次。"""
        from backtest.engine.sweep import sweep

        res = sweep(*pieces, {"take_profit": [0.5, 1.0, 1.5]})
        assert res.trials == 3

    def test_runs_are_independent_and_reproducible(self, pieces):
        from backtest.engine.sweep import sweep

        grid = {"take_profit": [0.5, 1.0]}
        first = sweep(*pieces, grid).table
        second = sweep(*pieces, grid).table
        pd.testing.assert_frame_equal(first, second)

    def test_rejects_unknown_parameter(self, pieces):
        from backtest.engine.sweep import sweep

        with pytest.raises(StartupError, match="不可扫描"):
            sweep(*pieces, {"leverage": [1, 2]})

    def test_rejects_empty_grid(self, pieces):
        from backtest.engine.sweep import sweep

        with pytest.raises(StartupError, match="为空"):
            sweep(*pieces, {})

    def test_dte_window_follows_target(self, pieces):
        """扫 target_dte 时窗口自动跟随，否则会撞上"目标不在窗口内"的校验。"""
        from backtest.engine.sweep import sweep

        res = sweep(*pieces, {"target_dte": [14, 30, 45]})
        assert res.trials == 3 and res.table["count"].sum() > 0

    def test_top_is_ordered(self, pieces):
        from backtest.engine.sweep import sweep

        res = sweep(*pieces, {"take_profit": [0.5, 1.0, 1.5]})
        top = res.top(3)
        assert list(top["net_pnl"]) == sorted(top["net_pnl"], reverse=True)
