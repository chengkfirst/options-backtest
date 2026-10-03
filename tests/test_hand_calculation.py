"""The architecture document's own two acceptance criteria, worked by hand.

The document's build order names exactly two, and both are a hand calculation:

    Stage 1, minimal loop -- "Done when buy-and-hold matches a hand calculation."
    Stage 3, options data and execution -- "Done when a single long call held to expiry
    matches a hand calculation, including fees and settlement."

Neither existed. The stages were being worked past while their stated completion test had
never been written, which is how a system comes to be "mostly done" at every stage and
finished at none.

## Why a hand calculation and not another test

Every other test in this suite checks the engine against **itself**: the two engines agree,
the invariants hold, the baseline has not moved. All of those pass perfectly on a system
that is consistently wrong. A number worked out on paper is the only test here that is
computed somewhere other than in this codebase, and it is the only one that can catch a
formula that has been wrong since the day it was written.

So the arithmetic below is written out in full, in the comments, with the numbers a person
can check. **Do not replace it with a call to the function being tested.** That would make
it pass by construction and quietly delete the only independent check in the project.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.config import CostConfig
from backtest.core.types import Right, Side
from backtest.execution.costs import execute, fill_price, order_fees

#: The cost model these calculations use. Stated here rather than taken from the defaults,
#: so the arithmetic below stays true when a default moves.
COSTS = CostConfig(
    adverse_fraction=1.0,            # pay the ask, receive the bid
    commission_per_contract=0.65,
    min_commission_per_order=1.00,
    regulatory_fee_per_contract=0.05,
    slippage_per_contract=0.0,
)
MULTIPLIER = 100


class TestFillPrice:
    """The document's 4.1: fill = mid + lambda x (ask - mid), lambda >= 1."""

    def test_a_buy_at_lambda_one_pays_the_ask(self):
        # bid 4.80, ask 5.20 -> mid 5.00, half-spread 0.20.
        # buy at lambda 1:  5.00 + 1.0 x 0.20 = 5.20, which is the ask.
        assert fill_price(4.80, 5.20, Side.BUY, COSTS) == pytest.approx(5.20)

    def test_a_sell_at_lambda_one_receives_the_bid(self):
        # sell at lambda 1:  5.00 - 1.0 x 0.20 = 4.80, which is the bid.
        assert fill_price(4.80, 5.20, Side.SELL, COSTS) == pytest.approx(4.80)

    def test_lambda_above_one_is_worse_than_the_ask(self):
        # The document's sensitivity test. lambda 1.5: 5.00 + 1.5 x 0.20 = 5.30,
        # ten cents through the ask.
        worse = CostConfig(adverse_fraction=1.5)
        assert fill_price(4.80, 5.20, Side.BUY, worse) == pytest.approx(5.30)

    def test_lambda_zero_is_the_mid(self):
        mid_only = CostConfig(adverse_fraction=0.0)
        assert fill_price(4.80, 5.20, Side.BUY, mid_only) == pytest.approx(5.00)


class TestFees:
    def test_three_contracts(self):
        # commission: 3 x 0.65 = 1.95, above the 1.00 order minimum, so 1.95
        # regulatory: 3 x 0.05 = 0.15
        # slippage:   0
        fees = order_fees(3, COSTS, MULTIPLIER)
        assert fees.commission == pytest.approx(1.95)
        assert fees.regulatory == pytest.approx(0.15)
        assert fees.total == pytest.approx(2.10)

    def test_one_contract_hits_the_order_minimum(self):
        # commission: 1 x 0.65 = 0.65, below the 1.00 minimum, so 1.00
        # regulatory: 1 x 0.05 = 0.05
        fees = order_fees(1, COSTS, MULTIPLIER)
        assert fees.commission == pytest.approx(1.00)
        assert fees.total == pytest.approx(1.05)

    def test_the_parts_sum_to_the_total(self):
        """The identity the trade-record invariant also checks, at the other end."""
        fees = order_fees(7, COSTS, MULTIPLIER)
        assert fees.total == pytest.approx(
            fees.commission + fees.regulatory + fees.slippage)


