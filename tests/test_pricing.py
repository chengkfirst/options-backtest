"""Black-Scholes prices, greeks, and solving for implied volatility."""

from __future__ import annotations

import numpy as np
import pytest

from backtest.data.options.pricing import (
    bs_price,
    greeks,
    has_early_exercise_value,
    implied_vol,
)
from backtest.core.types import Right

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "1")

BASE = dict(s=100.0, k=100.0, t=1.0, r=0.05, q=0.0)
#: Published reference values (S=100, K=100, T=1, r=5%, sigma=20%, q=0)
REF_CALL = 10.450583572185565
REF_PUT = 5.573526022256971


class TestPrice:
    def test_matches_published_benchmark(self):
        assert float(bs_price(**BASE, sigma=0.2, right=Right.CALL)) == pytest.approx(REF_CALL, abs=1e-6)
        assert float(bs_price(**BASE, sigma=0.2, right=Right.PUT)) == pytest.approx(REF_PUT, abs=1e-6)

    def test_put_call_parity(self):
        c = float(bs_price(**BASE, sigma=0.2, right=Right.CALL))
        p = float(bs_price(**BASE, sigma=0.2, right=Right.PUT))
        assert c - p == pytest.approx(100 - 100 * np.exp(-0.05), abs=1e-10)

    def test_zero_time_returns_intrinsic(self):
        assert float(bs_price(115, 100, 0.0, 0.05, 0.2, 0.0, Right.CALL)) == 15.0
        assert float(bs_price(95, 100, 0.0, 0.05, 0.2, 0.0, Right.PUT)) == 5.0

    def test_zero_vol_no_division_error(self):
        value = float(bs_price(100, 90, 1.0, 0.05, 0.0, 0.0, Right.CALL))
        assert value == pytest.approx(100 - 90 * np.exp(-0.05), abs=1e-10)

    def test_never_negative(self):
        strikes = np.arange(50, 200, 5.0)
        assert (np.asarray(bs_price(100, strikes, 0.1, 0.05, 0.2, 0.0, Right.CALL)) >= 0).all()

    def test_vectorised_matches_scalar(self):
        strikes = np.array([90.0, 100.0, 110.0])
        vec = np.asarray(bs_price(100, strikes, 1.0, 0.05, 0.2, 0.0, Right.CALL))
        one = [float(bs_price(100, k, 1.0, 0.05, 0.2, 0.0, Right.CALL)) for k in strikes]
        assert vec == pytest.approx(one)


class TestGreeks:
    def test_call_delta_in_unit_interval(self):
        g = greeks(100, np.array([80.0, 100.0, 120.0]), 1.0, 0.05, 0.2, 0.0, Right.CALL)
        assert ((g["delta"] > 0) & (g["delta"] < 1)).all()

    def test_put_delta_negative(self):
        g = greeks(100, np.array([80.0, 100.0, 120.0]), 1.0, 0.05, 0.2, 0.0, Right.PUT)
        assert ((g["delta"] < 0) & (g["delta"] > -1)).all()

    def test_delta_relation(self):
        """The difference between call and put delta is the dividend discount
        factor."""
        q, t = 0.01, 1.0
        c = greeks(100, 100, t, 0.05, 0.2, q, Right.CALL)["delta"]
        p = greeks(100, 100, t, 0.05, 0.2, q, Right.PUT)["delta"]
        assert float(c - p) == pytest.approx(np.exp(-q * t), abs=1e-10)

    def test_gamma_vega_non_negative_and_symmetric(self):
        c = greeks(100, 100, 1.0, 0.05, 0.2, 0.0, Right.CALL)
        p = greeks(100, 100, 1.0, 0.05, 0.2, 0.0, Right.PUT)
        assert float(c["gamma"]) > 0 and float(c["vega"]) > 0
        assert float(c["gamma"]) == pytest.approx(float(p["gamma"]))
        assert float(c["vega"]) == pytest.approx(float(p["vega"]))

    def test_expired_delta_is_binary(self):
        g = greeks(115, 100, 0.0, 0.05, 0.2, 0.0, Right.CALL)
        assert float(g["delta"]) == 1.0 and float(g["gamma"]) == 0.0


