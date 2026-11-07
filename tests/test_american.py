"""The binomial tree, against Black-Scholes and against theory — requirement 4.2.

## What makes this testable without trusting it

Every other price in this system is checked against itself. A tree is not: the same tree
that never exercises early **must** converge to Black-Scholes, which is a closed-form
answer computed by different code from different mathematics. That is the strongest check
available here and it is the first class below.

The second is theory, which pins the cases a number alone cannot:

- an American option is never worth less than a European one, and never less than its
  intrinsic value
- **a call on a non-dividend-paying stock is never exercised early** -- the textbook
  result, and the one a tree with a sign error gets wrong
- a deep in-the-money put with positive rates **is** exercised early
- more steps must move the price less, or the step count is not a convergence parameter

## The bug the impossibility caught

The first version read the root's hold value off `values[0]` after step 0 had already
overwritten it, mixing a root value with a step-1 value. It reported a hold value
**above** the American value, which cannot happen -- and that impossibility is what
showed it rather than any comparison with a reference price.
"""

from __future__ import annotations

import numpy as np
import pytest

from backtest.core.errors import StartupError
from backtest.core.types import Right
from backtest.data.options.american import DEFAULT_STEPS, value
from backtest.data.options.pricing import bs_price

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("known_answer", "1")


def _bs(spot, strike, years, rate, vol, dividend, right) -> float:
    return float(np.asarray(bs_price(spot, strike, years, rate, vol, dividend, right)))


#: spot, strike, years, rate, vol, dividend yield — spanning moneyness, tenor and rates.
CASES = [
    (400.0, 410.0, 0.25, 0.045, 0.22, 0.0),
    (400.0, 390.0, 0.25, 0.045, 0.22, 0.0),
    (400.0, 400.0, 0.08, 0.045, 0.22, 0.0),
    (400.0, 400.0, 2.00, 0.045, 0.22, 0.0),
    (400.0, 360.0, 0.50, 0.045, 0.45, 0.0),
    (400.0, 410.0, 0.25, 0.000, 0.22, 0.0),
    (400.0, 410.0, 0.25, 0.045, 0.22, 0.02),
]


class TestTheTreeAgreesWithBlackScholes:
    """The tree's **European** price, which is the same tree with exercise switched off.

    Computed by different mathematics in different code, so agreement is evidence rather
    than a tautology. This is the only check in the file that does not rest on theory.
    """

    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    @pytest.mark.parametrize("spot,strike,years,rate,vol,div", CASES)
    def test_the_european_leg_converges(self, right, spot, strike, years, rate, vol, div):
        got = value(spot=spot, strike=strike, years=years, rate=rate, vol=vol,
                     dividend_yield=div, right=right)
        expected = _bs(spot, strike, years, rate, vol, div, right)
        # A tolerance in the price's own terms, not absolute: a 0.05 error on a 30.00
        # option and on a 0.30 option are different things.
        assert got.european == pytest.approx(expected, rel=0.005, abs=0.02), (
            f"the tree's European leg is {got.european:.4f} and Black-Scholes says "
            f"{expected:.4f}. The two are different code and different mathematics, so a "
            f"disagreement is a bug in one of them rather than a tolerance to widen"
        )

    def test_more_steps_move_the_price_less(self):
        """Otherwise the step count is not a convergence parameter and ``DEFAULT_STEPS``
        is a number someone liked."""
        args = dict(spot=400.0, strike=400.0, years=0.5, rate=0.045, vol=0.22,
                     dividend_yield=0.0, right=Right.CALL)
        prices = [value(**args, steps=n).european for n in (25, 50, 100, 200, 400)]
        gaps = [abs(b - a) for a, b in zip(prices, prices[1:])]
        assert gaps == sorted(gaps, reverse=True), (
            f"the price is not settling as the tree is refined: successive changes are "
            f"{[round(g, 5) for g in gaps]}"
        )

    def test_the_default_step_count_is_within_a_cent_of_black_scholes(self):
        """``DEFAULT_STEPS`` is justified by measurement, and the first figure was wrong.

        The module said 200 steps stopped the price moving at the fourth decimal. This
        test disagreed immediately: a CRR tree converges as O(1/n), so the error **halves**
        each time the steps double rather than vanishing, and the fourth decimal would
        need tens of thousands of steps.

        So the criterion is the one that matters for the use -- within a cent, which is
        finer than the chain's own tick -- rather than a decimal place that sounded
        rigorous.
        """
        args = dict(spot=400.0, strike=400.0, years=0.5, rate=0.045, vol=0.22,
                     dividend_yield=0.0, right=Right.CALL)
        tree = value(**args, steps=DEFAULT_STEPS).american
        closed_form = _bs(400.0, 400.0, 0.5, 0.045, 0.22, 0.0, Right.CALL)
        assert abs(tree - closed_form) < 0.01, (
            f"at {DEFAULT_STEPS} steps the tree is {abs(tree - closed_form):.5f} from "
            f"Black-Scholes on a $29 option, which is coarser than the chain's tick"
        )

    def test_the_error_halves_when_the_steps_double(self):
        """The convergence **rate**, which is what makes the step count a trade-off
        rather than a guess. O(1/n) for a CRR tree, so a reader doubling the steps knows
        what they are buying."""
        args = dict(spot=400.0, strike=400.0, years=0.5, rate=0.045, vol=0.22,
                     dividend_yield=0.0, right=Right.CALL)
        closed_form = _bs(400.0, 400.0, 0.5, 0.045, 0.22, 0.0, Right.CALL)
        errors = [abs(value(**args, steps=n).european - closed_form)
                  for n in (200, 400, 800, 1600)]
        for coarse, fine in zip(errors, errors[1:]):
            assert fine == pytest.approx(coarse / 2, rel=0.15), (
                f"the error went from {coarse:.5f} to {fine:.5f} when the steps doubled, "
                f"which is not the O(1/n) a CRR tree has -- so either the tree is not "
                f"CRR or the comparison is not converging to Black-Scholes"
            )


