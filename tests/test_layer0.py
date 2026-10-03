"""Layer 0, and the two research tools it unblocked.

The invariants, the run_id, the data view, the baselines and the ablation tool. These are
grouped because they share a failure mode: each one is **machinery for catching mistakes**,
and machinery that does not work is worse than none — it reports that nothing is wrong.

So every test here is of the form "plant the mistake, confirm it is caught". A test that
only confirms the clean case passes would leave a broken guard looking healthy.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest.core.dataview import DataView, LookAheadError
from backtest.core.errors import InvariantError, StartupError
from backtest.core.invariants import (
    cash_not_negative,
    check_all,
    daily_pnl_balances,
    equity_balances,
    no_expired_position,
    position_matches_fills,
)
from backtest.core.run_id import run_id

DAY = date(2024, 1, 2)


class TestInvariants:
    def test_a_clean_state_passes(self):
        check_all(when=DAY, cash=1_000.0, equity=1_500.0, position_value=500.0,
                  stock_value=0.0, contracts=[])

    def test_negative_cash_is_caught_in_a_cash_account(self):
        """A fill paid for with money the account did not have."""
        with pytest.raises(InvariantError, match="cash is negative"):
            cash_not_negative(-0.01, when=DAY)

    def test_negative_cash_is_allowed_on_margin(self):
        cash_not_negative(-5_000.0, when=DAY, margin=True)

    def test_equity_that_does_not_balance_is_caught(self):
        """Catches a multiplier changed without revaluing the position."""
        with pytest.raises(InvariantError, match="does not balance"):
            equity_balances(equity=1_500.0, cash=1_000.0, position_value=400.0,
                            stock_value=0.0, when=DAY)

    def test_daily_pnl_that_does_not_match_the_equity_change_is_caught(self):
        """Catches P&L double-counted or a fee taken from cash and not from P&L."""
        with pytest.raises(InvariantError, match="does not match the equity change"):
            daily_pnl_balances(equity_today=1_100.0, equity_yesterday=1_000.0,
                               external_flows=0.0, pnl=50.0, when=DAY)

    def test_external_flows_are_not_counted_as_pnl(self):
        """A deposit raises equity without being a profit."""
        daily_pnl_balances(equity_today=11_000.0, equity_yesterday=1_000.0,
                           external_flows=10_000.0, pnl=0.0, when=DAY)

    def test_a_position_that_does_not_match_its_fills_is_caught(self):
        """Catches a position written by something other than the ledger."""
        with pytest.raises(InvariantError, match="without a fill"):
            position_matches_fills({"QQQ_C": 2}, {"QQQ_C": 1}, when=DAY)

    def test_an_expired_position_still_held_is_caught(self):
        """Catches an expiry the settlement step missed. Left open, it keeps being marked
        against a quote that no longer exists."""
        class Stale:
            expiry = DAY - timedelta(days=1)
            def __str__(self): return "QQQ_20231229_C_00400000"

        with pytest.raises(InvariantError, match="expired before"):
            no_expired_position([Stale()], when=DAY)


class TestRunId:
    STRAT = {"entry": "sma_20 > sma_50", "exit": {"stop_loss": 0.5}}
    SETTINGS = {"start": "2020-01-01", "capital": 100_000}

    def _id(self, **over):
        strat = {**self.STRAT, **over.pop("strategy", {})}
        return run_id(strategy=strat, settings={**self.SETTINGS, **over.pop("settings", {})},
                      commit="abc123", **over)

    def test_the_same_configuration_hashes_the_same(self):
        assert self._id() == self._id()

    def test_key_order_does_not_change_the_hash(self):
        a = run_id(strategy={"a": 1, "b": 2}, settings={}, commit="x")
        b = run_id(strategy={"b": 2, "a": 1}, settings={}, commit="x")
        assert a == b

    def test_a_changed_parameter_changes_the_hash(self):
        assert self._id() != self._id(strategy={"exit": {"stop_loss": 0.4}})

    def test_the_data_version_changes_the_hash(self):
        """Two runs of identical code and config on different data are different runs."""
        assert self._id() != self._id(data_version="vendor-2024-06")

    def test_the_commit_changes_the_hash(self):
        assert self._id() != run_id(strategy=self.STRAT, settings=self.SETTINGS,
                                    commit="def456")

    def test_volatile_fields_are_ignored(self):
        """A run name or a note must not make a repeat look like a new trial, or the
        trial count the Deflated Sharpe needs becomes meaningless."""
        assert self._id(strategy={"name": "attempt 1"}) == \
               self._id(strategy={"name": "attempt 2"})

    def test_two_different_callables_do_not_hash_alike(self):
        def alpha(view): return True
        def beta(view): return False

        a = run_id(strategy={"entry": alpha}, settings={}, commit="x")
        b = run_id(strategy={"entry": beta}, settings={}, commit="x")
        assert a != b


class TestDataView:
    def _view(self, market, signals, chain, day=None):
        return DataView.at(day or market.sessions()[60], market=market,
                           signals=signals, chain=chain)

    def test_reading_the_future_raises(self, market, signals, chain):
        """The whole point. Before this, a read past the decision day succeeded."""
        v = self._view(market, signals, chain)
        with pytest.raises(LookAheadError, match="look-ahead"):
            v.spot(v.as_of + timedelta(days=1))

    def test_reading_today_and_the_past_is_allowed(self, market, signals, chain):
        v = self._view(market, signals, chain)
        assert v.spot().is_present
        assert v.spot(market.sessions()[10]).is_present

    def test_history_stops_at_as_of(self, market, signals, chain):
        v = self._view(market, signals, chain)
        h = v.history("close")
        assert h.index.max().date() <= v.as_of

    def test_a_view_cannot_roll_backwards(self, market, signals, chain):
        """Rolling back would make as_of stop meaning "the frontier"."""
        v = self._view(market, signals, chain)
        assert v.rolled_to(v.as_of + timedelta(days=1)).as_of > v.as_of
        with pytest.raises(LookAheadError, match="cannot roll backwards"):
            v.rolled_to(v.as_of - timedelta(days=1))

    def test_a_missing_signal_is_an_explicit_absence(self, market, signals, chain):
        early = DataView.at(market.bars.dates[0], market=market, signals=signals)
        assert early.signal("sma_200").is_missing

    def test_the_unbuilt_queries_say_what_is_missing(self, market, signals, chain):
        """Absent would read as "not needed here"; these are needed and not built."""
        v = self._view(market, signals, chain)
        for call in (lambda: v.cross_section("close"),
                     lambda: v.panel(["close"], 20),
                     lambda: v.surface("QQQ")):
            with pytest.raises(NotImplementedError, match="requirements.md"):
                call()


class TestBaselines:
    def test_a_strategy_inside_the_random_distribution_is_reported_as_such(self):
        from backtest.research.baselines import Comparison

        c = Comparison("random", 100.0, 0.0, percentile=0.5)
        assert "inside" in c.verdict()

    def test_one_random_draw_is_refused(self):
        """A percentile from one draw reads as a win or a loss it cannot support."""
        from backtest.research.baselines import random_entry

        with pytest.raises(ValueError, match="cannot support a percentile"):
            random_entry(lambda dates: 0.0, n_entries=1, sessions=[DAY], draws=1,
                         strategy_pnl=0.0)

    def test_the_delta_equivalent_baseline_is_flagged_on_synthetic_data(self):
        """One side is priced by the chain and the other is not, so a synthetic chain
        biases the comparison itself."""
        from backtest.research.baselines import delta_equivalent
        import pandas as pd

        empty = pd.DataFrame(columns=["entry_date", "exit_date", "entry_delta",
                                      "quantity", "net_pnl"])
        assert delta_equivalent(empty, None).valid_on_synthetic is False

    def test_random_entry_dates_come_from_the_sessions_given(self):
        """Drawn from the days the strategy could have traded, not from the calendar."""
        from backtest.research.baselines import random_entry_dates

        sessions = [DAY + timedelta(days=i) for i in range(40)]
        got = random_entry_dates(sessions, 10, seed=1)
        assert len(got) == 10 and set(got) <= set(sessions) and got == sorted(got)

    def test_the_same_seed_draws_the_same_dates(self):
        from backtest.research.baselines import random_entry_dates

        sessions = [DAY + timedelta(days=i) for i in range(40)]
        assert random_entry_dates(sessions, 8, seed=7) == \
               random_entry_dates(sessions, 8, seed=7)


class TestAblation:
    def _strategy(self, **over):
        from backtest.core.types import Right
        from backtest.engine.strategy import Strategy
        from backtest.portfolio.positioning import Sizing
        from backtest.signals.contracts import SelectionRule
        from backtest.signals.strategy import EntryRule, ExitRule

        base = dict(entry=EntryRule("x", lambda v: True, ()),
                    select=SelectionRule(right=Right.CALL), exit=ExitRule(),
                    sizing=Sizing(method="fixed", contracts=3))
        return Strategy(**(base | over))

    def test_neutralising_a_slot_changes_only_that_slot(self):
        from backtest.research.ablation import neutralise

        s = self._strategy()
        off, _ = neutralise(s, "take_profit")
        assert off.exit.take_profit is None
        assert off.exit.stop_loss == s.exit.stop_loss
        assert off.exit.hold_days == s.exit.hold_days
        assert off.sizing == s.sizing

    def test_the_time_based_exits_survive_an_exit_ablation(self):
        """Otherwise the trade never closes and the two runs are not comparable."""
        from backtest.research.ablation import neutralise

        for slot in ("take_profit", "stop_loss"):
            off, _ = neutralise(self._strategy(), slot)
            assert off.exit.hold_days is not None or off.exit.dte_floor is not None

    def test_ablating_the_stop_under_risk_pct_is_refused(self):
        """Removing the stop also changes the size, because size = risk budget / stop
        distance. Two variables moved, so the difference cannot be attributed to one."""
        from backtest.portfolio.positioning import Sizing
        from backtest.research.ablation import neutralise

        s = self._strategy(sizing=Sizing(method="risk_pct", risk_pct=0.02))
        with pytest.raises(StartupError, match="two things at once"):
            neutralise(s, "stop_loss")
        _, caveat = neutralise(s, "stop_loss", accept_coupled=True)
        assert "joint effect" in caveat

    def test_ablating_the_stop_under_fixed_sizing_is_clean(self):
        from backtest.research.ablation import neutralise

        _, caveat = neutralise(self._strategy(), "stop_loss")
        assert caveat == ""

    def test_an_unbuilt_slot_raises_with_the_reason(self):
        """Reporting a contribution of 0 would be a wrong answer, not no answer."""
        from backtest.research.ablation import NOT_YET, neutralise

        for slot in NOT_YET:
            with pytest.raises(StartupError, match="cannot be ablated|not an ablatable"):
                neutralise(self._strategy(), slot)

    def test_an_already_neutral_slot_is_reported_as_unused(self):
        """"Worth nothing" and "never switched on" are different claims."""
        from backtest.research.ablation import ablate, is_already_neutral

        s = self._strategy(review=None)
        assert is_already_neutral(s, "review")
        got = ablate(s, "review", lambda strat: (100.0, 5))
        assert got.was_unused and "not in use" in got.verdict()

    def test_every_ablatable_slot_has_a_neutral_and_a_description(self):
        from backtest.research.ablation import NEUTRAL, neutralise

        for slot in NEUTRAL:
            assert len(NEUTRAL[slot]) > 20, f"{slot} does not say what off means"
            neutralise(self._strategy(), slot)
