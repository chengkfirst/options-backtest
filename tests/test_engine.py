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

from backtest.api.sdk import run
from datetime import timedelta

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "5")


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


#: A short run used by the guards below: a real strategy on real sessions, small enough
#: to run several times in a test. Moved here with the tests that use it when
#: ``test_gaps.py`` was split up -- that file was organised by **when** its tests were
#: written ("the ones a review found missing"), which is a provenance rather than a
#: subject, so no behaviour in it had an owner.
GAP_RUN = dict(
    symbol="QQQ", start="2023-01-01", end="2023-06-30",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


class TestNoTradeOutlivesTheRange:
    """A trade whose outcome comes from prices after the backtest ended is not a result.

    The trade-table engine was recording one: an entry on the final session, settled at an
    expiry weeks later, with a P&L computed outside the window. The event-driven engine
    never could -- its loop has no session left to exit on -- so the two engines disagreed,
    and the dual-engine comparison missed it because the built-in selection rule happened
    not to enter on the last day. It took a custom selector to surface it.
    """

    @staticmethod
    def _chooser(chain, view):
        calls = chain[chain["is_call"]]
        if not len(calls):
            return None
        return calls.iloc[(calls["delta"] - 0.4).abs().argsort()].iloc[0]

    def _run(self, engine):
        return run(symbol="QQQ", start="2023-01-01", end="2023-06-30",
                   entry="close < sma_20 * 0.98", select=self._chooser,
                   exit={"hold_days": 10, "dte_floor": 7}, engine=engine,
                   sizing={"method": "fixed"}, quantity=1)

    @pytest.mark.parametrize("engine", ["trade_table", "event_driven"])
    @pytest.mark.req("S.range")
    def test_every_exit_falls_inside_the_range(self, engine):
        result = self._run(engine)
        end = result.raw.config.end
        beyond = result.trades.loc[result.trades["exit_date"] > end]
        assert beyond.empty, (
            f"{engine} reported {len(beyond)} trade(s) exiting after {end}, with results "
            f"from prices the backtest never covered"
        )

    def test_the_two_engines_agree_under_a_custom_selector(self):
        """The dual-engine check covers the built-in rule. This covers the path that
        actually diverged."""
        table, driven = self._run("trade_table"), self._run("event_driven")
        assert len(table.trades) == len(driven.trades)
        assert list(table.trades["entry_date"]) == list(driven.trades["entry_date"])

    def test_the_dropped_trade_is_reported_not_silently_omitted(self):
        """Fewer trades with no explanation is indistinguishable from a signal that did
        not fire."""
        result = self._run("trade_table")
        assert result.raw.events.counts().get("unresolved_position", 0) >= 1
@kind("guard")
class TestAPositionOpenAtTheEnd:
    """There is no forced close at the range end, and that is correct.

    Closing would invent a fill on a day the range does not cover; recording it as a time
    exit would put a trade that never ended into the exit-reason statistics. What was
    wrong is that the engine said nothing: fewer trades with no explanation reads exactly
    like a signal that did not fire.
    """

    @pytest.mark.req("S.unresolved")
    def test_an_ordinary_run_leaves_positions_open_and_now_says_so(self):
        """**Found by adding the notice, on a configuration already in this file.**

        A six-month run of the standard setup leaves positions open: entered late enough
        that nothing closed them before the range ended. They were absent from the trade
        log and nothing anywhere said so -- ten trades reported where twelve were opened,
        which reads as a signal that fired ten times.
        """
        from backtest.core.run_log import EventKind

        short = run(**(GAP_RUN | {"start": "2023-01-01", "end": "2023-06-30"}))
        assert len(short.raw.ledger) > 0, (
            "this range used to leave positions open; if it no longer does, this test "
            "needs a range that does, not deleting"
        )
        notices = short.raw.events.of_kind(EventKind.UNRESOLVED_POSITION)
        assert notices, (
            f"{len(short.raw.ledger)} positions were left open and absent from the trade "
            f"log with no notice"
        )
        assert "still open" in notices[0].detail

    @pytest.mark.parametrize("end", ["2023-06-30", "2023-12-31"])
    def test_one_notice_for_each_position_left_open_and_none_otherwise(self, end):
        """Both halves as one invariant, so neither can pass by luck.

        This was two tests: one range that left positions open, and one asserted to leave
        none. The second stopped being clean when the trading calendar was extended past
        the backtest range -- the days-to-expiry floor had been firing near the end on
        contracts that were not near expiry at all, and force-closing them is what made
        that range look tidy. The test was resting on a bug to hold its premise.

        Counting instead of asserting a state means the range is an input rather than a
        claim: whatever this run leaves open, the notice accounts for all of it and there
        is no notice when there is nothing to account for.

        **Every contract, not a sample.** Written as a count rather than a presence check
        because the notice listed only the first five open positions and said nothing
        about the rest -- so on a run with seven, two were as absent as they had been
        before the notice existed.
        """
        from backtest.core.run_log import EventKind

        result = run(**(GAP_RUN | {"start": "2023-01-01", "end": end}))
        notices = result.raw.events.of_kind(EventKind.UNRESOLVED_POSITION)
        open_positions = list(result.raw.ledger.positions)

        if not open_positions:
            assert not notices, "nothing was left open and something was reported"
            return

        assert len(notices) == 1, "one notice accounts for the whole of what is left open"
        notice = notices[0]
        assert f"{len(open_positions)} position(s)" in notice.detail
        for position in open_positions:
            assert str(position.contract) in (notice.subject or ""), (
                f"{position.contract} was left open and is not named in the notice, so "
                f"it is as absent from the output as it was before the notice existed"
            )

    def test_an_expiry_past_the_range_is_counted_over_the_real_calendar(self, market,
                                                                        config):
        """It used to count as a handful of sessions, and the floor fired on it.

        ``sessions_between`` bisected the session list, and a date past the last session
        bisects to the end of it -- so an expiry 400 days beyond the range counted as
        however many sessions remained in the range. The days-to-expiry floor then closed
        the position and recorded ``dte_floor``, a reason that was not true.

        This was a test labelled ``limit`` asserting the undercount, which is how a bug
        becomes permanent: the label says "the method cannot do this", the assertion
        pins the wrong answer in place, and the suite then fails if anyone fixes it. The
        calendar now runs past the bars and the count is the real one.
        """
        far = config.end + timedelta(days=400)
        remaining = market.calendar.sessions_between(config.end, far)
        assert 250 < remaining < 290, (
            f"400 calendar days past the range is {remaining} sessions; roughly 274 are "
            f"expected, and a much smaller number means the count is being clamped at "
            f"the end of the calendar again"
        )

    def test_a_date_beyond_the_calendar_raises_rather_than_clamping(self, market):
        """The guard behind the fix above.

        Extending the calendar makes the undercount unreachable for ordinary tenors; it
        does not make it impossible. A question past even the extended end has no answer,
        and returning a small number for it is the same failure one step further out.
        """
        from backtest.core.errors import StartupError

        beyond = market.calendar.last + timedelta(days=365)
        with pytest.raises(StartupError) as caught:
            market.calendar.sessions_between(market.calendar.last, beyond)
        assert "outside the calendar" in str(caught.value)


@kind("guard")
class TestShadowTrackingIsPurelyObservational:
    """Keeping quotes after a trade closes must not change the trade.

    If it did, every counterfactual study would be measuring a book that differs from the
    one it claims to describe -- and the difference would be invisible, because the
    shadowed run is the only one anybody looks at.
    """

    @pytest.mark.req("5.2.shadow")
    def test_the_trades_are_identical(self):
        off = run(**GAP_RUN, shadow_days=0)
        on = run(**GAP_RUN, shadow_days=20)
        import pandas as pd

        pd.testing.assert_frame_equal(off.trades, on.trades)

    def test_the_equity_curve_is_identical(self):
        import pandas as pd

        off = run(**GAP_RUN, shadow_days=0)
        on = run(**GAP_RUN, shadow_days=20)
        pd.testing.assert_frame_equal(off.raw.equity, on.raw.equity)

    def test_but_the_fingerprint_differs(self):
        """It changes what the run can *answer*, so it is part of the configuration even
        though it changes no number."""
        assert run(**GAP_RUN, shadow_days=0).manifest()["run_id"] != \
               run(**GAP_RUN, shadow_days=20).manifest()["run_id"]

def _half_spread(price: float) -> float:
    """The scripted chain's half-spread: 2% of the price, with a one-cent floor."""
    return max(price * 0.02, 0.01) / 2


def _bid_of(price: float) -> float:
    """The bid the scripted chain below quotes at ``price``.

    Defined once and used both by the chain that serves the quote and by the assertion
    that checks the fill. Writing the arithmetic out a second time in the assertion is
    how a test comes to agree with the code instead of with the intent -- and it is the
    exact shape of the failure this file's split was for.
    """
    return round(price - _half_spread(price), 4)


@kind("guard")
class TestAMissingQuote:
    """The engine defers an exit when the contract has no quote on the fill day.

    `quote_gaps` and `deferred_days` are columns on every trade, so the logic exists and is
    reported -- and nothing tested it. A deferral that silently did not happen would fill
    against a quote that was not there; one that never ended would hold a position for ever.
    """

    @staticmethod
    def _scripted(market, config, prices_by_day):
        """A chain that simply has no row on the days left out of ``prices_by_day``."""
        import numpy as np
        import pandas as pd

        from backtest.core.types import CHAIN_COLUMNS, ContractId, Right

        sessions = [d for d in market.calendar.sessions
                    if config.start <= d <= config.end]
        entry = sessions[60]
        expiry = market.calendar.shift(entry, 40)
        contract = ContractId("QQQ", expiry, Right.CALL, 400.0)
        window = [d for d in sessions if d >= entry][: len(prices_by_day)]
        prices = {d: p for d, p in zip(window, prices_by_day) if p is not None}

        class Gappy:
            availability_assumed = False

            def snapshot(self, day, *, known_by=None):
                if day not in prices:
                    return pd.DataFrame(columns=list(CHAIN_COLUMNS))
                return pd.DataFrame([self._row(day, prices[day])])

            def series(self, c, days):
                rows = [self._row(d, prices[d]) for d in days if d in prices]
                return pd.DataFrame(rows, columns=list(CHAIN_COLUMNS)) if rows \
                    else pd.DataFrame(columns=list(CHAIN_COLUMNS))

            @staticmethod
            def _row(day, price):
                row = {c: np.nan for c in CHAIN_COLUMNS}
                half = _half_spread(price)
                row.update(asof=day, available_at=day, symbol="QQQ", expiry=expiry,
                           strike=400.0, right="C", bid=_bid_of(price),
                           ask=round(price + half, 4), last=price, iv=0.22, delta=0.3,
                           gamma=0.01, vega=0.2, theta=-0.05, underlying_price=400.0,
                           source="synthetic", quality_flags="",
                           greeks_model_derived=True)
                return row

        return entry, window, contract, Gappy()

    def _strategy(self, signals, entry_day, expiry, entry, hold_days=2):
        from backtest.engine.strategy import Strategy
        from backtest.portfolio.positioning import Sizing
        from backtest.signals.contracts import SelectionRule
        from backtest.core.types import Right
        from backtest.signals.strategy import EntryRule, ExitRule

        return Strategy(
            entry=EntryRule("scripted", lambda view: view.day == entry_day),
            select=SelectionRule(right=Right.CALL, target_dte=(expiry - entry).days,
                                 dte_window=(1, 400), target_delta=0.30),
            exit=ExitRule(hold_days=2, dte_floor=2),
            quantity=1,
            sizing=Sizing(method="fixed", contracts=1),
        )

    @pytest.mark.parametrize("engine_name", ["trade_table", "event_driven"])
    def test_a_gap_on_the_fill_day_defers_to_the_next_quote(self, market, config,
                                                            signals, engine_name):
        """Scripted prices have one right answer, so assert it rather than a range.

        Path [5.0, 5.1, 5.2, 5.3, None, 5.4] with hold_days=2. The entry fills on
        window[1]; two sessions later the cap is reached on window[3], which **has** a
        quote, so the exit is decided there and due on window[4] -- which has none. The
        order waits one session and fills on window[5] at that day's bid.

        The first version asserted `deferred_days >= 0`, true of every integer it could
        hold, and returned early when there were no trades at all. Getting it to a single
        right answer took moving the gap: with the gap on the **decision** day the
        event-driven engine simply decides a day later and nothing is deferred, which is
        a different behaviour wearing the same name.
        """
        from backtest.engine.loop import EventDrivenEngine
        from backtest.engine.vectorized import TradeTableEngine

        entry, window, contract, chain = self._scripted(
            market, config, [5.0, 5.1, 5.2, 5.3, None, 5.4])
        strategy = self._strategy(signals, entry, contract.expiry, entry, hold_days=2)
        engine = (TradeTableEngine(market, chain, signals, config)
                  if engine_name == "trade_table"
                  else EventDrivenEngine(market, chain, signals, config,
                                         enforce_capital=False))
        result = engine.run(strategy)

        assert result.records, f"{engine_name} opened no trade, so nothing was deferred"
        record = result.records[0]
        assert record.exit_date == window[5], (
            f"{engine_name} filled on {record.exit_date}; window[4] had no quote so the "
            f"fill belongs on {window[5]}"
        )
        assert record.deferred_days == 1, (
            f"{engine_name} reported {record.deferred_days} days deferred, and exactly "
            f"one session was skipped"
        )
        assert record.exit_price == pytest.approx(_bid_of(5.4)), (
            f"{engine_name} filled at {record.exit_price}, not the bid on the day it "
            f"actually filled"
        )

    def test_the_columns_exist_on_every_trade(self):
        """They are read by the reports, so a run that stopped producing them would show
        as a blank column rather than an error."""
        result = run(**GAP_RUN)
        assert {"quote_gaps", "deferred_days"} <= set(result.trades.columns)
        assert (result.trades["deferred_days"] >= 0).all()
