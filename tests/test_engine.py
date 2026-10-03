"""The trade-table engine: execution lag, first trigger, precedence, guards, passive
expiry.

Logic like the first trigger is tested with **scripted quotes** rather than the
synthetic chain — only when the test dictates each day's price does "which day, and for
which reason, should it exit" have one correct answer.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd
import pytest

from backtest.signals.strategy import EntryRule, ExitRule

from backtest.engine.strategy import Strategy
from backtest.engine.vectorized import TradeTableEngine
from backtest.core.errors import StartupError
from backtest.signals.contracts import SelectionRule

from tests.conftest import exits, selection
from backtest.core.types import CHAIN_COLUMNS, ContractId, ExitReason, Right


class ScriptedChain:
    """A chain priced from a script: ``prices`` is {date: theoretical price}, with a
    fixed 2% spread."""

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
    """Construct one controlled trade: the signal day, the entry day, and the price
    script that follows."""

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
            select=selection(
                target_dte=(expiry - entry_day).days,
                dte_window=(1, 400),
                target_delta=0.30,
            ),
            exit=exit_rule,
            quantity=1,
        )
        return signal_day, entry_day, contract, chain, strategy, window

    return build


class TestExecutionLag:
    @pytest.mark.req("4.2.latency")
    def test_entry_is_next_session_after_signal(self, market, chain, signals, config):
        """The intent forms at T's close and executes at T+1's. Using T's close for
        both the decision and the fill is look-ahead."""
        sessions = market.sessions()
        signal_day = sessions[60]
        strategy = Strategy(
            entry=EntryRule("one_day", enter_on({signal_day})),
            select=selection(target_delta=0.30),
            exit=ExitRule(hold_days=5, take_profit=None, stop_loss=None, dte_floor=5),
        )
        result = TradeTableEngine(market, chain, signals, config).run(strategy)
        assert len(result.records) == 1
        assert result.records[0].entry_date == market.calendar.shift(signal_day, 1)

    def test_signal_on_last_session_is_dropped(self, market, chain, signals, config):
        last = market.sessions()[-1]
        strategy = Strategy(
            entry=EntryRule("last", enter_on({last})),
            select=selection(target_delta=0.30),
            exit=ExitRule(hold_days=5, take_profit=None, stop_loss=None, dte_floor=5),
        )
        result = TradeTableEngine(market, chain, signals, config).run(strategy)
        assert not result.records
        assert "candidate_dropped" in result.events.counts()


def _bid(price: float) -> float:
    """ScriptedChain's bid for a theoretical price: a fixed 2% spread, so the sell side
    is price minus half of it. Written out so the expected exit price below is a number
    worked from the fixture rather than read back from the engine."""
    return round(price - max(price * 0.02, 0.01) / 2, 4)


class TestFirstTrigger:
    """Scripted prices exist so that **which day, at what price, for what reason** each
    have one right answer.

    These used to assert only the reason. A rule that fired a day late, or filled at the
    trigger day's price instead of the fill day's, or filled at the mid instead of the
    bid, would pass every one of them -- and each of those is a quiet overstatement of the
    result rather than a crash.
    """

    RULE = ExitRule(hold_days=20, take_profit=1.0, stop_loss=0.5, dte_floor=2)

    def test_take_profit(self, market, signals, config, scripted):
        # entry 5.00 on window[0]; 11.00 on window[2] is +120%, past the +100% target.
        # Decided on window[2], filled on window[3] at that day's bid.
        path = [5.0, 5.5, 11.0, 12.0, 6.0]
        _, _, _, chain, strategy, window = scripted(path, exit_rule=self.RULE)
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.TAKE_PROFIT
        assert rec.exit_date == window[3], "the fill is the day after the trigger"
        assert rec.exit_price == pytest.approx(_bid(12.0)), "sells hit the bid"

    def test_stop_loss(self, market, signals, config, scripted):
        # 2.00 on window[2] is -60%, past the -50% stop. Filled on window[3].
        path = [5.0, 4.5, 2.0, 1.9, 6.0]
        _, _, _, chain, strategy, window = scripted(path, exit_rule=self.RULE)
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.STOP_LOSS
        assert rec.exit_date == window[3]
        assert rec.exit_price == pytest.approx(_bid(1.9))

    def test_the_stop_fills_below_the_stop_level_when_the_price_gapped(
            self, market, signals, config, scripted):
        """**What a stop actually costs.**

        The level is -50%, so a stop is assumed to lose 2.50 of a 5.00 premium -- which is
        what `risk_pct` sizing divides by. Here the price is through the stop on the
        trigger day and lower still on the fill day, so the realised loss is larger. The
        sizer's budget is therefore an underestimate of the risk actually taken, which is
        the direction that matters.
        """
        path = [5.0, 4.5, 2.0, 1.0, 6.0]       # gaps down again before the fill
        _, _, _, chain, strategy, window = scripted(path, exit_rule=self.RULE)
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.STOP_LOSS
        assert rec.exit_price == pytest.approx(_bid(1.0))

        assumed_loss = 5.0 * 0.5                           # the stop distance
        realised_loss = 5.0 - rec.exit_price
        assert realised_loss > assumed_loss, (
            f"the stop assumed a {assumed_loss:.2f} loss and the fill lost "
            f"{realised_loss:.2f}"
        )

    def test_time_exit_when_nothing_triggers(self, market, signals, config, scripted):
        path = [5.0, 5.1, 5.2, 5.1, 5.0, 5.05, 5.0, 5.0]
        _, _, _, chain, strategy, window = scripted(
            path, exit_rule=ExitRule(hold_days=4, take_profit=1.0, stop_loss=0.5,
                                     dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.TIME_EXIT
        # Held four trading days: the decision falls on window[4] and the fill on
        # window[5], one execution lag later. The window skips the Labor Day holiday, so
        # the dates are not five calendar days apart -- which is the sort of thing only an
        # explicit date assertion catches.
        assert rec.exit_date == window[5]
        assert rec.exit_price == pytest.approx(_bid(path[5]))
        # **The realised holding is hold_days plus the execution lag**, not hold_days.
        # The cap is tested on the close and the fill is the next one, so a rule written
        # as "hold 4 days" holds 5. Pinned because it is invisible in the configuration
        # and anyone computing theta over the stated window would be a day out.
        assert rec.holding_days == 4 + config.execution_lag_days

    def test_earliest_trigger_wins(self, market, signals, config, scripted):
        """Through the stop first, then up to the take-profit: it records the stop,
        not whichever turned out better with hindsight."""
        path = [5.0, 2.0, 2.1, 12.0, 12.0]
        _, _, _, chain, strategy, _w = scripted(
            path, exit_rule=ExitRule(hold_days=20, take_profit=1.0, stop_loss=0.5, dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.STOP_LOSS

    def test_same_day_conflict_takes_priority_and_records_suppressed(
        self, market, signals, config, scripted
    ):
        """Take-profit and the holding cap on the same day: precedence takes the
        take-profit, and the suppressed reason is still recorded.

        Take-profit and stop-loss cannot hold on one day (a price cannot be both +100%
        and -50%); the real same-day conflict is between a price condition and a time
        condition.
        """
        path = [5.0, 5.2, 11.0, 10.8]  # day 2 both doubles and hits the holding cap;
                                       # fills on day 3
        _, _, _, chain, strategy, _w = scripted(
            path, exit_rule=ExitRule(hold_days=2, take_profit=1.0, stop_loss=0.5, dte_floor=2)
        )
        rec = TradeTableEngine(market, chain, signals, config).run(strategy).records[0]
        assert rec.exit_reason is ExitReason.TAKE_PROFIT
        assert ExitReason.TIME_EXIT in rec.suppressed_reasons

    def test_dte_floor_triggers(self, market, signals, config, scripted):
        path = [5.0] * 12
        _, _, _, chain, strategy, _w = scripted(
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
            select=selection(target_delta=0.30),
            exit=exits(),
        )
        with pytest.raises(StartupError, match="unregistered signals"):
            TradeTableEngine(market, chain, signals, config).run(strategy)

    def test_exit_rule_requires_time_condition(self):
        """With no time-based exit, positions get dragged into expiry."""
        with pytest.raises(StartupError, match="time-based exit"):
            ExitRule(hold_days=None, dte_floor=None, take_profit=1.0, stop_loss=0.5)

    def test_rejects_dte_floor_below_execution_lag(self, market, chain, signals, config):
        """Below lag+1 the exit intent comes to nothing and the position is silently
        dragged into expiry."""
        strategy = Strategy(
            entry=EntryRule("all", always_enter),
            select=selection(target_delta=0.30),
            exit=ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5, dte_floor=1),
        )
        with pytest.raises(StartupError, match="days-to-expiry floor"):
            TradeTableEngine(market, chain, signals, config).run(strategy)

    def test_quantity_must_be_positive(self):
        with pytest.raises(StartupError):
            Strategy(
                entry=EntryRule("x", always_enter),
                select=selection(target_delta=0.3),
                exit=exits(),
                quantity=0,
            )


class TestPnlIntegrity:
    def test_net_equals_gross_minus_fees(self, market, chain, signals, config):
        strategy = Strategy(
            entry=EntryRule("all", always_enter),
            select=selection(target_delta=0.30),
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
            select=selection(target_delta=0.30),
            exit=ExitRule(hold_days=8, take_profit=1.0, stop_loss=0.5, dte_floor=5),
        )
        result = TradeTableEngine(market, chain, signals, config).run(strategy)
        assert result.concurrency.max() >= 1
        check = result.capital_check()
        assert check["peak_capital"] > 0 and isinstance(check["feasible"], bool)


class TestSweep:
    """Parameter sweeps: speed is the value, the trial count is the brake."""

    @pytest.fixture
    def pieces(self, market, chain, signals, config):
        strategy = Strategy(
            entry=EntryRule("all", always_enter),
            select=selection(target_delta=0.30),
            exit=ExitRule(hold_days=8, take_profit=1.0, stop_loss=0.5, dte_floor=5),
        )
        return market, chain, signals, config, strategy

    def test_covers_full_grid(self, pieces):
        from backtest.research.sweep import sweep

        res = sweep(*pieces, {"take_profit": [0.5, 1.0], "stop_loss": [0.3, 0.5]})
        assert res.trials == 4 and len(res.table) == 4

    def test_records_trial_count(self, pieces):
        """How many sets were swept has to be recorded — the multiple-testing
        correction needs it, and nobody gets to pretend they tried once."""
        from backtest.research.sweep import sweep

        res = sweep(*pieces, {"take_profit": [0.5, 1.0, 1.5]})
        assert res.trials == 3

    def test_runs_are_independent_and_reproducible(self, pieces):
        from backtest.research.sweep import sweep

        grid = {"take_profit": [0.5, 1.0]}
        first = sweep(*pieces, grid).table
        second = sweep(*pieces, grid).table
        pd.testing.assert_frame_equal(first, second)

    def test_rejects_unknown_parameter(self, pieces):
        from backtest.research.sweep import sweep

        with pytest.raises(StartupError, match="cannot be swept"):
            sweep(*pieces, {"leverage": [1, 2]})

    def test_rejects_empty_grid(self, pieces):
        from backtest.research.sweep import sweep

        with pytest.raises(StartupError, match="grid is empty"):
            sweep(*pieces, {})

    def test_dte_window_follows_target(self, pieces):
        """Sweeping target_dte moves the window with it, or it would hit the "target
        outside the window" check."""
        from backtest.research.sweep import sweep

        res = sweep(*pieces, {"target_dte": [14, 30, 45]})
        assert res.trials == 3 and res.table["count"].sum() > 0

    def test_top_is_ordered(self, pieces):
        from backtest.research.sweep import sweep

        res = sweep(*pieces, {"take_profit": [0.5, 1.0, 1.5]})
        top = res.top(3)
        assert list(top["net_pnl"]) == sorted(top["net_pnl"], reverse=True)
