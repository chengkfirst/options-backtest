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

from backtest.api.sdk import run

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "2")


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
        """A contract expiring inside the execution window is unusable: an exit decided on
        it could not be filled before it settled.

        The first version asked for a 1-6 day window and then branched on whether anything
        came back, asserting something either way -- so which path ran depended on the
        fixture and the case it describes was never necessarily constructed. This builds
        the contract that must be excluded and checks it is.
        """
        import pandas as pd

        from backtest.core.types import CHAIN_COLUMNS

        # An expiry one session out, which is inside the floor however the floor is set.
        tomorrow = market.calendar.shift(day, 1)
        row = {c: float("nan") for c in CHAIN_COLUMNS}
        row.update(asof=day, symbol="QQQ", expiry=tomorrow, strike=400.0, right="C",
                   bid=5.0, ask=5.2, underlying_price=400.0, delta=0.30, iv=0.22,
                   available_at=day, source="synthetic", quality_flags="",
                   greeks_model_derived=True)
        only_near = pd.DataFrame([row])

        res = select_contract(only_near, selection(target_dte=1, dte_window=(1, 400),
                                                   target_delta=0.30),
                              config, market.calendar, day)
        assert not res.ok, (
            f"a contract expiring on {tomorrow}, one session after the decision, was "
            f"selected under a floor of {config.min_days_to_expiry} sessions"
        )
        assert "executable" in res.describe().lower() or "expiry" in res.describe().lower()

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

    def test_slippage_is_a_cash_fee_not_part_of_the_fill(self):
        """It used to be both, so it was charged twice.

        ``fill_price`` moved the price *and* ``order_fees`` charged the same amount, which
        on a 0.10 slippage over one contract cost 20.00 instead of 10.00. Every test set it
        to zero, and so does the default, which is why it stood. It is cash now, which is
        what this module's own docstring always said it was for.
        """
        from backtest.execution.costs import order_fees

        c = CostConfig(slippage_per_contract=0.02)
        assert fill_price(2.00, 2.20, Side.BUY, c) == pytest.approx(2.20)   # the ask
        assert fill_price(2.00, 2.20, Side.SELL, c) == pytest.approx(2.00)  # the bid
        # 0.02 per contract x 3 contracts x 100 multiplier.
        assert order_fees(3, c, 100).slippage == pytest.approx(6.0)

    def test_slippage_is_adverse_on_both_sides(self):
        """A buy pays it and a sell pays it; it is never a rebate."""
        from backtest.execution.costs import execute

        c = CostConfig(slippage_per_contract=0.02, commission_per_contract=0.0,
                       min_commission_per_order=0.0, regulatory_fee_per_contract=0.0)
        buy_price, buy_fees = execute(bid=2.0, ask=2.0, side=Side.BUY, quantity=1,
                                      multiplier=100, costs=c)
        sell_price, sell_fees = execute(bid=2.0, ask=2.0, side=Side.SELL, quantity=1,
                                        multiplier=100, costs=c)
        assert buy_price * 100 + buy_fees.total == pytest.approx(202.0)
        assert sell_price * 100 - sell_fees.total == pytest.approx(198.0)

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


