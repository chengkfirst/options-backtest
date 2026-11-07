"""The target portfolio — requirement 3.2.

## What the engine did before

A decision became an order in the same step. So there was no moment at which the
**intended** book existed, and "why is this position this size" could only be answered by
replaying the decisions.

## The case it exists for

Two slots wanting the same contract on the same day. The book-wide risk rule closing a
position and that position's own stop firing used to be two orders, and whichever was
queued first decided the attribution -- the engine ran the risk rule before the per-trade
rules specifically so that the race had a fixed winner, which is a workaround rather than
a resolution.

Against a target they are two adjustments to one number, both reasons survive, and
execution reads the number.

## What is asserted here

The composition and the subtraction, on constructed inputs. Nothing about what any of it
earns: that is a research question and the research has not been done.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.errors import InvariantError, StartupError
from backtest.core.types import ContractId, Right
from backtest.portfolio.target import Adjustment, Order, TargetPortfolio

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "3")

DAY = date(2023, 5, 1)


def _contract(strike: float = 400.0, right: Right = Right.CALL) -> ContractId:
    return ContractId(symbol="QQQ", expiry=date(2023, 6, 16), strike=strike, right=right)


def _want(target: TargetPortfolio, contract, quantity: int, slot: str, reason: str):
    target.want(Adjustment(contract=contract, quantity=quantity, slot=slot,
                           reason=reason, when=DAY))


class TestTheDiff:
    """Target minus held, which is the whole of what execution needs."""

    def test_a_target_of_zero_against_a_position_sells_all_of_it(self):
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 0, "stop_loss", "premium -50%")
        orders = target.orders({contract: 2})
        assert len(orders) == 1
        assert orders[0].quantity == -2 and orders[0].held == 2 and orders[0].target == 0

    def test_a_target_above_the_position_buys_the_difference(self):
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 5, "review", "adding to a winner")
        assert target.orders({contract: 2})[0].quantity == 3

    def test_a_target_equal_to_the_position_produces_nothing(self):
        """Which is why a slot restating a position's current size costs nothing."""
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 2, "review", "hold")
        assert target.orders({contract: 2}) == ()

    def test_a_contract_with_no_target_is_untouched(self):
        """**No opinion is not a target of zero.** No opinion leaves a position alone; a
        target of zero closes it. Collapsing the two would make "no slot looked at this
        today" indistinguishable from "every slot wants it gone" -- and the second closes
        the book."""
        looked_at, ignored = _contract(400.0), _contract(410.0)
        target = TargetPortfolio()
        _want(target, looked_at, 0, "stop_loss", "fired")
        orders = target.orders({looked_at: 1, ignored: 3})
        assert [o.contract for o in orders] == [looked_at]
        assert target.target_for(ignored) is None

    def test_a_short_position_is_signed(self):
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 0, "stop_loss", "fired")
        assert target.orders({contract: -2})[0].quantity == 2    # buying back

    def test_sells_come_before_buys(self):
        """**So the cash suffices.** The document says this of the order generator and it
        was missing: orders came out sorted by contract, so a buy could be placed before
        the sell that pays for it. In a cash account that is a rejected order on a day the
        strategy intended to trade -- and the trade is then absent from the result with
        only a rejection event to say why.
        """
        buying, selling = _contract(400.0), _contract(410.0)
        target = TargetPortfolio()
        # Written buy-first, so a stable sort on the contract alone would keep it there.
        _want(target, buying, 3, "entry", "a new position")
        _want(target, selling, 0, "stop_loss", "fired")
        orders = target.orders({buying: 0, selling: 2})
        assert [o.quantity > 0 for o in orders] == [False, True], (
            f"the orders came out {[o.quantity for o in orders]}; the sell has to be "
            f"first or the buy may be rejected for cash on a day the strategy traded"
        )

    def test_the_orders_are_in_a_stable_order(self):
        """Not dictionary order. Two runs of the same day must queue the same orders in
        the same sequence, or the fill prices differ for a reason that is not the
        strategy."""
        first, second = _contract(400.0), _contract(410.0)
        a, b = TargetPortfolio(), TargetPortfolio()
        _want(a, first, 0, "stop_loss", "x")
        _want(a, second, 0, "stop_loss", "x")
        _want(b, second, 0, "stop_loss", "x")
        _want(b, first, 0, "stop_loss", "x")
        held = {first: 1, second: 1}
        assert [o.contract for o in a.orders(held)] == [o.contract for o in b.orders(held)]


