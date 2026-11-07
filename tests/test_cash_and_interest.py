"""Interest, dividends, frozen cash and the T+1 rule — the Details column of Layer 5.

## The figure that makes this matter

Over three years on $100,000 at the fixture's rates, **interest is 9,285 and the
strategy's net P&L is −473**. Twenty times the thing being measured.

Folded into `realised`, a losing strategy would read as a profitable one. Left out of the
accounting identity, the identity fails on a correct run — which is how this was found:
the identity refused the first session after interest was booked, which is exactly what
an identity is for.

So interest is a **term of its own**, and so are external flows. Neither is the
strategy's P&L and both move the equity.

## What else the Details column asked for

- **frozen by open orders.** Two entries decided on the same day were each checked
  against the whole balance and both could fill; the capital check then refused the
  second *at the fill*. Right outcome, wrong route — the trade was reported as refused
  rather than never placed, and a real account would not have accepted the order.
- **sellable quantity under the settlement rule.** US equity options settle T+1. Nothing
  here has hit it because a one-day execution lag is enforced and no position is closed
  on the session it opened — which is why the rule had to be written down rather than
  relied on: it holds by a coincidence of configuration.
- **dividends to cash.** The ledger booked none. The word appeared in a dozen files and
  every one was *pricing* — the yield into Black-Scholes, into the tree, into the
  early-exercise test. An option pays no dividend, so that was correct until physical
  delivery started producing shares.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.accounting.ledger import Ledger
from backtest.core.errors import InvariantError
from backtest.core.types import ContractId, ContractSpec, Right, Side
from backtest.execution.costs import ZERO_FEES

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "5")

DAY = date(2023, 5, 1)
NEXT = date(2023, 5, 2)


def _ledger(cash: float = 100_000.0) -> Ledger:
    return Ledger(cash, ContractSpec(symbol="QQQ", vol_proxy="^VXN"))


def _contract(strike: float = 400.0, right: Right = Right.CALL) -> ContractId:
    return ContractId(symbol="QQQ", expiry=date(2023, 6, 16), strike=strike, right=right)


class TestInterestIsNotTheStrategysPnL:
    def test_it_is_tracked_apart_from_realised(self):
        """**The reason this is separate at all.** Over three years the interest is
        twenty times the strategy's net P&L in the fixture, so folding it into realised
        would turn a losing strategy into a profitable one."""
        ledger = _ledger()
        earned = ledger.accrue_interest(DAY, 0.045)
        assert earned == pytest.approx(100_000 * 0.045 / 365)
        assert ledger.total_interest == pytest.approx(earned)
        assert ledger.realised == 0.0, (
            "interest went into realised P&L, which credits the strategy with the "
            "risk-free rate"
        )
        assert ledger.cash == pytest.approx(100_000 + earned)

    def test_a_rate_of_zero_books_nothing(self):
        """So a run that supplies no rate is unchanged — which is every run before this
        existed, and why nothing in the trade table moved."""
        ledger = _ledger()
        assert ledger.accrue_interest(DAY, 0.0) == 0.0
        assert not ledger.events
        assert ledger.cash == 100_000.0

    def test_it_is_a_term_of_the_accounting_identity(self):
        """Left out, the identity fails on a correct run. That is how this was found."""
        ledger = _ledger()
        ledger.accrue_interest(DAY, 0.045)
        snapshot = ledger.snapshot(DAY, {}, __import__(
            "backtest.core.maybe", fromlist=["Maybe"]).Maybe.missing("no spot"))
        ledger.check_invariant(snapshot)        # raises if interest is not a term

    def test_the_entry_says_what_it_was_earned_on(self):
        ledger = _ledger()
        ledger.accrue_interest(DAY, 0.045)
        entry = ledger.events[-1]
        assert entry.kind == "interest"
        assert "4.5000%" in entry.detail and "100,000.00" in entry.detail


class TestDividendsReachCashOnlyForShares:
    def test_an_option_position_receives_nothing(self):
        """An option pays no dividend. The ledger booking one would be inventing a cash
        flow the holder never receives."""
        ledger = _ledger()
        ledger.apply_fill(contract=_contract(), side=Side.BUY, quantity=1, price=5.0,
                          fees=ZERO_FEES, when=DAY, trade_id=1)
        before = ledger.cash
        assert ledger.credit_dividend("QQQ", 0.60, DAY) == 0.0
        assert ledger.cash == before

    def test_shares_from_delivery_do_receive_one(self):
        """Which is the case that made the gap real: physical delivery produces shares,
        and shares held across an ex-date are paid."""
        ledger = _ledger()
        contract = _contract(strike=380.0)
        ledger.apply_fill(contract=contract, side=Side.BUY, quantity=1, price=25.0,
                          fees=ZERO_FEES, when=DAY, trade_id=1)
        from backtest.core.maybe import Maybe

        ledger.settle_expiry(contract, Maybe.some(400.0), NEXT)
        held = {p.symbol: p for p in ledger.stock_positions}
        assert held["QQQ"].shares == 100

        paid = ledger.credit_dividend("QQQ", 0.60, NEXT)
        assert paid == pytest.approx(60.0)
        assert ledger.events[-1].kind == "dividend"
        assert "gross of any withholding" in ledger.events[-1].detail, (
            "the entry does not say it is gross, so a reader cannot tell whether a "
            "withholding rate was applied or omitted"
        )


@kind("guard")
class TestFrozenCashRefusesAtTheOrder:
    def test_available_is_the_balance_less_what_is_promised(self):
        ledger = _ledger(10_000.0)
        ledger.freeze(order_id=1, amount=4_000.0)
        assert ledger.cash == 10_000.0, "freezing moved cash; it only commits it"
        assert ledger.frozen == 4_000.0
        assert ledger.available == 6_000.0

    def test_a_second_order_over_the_remainder_is_refused_when_placed(self):
        """**The route, not only the outcome.** Without this the engine checks each
        entry against the whole balance, both fill, and the capital check refuses the
        second at the fill — reported as a refused trade rather than an order never
        placed, which a real account would not have accepted."""
        ledger = _ledger(10_000.0)
        ledger.freeze(order_id=1, amount=7_000.0)
        with pytest.raises(InvariantError) as caught:
            ledger.freeze(order_id=2, amount=4_000.0)
        text = str(caught.value)
        assert "4,000.00" in text and "3,000.00" in text, (
            "the refusal does not say what was asked for and what was available"
        )

    def test_freezing_one_order_twice_is_refused(self):
        """It would commit the cash twice, and the second freeze would look like a
        second order."""
        ledger = _ledger()
        ledger.freeze(order_id=1, amount=100.0)
        with pytest.raises(InvariantError) as caught:
            ledger.freeze(order_id=1, amount=100.0)
        assert "already has" in str(caught.value)

    def test_releasing_returns_the_amount_and_is_idempotent(self):
        """An order fills, or is cancelled, or is rejected; all three release it, and a
        release of something never frozen is zero rather than an error."""
        ledger = _ledger()
        ledger.freeze(order_id=1, amount=250.0)
        assert ledger.unfreeze(1) == 250.0
        assert ledger.unfreeze(1) == 0.0
        assert ledger.available == ledger.cash


@kind("guard")
class TestTheSettlementRule:
    def test_a_position_cannot_be_sold_on_the_day_it_opened(self):
        """US equity options settle T+1.

        Nothing in this system has hit it, because a one-day execution lag is enforced
        and no position is closed on the session it opened. That is why the rule is
        written down rather than relied on: it holds by a coincidence of configuration,
        and the coincidence ends the moment the lag is configurable or an intraday
        snapshot is added.
        """
        ledger = _ledger()
        contract = _contract()
        ledger.apply_fill(contract=contract, side=Side.BUY, quantity=3, price=5.0,
                          fees=ZERO_FEES, when=DAY, trade_id=1)
        assert ledger.sellable(contract, DAY) == 0
        assert ledger.sellable(contract, NEXT) == 3

    def test_a_contract_not_held_is_zero_rather_than_an_error(self):
        assert _ledger().sellable(_contract(), DAY) == 0


class TestExternalFlows:
    def test_a_deposit_is_not_a_profit(self):
        ledger = _ledger()
        ledger.deposit(5_000.0, DAY, reason="a further allocation")
        assert ledger.cash == 105_000.0
        assert ledger.realised == 0.0
        assert ledger.external_flows == 5_000.0

    def test_it_is_a_term_of_the_identity(self):
        from backtest.core.maybe import Maybe

        ledger = _ledger()
        ledger.deposit(5_000.0, DAY, reason="a further allocation")
        ledger.check_invariant(
            ledger.snapshot(DAY, {}, Maybe.missing("no spot")))

    def test_a_flow_with_no_reason_is_refused(self):
        """The identity balances either way, and an unexplained equity change is what
        it exists to surface."""
        with pytest.raises(InvariantError) as caught:
            _ledger().deposit(1_000.0, DAY, reason="")
        assert "needs a reason" in str(caught.value)

    def test_a_withdrawal_below_zero_is_refused(self):
        with pytest.raises(InvariantError):
            _ledger(1_000.0).deposit(-2_000.0, DAY, reason="too much")


class TestPremiumAtRisk:
    def test_it_is_the_long_premium_and_not_the_capital_tied_up(self):
        """The two are the same for a long-only book and part company the moment
        anything is short: a short put's collateral is capital tied up and comes back
        unless the option is exercised, so counting it as at risk overstates the loss a
        budget is set against."""
        from backtest.core.maybe import Maybe

        ledger = _ledger()
        ledger.apply_fill(contract=_contract(), side=Side.BUY, quantity=2, price=5.0,
                          fees=ZERO_FEES, when=DAY, trade_id=1)
        snapshot = ledger.snapshot(DAY, {}, Maybe.missing("no spot"))
        assert snapshot.premium_at_risk == pytest.approx(2 * 5.0 * 100)
        assert snapshot.deployed == pytest.approx(snapshot.premium_at_risk)
