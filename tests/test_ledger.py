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

from backtest.api.sdk import run
import inspect

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "5")

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


class TestTheTwoEnginesSettleExpiryDifferently:
    """Renamed, because they do not settle alike and the old test did not look.

    It was called "both engines settle expiry alike" and called **one** of them. The
    ledger honours the contract's settlement style -- QQQ is physically settled -- and the
    OCC auto-exercise threshold; the trade-table engine looks at neither and books
    intrinsic value in cash. So the dual-engine comparison, which never reaches expiry
    because the days-to-expiry floor closes everything first, is covering over a real
    divergence.

    Asserted as it is rather than hidden: a test that is green while two engines disagree
    is worse than one that is red.
    """

    def _held(self, market, config, right, offset):
        """One expiring position in the ledger, and what intrinsic it should settle at."""
        from backtest.accounting.ledger import Ledger
        from backtest.core.types import ContractId, Right, Side
        from backtest.execution.costs import order_fees

        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()
        strike = round(settle + offset, 2)
        contract = ContractId(symbol="QQQ", expiry=expiry, strike=strike, right=right)
        intrinsic = (max(settle - strike, 0.0) if right is Right.CALL
                     else max(strike - settle, 0.0))

        ledger = Ledger(initial_cash=1_000_000.0, spec=config.contract)
        ledger.apply_fill(contract=contract, side=Side.BUY, quantity=1, price=5.0,
                          fees=order_fees(1, config.costs, config.contract.multiplier),
                          when=config.start, trade_id=1)
        return ledger, contract, market.spot(expiry), expiry, intrinsic

    @pytest.mark.req("4.2.exercise")
    def test_the_ledger_delivers_shares_for_an_in_the_money_call(self, market, config):
        """QQQ options are physically settled and the ledger does it: the account pays
        the strike and receives shares."""
        from backtest.core.types import Right, SettlementStyle

        assert config.contract.settlement is SettlementStyle.PHYSICAL
        ledger, contract, spot, expiry, intrinsic = self._held(
            market, config, Right.CALL, -15.0)
        assert intrinsic > 0, "the setup did not produce an in-the-money call"

        cash_before = ledger.cash
        kind = ledger.settle_expiry(contract, spot, expiry)
        assert kind == "physical"
        held = {p.symbol: p for p in ledger.stock_positions}
        assert held["QQQ"].shares == config.contract.multiplier
        assert ledger.cash == pytest.approx(
            cash_before - contract.strike * config.contract.multiplier)

    def test_the_ledger_applies_the_occ_one_cent_threshold(self, market, config):
        """A cent in the money is exercised; less is not. The boundary is where a
        half-built threshold shows."""
        from backtest.core.types import ContractId, Right, Side
        from backtest.accounting.ledger import Ledger
        from backtest.execution.costs import order_fees
        from backtest.core.maybe import Maybe

        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()

        for offset, expected in ((-0.02, "physical"), (-0.005, "worthless")):
            contract = ContractId(symbol="QQQ", expiry=expiry,
                                  strike=round(settle + offset, 3), right=Right.CALL)
            ledger = Ledger(initial_cash=1_000_000.0, spec=config.contract)
            ledger.apply_fill(contract=contract, side=Side.BUY, quantity=1, price=0.01,
                              fees=order_fees(1, config.costs,
                                              config.contract.multiplier),
                              when=config.start, trade_id=1)
            assert ledger.settle_expiry(contract, Maybe.some(settle), expiry) == expected

    @kind("limit")
    def test_the_trade_table_books_cash_where_the_ledger_delivers_shares(
            self, market, chain, signals, config):
        """**The divergence, asserted so it cannot be forgotten.**

        The trade-table engine does not look at the settlement style: it books intrinsic
        value in cash. The ledger delivers shares at the strike. The P&L differs by one
        session of exposure to the underlying, plus the cash the account would have had to
        find. Only the path through a failed close reaches it, which is why it has stood.
        """
        from backtest.core.types import Right
        from backtest.engine.vectorized import TradeTableEngine

        ledger, contract, spot, expiry, intrinsic = self._held(
            market, config, Right.CALL, -15.0)
        ledger_kind = ledger.settle_expiry(contract, spot, expiry)

        engine = TradeTableEngine(market, chain, signals, config)
        source = inspect.getsource(engine._settle_at_expiry)
        assert "settlement" not in source and "SettlementStyle" not in source, (
            "the trade-table engine now reads the settlement style; this divergence is "
            "fixed and this test should go"
        )
        assert ledger_kind == "physical", (
            "the ledger stopped delivering shares, so the two may now agree"
        )