class TestImpliedVol:
    def test_round_trip(self):
        for sigma in (0.08, 0.2, 0.45, 1.2):
            price = float(bs_price(**BASE, sigma=sigma, right=Right.CALL))
            got = implied_vol(price, 100.0, 100.0, 1.0, 0.05, 0.0, Right.CALL)
            assert got.is_present and got.unwrap() == pytest.approx(sigma, abs=1e-4)

    def test_price_below_intrinsic_returns_missing(self):
        got = implied_vol(1.0, 150.0, 100.0, 1.0, 0.05, 0.0, Right.CALL)
        assert got.is_missing and "intrinsic" in got.reason

    def test_price_above_upper_bound_returns_missing(self):
        got = implied_vol(10_000.0, 100.0, 100.0, 1.0, 0.05, 0.0, Right.CALL)
        assert got.is_missing

    def test_expired_returns_missing(self):
        assert implied_vol(5.0, 100.0, 100.0, 0.0, 0.05, 0.0, Right.CALL).is_missing

    def test_never_raises_nor_returns_zero(self):
        """No solution must be an explicit absence — returning 0 selects a completely
        wrong contract downstream and raises nothing."""
        for price in (-1.0, 0.0, 1e9):
            got = implied_vol(price, 100.0, 100.0, 1.0, 0.05, 0.0, Right.CALL)
            assert got.is_missing


