"""The tests a review found missing, each for a failure that produces a plausible number.

None of these would crash. Every one of them would produce a result that looks ordinary
and is wrong by an amount nobody could see from the output -- which is the only kind of
failure this project is actually trying to catch.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.api.sdk import run
from backtest.core.config import CostConfig
from backtest.core.types import Side
from backtest.execution.costs import execute

BASE = dict(
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
        plain = run(**BASE)
        slipped = run(**BASE, costs=CostConfig(slippage_per_contract=0.05))
        if len(plain.trades) != len(slipped.trades):
            pytest.skip("slippage changed which trades happened; not a clean comparison")
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


class TestTheFingerprintCoversCosts:
    """The cost model decides the result and was not in the end-to-end fingerprint check.

    Lambda is the realism assumption the document says moves results most. Two runs that
    differ only in lambda are two different runs, and an id that cannot tell them apart
    makes a search look smaller than it was -- which flatters the deflated result.
    """

    def _id(self, **over):
        return run(**(BASE | over)).manifest()["run_id"]

    def test_lambda_changes_it(self):
        assert self._id(costs=CostConfig(adverse_fraction=1.0)) != \
               self._id(costs=CostConfig(adverse_fraction=1.5))

    def test_commission_changes_it(self):
        assert self._id(costs=CostConfig(commission_per_contract=0.65)) != \
               self._id(costs=CostConfig(commission_per_contract=1.30))

    def test_slippage_changes_it(self):
        assert self._id(costs=CostConfig(slippage_per_contract=0.0)) != \
               self._id(costs=CostConfig(slippage_per_contract=0.05))

    def test_capital_changes_it(self):
        assert self._id(capital=100_000.0) != self._id(capital=250_000.0)


class TestShadowTrackingIsPurelyObservational:
    """Keeping quotes after a trade closes must not change the trade.

    If it did, every counterfactual study would be measuring a book that differs from the
    one it claims to describe -- and the difference would be invisible, because the
    shadowed run is the only one anybody looks at.
    """

    def test_the_trades_are_identical(self):
        off = run(**BASE, shadow_days=0)
        on = run(**BASE, shadow_days=20)
        import pandas as pd

        pd.testing.assert_frame_equal(off.trades, on.trades)

    def test_the_equity_curve_is_identical(self):
        import pandas as pd

        off = run(**BASE, shadow_days=0)
        on = run(**BASE, shadow_days=20)
        pd.testing.assert_frame_equal(off.raw.equity, on.raw.equity)

    def test_but_the_fingerprint_differs(self):
        """It changes what the run can *answer*, so it is part of the configuration even
        though it changes no number."""
        assert run(**BASE, shadow_days=0).manifest()["run_id"] != \
               run(**BASE, shadow_days=20).manifest()["run_id"]


class TestHolidayExpiry:
    """Good Friday 2022 fell on 15 April, which was also the third Friday.

    The monthly expiry moved to Thursday 14 April. A calendar that returns the third
    Friday without checking it is a session puts every April 2022 contract one day out,
    and nothing about the result would look wrong.
    """

    def test_april_2022_monthly_expiry_is_the_thursday(self, market):
        from backtest.core.clock import expiry_dates, third_friday

        nominal = third_friday(2022, 4)
        assert nominal == date(2022, 4, 15), "the third Friday of April 2022"
        assert nominal not in market.calendar.sessions, "Good Friday is not a session"

        expiries = [d for d in expiry_dates(market.calendar, include_weekly=False)
                    if date(2022, 4, 1) <= d <= date(2022, 4, 30)]
        assert date(2022, 4, 14) in expiries, (
            f"April 2022's expiry should move back to the Thursday, got {expiries}"
        )
        assert date(2022, 4, 15) not in expiries

    def test_every_expiry_is_a_trading_session(self, market):
        """The general form. A contract expiring on a day the market is shut cannot
        settle, and the engine would mark it against a price that does not exist."""
        from backtest.core.clock import expiry_dates

        sessions = set(market.calendar.sessions)
        expiries = expiry_dates(market.calendar)
        off = [d for d in expiries if d not in sessions]
        assert not off, f"these expiries are not trading sessions: {off}"


class TestBothEnginesSettleExpiryAlike:
    """The dual-engine check never reaches expiry, because the days-to-expiry floor closes
    everything first. So the one step the two engines could silently disagree on is the
    one step the comparison does not cover."""

    def test_the_same_contract_settles_to_the_same_value(self, market, chain, signals,
                                                         config):
        from backtest.core.types import ContractId, Right
        from backtest.engine.vectorized import TradeTableEngine
        from backtest.execution.costs import order_fees

        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()

        for right, strike in ((Right.CALL, settle - 10), (Right.CALL, settle + 10),
                              (Right.PUT, settle - 10), (Right.PUT, settle + 10)):
            contract = ContractId(symbol="QQQ", expiry=expiry, strike=round(strike, 2),
                                  right=right)
            intrinsic = (max(settle - contract.strike, 0.0) if right is Right.CALL
                         else max(contract.strike - settle, 0.0))

            engine = TradeTableEngine(market, chain, signals, config)
            record = engine._settle_at_expiry(
                trade_id=1, contract=contract, entry_day=config.start, entry_price=5.0,
                entry_fees=order_fees(1, config.costs, config.contract.multiplier),
                entry_quote={"asof": config.start, "underlying_price": settle,
                             "bid": 5.0, "ask": 5.4, "iv": 0.2},
                strategy=_minimal(signals, right), gaps=0, deferred=0,
            )
            assert record.exit_price == pytest.approx(intrinsic), (
                f"{right.value} struck {contract.strike:.2f} with the underlying at "
                f"{settle:.2f} settled to {record.exit_price}, not {intrinsic}"
            )

    def test_an_expiry_settlement_charges_no_trading_fee(self, market, chain, signals,
                                                         config):
        """Nothing is traded, so nothing is charged. A commission here would make every
        held-to-expiry trade slightly worse for a reason that does not exist."""
        from backtest.core.types import ContractId, Right
        from backtest.engine.vectorized import TradeTableEngine
        from backtest.execution.costs import order_fees

        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()
        entry_fees = order_fees(1, config.costs, config.contract.multiplier)
        record = TradeTableEngine(market, chain, signals, config)._settle_at_expiry(
            trade_id=1,
            contract=ContractId(symbol="QQQ", expiry=expiry, strike=round(settle - 10, 2),
                                right=Right.CALL),
            entry_day=config.start, entry_price=5.0, entry_fees=entry_fees,
            entry_quote={"asof": config.start, "underlying_price": settle,
                         "bid": 5.0, "ask": 5.4, "iv": 0.2},
            strategy=_minimal(signals, Right.CALL), gaps=0, deferred=0,
        )
        assert record.fees == pytest.approx(entry_fees.total)


def _minimal(signals, right):
    from backtest.core.types import Right as R
    from backtest.engine.strategy import Strategy
    from backtest.portfolio.positioning import Sizing
    from backtest.signals.contracts import SelectionRule
    from backtest.signals.entry.expression import parse_condition
    from backtest.signals.strategy import EntryRule, ExitRule

    condition, requires = parse_condition("close > sma_20", signals.names)
    return Strategy(
        entry=EntryRule("close > sma_20", condition, requires, is_expression=True),
        select=SelectionRule(right=right, target_dte=30, dte_window=(21, 45),
                             target_delta=0.30),
        exit=ExitRule(hold_days=90, dte_floor=2),
        quantity=1,
        sizing=Sizing(method="fixed", contracts=1),
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
        assert result.raw.events.counts().get("range_truncated", 0) >= 1