class TestSlippageIsChargedOnce:
    """It was charged twice: added to the fill price and again as a fee.

    Every test set slippage to 0 and so does the default, so nothing noticed -- and the
    architecture document's register lists slippage among the realism assumptions to run a
    **sensitivity test** on, which is exactly when the doubling would have started
    producing wrong numbers.
    """

    COSTS = CostConfig(adverse_fraction=1.0, commission_per_contract=0.0,
                       min_commission_per_order=0.0, regulatory_fee_per_contract=0.0,
                       slippage_per_contract=0.10)

    def test_a_buy_pays_it_once(self):
        # No spread, so the only cost is slippage: 0.10 per contract x 100 multiplier.
        price, fees = execute(bid=5.0, ask=5.0, side=Side.BUY, quantity=1,
                              multiplier=100, costs=self.COSTS)
        paid = price * 100 + fees.total
        assert paid == pytest.approx(500.0 + 10.0), (
            f"paid {paid:,.2f} for a 500.00 position with 10.00 of slippage"
        )

    def test_a_sell_pays_it_once(self):
        price, fees = execute(bid=5.0, ask=5.0, side=Side.SELL, quantity=1,
                              multiplier=100, costs=self.COSTS)
        received = price * 100 - fees.total
        assert received == pytest.approx(500.0 - 10.0)

    def test_the_fill_price_itself_carries_no_slippage(self):
        """It is a cash fee, which is where this module's docstring always said it was --
        "listed separately, because folded into the fill price it is invisible"."""
        price, _ = execute(bid=5.0, ask=5.0, side=Side.BUY, quantity=1,
                           multiplier=100, costs=self.COSTS)
        assert price == pytest.approx(5.0)

    def test_a_run_with_slippage_costs_more_by_exactly_the_slippage(self):
        """End to end, which is where a double charge would have shown up as a strategy
        that stopped working under a sensitivity test it should have survived."""
        # Fixed sizing and no capital constraint, so slippage cannot change **which**
        # trades happen -- only what they cost. The first version skipped when the counts
        # differed, which would have made it skip for ever if slippage always moved them,
        # with nobody noticing.
        fixed = GAP_RUN | {"sizing": {"method": "fixed", "contracts": 1}, "capital": 1e9}
        plain = run(**fixed)
        slipped = run(**fixed, costs=CostConfig(slippage_per_contract=0.05))
        assert len(plain.trades) == len(slipped.trades), (
            "slippage changed which trades happened under fixed sizing with no capital "
            "constraint, which it cannot do by any route this test knows about"
        )
        multiplier = plain.raw.config.contract.multiplier
        # 0.05 per contract per **fill**, times the multiplier. A trade that was traded
        # out has two fills; one settled at expiry has only the entry, because nothing is
        # traded at settlement.
        settled = plain.trades["exit_reason"].isin(["expiry_itm", "expiry_otm"])
        fills = (~settled).sum() * 2 + settled.sum()
        expected = 0.05 * multiplier * fills * plain.trades["quantity"].iloc[0]
        extra = plain.trades["net_pnl"].sum() - slipped.trades["net_pnl"].sum()
        assert extra == pytest.approx(expected, rel=1e-6), (
            f"{fills} fills at 0.05 x {multiplier} should cost {expected:,.2f}; the "
            f"difference was {extra:,.2f}"
        )
@kind("guard")
class TestAZeroBidContract:
    """Real chains quote 0.00 bid on deep out-of-the-money contracts, every day.

    Nothing is wrong with the data -- there is simply no buyer. What is wrong is treating
    the mid as a price: half the ask is not a price anyone would pay, the implied vol
    solved from it is an artefact, and a delta computed from that vol is worse than
    missing. The spread filter already excludes these; this is what pins it there, since
    the behaviour was never tested and it will arrive with the first real chain.
    """

    COSTS = CostConfig(max_relative_spread=0.2)

    @pytest.mark.parametrize("bid,ask", [(0.0, 0.05), (0.0, 1.00), (0.01, 0.05)])
    @pytest.mark.req("4.2.reject")
    def test_an_unbuyable_contract_is_not_tradable(self, bid, ask):
        from backtest.execution.costs import check_tradable

        verdict = check_tradable(bid, ask, self.COSTS)
        assert not verdict.ok
        assert "spread" in (verdict.reason or "")

    def test_a_two_sided_quote_is_tradable(self):
        from backtest.execution.costs import check_tradable

        assert check_tradable(4.80, 5.20, self.COSTS).ok

    def test_a_zero_zero_quote_is_refused_before_the_spread_is_computed(self):
        """Dividing by a zero mid would raise or produce infinity; it is caught first."""
        from backtest.execution.costs import check_tradable

        verdict = check_tradable(0.0, 0.0, self.COSTS)
        assert not verdict.ok and "non-positive" in (verdict.reason or "")

    @kind("limit")
    def test_the_vol_solved_from_half_an_ask_is_not_refused_by_the_solver(self):
        """**The solver cannot save you here**, which is why the filter has to.

        A 0.00 / 0.05 quote has a mid of 0.025 and implied vol solves happily to a
        plausible-looking number. Nothing downstream could tell it from a real one.
        """
        from backtest.core.types import Right
        from backtest.data.options.pricing import implied_vol

        solved = implied_vol(price=0.025, s=400.0, k=520.0, t=0.08, r=0.045, q=0.0,
                             right=Right.CALL)
        assert solved.is_present, (
            "if this ever starts refusing, the spread filter is no longer the only thing "
            "standing between a zero bid and a plausible-looking delta"
        )