class TestTwoSlotsWantingOneContract:
    """The case the target exists for."""

    def test_both_reasons_survive(self):
        """An order for "sell 2" carries one reason. A target of 0 that the stop and the
        review both asked for carries both, which is what a reader asking why a position
        went to zero wants."""
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 0, "headwind", "drawdown -15%")
        _want(target, contract, 0, "stop_loss", "premium -50%")
        order = target.orders({contract: 2})[0]
        assert "headwind: drawdown -15%" in order.reason
        assert "stop_loss: premium -50%" in order.reason

    def test_they_compose_to_one_order_and_not_two(self):
        """Two orders against one position is what used to happen, and the ledger
        refuses the second: reversing through zero is not supported."""
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 0, "headwind", "liquidate")
        _want(target, contract, 0, "stop_loss", "fired")
        assert len(target.orders({contract: 2})) == 1

    def test_the_later_target_wins_and_the_earlier_reason_is_kept(self):
        """A reduction after a close, or the other way round: the quantity is whichever
        slot spoke last, and both reasons are on the order. Replacing the reason too
        would lose the earlier slot's part in a target they agreed on."""
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 1, "headwind", "scale down by half")
        _want(target, contract, 0, "stop_loss", "fired")
        order = target.orders({contract: 2})[0]
        assert order.target == 0
        assert "scale down by half" in order.reason and "fired" in order.reason


@kind("guard")
class TestWhatIsRefused:
    def test_an_adjustment_with_no_reason_is_refused(self):
        """"An adjustment whose reason cannot be named" is this requirement's own
        falsifier."""
        with pytest.raises(StartupError) as caught:
            Adjustment(contract=_contract(), quantity=0, slot="stop_loss", reason="",
                       when=DAY)
        assert "has no reason" in str(caught.value)

    def test_an_adjustment_with_no_slot_is_refused(self):
        with pytest.raises(StartupError) as caught:
            Adjustment(contract=_contract(), quantity=0, slot="", reason="fired",
                       when=DAY)
        assert "cannot be attributed" in str(caught.value)

    def test_two_slots_on_opposite_sides_are_refused(self):
        """One slot long and another short in the same contract is not an overlap this
        resolves -- it is two strategies in one book, and silently taking the later one
        would run neither."""
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 2, "review", "long it")
        with pytest.raises(InvariantError) as caught:
            _want(target, contract, -2, "hedge", "short it")
        assert "two strategies in one book" in str(caught.value)

    def test_a_target_is_cleared_between_steps(self):
        """A target is what the book should hold **now**. One carried into tomorrow would
        re-issue today's order against tomorrow's book, which is how an exit that failed
        to fill comes to be decided twice -- and carrying an unfilled instruction forward
        is the queue's job, which is a different thing."""
        contract = _contract()
        target = TargetPortfolio()
        _want(target, contract, 0, "stop_loss", "fired")
        target.clear()
        assert len(target) == 0
        assert target.orders({contract: 2}) == ()


class TestTheEngineUsesIt:
    """The unit tests prove the composition. This proves the engine goes through it --
    the half that rots, because an engine that queued intents directly again would
    produce the same result on any day where no two slots overlap."""

    RUN = dict(
        symbol="QQQ", start="2023-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    @pytest.mark.req("3.2.target")
    def test_every_exit_order_names_a_target_and_a_slot(self):
        """The TARGET_PORTFOLIO event is published from the target, so a run's stream
        says what the book should have held and why -- which is the state that did not
        exist before."""
        from backtest.api.sdk import run
        from backtest.core.events import EventType

        result = run(**self.RUN)
        targets = result.raw.bus.of_type(EventType.TARGET_PORTFOLIO)
        assert targets, "no target was published in a run that traded"
        for event in targets:
            assert "hold" in event.reason, f"{event.describe()} names no target quantity"
            assert event.component_id, f"{event.describe()} names no slot"

    @pytest.mark.req("3.2.target")
    def test_the_order_that_follows_a_target_closes_the_gap_it_named(self):
        """The subtraction, checked end to end: the order's quantity is the gap the
        target event reported, not a quantity decided somewhere else."""
        from backtest.api.sdk import run
        from backtest.core.events import EventType

        result = run(**self.RUN)
        bus = result.raw.bus
        checked = 0
        for order in bus.of_type(EventType.ORDER):
            target = next((e for e in bus.trace(order.id).links
                           if e.type is EventType.TARGET_PORTFOLIO), None)
            if target is None:
                continue
            gap = abs(target.data["target"] - target.data["current"])
            assert order.data["quantity"] == gap, (
                f"{order.describe()} is for {order.data['quantity']} and the target it "
                f"came from named a gap of {gap}"
            )
            checked += 1
        assert checked >= 5, "too few orders traced to a target for this to mean much"
