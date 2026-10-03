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
    def test_flags_deep_itm_call_before_dividend(self):
        assert has_early_exercise_value(
            right=Right.CALL, option_price=20.2, intrinsic=20.0, dividend_in_window=0.5
        )

    def test_quiet_for_otm(self):
        assert not has_early_exercise_value(
            right=Right.CALL, option_price=1.2, intrinsic=0.0, dividend_in_window=0.5
        )

    def test_a_deep_itm_put_has_early_exercise_value_when_rates_are_positive(self):
        """The old version of this asserted that puts never do, which is not true.

        Exercising a put hands over the stock now and receives the strike now, and the
        strike earns interest for the remaining life. With the fixture's 4.5% and a year
        to run, a put whose time value is a few cents is worth exercising -- and a
        European price for it is simply wrong.
        """
        assert has_early_exercise_value(
            right=Right.PUT, option_price=20.2, intrinsic=20.0,
            dividend_in_window=0.0, rate=0.045, years_to_expiry=1.0,
        )

    def test_a_put_is_quiet_when_rates_are_zero(self):
        """With no carry on the strike there is nothing to gain by exercising early."""
        assert not has_early_exercise_value(
            right=Right.PUT, option_price=20.2, intrinsic=20.0,
            dividend_in_window=0.0, rate=0.0, years_to_expiry=1.0,
        )

    def test_a_put_with_plenty_of_time_value_is_quiet(self):
        """Time value above the carry means holding is worth more than exercising."""
        assert not has_early_exercise_value(
            right=Right.PUT, option_price=25.0, intrinsic=20.0,
            dividend_in_window=0.0, rate=0.045, years_to_expiry=1.0,
        )

    def test_a_dividend_makes_a_put_less_worth_exercising(self):
        """It works the other way for a put: the coming drop in the stock makes holding
        more attractive, so the same option can flip to quiet."""
        kwargs = dict(right=Right.PUT, option_price=20.2, intrinsic=20.0,
                      rate=0.045, years_to_expiry=1.0)
        assert has_early_exercise_value(dividend_in_window=0.0, **kwargs)
        assert not has_early_exercise_value(dividend_in_window=2.0, **kwargs)

    def test_a_put_without_the_rate_cannot_be_assessed(self):
        """False because there is nothing to test against, not because the answer is no.
        The caller holding the rate should pass it."""
        assert not has_early_exercise_value(
            right=Right.PUT, option_price=20.2, intrinsic=20.0, dividend_in_window=0.5
        )