class TestWhatTheoryRequires:
    """The cases a reference price cannot pin, because Black-Scholes does not answer
    them."""

    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    @pytest.mark.parametrize("spot,strike,years,rate,vol,div", CASES)
    def test_american_is_never_worth_less_than_european(self, right, spot, strike, years,
                                                        rate, vol, div):
        got = value(spot=spot, strike=strike, years=years, rate=rate, vol=vol,
                     dividend_yield=div, right=right)
        assert got.american >= got.european - 1e-9
        assert got.premium_over_european >= -1e-9

    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    @pytest.mark.parametrize("spot,strike,years,rate,vol,div", CASES)
    def test_american_is_never_worth_less_than_intrinsic(self, right, spot, strike,
                                                         years, rate, vol, div):
        """An American option can be exercised now, so it cannot be worth less than
        exercising now. A price below intrinsic is an arbitrage in the model."""
        got = value(spot=spot, strike=strike, years=years, rate=rate, vol=vol,
                     dividend_yield=div, right=right)
        assert got.american >= got.intrinsic - 1e-9

    @pytest.mark.parametrize("spot", [300.0, 400.0, 460.0, 600.0])
    def test_a_call_on_a_non_dividend_payer_is_never_exercised_early(self, spot):
        """**The textbook result**, and the one a tree with a sign error gets wrong.

        With no dividend there is nothing to capture by exercising, and exercising hands
        over the strike early for no reason -- so the American call equals the European
        one at every moneyness.
        """
        got = value(spot=spot, strike=400.0, years=1.0, rate=0.05, vol=0.25,
                     dividend_yield=0.0, right=Right.CALL)
        assert not got.exercise_now
        assert got.american == pytest.approx(got.european, rel=1e-6, abs=1e-6), (
            f"at spot {spot} the American call is {got.american:.4f} against a European "
            f"{got.european:.4f}. With no dividend they are the same option"
        )

    def test_a_deep_in_the_money_put_with_positive_rates_is_exercised(self):
        """The mirror, and the case the chain's flag was built for. Exercising collects
        the strike now, and the strike earns interest for the remaining life."""
        got = value(spot=300.0, strike=400.0, years=1.0, rate=0.05, vol=0.20,
                     dividend_yield=0.0, right=Right.PUT)
        assert got.exercise_now
        assert got.intrinsic > got.hold
        assert got.american == pytest.approx(got.intrinsic)

    def test_a_put_worth_exercising_later_but_not_now_is_worth_more_than_european(self):
        """**The case that separates the tree from a European formula with a floor.**

        A put 5% in the money with a year to run should not be exercised today -- the
        carry on the strike does not yet beat the remaining time value -- and it is still
        worth more than the European one, because at some nodes **later** in the tree
        exercising will be right.

        Measured: 33.81 American against 30.54 European. That 3.27 can only come from
        interior exercise, so a tree that takes the greater of holding and exercising
        **only at the root** reproduces the European price exactly and this is what
        catches it. Both the ordinary deep-in-the-money cases are masked by the root's own
        max, which is why they are not the test for this.
        """
        got = value(spot=380.0, strike=400.0, years=1.0, rate=0.05, vol=0.20,
                     dividend_yield=0.0, right=Right.PUT)
        assert not got.exercise_now, "the premise is that today is not the day"
        assert got.american > got.european + 1.0, (
            f"the American put is {got.american:.4f} and the European one "
            f"{got.european:.4f}. A put that will be worth exercising later is worth "
            f"more than one that never can be, and the difference can only come from "
            f"exercising at interior nodes"
        )
        assert got.american > got.intrinsic, (
            "and it is above its intrinsic value, so the premium is not the root's own "
            "floor"
        )

    def test_a_put_at_zero_rates_is_not_exercised_early(self):
        """With nothing to earn on the strike there is nothing to collect it for."""
        got = value(spot=300.0, strike=400.0, years=1.0, rate=0.0, vol=0.20,
                     dividend_yield=0.0, right=Right.PUT)
        assert not got.exercise_now

    def test_an_in_the_money_call_before_a_large_dividend_is_exercised(self):
        """The other case the chain flags: the dividend is worth more than the time
        value plus the interest given up."""
        got = value(spot=460.0, strike=400.0, years=0.25, rate=0.01, vol=0.18,
                     dividend_yield=0.08, right=Right.CALL)
        assert got.exercise_now


