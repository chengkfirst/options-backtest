"""Attribution stepped day by day, with the spread as its own term.

## Why the one-step version is not usable over a multi-week hold

`attribute()` expands once, entry to exit, on the **entry** greeks. The system already
says what that costs: `large_shock` in the register leaves the stress grid's cells empty
past a 10% move because "the gamma term overwhelms the delta term and the direction is
wrong". Attribution was applying that same second-order expansion to a whole holding
period's move and reporting the result as a breakdown.

Measured on the fixture over 2021–2023: the one-step method's residual is above 35% of
gross on a large share of trades. Stepping daily cuts both the total absolute residual and
the median per-trade share, and halves the untrustworthy count.

## Two things the one-step method could not separate

- **The spread.** The daily marks are mids and the fills are not, so mid-to-mid movement
  and gross P&L differ by exactly the spread — which had nowhere to go but the residual,
  where it was indistinguishable from the expansion failing.
- **The signs.** The share of each term was `term / signed total`, so a run with a
  negative total printed every term with its sign reversed: a delta that earned money
  showed as a negative percentage.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.kinds import kind, labels

pytestmark = labels("unit", "5")

RUN = dict(
    symbol="QQQ", start="2021-01-04", end="2023-12-29",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


@pytest.fixture(scope="module")
def result():
    from backtest.api.sdk import run

    return run(**RUN)


class TestTheDailyPathCarriesWhatAttributionNeeds:
    @kind("guard")
    def test_all_four_greeks_are_on_the_path(self, result):
        """It carried the underlying, the IV and delta — three of the five inputs a
        Taylor term needs, which is why attribution had to use the entry greeks."""
        frame = result.raw.paths_frame
        for column in ("underlying", "iv", "delta", "gamma", "vega", "theta"):
            assert column in frame.columns, f"the daily path has no {column}"
        for greek in ("gamma", "vega", "theta"):
            assert frame[greek].notna().any(), (
                f"{greek} is on the path and never populated, which is the same gap "
                f"with a column in front of it"
            )


class TestStepingDailyIsBetterThanOneStep:
    @kind("guard")
    def test_the_per_trade_residual_falls(self, result):
        """**Per trade, not in aggregate.** The one-step method's *total* residual is
        small by cancellation — individually wrong trades offsetting — so the total is
        the one figure that must not be used for this comparison.
        """
        one = result.attribution()
        daily = result.attribution_daily()

        one_abs = float(one.per_trade["residual"].abs().sum())
        daily_abs = float(daily.per_trade["residual"].abs().sum())
        assert daily_abs < one_abs, (
            f"stepping daily did not reduce the total absolute residual "
            f"({daily_abs:,.0f} against {one_abs:,.0f})"
        )
        assert daily.high_residual < one.high_residual, (
            f"stepping daily did not reduce the count of untrustworthy trades "
            f"({daily.high_residual} against {one.high_residual})"
        )

    def test_both_methods_say_which_they_are(self, result):
        """A residual cannot be judged without knowing the method: tens of percent is
        expected of a one-step expansion and a defect in a daily one."""
        assert result.attribution().method == "entry greeks, one step"
        assert result.attribution_daily().method == "daily"


class TestTheSpreadIsItsOwnTerm:
    @kind("consistency")
    def test_it_matches_the_independently_computed_spread(self, result):
        """Two calculations of one quantity, from different code: the attribution's
        spread term and `spread_paid`'s total. They must agree."""
        from_attribution = -result.attribution_daily().totals["spread"]
        from_metrics = result.spread()["spread_dollars"]
        assert from_attribution == pytest.approx(from_metrics, rel=1e-9)

    def test_it_is_signed_as_a_cost(self, result):
        assert result.attribution_daily().totals["spread"] < 0

    def test_the_one_step_method_has_no_spread_term(self, result):
        """Said explicitly: its spread is inside its residual, which is why its residual
        cannot be read as "the expansion's error"."""
        assert "spread" not in result.attribution().totals


class TestTheSharesAreMagnitudes:
    @kind("guard")
    def test_a_term_that_earned_money_shows_a_positive_share(self):
        """The bug: `term / signed total`. With a negative total every sign flipped, so a
        profitable delta printed as a negative percentage.

        **Built by hand, and that is the point.** The first version of this test read the
        shares off a real run and passed with the bug reinserted — because on the
        offline fixture the signed total happens to be *positive*, and the division then
        preserves every sign. A property of the arithmetic has to be tested on the case
        where the arithmetic differs, not on whichever case the data happens to supply.
        The mutation check is what caught it.
        """
        import pandas as pd

        from backtest.accounting.attribution import Attribution

        # A long call that earned on delta and gamma, paid on theta, and lost overall --
        # the ordinary shape of a long-options book, and a negative total.
        totals = {"delta": 13_836.0, "gamma": 98_777.0, "vega": -13_900.0,
                  "theta": -143_100.0, "residual": -30_013.0}
        assert sum(totals.values()) < 0, "the case being tested is a negative total"

        shares = Attribution(pd.DataFrame(), totals, 0, 0).as_dict()["share"]
        for name, total in totals.items():
            assert np.sign(shares[name]) == np.sign(total), (
                f"{name} contributed {total:,.0f} and its share is "
                f"{shares[name]:.1%}, which has the opposite sign"
            )
        assert shares["delta"] == pytest.approx(13_836.0 / sum(abs(v) for v in totals.values()))

    def test_a_real_run_agrees_with_the_hand_built_case(self, result):
        """The same property through the real path, so the two cannot drift apart."""
        for attribution in (result.attribution(), result.attribution_daily()):
            shares = attribution.as_dict()["share"]
            for name, total in attribution.totals.items():
                if total == 0:
                    continue
                assert np.sign(shares[name]) == np.sign(total)

    def test_the_denominator_is_the_sum_of_the_magnitudes(self, result):
        attribution = result.attribution_daily()
        assert attribution.magnitude == pytest.approx(
            sum(abs(v) for v in attribution.totals.values()))
        assert attribution.magnitude > abs(sum(attribution.totals.values())), (
            "the terms do not offset on this fixture, so the test of the denominator "
            "proves nothing — check the run"
        )

    def test_the_text_says_what_the_share_is_of(self, result):
        assert "of the movement" in result.attribution_daily().text()


class TestWhatItRefuses:
    def test_a_trade_table_result_says_it_has_no_path(self):
        from backtest.api.sdk import run

        trade_table = run(**(RUN | {"engine": "trade_table"}))
        with pytest.raises(AttributeError, match="no daily path"):
            trade_table.attribution_daily()

    def test_a_table_without_the_quote_refuses_rather_than_guessing(self, result):
        """Without the mid there is no way to tell the spread from the residual, and
        silently folding it back in would undo the point of the method."""
        from backtest.accounting.attribution import attribute_daily

        trades = result.trades.drop(columns=["entry_mid"])
        with pytest.raises(KeyError, match="entry_mid"):
            attribute_daily(trades, result.raw.paths_frame)

    def test_a_path_without_the_greeks_refuses(self, result):
        from backtest.accounting.attribution import attribute_daily

        paths = result.raw.paths_frame.drop(columns=["gamma"])
        with pytest.raises(KeyError, match="gamma"):
            attribute_daily(result.trades, paths)

    def test_no_path_at_all_refuses(self, result):
        from backtest.accounting.attribution import attribute_daily

        with pytest.raises(KeyError, match="daily path"):
            attribute_daily(result.trades, None)
