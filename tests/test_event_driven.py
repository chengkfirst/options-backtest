"""The event-driven engine.

The most important one is :class:`TestDualEngine` — with no capital constraint and no
concurrency limit, the two engines must produce row-identical trade logs for the same
strategy. Their implementations are entirely different (one a table lookup, one a daily
walk), so an error on either side is shown up by the other. The first run of this test
caught a semantic divergence: the trade table allowed repeated entries in one contract
and the event-driven engine did not.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtest.engine.loop import EventDrivenEngine
from backtest.signals.entry.expression import parse_condition
from backtest.signals.strategy import EntryRule, ExitRule
from backtest.engine.strategy import Strategy
from backtest.engine.vectorized import TradeTableEngine
from backtest.core.errors import StartupError
from backtest.signals.contracts import SelectionRule
from backtest.core.types import Right


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
    """The two engines order their records differently (one by entry, one by exit), so
    sort before comparing."""
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
    """One strategy on both engines. The event-driven one has its capital constraint
    and concurrency limit off, for comparability."""
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
        """Row for row. A difference means one side computed wrongly, or the semantics
        diverged quietly."""
        table, driven = both
        pd.testing.assert_frame_equal(comparable(table.records), comparable(driven.records))

    def test_event_driven_rejects_stacking(self, market, chain, signals, config):
        """The event-driven engine refuses allow_stacking rather than silently dropping
        the trades that cannot be split back out after netting.

        The ledger nets positions by contract (as a real broker does) and the engine
        tracks logical trades by trade_id; on a second entry in the same contract the
        two no longer line up and the second trade hangs for ever. Better to raise.
        """
        stacked = build_strategy(signals, allow_stacking=True)
        with pytest.raises(StartupError, match="allow_stacking"):
            EventDrivenEngine(market, chain, signals, config).run(stacked)

    def test_trade_table_still_allows_stacking(self, market, chain, signals, config):
        """The trade table computes trades independently, so the flag is valid there —
        it just makes the results incomparable with the event-driven engine's."""
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
        """Longs marked at the bid and shorts at the ask, so liquidation equity cannot
        exceed mid equity."""
        equity = result.equity
        assert (equity["equity_liq"] <= equity["equity_mid"] + 1e-9).all()

    def test_concurrency_limit_respected(self, result):
        assert result.equity["positions"].max() <= 3

    def test_no_position_left_open(self, result):
        """Nothing should still be open at the end of the range — that would mean a
        hole in the exit paths."""
        assert len(result.ledger) == 0

    def test_every_trade_has_both_contexts(self, result):
        for record in result.records[:20]:
            assert "iv" in record.entry_context and "iv" in record.exit_context
            assert any(k.startswith("sig_") for k in record.entry_context)

    def test_accounting_conserved_throughout(self, result):
        """The main loop checks the identity every day; finishing means all 753 days
        passed."""
        assert result.snapshots

    def test_cash_never_negative_when_enforced(self, result):
        assert (result.equity["cash"] >= -1e-9).all()


class TestCapitalConstraint:
    def test_small_capital_reduces_trades(self, market, chain, signals, config):
        """Not enough cash means no entry — exactly what the trade-table engine cannot
        see."""
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
        with pytest.raises(StartupError, match="unregistered signals"):
            EventDrivenEngine(market, chain, signals, config).run(bad)

    def test_rejects_dte_floor_below_lag(self, market, chain, signals, config):
        bad = build_strategy(
            signals, exit=ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5, dte_floor=1)
        )
        with pytest.raises(StartupError, match="days-to-expiry floor"):
            EventDrivenEngine(market, chain, signals, config).run(bad)


class TestDeterminism:
    def test_two_runs_identical(self, market, chain, signals, config):
        strategy = build_strategy(signals)
        a = EventDrivenEngine(market, chain, signals, config, max_concurrent=3).run(strategy)
        b = EventDrivenEngine(market, chain, signals, config, max_concurrent=3).run(strategy)
        pd.testing.assert_frame_equal(comparable(a.records), comparable(b.records))
        pd.testing.assert_frame_equal(a.equity, b.equity)


class TestSharedTradeContext:
    """The context columns the research layer slices on must match between engines.

    The dual-engine comparison above checks eight fields and not the context, so for a
    long time the two engines built it separately with nothing keeping them aligned —
    the check meant to catch drift could not see this particular drift. They now call
    one function; this asserts the result.
    """

    def _both(self, market, chain, signals, config):
        strategy = build_strategy(signals)
        vec = TradeTableEngine(market, chain, signals, config).run(strategy).trades
        ev = EventDrivenEngine(
            market, chain, signals, config, max_concurrent=None, enforce_capital=False
        ).run(strategy).trades
        assert len(vec) and len(ev)
        return vec, ev

    @staticmethod
    def _context_columns(frame):
        return {c for c in frame.columns if c.startswith(("entry_", "exit_"))}

    def test_both_engines_produce_the_same_context_columns(
        self, market, chain, signals, config
    ):
        vec, ev = self._both(market, chain, signals, config)
        a, b = self._context_columns(vec), self._context_columns(ev)
        assert a == b, f"only one engine has {a ^ b}"

    def test_the_context_values_agree_on_a_matched_trade(
        self, market, chain, signals, config
    ):
        """Identical columns holding different values would be worse than a missing
        column: a grouped analysis would silently depend on which engine ran."""
        vec, ev = self._both(market, chain, signals, config)
        key = ["entry_date", "exit_date", "contract"]
        # Numeric context only: exit_reason is a label, and the dates and prices are
        # already compared by the dual-engine test above.
        cols = sorted(
            c for c in self._context_columns(vec)
            if c not in key and vec[c].dtype.kind == "f" and ev[c].dtype.kind == "f"
        )
        assert cols, "no numeric context columns to compare"
        a = vec.set_index(key)[cols].sort_index()
        b = ev.set_index(key)[cols].sort_index()
        shared = a.index.intersection(b.index)
        assert len(shared), "the two engines share no trade to compare"
        pd.testing.assert_frame_equal(
            a.loc[shared].astype(float),
            b.loc[shared].astype(float),
            check_exact=False,
            atol=1e-8,
        )