@kind("guard")
class TestCashSettlementIsNamedNotHidden:
    """An in-the-money expiry delivers shares, sells them, and says what that cost.

    This class previously asserted that settlement was **to cash** and that physical
    delivery was a measured omission. Delivery is built now, and both tests skipped
    themselves when the run happened not to produce an in-the-money expiry -- which this
    data does produce, so the skip was covering nothing and would have covered a real
    regression.

    What is left of the original measurement is the last test: under the default floor an
    in-the-money expiry is reached zero times, which is why this path is worth being
    correct about and not worth optimising.
    """

    @staticmethod
    def _settled(market, chain, signals, config, *, offset: float):
        """An in-the-money call held to its expiry, settled through the engine.

        Constructed rather than reached for. The tests here used to run a three-year
        backtest and skip themselves if it happened not to produce an in-the-money
        expiry -- which on the offline fixture it never does, so both of them covered
        nothing while reading as though they covered this path.

        Returns the engine, the ledger and the day it settled on, so each test below
        asserts one thing about a situation that is the same every time it runs.

        The engine is given its **own** ``EventLog``. ``EventDrivenEngine`` falls back to
        ``market.events``, and ``market`` is a session-scoped fixture -- so engines built
        across tests share one log and each test sees every other test's events. The
        out-of-the-money case below asserts that nothing was recorded, which passed alone
        and failed in the class: it was reading the in-the-money case's note.
        """
        from backtest.core.run_log import EventLog
        from backtest.accounting.ledger import Ledger
        from backtest.core.types import ContractId, Right
        from backtest.engine.loop import EventDrivenEngine, OpenTrade
        from backtest.execution.costs import order_fees

        expiry = next(d for d in market.calendar.sessions
                      if config.start < d < config.end and market.spot(d).is_present
                      and market.calendar.next_session(d) is not None
                      and market.calendar.next_session(d) <= config.end)
        settle = market.spot(expiry).unwrap()
        contract = ContractId(symbol="QQQ", expiry=expiry,
                              strike=round(settle - offset, 2), right=Right.CALL)

        engine = EventDrivenEngine(market, chain, signals, config, EventLog())
        ledger = Ledger(initial_cash=1_000_000.0, spec=config.contract)
        ledger.apply_fill(contract=contract, side=Side.BUY, quantity=1, price=5.0,
                          fees=order_fees(1, config.costs, config.contract.multiplier),
                          when=config.start, trade_id=1)
        trades = {1: OpenTrade(trade_id=1, contract=contract, entry_date=config.start,
                               entry_price=5.0, quantity=1, side=Side.BUY, fees=0.0,
                               entry_quote={}, entry_signals={})}

        deliveries: dict[str, date] = {}
        before = {p.symbol for p in ledger.stock_positions}
        engine._settle(ledger.positions[0], ledger, trades, expiry)
        for p in ledger.stock_positions:
            if p.symbol not in before:
                deliveries[p.symbol] = expiry
        return engine, ledger, contract, expiry, deliveries

    def test_the_note_says_what_taking_delivery_needed(self, market, chain, signals,
                                                       config):
        """The capital the entry check did not see.

        The note used to say the opposite of what the code does -- that settlement was to
        cash and delivery an approximation -- which is the worse kind of stale comment,
        because a reader checks the note and stops there.
        """
        from backtest.core.run_log import EventKind

        engine, ledger, contract, _, _ = self._settled(
            market, chain, signals, config, offset=15.0)

        notes = engine.events.of_kind(EventKind.EARLY_EXERCISE_RISK)
        assert len(notes) == 1, "an in-the-money expiry delivered shares and said nothing"
        detail = notes[0].detail
        assert "settled physically" in detail
        assert "cash to take delivery" in detail
        assert "sized this trade on the premium" in detail
        # The figure, not just the sentence: one contract is 100 shares at the strike.
        assert f"{contract.strike * 100:,.0f}" in detail

    def test_the_shares_arrive_and_the_strike_is_paid(self, market, chain, signals,
                                                      config):
        from backtest.core.types import Right

        _, ledger, contract, _, _ = self._settled(
            market, chain, signals, config, offset=15.0)
        held = {p.symbol: p for p in ledger.stock_positions}
        assert held["QQQ"].shares == config.contract.multiplier
        assert held["QQQ"].avg_price == pytest.approx(contract.strike)

    def test_an_out_of_the_money_expiry_delivers_nothing_and_says_nothing(
            self, market, chain, signals, config):
        """It settles to zero, which is exactly right and needs no caveat."""
        from backtest.core.run_log import EventKind

        engine, ledger, _, _, deliveries = self._settled(
            market, chain, signals, config, offset=-15.0)
        assert not list(ledger.stock_positions)
        assert not deliveries
        assert not engine.events.of_kind(EventKind.EARLY_EXERCISE_RISK)

    def test_the_delivered_shares_are_sold_on_the_next_session(
            self, market, chain, signals, config):
        """Holding them is no longer an option strategy.

        ``Ledger.liquidate_stock`` said this in its own docstring and nothing called it,
        so delivered shares stayed in the book for the rest of the run as unhedged
        exposure to the underlying -- inside a result reported as an options result.

        Sold on the session **after** delivery, not on the expiry day: delivery is
        overnight, and selling at the expiry close would be a fill before the shares
        existed.
        """
        from backtest.core.run_log import EventKind

        engine, ledger, _, expiry, deliveries = self._settled(
            market, chain, signals, config, offset=15.0)
        assert deliveries, "nothing was recorded as delivered"

        # The expiry day itself: the shares exist and are not yet sellable.
        engine._dispose_deliveries(ledger, deliveries, expiry)
        assert list(ledger.stock_positions), (
            "the shares were sold on the day the option expired, before delivery"
        )

        after = market.calendar.next_session(expiry)
        engine._dispose_deliveries(ledger, deliveries, after)
        assert not list(ledger.stock_positions), "the shares were not sold"
        assert not deliveries

        sales = [e for e in engine.events.of_kind(EventKind.PASSIVE_EXPIRY)
                 if "shares delivered" in e.detail]
        assert len(sales) == 1 and sales[0].when == after
        assert "no commission" in sales[0].detail, (
            "the share sale does not say it was charged nothing; this cost model prices "
            "option orders and has no share rate, and an unstated omission is the thing "
            "this project does not do"
        )

    ITM_CONFIG = dict(
        symbol="QQQ", start="2021-01-01", end="2023-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.60, "delta_hi": 0.85},
        exit={"hold_days": 200, "dte_floor": 2},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    def test_the_default_floor_never_reaches_an_in_the_money_expiry(self):
        """**The measurement the decision rests on.**

        Not a limitation -- a measurement, which is why it no longer carries the ``limit``
        label. It says this path is rare under ordinary settings, which is the reason it
        is worth being correct about rather than fast.

        If this starts failing, in-the-money expiries have become reachable under the
        default floor and the overnight share exposure stops being a rare path.
        """
        default = dict(self.ITM_CONFIG,
                       exit={"hold_days": 20, "dte_floor": 7},
                       select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                               "delta_lo": 0.25, "delta_hi": 0.45})
        trades = run(**default).trades
        itm = int((trades["exit_reason"] == "expiry_itm").sum())
        assert itm == 0, (
            f"{itm} of {len(trades)} trades reached an in-the-money expiry under the "
            f"default floor; the overnight share exposure is no longer a rare path"
        )