@kind("guard")
class TestTheImpossibilities:
    """Each of these cannot happen, and each caught something or would have."""

    @pytest.mark.parametrize("right", [Right.CALL, Right.PUT])
    @pytest.mark.parametrize("spot,strike,years,rate,vol,div", CASES)
    def test_holding_is_never_worth_more_than_the_american_value(self, right, spot,
                                                                 strike, years, rate,
                                                                 vol, div):
        """**The bug this found.**

        The American value is the greater of holding and exercising, so holding cannot
        exceed it. The first version read the root's hold off ``values[0]`` after step 0
        had overwritten it, mixing a root value with a step-1 value, and reported a hold
        above the American value -- an impossibility, which is what showed it rather than
        any comparison with a reference price.
        """
        got = value(spot=spot, strike=strike, years=years, rate=rate, vol=vol,
                     dividend_yield=div, right=right)
        assert got.hold <= got.american + 1e-9, (
            f"holding is worth {got.hold:.6f} and the American value is "
            f"{got.american:.6f}. The second is the max of the first and the intrinsic, "
            f"so this is arithmetic rather than a model disagreement"
        )

    def test_exercise_now_compares_the_intrinsic_with_holding_not_with_itself(self):
        """The failure the separate ``hold`` field exists to prevent.

        Reading the root off the tree gives the *max* of holding and exercising, so
        comparing the intrinsic with it makes ``exercise_now`` false for every option
        ever -- including a deep in-the-money put at 5%, where it is plainly true.
        """
        got = value(spot=300.0, strike=400.0, years=1.0, rate=0.05, vol=0.20,
                     dividend_yield=0.0, right=Right.PUT)
        assert got.hold < got.american, (
            "the hold value equals the American value on an option that should be "
            "exercised, so it is being read off the root"
        )

    def test_a_volatility_of_zero_is_refused(self):
        """Every path becomes the forward, so the tree has one node per step and prices
        a certainty rather than an option."""
        with pytest.raises(StartupError) as caught:
            value(spot=400.0, strike=400.0, years=1.0, rate=0.05, vol=0.0,
                   dividend_yield=0.0, right=Right.CALL)
        assert "certainty" in str(caught.value)

    def test_a_drift_outside_the_trees_reach_is_refused(self):
        """An arbitrage in the model rather than a numerical wobble: the risk-neutral
        probability falls outside [0, 1] and a price computed through it is a number with
        no meaning."""
        with pytest.raises(StartupError) as caught:
            value(spot=400.0, strike=400.0, years=1.0, rate=2.0, vol=0.01,
                   dividend_yield=0.0, right=Right.CALL, steps=4)
        assert "arbitrage in the model" in str(caught.value)

    def test_an_expiring_option_is_answered_rather_than_refused(self):
        """There is nothing to hold, so holding is worth the intrinsic and the
        comparison is an equality. An expiring position is a real thing to ask about."""
        got = value(spot=460.0, strike=400.0, years=0.0, rate=0.05, vol=0.20,
                     dividend_yield=0.0, right=Right.CALL)
        assert got.intrinsic == got.hold == got.american == 60.0
        assert not got.exercise_now