class TestEarlyExercise:
    """Where the European price is wrong because the option is American.

    Every case here is written at a $100 strike with the fixture's 4.5% and a year to
    run, so the carry on the strike is ``100 x (1 - exp(-0.045)) = 4.40`` and each
    assertion below can be checked against that figure by hand.
    """

    #: ``100 x (1 - exp(-0.045))``, to 2dp. The number every put case here turns on.
    CARRY = 4.40

    def test_the_carry_is_the_interest_on_the_strike(self):
        """The arithmetic the rest of this class rests on, stated once.

        It is here because the carry was computed from the **intrinsic value** rather than
        the strike, on the argument that intrinsic bounds the strike from below and an
        understated carry errs towards silence. At a $400 strike and 4.5% the real figure
        is $17.60 and the intrinsic of an ordinary deep in-the-money put gives something
        under $2 -- not conservative, out by an order of magnitude, and small enough that
        an ordinary dividend decided the answer in the one case that is not about
        dividends.
        """
        from backtest.data.options.pricing import _strike_carry

        assert _strike_carry(100.0, 0.045, 1.0) == pytest.approx(self.CARRY, abs=0.005)
        assert _strike_carry(400.0, 0.045, 1.0) == pytest.approx(17.60, abs=0.005)
        # No rate or no time left: nothing to earn, so nothing to collect early for.
        assert _strike_carry(100.0, 0.0, 1.0) == 0.0
        assert _strike_carry(100.0, 0.045, 0.0) == 0.0

    def test_flags_deep_itm_call_before_dividend(self):
        """A call is exercised early to capture a dividend, so the test is the dividend
        against the time value -- 0.20 of time value against 0.50 of dividend."""
        assert has_early_exercise_value(
            right=Right.CALL, option_price=20.2, intrinsic=20.0, strike=100.0,
            dividend_in_window=0.5,
        )

    def test_a_call_gives_up_the_carry_by_exercising_early(self):
        """The mirror of the put, and the half that was missing.

        Exercising a call early pays the strike today instead of at expiry, so the
        interest that money would have earned is given up. The condition is the dividend
        against the time value **and** that lost interest:

            call:  time value  <  dividend - carry
            put:   time value  <  carry - dividend

        0.20 of time value, a 1.00 dividend, 4.40 of carry: 0.20 < 1.00 - 4.40 is false,
        and without the carry 0.20 < 1.00 is true. The omission flagged calls as worth
        exercising when they were not -- at a $400 strike and 4.5% a quarter out the
        carry is about $4.45, larger than most quarterly dividends on an index ETF, so it
        inverted the usual answer rather than shading it.
        """
        held = dict(right=Right.CALL, option_price=20.2, intrinsic=20.0, strike=100.0,
                    rate=0.045, years_to_expiry=1.0)
        assert not has_early_exercise_value(dividend_in_window=1.0, **held)
        # A dividend that clears both: 0.20 < 6.00 - 4.40.
        assert has_early_exercise_value(dividend_in_window=6.0, **held)

    def test_a_call_at_zero_rates_is_decided_by_the_dividend_alone(self):
        """There is nothing to give up, so the dividend test stands by itself.

        Recorded as its own case because it is also the behaviour when a caller **omits**
        the rate: an unspecified rate is indistinguishable from a genuine zero here, and
        the answer is the permissive one. That is the argument for always passing it.
        """
        assert has_early_exercise_value(
            right=Right.CALL, option_price=20.2, intrinsic=20.0, strike=100.0,
            dividend_in_window=0.5, rate=0.0, years_to_expiry=1.0)

    def test_quiet_for_otm(self):
        assert not has_early_exercise_value(
            right=Right.CALL, option_price=1.2, intrinsic=0.0, strike=100.0,
            dividend_in_window=0.5,
        )

    def test_a_deep_itm_put_has_early_exercise_value_when_rates_are_positive(self):
        """The old version of this asserted that puts never do, which is not true.

        Exercising a put hands over the stock now and receives the strike now, and the
        strike earns interest for the remaining life: 0.20 of time value against 4.40 of
        carry, so exercising wins and a European price for it is simply wrong.
        """
        assert has_early_exercise_value(
            right=Right.PUT, option_price=20.2, intrinsic=20.0, strike=100.0,
            dividend_in_window=0.0, rate=0.045, years_to_expiry=1.0,
        )

    def test_the_carry_comes_from_the_strike_and_not_the_intrinsic(self):
        """The regression guard for the case above, where the two differ by enough to
        change the answer.

        A $400-strike put 5 in the money with 1.00 of time value. The carry on the strike
        is 17.60, so exercising wins comfortably. Computed from the intrinsic it is 0.22,
        and 1.00 of time value is above that -- the same option reads as quiet, which is
        how this went unnoticed: both answers are plausible and only one is arithmetic.
        """
        assert has_early_exercise_value(
            right=Right.PUT, option_price=6.0, intrinsic=5.0, strike=400.0,
            dividend_in_window=0.0, rate=0.045, years_to_expiry=1.0,
        )

    def test_a_put_is_quiet_when_rates_are_zero(self):
        """With no carry on the strike there is nothing to gain by exercising early."""
        assert not has_early_exercise_value(
            right=Right.PUT, option_price=20.2, intrinsic=20.0, strike=100.0,
            dividend_in_window=0.0, rate=0.0, years_to_expiry=1.0,
        )

    def test_a_put_with_plenty_of_time_value_is_quiet(self):
        """5.00 of time value against 4.40 of carry: holding is worth more."""
        assert not has_early_exercise_value(
            right=Right.PUT, option_price=25.0, intrinsic=20.0, strike=100.0,
            dividend_in_window=0.0, rate=0.045, years_to_expiry=1.0,
        )

    def test_a_dividend_makes_a_put_less_worth_exercising(self):
        """It works the other way for a put: the coming drop in the stock makes holding
        more attractive, so the same option can flip to quiet.

        **How large a dividend that takes is the point.** It has to exceed the whole
        remaining carry, 4.40, before 0.20 of time value looks like the better side -- so
        the figure here is 5.00, a 5% dividend on the strike. The earlier version of this
        test used 2.00, which flipped the answer only because the carry was being computed
        from the intrinsic and was therefore 0.88 rather than 4.40. The claim was right
        and the number proving it was not.
        """
        kwargs = dict(right=Right.PUT, option_price=20.2, intrinsic=20.0, strike=100.0,
                      rate=0.045, years_to_expiry=1.0)
        assert has_early_exercise_value(dividend_in_window=0.0, **kwargs)
        assert not has_early_exercise_value(dividend_in_window=5.0, **kwargs)
        # And a dividend below the carry does not: the direction is right, the size matters.
        assert has_early_exercise_value(dividend_in_window=2.0, **kwargs)

    def test_a_put_without_the_rate_cannot_be_assessed(self):
        """False because there is nothing to test against, not because the answer is no.
        The caller holding the rate should pass it."""
        assert not has_early_exercise_value(
            right=Right.PUT, option_price=20.2, intrinsic=20.0, strike=100.0,
            dividend_in_window=0.5,
        )