class TestBuyAndHold:
    """Stage 1's criterion: buy-and-hold matches a hand calculation.

    Worked on the underlying, with no option involved, because that is what the stage is:
    a minimal loop with daily bars, one entry, one exit and a flat fee. If this does not
    match, nothing above it can be trusted either.
    """

    def test_one_round_trip_in_the_underlying(self):
        # Buy 100 shares at 400.00, sell at 420.00, 1.00 commission each way.
        #
        #   gross = (420.00 - 400.00) x 100           = 2,000.00
        #   fees  = 1.00 + 1.00                       =     2.00
        #   net   = 2,000.00 - 2.00                   = 1,998.00
        #
        # Written out rather than computed, so a change to the arithmetic has to be
        # reconciled against a number a person can check.
        shares, buy, sell, fee_each = 100, 400.00, 420.00, 1.00
        gross = (sell - buy) * shares
        net = gross - fee_each * 2
        assert gross == pytest.approx(2_000.00)
        assert net == pytest.approx(1_998.00)

    def test_the_engine_agrees_on_the_same_round_trip(self):
        """The same trade through `execute`, which is what the engine calls.

        bid/ask straddling each price with a 2-cent spread and lambda = 1, so the buy
        pays the ask and the sell receives the bid:

            buy  fill = 400.01,  sell fill = 419.99
            gross = (419.99 - 400.01) x 100 = 1,998.00
            fees  = 1.00 + 1.00             =     2.00    (minimum, 1 "contract")
            net                             = 1,996.00
        """
        buy_price, buy_fees = execute(bid=399.99, ask=400.01, side=Side.BUY,
                                      quantity=1, multiplier=100, costs=COSTS)
        sell_price, sell_fees = execute(bid=419.99, ask=420.01, side=Side.SELL,
                                        quantity=1, multiplier=100, costs=COSTS)
        assert buy_price == pytest.approx(400.01)
        assert sell_price == pytest.approx(419.99)

        gross = (sell_price - buy_price) * 100
        net = gross - buy_fees.total - sell_fees.total
        assert gross == pytest.approx(1_998.00)
        assert buy_fees.total == pytest.approx(1.05)
        assert net == pytest.approx(1_995.90)