class TestTheAmericanIVSolve:
    """The document asks for "a European or American model **matching the contract
    style** (US equity and ETF options are American)", and the IV solve used a European
    model for everything.

    The error is one-sided and has a name. An American option is worth at least its
    European counterpart, so a European solve returns an IV that is **too low** wherever
    the early-exercise premium is real — and that premium is largest on exactly the
    contracts a researcher looks at hardest: a deep in-the-money put, a call before a
    dividend.
    """

    @staticmethod
    def _round_trip(*, vol: float, **kw):
        """Price at a known volatility, then solve it back."""
        from backtest.data.options.american import implied_vol_american

        priced = value(vol=vol, **kw)
        return priced, implied_vol_american(price=priced.american, **kw)

    def test_it_recovers_the_volatility_it_was_priced_at(self):
        """The round trip, which is the only check that does not rest on theory: price
        at a known σ with one function and solve it back with another."""
        for kw, truth in (
            (dict(spot=400.0, strike=400.0, years=0.5, rate=0.045,
                  dividend_yield=0.0, right=Right.CALL), 0.22),
            (dict(spot=380.0, strike=400.0, years=1.0, rate=0.05,
                  dividend_yield=0.0, right=Right.PUT), 0.25),
            (dict(spot=440.0, strike=400.0, years=0.5, rate=0.02,
                  dividend_yield=0.04, right=Right.CALL), 0.20),
        ):
            _, got = self._round_trip(vol=truth, **kw)
            assert got.is_present, got.reason
            assert got.unwrap() == pytest.approx(truth, abs=0.002), kw

    @kind("guard")
    def test_a_price_at_the_intrinsic_has_no_implied_volatility(self):
        """**The failure this found, and it is the project's own failure mode.**

        When exercising now is optimal the American value *is* the intrinsic, at every
        volatility — the root takes the max of holding and exercising and exercising wins
        throughout. So the price carries no information about σ, and bisection on a flat
        function returns wherever it stopped: on a 300/400 put at 5% it returned **0.1631
        for a contract priced at a true 0.25**. A plausible number with nothing behind it,
        returned because the arithmetic had to return something.
        """
        from backtest.data.options.american import implied_vol_american

        kw = dict(spot=300.0, strike=400.0, years=1.0, rate=0.05,
                  dividend_yield=0.0, right=Right.PUT)
        priced = value(vol=0.25, **kw)
        assert priced.exercise_now, "the premise is that exercising now is optimal"
        assert priced.american == pytest.approx(priced.intrinsic)

        got = implied_vol_american(price=priced.american, **kw)
        assert got.is_missing, (
            f"the solve returned {got.unwrap() if got.is_present else '?'} for a price "
            f"that every volatility produces"
        )
        assert "every" in got.reason and "intrinsic" in got.reason

    def test_a_european_solve_understates_it_where_the_premium_is_real(self):
        """The reason the American solve had to exist, with the figures.

        A put 5% in the money with a year to run: the American value is above the
        European one by the early-exercise premium, so solving that price with a European
        model has to invent extra volatility to reach it.
        """
        from backtest.data.options.pricing import implied_vol

        kw = dict(spot=380.0, strike=400.0, years=1.0, rate=0.05,
                  dividend_yield=0.0, right=Right.PUT)
        priced = value(vol=0.25, **kw)
        assert priced.american > priced.european + 1.0, "no premium, so nothing to show"

        european = implied_vol(priced.american, kw["spot"], kw["strike"], kw["years"],
                               kw["rate"], kw["dividend_yield"], kw["right"])
        assert european.is_present
        # **The direction is the claim, not the size.** An American option is worth at
        # least its European counterpart, so a European solve must reach for *more*
        # volatility -- that follows from the models and not from this data. How much
        # more depends on the strike, the tenor and the rate, so a threshold here would
        # be a measurement dressed as a law: I wrote 0.02 and the figure is 0.0196.
        assert european.unwrap() > 0.25, (
            f"the European solve returned {european.unwrap():.4f} for a price produced "
            f"at 0.25, and it has to be **higher**: it must invent volatility to reach "
            f"a price that early exercise explains"
        )
        # And the American solve does not have to, which is the pair that makes the
        # point: same price, two models, and only one of them needs the extra.
        from backtest.data.options.american import implied_vol_american

        american = implied_vol_american(price=priced.american, **kw)
        assert american.is_present
        assert abs(american.unwrap() - 0.25) < abs(european.unwrap() - 0.25), (
            f"the American solve ({american.unwrap():.4f}) is no closer to the truth "
            f"than the European one ({european.unwrap():.4f})"
        )

    @kind("guard")
    def test_the_bracket_starts_where_the_tree_can_price(self):
        """Not at an arbitrary epsilon.

        A CRR tree refuses a volatility far below the rate — the drift per step leaves
        the up/down move and the risk-neutral probability leaves [0, 1], which is an
        arbitrage in the model. The first version of this solve bracketed from 1e-4 and
        **raised that refusal** on its first call. The floor is derived from the same
        condition now: ``vol > |r − q| · sqrt(dt)``, with a factor of two of headroom.
        """
        from backtest.data.options.american import implied_vol_american

        # A high rate makes the floor bite, and the solve must still answer.
        got = implied_vol_american(
            price=value(spot=400.0, strike=400.0, years=1.0, rate=0.20, vol=0.22,
                        dividend_yield=0.0, right=Right.CALL).american,
            spot=400.0, strike=400.0, years=1.0, rate=0.20, dividend_yield=0.0,
            right=Right.CALL)
        assert got.is_present, got.reason
        assert got.unwrap() == pytest.approx(0.22, abs=0.005)

    def test_a_price_no_volatility_produces_is_refused(self):
        from backtest.data.options.american import implied_vol_american

        kw = dict(spot=400.0, strike=400.0, years=0.5, rate=0.045,
                  dividend_yield=0.0, right=Right.CALL)
        assert implied_vol_american(price=1e9, **kw).is_missing
        assert implied_vol_american(price=0.0, **kw).is_missing
        assert implied_vol_american(price=-1.0, **kw).is_missing

    def test_a_price_below_intrinsic_is_refused_with_the_reason(self):
        """An American option can be exercised now, so no volatility produces a price
        below its intrinsic value."""
        from backtest.data.options.american import implied_vol_american

        got = implied_vol_american(price=30.0, spot=440.0, strike=400.0, years=0.5,
                                   rate=0.045, dividend_yield=0.0, right=Right.CALL)
        assert got.is_missing and "intrinsic" in got.reason

    def test_an_expired_contract_has_no_implied_volatility(self):
        from backtest.data.options.american import implied_vol_american

        got = implied_vol_american(price=5.0, spot=400.0, strike=400.0, years=0.0,
                                   rate=0.045, dividend_yield=0.0, right=Right.CALL)
        assert got.is_missing and "expired" in got.reason
