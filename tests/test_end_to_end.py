"""端到端：表达式、确定性、导出、账目守恒。

这些用离线夹具跑，因此不依赖网络、结果逐位可复现。
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from backtest.api import Result
from backtest.engine.expression import parse_condition
from backtest.engine.strategy import EntryRule, ExitRule, Strategy
from backtest.engine.trade_table import TradeTableEngine
from backtest.errors import StartupError
from backtest.selection.rules import SelectionRule
from backtest.types import Right


@pytest.fixture
def baseline(market, chain, signals, config):
    """基线策略：均线入场、30DTE、delta 0.30 看涨、四条件先达者了结。"""
    condition, requires = parse_condition("sma_20 > sma_50", signals.names)
    strategy = Strategy(
        name="baseline",
        entry=EntryRule("sma_20 > sma_50", condition, requires),
        select=SelectionRule(right=Right.CALL, target_dte=30, dte_window=(21, 45), target_delta=0.30),
        exit=ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5, dte_floor=5),
        quantity=1,
    )
    return lambda: Result(TradeTableEngine(market, chain, signals, config).run(strategy))


class TestExpression:
    def test_parses_comparison(self, signals):
        condition, names = parse_condition("sma_20 > sma_50", signals.names)
        assert names == ("sma_20", "sma_50")

    def test_boolean_composition(self, signals, market):
        condition, _ = parse_condition("sma_20 > sma_50 and rsi_14 < 70", signals.names)
        assert isinstance(condition(signals.on(market.sessions()[100])), bool)

    def test_unknown_name_fails_at_startup(self, signals):
        with pytest.raises(StartupError, match="未注册的信号"):
            parse_condition("sma_20 > nope", signals.names)

    def test_syntax_error_reported(self, signals):
        with pytest.raises(StartupError, match="语法错误"):
            parse_condition("sma_20 >", signals.names)

    @pytest.mark.parametrize(
        "expr", ["__import__('os').system('ls')", "open('/etc/passwd')", "[x for x in ()]"]
    )
    def test_rejects_non_whitelisted_nodes(self, signals, expr):
        """只允许比较、and/or/not 与四则运算——表达式不是执行任意代码的入口。"""
        with pytest.raises(StartupError):
            parse_condition(expr, signals.names)

    def test_missing_signal_means_no_entry(self, signals, market):
        """任一信号缺失则整条件不成立；缺失绝不被当成 0 参与比较。"""
        condition, _ = parse_condition("sma_200 > 0", signals.names)
        assert condition(signals.on(market.bars.dates[0])) is False


class TestDeterminism:
    def test_two_runs_are_identical(self, baseline):
        a, b = baseline().trades, baseline().trades
        pd.testing.assert_frame_equal(a, b)

    def test_export_is_byte_identical(self, baseline, tmp_path):
        first = baseline().export(tmp_path / "a")
        second = baseline().export(tmp_path / "b")
        for key in ("trades", "pnl"):
            assert first[key].read_bytes() == second[key].read_bytes()


class TestAccounting:
    def test_pnl_conservation(self, baseline):
        """每笔净盈亏 = 了结金额 − 入场金额 − 全部费用。"""
        trades = baseline().trades
        assert not trades.empty
        expected = (
            (trades["exit_price"] - trades["entry_price"])
            * trades["multiplier"]
            * trades["quantity"]
            - trades["fees"]
        )
        pd.testing.assert_series_equal(
            trades["net_pnl"], expected, check_names=False, rtol=1e-12
        )

    def test_fees_are_decomposed(self, baseline):
        trades = baseline().trades
        total = trades[["fee_commission", "fee_regulatory", "fee_slippage"]].sum(axis=1)
        pd.testing.assert_series_equal(trades["fees"], total, check_names=False, rtol=1e-12)

    def test_holding_days_non_negative(self, baseline):
        assert (baseline().trades["holding_days"] >= 0).all()

    def test_exit_never_before_entry(self, baseline):
        trades = baseline().trades
        assert (trades["exit_date"] >= trades["entry_date"]).all()


class TestResultSurface:
    def test_trades_carry_entry_and_exit_context(self, baseline):
        """入场与了结上下文是任意事后分组分析的前提，不能事后补。"""
        columns = set(baseline().trades.columns)
        for name in ("underlying_price", "iv", "delta", "moneyness", "dte_calendar"):
            assert f"entry_{name}" in columns and f"exit_{name}" in columns

    def test_summary_discloses_synthetic_data(self, baseline):
        text = baseline().summary(printout=False)
        assert "合成期权链" in text and "不可据此调优" in text

    def test_summary_discloses_engine_scope(self, baseline):
        """成交表没有权益曲线，报告必须说清楚没有回撤与夏普。"""
        text = baseline().summary(printout=False)
        assert "不含资金约束" in text and "没有回撤与夏普" in text

    def test_summary_reports_concurrency_and_capital(self, baseline):
        assert "峰值同时持仓" in baseline().summary(printout=False)

    def test_manifest_is_complete(self, baseline, tmp_path):
        paths = baseline().export(tmp_path)
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
        assert {"strategy", "config", "environment", "provenance"} <= set(manifest)
        assert manifest["config"]["symbol"] == "QQQ"

    def test_exported_trades_reload(self, baseline, tmp_path):
        paths = baseline().export(tmp_path)
        reloaded = pd.read_csv(paths["trades"])
        assert len(reloaded) == len(baseline().trades)

    def test_events_are_queryable(self, baseline):
        events = baseline().events
        assert list(events.columns) == ["kind", "date", "subject", "detail"]


class TestNoTrades:
    def test_impossible_entry_yields_empty_result(self, market, chain, signals, config):
        strategy = Strategy(
            entry=EntryRule("never", lambda view: False),
            select=SelectionRule(target_delta=0.30),
            exit=ExitRule(),
        )
        result = Result(TradeTableEngine(market, chain, signals, config).run(strategy))
        assert result.trades.empty
        assert "未产生任何交易" in result.summary(printout=False)
