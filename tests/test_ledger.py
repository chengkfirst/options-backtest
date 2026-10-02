"""The portfolio ledger: cash and positions staying consistent, weighted average
cost, expiry settlement, the accounting identity.

The ledger is the only place in the event-driven engine that holds state, so a
state bug can only be here. These tests go after exactly those bugs.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.execution.costs import ZERO_FEES, FeeBreakdown
from backtest.core.errors import InvariantError
from backtest.core.maybe import Maybe
from backtest.accounting.ledger import Ledger
from backtest.core.types import ContractId, ContractSpec, Right, SettlementStyle, Side

SPEC = ContractSpec(symbol="QQQ")
CASH_SPEC = ContractSpec(symbol="SPX", settlement=SettlementStyle.CASH)
CALL = ContractId("QQQ", date(2024, 3, 15), Right.CALL, 400.0)
PUT = ContractId("QQQ", date(2024, 3, 15), Right.PUT, 380.0)
FEE = FeeBreakdown(1.0, 0.05, 0.0)
T0, T1 = date(2024, 2, 1), date(2024, 2, 10)


@pytest.fixture
def ledger():
    return Ledger(100_000, SPEC)


class TestLong:
    def test_buy_reduces_cash_by_premium_and_fees(self, ledger):
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=2, price=5.0,
                          fees=FEE, when=T0, trade_id=1)
        assert ledger.cash == pytest.approx(100_000 - 1000 - FEE.total)
        assert ledger.position(CALL).quantity == 2

    def test_partial_close_keeps_unit_cost(self, ledger):
        """After a partial close the remaining unit cost is unchanged; only the
        quantity falls."""
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=3, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        realised = ledger.apply_fill(contract=CALL, side=Side.SELL, quantity=1, price=8.0,
                                     fees=ZERO_FEES, when=T1, trade_id=1)
        assert realised == pytest.approx(300.0)
        pos = ledger.position(CALL)
        assert pos.quantity == 2 and pos.avg_price == pytest.approx(5.0)

    def test_weighted_average_on_add(self, ledger):
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=2, price=2.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=2, price=4.0,
                          fees=ZERO_FEES, when=T1, trade_id=1)
        assert ledger.position(CALL).avg_price == pytest.approx(3.0)

    def test_full_close_removes_position(self, ledger):
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        ledger.apply_fill(contract=CALL, side=Side.SELL, quantity=1, price=6.0,
                          fees=ZERO_FEES, when=T1, trade_id=1)
        assert len(ledger) == 0 and ledger.position(CALL) is None

    def test_rejects_oversell(self, ledger):
        """Overselling means reversing through zero, which is unsupported — it has
        to raise rather than quietly open a short."""
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        with pytest.raises(InvariantError, match="reversing through zero"):
            ledger.apply_fill(contract=CALL, side=Side.SELL, quantity=3, price=6.0,
                              fees=ZERO_FEES, when=T1, trade_id=1)


class TestShort:
    def test_sell_increases_cash(self, ledger):
        """Selling receives premium and the position is negative."""
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        assert ledger.cash == pytest.approx(100_300)
        assert ledger.position(PUT).quantity == -1
        assert ledger.position(PUT).is_short

    def test_buy_back_realises_gain(self, ledger):
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        realised = ledger.apply_fill(contract=PUT, side=Side.BUY, quantity=1, price=1.0,
                                     fees=ZERO_FEES, when=T1, trade_id=1)
        assert realised == pytest.approx(200.0)
        assert len(ledger) == 0

    def test_market_value_is_negative(self, ledger):
        """A short's market value is negative — it is a liability, not an asset."""
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        assert ledger.position(PUT).market_value(2.0) == pytest.approx(-200.0)

    def test_put_collateral_is_strike_times_shares(self, ledger):
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=2, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        assert ledger._collateral(ledger.position(PUT)) == pytest.approx(380 * 2 * 100)

    def test_naked_call_collateral_is_nan(self, ledger):
        """There is no margin model for a naked call — returning NaN makes any use
        of it show up, rather than inventing a number."""
        import math

        ledger.apply_fill(contract=CALL, side=Side.SELL, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        assert math.isnan(ledger._collateral(ledger.position(CALL)))


class TestExpiry:
    def test_worthless(self, ledger):
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        kind = ledger.settle_expiry(CALL, Maybe.some(390.0), CALL.expiry)
        assert kind == "worthless"
        assert len(ledger) == 0 and ledger.realised == pytest.approx(-500.0)

    def test_short_put_assigned_gets_shares(self, ledger):
        """QQQ options settle physically: assignment delivers shares against cash,
        not a cash settlement."""
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        kind = ledger.settle_expiry(PUT, Maybe.some(370.0), PUT.expiry)
        assert kind == "physical"
        assert ledger.cash == pytest.approx(100_300 - 38_000)
        stock = ledger.stock_positions[0]
        assert stock.shares == 100 and stock.avg_price == pytest.approx(380.0)

    def test_premium_is_realised_on_assignment(self, ledger):
        """On assignment the premium must be realised — omitting it breaks the
        accounting identity."""
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        ledger.settle_expiry(PUT, Maybe.some(370.0), PUT.expiry)
        ledger.liquidate_stock("QQQ", 372.0, date(2024, 3, 18), ZERO_FEES)
        assert ledger.realised == pytest.approx(ledger.cash - 100_000)

    def test_cash_settlement_path(self):
        ledger = Ledger(100_000, CASH_SPEC)
        c = ContractId("SPX", date(2024, 3, 15), Right.CALL, 4000.0)
        ledger.apply_fill(contract=c, side=Side.BUY, quantity=1, price=20.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        assert ledger.settle_expiry(c, Maybe.some(4015.0), c.expiry) == "cash"
        assert ledger.cash == pytest.approx(100_000 - 2000 + 1500)
        assert not ledger.stock_positions

    def test_missing_spot_aborts(self, ledger):
        """With no underlying close on the expiry day this must raise — guessing a
        price makes the settlement quietly wrong."""
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        with pytest.raises(InvariantError, match="no underlying close"):
            ledger.settle_expiry(CALL, Maybe.missing("market closed"), CALL.expiry)

    def test_at_the_money_expires_worthless(self, ledger):
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        assert ledger.settle_expiry(CALL, Maybe.some(400.0), CALL.expiry) == "worthless"


class TestMarkToMarket:
    def test_liquidation_value_below_mid_for_long(self, ledger):
        """A long marked at the bid must come out below the mid — the mid is book
        value, the bid is what could be sold."""
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=2, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        snap = ledger.snapshot(T1, {CALL: (6.0, 6.4)}, Maybe.some(405.0))
        assert snap.equity_liq < snap.equity_mid

    def test_liquidation_value_below_mid_for_short(self, ledger):
        """A short is marked at the ask — buying it back costs more, so liquidation
        equity is lower."""
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        snap = ledger.snapshot(T1, {PUT: (1.0, 1.4)}, Maybe.some(395.0))
        assert snap.equity_liq < snap.equity_mid

    def test_missing_quote_counted_as_stale(self, ledger):
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        snap = ledger.snapshot(T1, {}, Maybe.some(405.0))
        assert snap.stale_marks == 1

    def test_stock_requires_spot(self, ledger):
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        ledger.settle_expiry(PUT, Maybe.some(370.0), PUT.expiry)
        with pytest.raises(InvariantError, match="no underlying close"):
            ledger.snapshot(T1, {}, Maybe.missing("market closed"))


class TestInvariant:
    """The accounting identity: closing equity = opening + realised − fees +
    unrealised."""

    @pytest.mark.parametrize("side,price", [(Side.BUY, 5.0), (Side.SELL, 3.0)])
    def test_holds_with_open_position(self, ledger, side, price):
        contract = CALL if side is Side.BUY else PUT
        ledger.apply_fill(contract=contract, side=side, quantity=2, price=price,
                          fees=FEE, when=T0, trade_id=1)
        snap = ledger.snapshot(T1, {contract: (price + 1, price + 1.2)}, Maybe.some(405.0))
        ledger.check_invariant(snap)

    def test_holds_after_assignment_and_liquidation(self, ledger):
        ledger.apply_fill(contract=PUT, side=Side.SELL, quantity=1, price=3.0,
                          fees=FEE, when=T0, trade_id=1)
        ledger.settle_expiry(PUT, Maybe.some(370.0), PUT.expiry)
        snap = ledger.snapshot(PUT.expiry, {}, Maybe.some(370.0))
        ledger.check_invariant(snap)
        ledger.liquidate_stock("QQQ", 372.0, date(2024, 3, 18), FEE)
        ledger.check_invariant(ledger.snapshot(date(2024, 3, 18), {}, Maybe.some(372.0)))

    def test_detects_drift(self, ledger):
        """The identity check has to catch a drift, or it is decoration."""
        ledger.apply_fill(contract=CALL, side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=T0, trade_id=1)
        ledger._cash += 0.01
        with pytest.raises(InvariantError, match="accounting identity fails"):
            ledger.check_invariant(ledger.snapshot(T1, {CALL: (5.0, 5.0)}, Maybe.some(400.0)))

    def test_rejects_non_positive_capital(self):
        with pytest.raises(ValueError):
            Ledger(0, SPEC)
