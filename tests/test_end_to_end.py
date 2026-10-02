"""End to end: expressions, determinism, export, the accounting identity.

These run on the offline fixture, so they need no network and reproduce bit for bit.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from backtest.api.sdk import Result
from backtest.signals.entry.expression import parse_condition
from backtest.signals.strategy import EntryRule, ExitRule
from backtest.engine.strategy import Strategy
from backtest.engine.vectorized import TradeTableEngine
from backtest.core.errors import StartupError
from backtest.signals.contracts import SelectionRule
from backtest.core.types import Right


@pytest.fixture
def baseline(market, chain, signals, config):
    """The baseline strategy: a moving-average entry, 30 DTE, a delta 0.30 call, and
    whichever of the four exit conditions comes first."""
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
        with pytest.raises(StartupError, match="unregistered signals"):
            parse_condition("sma_20 > nope", signals.names)

    def test_syntax_error_reported(self, signals):
        with pytest.raises(StartupError, match="syntax error"):
            parse_condition("sma_20 >", signals.names)

    @pytest.mark.parametrize(
        "expr", ["__import__('os').system('ls')", "open('/etc/passwd')", "[x for x in ()]"]
    )
    def test_rejects_non_whitelisted_nodes(self, signals, expr):
        """Comparisons, and/or/not and arithmetic only — an expression is not a way in
        to arbitrary code."""
        with pytest.raises(StartupError):
            parse_condition(expr, signals.names)

    def test_missing_signal_means_no_entry(self, signals, market):
        """Any signal absent makes the whole condition false; an absence never takes
        part in a comparison as 0."""
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
        """Each trade's net P&L = exit proceeds − entry cost − all fees."""
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
        """The entry and exit context is the prerequisite for any grouped analysis
        afterwards, and cannot be reconstructed later."""
        columns = set(baseline().trades.columns)
        for name in ("underlying_price", "iv", "delta", "moneyness", "dte_calendar"):
            assert f"entry_{name}" in columns and f"exit_{name}" in columns

    def test_summary_discloses_synthetic_data(self, baseline):
        text = baseline().summary(printout=False)
        assert "synthetic option chain" in text and "must not be tuned on it" in text

    def test_summary_discloses_engine_scope(self, baseline):
        """The trade table has no equity curve, and the report has to say there is no
        drawdown and no Sharpe."""
        text = baseline().summary(printout=False)
        assert "no capital constraint" in text and "no drawdown and no Sharpe" in text

    def test_summary_reports_concurrency_and_capital(self, baseline):
        assert "Peak concurrent positions" in baseline().summary(printout=False)

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
        assert "No trades were produced" in result.summary(printout=False)