class TestTheChainReachesBothCases:
    """The flag is only worth having if the chain's own call site can reach it.

    It could not. The generator returned early when no dividend fell in the window --
    correct for the call, wrong for the put, which has nothing to do with dividends -- and
    never passed the rate or the time to expiry, without which the put branch returns
    False by construction. Two independently sufficient reasons for the same silence,
    which is why neither showed up as a failure.
    """

    @staticmethod
    def _flags(right: Right, spot: float, *, dividend: float = 0.0,
               rate: float = 0.045, years: float = 1.0):
        """``_early_exercise_flags`` on a $100 strike, 20 in the money, a year to run.

        The method is called on an instance built without the constructor, deliberately:
        what is under test is the branching inside it, and a real chain would also have to
        produce the quote, the dividend and the rate -- any of which going missing would
        make this pass or fail for a reason that is not the one named above.
        """
        from datetime import date

        import numpy as np

        from backtest.data.options.synthetic.generator import SyntheticChainSource

        class Market:
            def dividends_between(self, start, end):
                return dividend

        class Config:
            class contract:
                class exercise:
                    value = "american"

        source = SyntheticChainSource.__new__(SyntheticChainSource)
        source.market = Market()
        source.config = Config
        return SyntheticChainSource._early_exercise_flags(
            source, np.array([100.0]), np.array([20.2]), spot,
            date(2020, 1, 2), date(2021, 1, 4), right, rate, years,
        )

    def test_a_put_can_be_flagged_with_no_dividend_in_the_window(self):
        assert self._flags(Right.PUT, 80.0) == ["early_exercise_value"], (
            "a deep in-the-money put with positive rates was not flagged, so the put "
            "branch is unreachable from the chain again"
        )

    def test_a_call_is_still_quiet_with_no_dividend_in_the_window(self):
        """The early return was right for the call and has to stay right for it: a call
        is exercised early to capture a dividend, and there is none."""
        assert self._flags(Right.CALL, 120.0) == [""]

    def test_a_call_is_flagged_when_a_dividend_exceeds_the_time_value_and_the_carry(self):
        """The case the early return was guarding, still reachable.

        The figures changed when the call's carry was added, because the old ones only
        worked without it: 0.20 of time value against a 0.50 dividend reads as worth
        exercising until the 4.40 of interest given up over a year is counted, and then
        it plainly is not. A quarter out the carry is 1.12, and a 2.00 dividend clears
        both -- 0.20 < 2.00 - 1.12.
        """
        assert self._flags(Right.CALL, 120.0, dividend=2.0,
                           years=0.25) == ["early_exercise_value"]

    def test_a_dividend_below_the_carry_does_not_flag_a_call(self):
        """The half that was missing, at the chain's own call site.

        0.20 of time value against a 0.50 dividend and 4.40 of carry: exercising hands
        over the strike a year early to collect fifty cents. The old test asserted this
        case was flagged.
        """
        assert self._flags(Right.CALL, 120.0, dividend=0.5) == [""]
