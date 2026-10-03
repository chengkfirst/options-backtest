"""Selection rules and the cost model."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.core.config import CostConfig
from backtest.execution.costs import check_tradable, execute, fill_price, order_fees
from backtest.core.errors import StartupError
from backtest.signals.contracts import SelectionRule, select_contract

from tests.conftest import selection
from backtest.core.types import CHAIN_COLUMNS, Right, Side


@pytest.fixture
def day(market):
    return market.sessions()[80]


@pytest.fixture
def snapshot(chain, day):
    return chain.snapshot(day)


class TestSelectionRule:
    #: The fields the class requires, so each test states only what it is checking.
    #: Spelled out rather than taken from the shared helper: these tests are about the
    #: class's own validation, and routing them through a helper would test the helper.
    REQUIRED = {"right": Right.CALL, "target_dte": 30, "dte_window": (21, 45)}

    def test_requires_exactly_one_strike_criterion(self):
        with pytest.raises(StartupError):
            SelectionRule(**self.REQUIRED, target_delta=0.3, moneyness=0.05)
        with pytest.raises(StartupError):
            SelectionRule(**self.REQUIRED, target_delta=None, moneyness=None)

    def test_target_dte_must_sit_in_window(self):
        with pytest.raises(StartupError):
            SelectionRule(**(self.REQUIRED | {"target_dte": 60, "dte_window": (21, 45)}),
                          target_delta=0.30)

    def test_the_platform_supplies_none_of_them(self):
        """Delta and tenor are the most researched parameters an option buyer sets, so
        omitting them is an error rather than an invitation for the platform to choose."""
        with pytest.raises(TypeError, match="right"):
            SelectionRule(target_delta=0.30)


class TestExpirySelection:
    def test_picks_nearest_to_target(self, snapshot, config, market, day):
        rule = selection(target_dte=30, dte_window=(21, 45), target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert res.ok
        assert 21 <= (res.contract.unwrap().expiry - day).days <= 45

    def test_no_expiry_in_window_means_no_trade(self, snapshot, config, market, day):
        rule = selection(target_dte=200, dte_window=(190, 210), target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert not res.ok and "expiry" in res.contract.reason

    def test_executability_floor_excludes_near_expiry(self, snapshot, config, market, day):
        """A contract expiring on or before the execution day is unusable, or an
        exit intent would come to nothing."""
        rule = selection(target_dte=3, dte_window=(1, 6), target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        if res.ok:
            remaining = market.calendar.sessions_between(day, res.contract.unwrap().expiry)
            assert remaining >= config.min_days_to_expiry
        else:
            assert "executable" in res.describe() or "expiry" in res.contract.reason

    def test_trace_is_explainable(self, snapshot, config, market, day):
        rule = selection(target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        names = [step for step, _ in res.trace]
        assert "on the chain" in names and "by option type" in names


class TestStrikeSelection:
    def test_picks_delta_closest_to_target(self, snapshot, config, market, day):
        rule = selection(target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert abs(abs(res.quote["delta"]) - 0.30) < 0.05

    def test_moneyness_selection(self, snapshot, config, market, day):
        rule = selection(target_delta=None, moneyness=0.05)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        spot = float(snapshot["underlying_price"].iloc[0])
        assert abs(res.contract.unwrap().strike - spot * 1.05) <= config.contract.strike_step

    def test_tie_breaks_toward_further_otm(self, config, market, day):
        """On an equal delta distance take the one further out of the money:
        conservative, and deterministic."""
        rows = []
        for strike, delta in ((400.0, 0.25), (410.0, 0.35)):
            row = {c: np.nan for c in CHAIN_COLUMNS}
            row.update(
                asof=day, symbol="QQQ", expiry=date(day.year + 1, 1, 19), strike=strike,
                right="C", bid=5.0, ask=5.2, last=5.1, iv=0.2, delta=delta,
                underlying_price=405.0, source="synthetic", quality_flags="",
                greeks_model_derived=True,
            )
            rows.append(row)
        frame = pd.DataFrame(rows)
        rule = selection(target_dte=30, dte_window=(1, 600), target_delta=0.30)
        res = select_contract(frame, rule, config, market.calendar, day)
        assert res.contract.unwrap().strike == 410.0

    def test_missing_delta_excluded(self, snapshot, config, market, day):
        frame = snapshot.copy()
        frame["delta"] = np.nan
        res = select_contract(frame, selection(target_delta=0.30), config, market.calendar, day)
        assert not res.ok


class TestLiquidityFilters:
    def test_unavailable_fields_are_skipped_not_failed(self, snapshot, config, market, day):
        """The synthetic chain supplies no volume; an absent field must not count
        as a failure."""
        rule = selection(target_delta=0.30, min_volume=100, min_open_interest=500)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert res.ok and len(res.skipped_filters) == 2

    def test_spread_cap_excludes_wide_quotes(self, snapshot, config, market, day):
        rule = selection(target_delta=0.30, max_relative_spread=1e-6)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert not res.ok and "liquidity" in res.contract.reason

    def test_empty_chain(self, config, market, day):
        empty = pd.DataFrame(columns=list(CHAIN_COLUMNS))
        res = select_contract(empty, selection(target_delta=0.3), config, market.calendar, day)
        assert not res.ok


class TestCosts:
    def test_buy_at_ask_sell_at_bid(self):
        c = CostConfig()
        assert fill_price(2.00, 2.20, Side.BUY, c) == pytest.approx(2.20)
        assert fill_price(2.00, 2.20, Side.SELL, c) == pytest.approx(2.00)

    def test_mid_fraction(self):
        c = CostConfig(adverse_fraction=0.5)
        assert fill_price(2.00, 2.20, Side.BUY, c) == pytest.approx(2.15)
        assert fill_price(2.00, 2.20, Side.SELL, c) == pytest.approx(2.05)

    def test_zero_fraction_is_mid(self):
        c = CostConfig(adverse_fraction=0.0)
        assert fill_price(2.00, 2.20, Side.BUY, c) == pytest.approx(2.10)

    def test_commission_per_contract(self):
        assert order_fees(3, CostConfig(), 100).commission == pytest.approx(1.95)

    def test_minimum_commission(self):
        assert order_fees(1, CostConfig(), 100).commission == pytest.approx(1.00)

    def test_regulatory_fee_is_separate(self):
        fees = order_fees(4, CostConfig(), 100)
        assert fees.regulatory == pytest.approx(0.20)
        assert fees.total == pytest.approx(fees.commission + fees.regulatory + fees.slippage)

    def test_slippage_always_adverse(self):
        c = CostConfig(slippage_per_contract=0.02)
        assert fill_price(2.00, 2.20, Side.BUY, c) == pytest.approx(2.22)
        assert fill_price(2.00, 2.20, Side.SELL, c) == pytest.approx(1.98)

    def test_fees_add_up(self):
        a = order_fees(2, CostConfig(), 100)
        b = order_fees(3, CostConfig(), 100)
        assert (a + b).total == pytest.approx(a.total + b.total)

    @pytest.mark.parametrize(
        "bid,ask,expected",
        [(np.nan, 2.0, "no quote"), (2.0, np.nan, "no quote"), (3.0, 2.0, "ask below bid")],
    )
    def test_untradable_reasons(self, bid, ask, expected):
        got = check_tradable(bid, ask, CostConfig())
        assert not got.ok and expected in got.reason

    def test_spread_cap(self):
        got = check_tradable(1.0, 2.0, CostConfig(max_relative_spread=0.2))
        assert not got.ok and "spread" in got.reason

    def test_tradable_reports_spread(self):
        got = check_tradable(2.00, 2.20, CostConfig())
        assert got.ok and got.relative_spread == pytest.approx(0.2 / 2.1)

    def test_execute_returns_price_and_fees(self):
        price, fees = execute(
            bid=2.0, ask=2.2, side=Side.BUY, quantity=2, multiplier=100, costs=CostConfig()
        )
        assert price == pytest.approx(2.2) and fees.total > 0
