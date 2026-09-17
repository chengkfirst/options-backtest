"""选约规则与成本模型。"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.config import CostConfig
from backtest.costs.model import check_tradable, execute, fill_price, order_fees
from backtest.errors import StartupError
from backtest.selection.rules import SelectionRule, select_contract
from backtest.types import CHAIN_COLUMNS, Right, Side


@pytest.fixture
def day(market):
    return market.sessions()[80]


@pytest.fixture
def snapshot(chain, day):
    return chain.snapshot(day)


class TestSelectionRule:
    def test_requires_exactly_one_strike_criterion(self):
        with pytest.raises(StartupError):
            SelectionRule(target_delta=0.3, moneyness=0.05)
        with pytest.raises(StartupError):
            SelectionRule(target_delta=None, moneyness=None)

    def test_target_dte_must_sit_in_window(self):
        with pytest.raises(StartupError):
            SelectionRule(target_dte=60, dte_window=(21, 45))


class TestExpirySelection:
    def test_picks_nearest_to_target(self, snapshot, config, market, day):
        rule = SelectionRule(target_dte=30, dte_window=(21, 45), target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert res.ok
        assert 21 <= (res.contract.unwrap().expiry - day).days <= 45

    def test_no_expiry_in_window_means_no_trade(self, snapshot, config, market, day):
        rule = SelectionRule(target_dte=200, dte_window=(190, 210), target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert not res.ok and "到期" in res.contract.reason

    def test_executability_floor_excludes_near_expiry(self, snapshot, config, market, day):
        """执行日或之前到期的合约不可用——否则了结意图会落空。"""
        rule = SelectionRule(target_dte=3, dte_window=(1, 6), target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        if res.ok:
            remaining = market.calendar.sessions_between(day, res.contract.unwrap().expiry)
            assert remaining >= config.min_days_to_expiry
        else:
            assert "可执行" in res.describe() or "到期" in res.contract.reason

    def test_trace_is_explainable(self, snapshot, config, market, day):
        rule = SelectionRule(target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        names = [step for step, _ in res.trace]
        assert "链上合约" in names and "按期权类型" in names


class TestStrikeSelection:
    def test_picks_delta_closest_to_target(self, snapshot, config, market, day):
        rule = SelectionRule(target_delta=0.30)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert abs(abs(res.quote["delta"]) - 0.30) < 0.05

    def test_moneyness_selection(self, snapshot, config, market, day):
        rule = SelectionRule(target_delta=None, moneyness=0.05)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        spot = float(snapshot["underlying_price"].iloc[0])
        assert abs(res.contract.unwrap().strike - spot * 1.05) <= config.contract.strike_step

    def test_tie_breaks_toward_further_otm(self, config, market, day):
        """delta 与目标等距时取更虚值的一张：保守，且结果确定。"""
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
        rule = SelectionRule(target_dte=30, dte_window=(1, 600), target_delta=0.30)
        res = select_contract(frame, rule, config, market.calendar, day)
        assert res.contract.unwrap().strike == 410.0

    def test_missing_delta_excluded(self, snapshot, config, market, day):
        frame = snapshot.copy()
        frame["delta"] = np.nan
        res = select_contract(frame, SelectionRule(target_delta=0.30), config, market.calendar, day)
        assert not res.ok


class TestLiquidityFilters:
    def test_unavailable_fields_are_skipped_not_failed(self, snapshot, config, market, day):
        """合成链不提供成交量；不得因字段缺失判为不合格。"""
        rule = SelectionRule(target_delta=0.30, min_volume=100, min_open_interest=500)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert res.ok and len(res.skipped_filters) == 2

    def test_spread_cap_excludes_wide_quotes(self, snapshot, config, market, day):
        rule = SelectionRule(target_delta=0.30, max_relative_spread=1e-6)
        res = select_contract(snapshot, rule, config, market.calendar, day)
        assert not res.ok and "流动性" in res.contract.reason

    def test_empty_chain(self, config, market, day):
        empty = pd.DataFrame(columns=list(CHAIN_COLUMNS))
        res = select_contract(empty, SelectionRule(target_delta=0.3), config, market.calendar, day)
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
        [(np.nan, 2.0, "报价缺失"), (2.0, np.nan, "报价缺失"), (3.0, 2.0, "卖价低于买价")],
    )
    def test_untradable_reasons(self, bid, ask, expected):
        got = check_tradable(bid, ask, CostConfig())
        assert not got.ok and expected in got.reason

    def test_spread_cap(self):
        got = check_tradable(1.0, 2.0, CostConfig(max_relative_spread=0.2))
        assert not got.ok and "价差" in got.reason

    def test_tradable_reports_spread(self):
        got = check_tradable(2.00, 2.20, CostConfig())
        assert got.ok and got.relative_spread == pytest.approx(0.2 / 2.1)

    def test_execute_returns_price_and_fees(self):
        price, fees = execute(
            bid=2.0, ask=2.2, side=Side.BUY, quantity=2, multiplier=100, costs=CostConfig()
        )
        assert price == pytest.approx(2.2) and fees.total > 0
