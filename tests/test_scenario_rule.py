"""A scenario loss limit that blocks a trade — requirement 3.3.

## What was missing

The stress grid has reported for a long time and **nothing read it before placing an
order**, so a limit a researcher had set lived in a report rather than in the book.

## The trap this module is mostly about

The grid gives **no number** outside roughly a 10% move, deliberately: for a buyer's book
gamma is positive, and under a large shock the second-order term overwhelms the delta term
and the result's *direction* is wrong -- the underlying falls 20% and the book "makes
money". The grid returns `NaN` rather than a confidently wrong figure.

So the obvious rule -- `if loss > limit: block` -- treats a `NaN` as a pass, because every
comparison with a missing value is false. **The one scenario a researcher most wants a
limit on is the one that would silently never block.**

Two mechanisms against it, and both are tested here: a limit asking about a shock the grid
cannot price is refused at construction, and an unknown cell inside the scenarios being
checked is reported as a breach rather than a pass.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from backtest.core.errors import StartupError
from backtest.portfolio.scenario_rule import (
    Breach,
    ScenarioLimit,
    assessable_shock,
    check,
    describe,
)
from backtest.portfolio.scenarios import PortfolioGreeks

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "3")


def _greeks(*, delta=50.0, gamma=2.0, vega=300.0, theta=-2000.0,
            spot=400.0) -> PortfolioGreeks:
    return PortfolioGreeks(day=date(2023, 5, 1), spot=spot, delta=delta, gamma=gamma,
                           vega=vega, theta=theta, n_positions=3, equity=100_000.0,
                           missing=0)


class TestTheLimitBinds:
    #: **The vol shock is in absolute volatility points, not a relative change.** The
    #: grid's own defaults are ±0.05 and ±0.10, and it labels 0.05 as "IV+5%" -- which at
    #: an IV of 25% a reader will take as +1.25 points rather than +5. The ambiguity is
    #: older than this module and is not changed here; what matters for these tests is
    #: using the scale the grid uses.
    #:
    #: It also matters which direction is adverse. A **long** call book gains from rising
    #: IV, so a scenario of "spot down, IV up" is not a loss scenario for it -- the first
    #: version of these tests used one and read the absence of a breach as the rule not
    #: being consulted.
    ADVERSE = (-0.05, -0.05)        # spot down 5%, IV down 5 points

    def test_a_loss_above_the_limit_is_a_breach(self):
        """Hand arithmetic, on the greeks in ``_greeks``.

            delta 50 x (-20)              = -1,000
            0.5 x gamma 2 x (-20)^2       =   +400
            vega 300 x (-0.05)            =    -15
                                            -------
                                             -615

        So a 600 limit is breached and a 700 limit is not.
        """
        greeks = _greeks()
        scenario = (self.ADVERSE,)
        assert check(greeks, ScenarioLimit(max_loss=600.0, scenarios=scenario))
        assert not check(greeks, ScenarioLimit(max_loss=700.0, scenarios=scenario))

    def test_which_direction_is_adverse_depends_on_the_book(self):
        """Asserted because getting it wrong is how these tests first went wrong.

        A **vega-dominated** long book -- small delta, large vega -- is ahead when IV
        rises and behind when it falls, with the same spot move either way:

            delta 5 x (-20)          =  -100
            vega 3,000 x (+0.05)     =  +150   ->  +50, a gain
            vega 3,000 x (-0.05)     =  -150   -> -250, a loss

        The book in ``_greeks`` is delta-dominated, so **both** IV directions are losses
        for it. The first version of this file asserted the opposite on that book and
        read the absence of a breach as the rule never being consulted -- which sent me
        looking for a bug in the engine that was not there.
        """
        vega_heavy = _greeks(delta=5.0, gamma=0.0, vega=3000.0, theta=0.0)
        assert not check(vega_heavy,
                         ScenarioLimit(max_loss=1.0, scenarios=((-0.05, 0.05),)))
        breaches = check(vega_heavy,
                         ScenarioLimit(max_loss=1.0, scenarios=((-0.05, -0.05),)))
        assert breaches and breaches[0].loss == pytest.approx(250.0, abs=1.0)

    def test_a_gain_is_never_a_breach(self):
        """A scenario the book profits in is not a risk, however large the move."""
        greeks = _greeks(delta=-50.0, gamma=0.0, vega=0.0, theta=0.0)
        assert not check(greeks, ScenarioLimit(max_loss=1.0,
                                                scenarios=((-0.05, 0.0),)))

    def test_the_breach_says_which_scenario_and_by_how_much(self):
        breach = check(_greeks(), ScenarioLimit(max_loss=100.0,
                                                 scenarios=(self.ADVERSE,)))[0]
        assert breach.spot_shock == -0.05 and breach.vol_shock == -0.05
        assert breach.loss == pytest.approx(615.0, abs=1.0)
        assert "spot -5%" in breach.describe() and "IV -5%" in breach.describe()

    def test_only_the_breaching_scenarios_are_returned(self):
        limit = ScenarioLimit(max_loss=600.0,
                              scenarios=((-0.05, -0.05), (0.05, 0.05), (-0.02, 0.0)))
        breaches = check(_greeks(), limit)
        assert {b.spot_shock for b in breaches} == {-0.05}
        assert "1 of 3 scenario(s)" in describe(breaches, limit)


@kind("guard")
class TestTheUnknownCellIsNotAPass:
    """The trap. Every comparison with a missing value is false."""

    def test_a_shock_the_grid_cannot_price_is_refused_at_construction(self):
        """The first mechanism, and the one that catches the mistake earliest.

        A limit on a 20% fall reads as the most prudent setting in the file and would
        never bind.
        """
        with pytest.raises(StartupError) as caught:
            ScenarioLimit(max_loss=1000.0, scenarios=((-0.20, 0.0),))
        text = str(caught.value)
        assert "outside what the stress grid can price" in text
        assert "direction" in text, "the refusal does not say why no number is given"
        assert "scenario_replay" in text, "it does not say what to use instead"

    def test_the_boundary_is_read_from_the_grid_and_not_written_again(self):
        """Two copies of the reachable shock drifting apart would make a limit that
        looks enforced and is not."""
        from backtest.portfolio.scenarios import _large_shock

        assert assessable_shock() == _large_shock()

    def test_an_unknown_cell_inside_the_checked_scenarios_is_a_breach(self):
        """The second mechanism, for a cell the grid declines even inside its range.

        Constructed by making the grid return no number for a shock the limit accepted,
        which is what a future change to the grid's own boundary would do.
        """
        import backtest.portfolio.scenario_rule as rule

        limit = ScenarioLimit(max_loss=1_000_000.0, scenarios=((-0.05, -0.05),))

        class NoNumber:
            def __getitem__(self, key):
                return float("nan")

        class Grid:
            @property
            def iloc(self):
                return {0: NoNumber()}

        original = rule.stress_grid if hasattr(rule, "stress_grid") else None
        import backtest.portfolio.scenarios as scenarios

        saved = scenarios.stress_grid
        scenarios.stress_grid = lambda *a, **k: Grid()
        try:
            breaches = check(_greeks(), limit)
        finally:
            scenarios.stress_grid = saved

        assert len(breaches) == 1
        assert breaches[0].unknown, "an unpriced cell was reported as a pass"
        assert math.isnan(breaches[0].loss), (
            "an unpriced cell was reported as a loss of zero, which reads as a figure"
        )
        assert "never bind" in breaches[0].describe()

    def test_a_limit_of_zero_is_refused(self):
        with pytest.raises(StartupError) as caught:
            ScenarioLimit(max_loss=0.0, scenarios=((-0.05, 0.0),))
        assert "it is a halt" in str(caught.value)

    def test_a_limit_with_no_scenarios_is_refused(self):
        """Which shocks a strategy should survive is a research choice, so there is no
        default -- a platform-supplied set would be the platform deciding what risk
        means."""
        with pytest.raises(StartupError) as caught:
            ScenarioLimit(max_loss=1000.0, scenarios=())
        assert "blocks nothing" in str(caught.value)


class TestTheEngineReadsIt:
    """The unit tests prove the rule binds. These prove the engine asks it before
    placing an order -- which is the half that was missing for the grid's whole life."""

    RUN = dict(
        symbol="QQQ", start="2023-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    @pytest.mark.req("3.3.scenario")
    def test_a_tight_limit_blocks_trades_and_a_loose_one_does_not(self):
        """Both halves, because each is meaningless alone: a limit that blocks
        everything is not enforcement, and one that blocks nothing is not a limit.

        Asserted as an ordering rather than as counts -- how many trades a particular
        limit removes on this data is a market outcome, and the research behind it has
        not been done.
        """
        from backtest.api.sdk import run

        base = len(run(**self.RUN).trades)
        assert base > 3, "too few trades for a limit to have anything to block"

        loose = len(run(**(self.RUN | {"scenario_limit": {
            "max_loss": 1e9, "scenarios": [[-0.05, -0.05]]}})).trades)
        assert loose == base, (
            "a limit of a billion changed the result, so it is not the limit that is "
            "being read"
        )

        tight = len(run(**(self.RUN | {"scenario_limit": {
            "max_loss": 1.0, "scenarios": [[-0.05, -0.05]]}})).trades)
        assert tight < base, (
            "a limit of one dollar blocked nothing, so the engine is not consulting it "
            "before placing an order"
        )

    def test_a_blocked_entry_says_which_scenario_blocked_it(self):
        from backtest.api.sdk import run
        from backtest.core.run_log import EventKind

        result = run(**(self.RUN | {"scenario_limit": {
            "max_loss": 1.0, "scenarios": [[-0.05, -0.05]]}}))
        blocked = [e for e in result.raw.events.of_kind(EventKind.CANDIDATE_DROPPED)
                   if "breach" in e.detail]
        assert blocked, "entries were blocked and nothing said why"
        assert "spot -5%" in blocked[0].detail
        assert "limit" in blocked[0].detail

    @pytest.mark.req("0.5.hash")
    def test_two_limits_do_not_share_a_run_id(self):
        """A limit that changes which trades happen and does not reach the fingerprint
        would make a search look smaller than it was. Fifth time this class of gap has
        been checked for."""
        from backtest.api.sdk import run

        def run_id(**over):
            return run(**(self.RUN | over)).manifest()["run_id"]

        plain = run_id()
        assert plain != run_id(scenario_limit={"max_loss": 1.0,
                                                "scenarios": [[-0.05, -0.05]]})

    def test_the_trade_table_engine_refuses_it(self):
        """It evaluates each trade independently with no book, and the limit is about
        the book as it would be with the trade in it."""
        from backtest.api.sdk import run

        with pytest.raises(StartupError) as caught:
            run(**(self.RUN | {"engine": "trade_table",
                                "scenario_limit": {"max_loss": 1.0,
                                                   "scenarios": [[-0.05, -0.05]]}}))
        assert "no book" in str(caught.value)

    def test_an_unknown_setting_is_refused(self):
        from backtest.api.sdk import run

        with pytest.raises(StartupError) as caught:
            run(**(self.RUN | {"scenario_limit": {"max_los": 1.0,
                                                   "scenarios": [[-0.05, -0.05]]}}))
        assert "max_los" in str(caught.value)