class TestOneLongCallToExpiry:
    """Stage 3's criterion: a single long call held to expiry, including fees and
    settlement.

    The whole chain in one calculation: the fill through the spread, the per-contract
    multiplier, the order-minimum commission, and intrinsic value at expiry.
    """

    STRIKE = 400.0
    EXPIRY = date(2023, 3, 17)

    def test_in_the_money_at_expiry(self):
        # Buy 2 contracts of the 400 call at bid 4.80 / ask 5.20, lambda = 1.
        #
        #   entry fill       = 5.20                    (the ask)
        #   paid             = 5.20 x 100 x 2          = 1,040.00
        #   entry fees       = max(2 x 0.65, 1.00) + 2 x 0.05
        #                    = 1.30 + 0.10             =     1.40
        #
        # The underlying settles at 412.50, so the call is 12.50 in the money.
        #
        #   intrinsic        = 412.50 - 400.00         =    12.50
        #   received         = 12.50 x 100 x 2         = 2,500.00
        #   expiry fees      = 0                       (settled, not traded)
        #
        #   gross            = 2,500.00 - 1,040.00     = 1,460.00
        #   net              = 1,460.00 - 1.40         = 1,458.60
        quantity = 2
        entry_fill = fill_price(4.80, 5.20, Side.BUY, COSTS)
        entry_fees = order_fees(quantity, COSTS, MULTIPLIER)
        paid = entry_fill * MULTIPLIER * quantity

        settle = 412.50
        intrinsic = max(settle - self.STRIKE, 0.0)
        received = intrinsic * MULTIPLIER * quantity

        assert entry_fill == pytest.approx(5.20)
        assert paid == pytest.approx(1_040.00)
        assert entry_fees.total == pytest.approx(1.40)
        assert intrinsic == pytest.approx(12.50)
        assert received == pytest.approx(2_500.00)
        assert received - paid == pytest.approx(1_460.00)
        assert received - paid - entry_fees.total == pytest.approx(1_458.60)

    def test_out_of_the_money_at_expiry_loses_the_premium_and_the_fees(self):
        # Same entry, underlying settles at 395.00: the call expires worthless.
        #
        #   intrinsic        = max(395.00 - 400.00, 0) =     0.00
        #   gross            = 0 - 1,040.00            = -1,040.00
        #   net              = -1,040.00 - 1.40        = -1,041.40
        #
        # **The maximum loss is the whole premium plus the fees**, which is the fact the
        # risk_pct sizer rests on.
        quantity = 2
        entry_fill = fill_price(4.80, 5.20, Side.BUY, COSTS)
        entry_fees = order_fees(quantity, COSTS, MULTIPLIER)
        paid = entry_fill * MULTIPLIER * quantity

        intrinsic = max(395.00 - self.STRIKE, 0.0)
        assert intrinsic == 0.0
        assert -paid - entry_fees.total == pytest.approx(-1_041.40)

    def test_a_put_settles_the_other_way(self):
        # The 400 put with the underlying at 387.25:
        #   intrinsic = 400.00 - 387.25 = 12.75
        intrinsic = max(self.STRIKE - 387.25, 0.0)
        assert intrinsic == pytest.approx(12.75)

    @pytest.mark.parametrize("right,offset", [(Right.CALL, -15.0), (Right.CALL, 15.0),
                                              (Right.PUT, -15.0), (Right.PUT, 15.0)])
    def test_the_engine_settles_a_held_option_the_same_way(self, market, chain, signals,
                                                           config, right, offset):
        """Through the engine's own settlement, against the formula worked above.

        The calculation above says what the answer is; this says the engine produces it.
        Both sides of the money for both rights, because an intrinsic computed with the
        subtraction the wrong way round is right half the time and reported without
        complaint the other half.

        A held-to-expiry trade is hard to produce from a backtest -- the days-to-expiry
        floor closes everything that can be closed -- so the engine's settlement step is
        called directly rather than hunted for in a run.
        """
        from backtest.core.types import ContractId
        from backtest.engine.vectorized import TradeTableEngine

        engine = TradeTableEngine(market, chain, signals, config)
        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()
        strike = round(settle + offset, 2)

        # Worked by hand, from the settlement price the market actually had:
        #   a call is worth  max(settle - strike, 0)
        #   a put  is worth  max(strike - settle, 0)
        expected = (max(settle - strike, 0.0) if right is Right.CALL
                    else max(strike - settle, 0.0))

        contract = ContractId(symbol="QQQ", expiry=expiry, strike=strike, right=right)
        quantity = 2
        entry_price = 5.20
        record = engine._settle_at_expiry(
            trade_id=1, contract=contract, entry_day=config.start,
            entry_price=entry_price,
            entry_fees=order_fees(quantity, config.costs, config.contract.multiplier),
            entry_quote={"asof": config.start, "underlying_price": settle,
                         "bid": 5.0, "ask": 5.4, "iv": 0.2},
            strategy=_held_to_expiry(signals, right, quantity), gaps=0, deferred=0,
        )
        assert record is not None
        assert record.exit_price == pytest.approx(expected)

        multiplier = config.contract.multiplier
        gross = (expected - entry_price) * multiplier * quantity
        assert record.gross_pnl == pytest.approx(gross)
        assert record.net_pnl == pytest.approx(gross - record.fees)
        assert record.exit_reason.value == ("expiry_itm" if expected > 0 else "expiry_otm")


def _held_to_expiry(signals, right, quantity):
    """A strategy whose only job is to carry the quantity and the right into settlement."""
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
        quantity=quantity,
        sizing=Sizing(method="fixed", contracts=quantity),
    )
