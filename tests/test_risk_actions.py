"""Book-wide risk: the four actions, and whether they act.

The architecture document limits a risk rule to four actions and says why -- so they can be
audited. A rule that can do anything leaves no way to ask afterwards what was done.

The failure this file exists to catch is the one that was there: the drawdown brake read as
a brake on the book and scaled **new entries only**, leaving every open position untouched.
The document's scale_down is "reduce all positions by a ratio". A rule that appears to fire
and changes nothing is worse than no rule, because the equity curve looks like the rule was
tested.
"""

from __future__ import annotations

import pytest

from backtest import run
from backtest.core.errors import StartupError
from backtest.portfolio.constraints import DrawdownBrake
from backtest.portfolio.risk_actions import (
    BUILT, NOTHING, RiskAction, RiskDecision, require_built,
)

BASE = dict(
    symbol="QQQ", start="2022-06-01", end="2023-12-31",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "risk_pct", "risk_pct": 0.05}, quantity=1,
)
#: Two configurations, because one cannot exercise both stages.
#:
#: That is the brake working, not a fixture problem: a rule loose enough to spend time in
#: the "reduced" band never draws down far enough to liquidate, and one tight enough to
#: liquidate does so before anything is reduced. Using one set of numbers for both would
#: mean one of the two tests was passing for a reason unrelated to what it claims.
SCALE = {"reduce_at": 0.05, "halt_at": 0.30, "resume_at": 0.03, "keep": 0.5}
LIQUIDATE = {"reduce_at": 0.05, "halt_at": 0.08, "resume_at": 0.03, "keep": 0.5,
             "liquidate_at": 0.10}
TIGHT = SCALE


class TestVocabulary:
    def test_every_action_says_whether_it_is_built(self):
        assert set(BUILT) == set(RiskAction)

    def test_an_unbuilt_action_is_refused_with_its_reason(self):
        """Absent from the enum would give an attribute error somewhere unhelpful;
        present and silently ignored would be a risk rule that appears to fire."""
        with pytest.raises(StartupError, match="not built"):
            require_built(RiskAction.PAUSE)

    def test_scale_down_needs_a_ratio(self):
        """How hard to cut is a risk decision. A platform-chosen ratio would size the
        whole book without appearing in the configuration."""
        with pytest.raises(StartupError, match="needs `keep`"):
            RiskDecision(RiskAction.SCALE_DOWN)

    def test_a_ratio_on_any_other_action_is_refused(self):
        with pytest.raises(StartupError, match="only means something for scale_down"):
            RiskDecision(RiskAction.LIQUIDATE, keep=0.5)

    def test_liquidation_blocks_new_entries(self):
        """Closing the book while still opening would be incoherent."""
        assert RiskDecision(RiskAction.LIQUIDATE).blocks_entries
        assert RiskDecision(RiskAction.NO_NEW_POSITIONS).blocks_entries
        assert not NOTHING.blocks_entries


class TestBrake:
    BRAKE = DrawdownBrake(reduce_at=0.10, halt_at=0.20, resume_at=0.05, keep=0.5,
                          liquidate_at=0.30)

    @pytest.mark.parametrize("drawdown,action", [
        (-0.02, RiskAction.NONE),
        (-0.12, RiskAction.SCALE_DOWN),
        (-0.22, RiskAction.NO_NEW_POSITIONS),
        (-0.35, RiskAction.LIQUIDATE),
    ])
    def test_each_stage_maps_to_an_action(self, drawdown, action):
        assert self.BRAKE.decide(drawdown).action is action

    def test_nothing_is_defaulted(self):
        """Where to start cutting and how hard govern the whole book."""
        with pytest.raises(TypeError):
            DrawdownBrake(reduce_at=0.10)

    def test_the_stages_must_widen(self):
        with pytest.raises(StartupError, match="must widen"):
            DrawdownBrake(reduce_at=0.30, halt_at=0.10, resume_at=0.05, keep=0.5)

    def test_liquidating_before_halting_is_refused(self):
        with pytest.raises(StartupError, match="while still opening is incoherent"):
            DrawdownBrake(reduce_at=0.10, halt_at=0.20, resume_at=0.05, keep=0.5,
                          liquidate_at=0.15)

    def test_never_liquidating_is_a_real_choice(self):
        brake = DrawdownBrake(reduce_at=0.10, halt_at=0.20, resume_at=0.05, keep=0.5)
        assert brake.decide(-0.90).action is RiskAction.NO_NEW_POSITIONS


class TestItActsOnTheBook:
    """The failure this catches: a rule that fires and changes nothing."""

    @pytest.fixture(scope="class")
    def braked(self):
        return run(**BASE, risk=SCALE)

    @pytest.fixture(scope="class")
    def liquidated(self):
        return run(**BASE, risk=LIQUIDATE)

    def test_scaling_down_reduces_positions_that_are_open(self, braked):
        actions = braked.raw.actions_frame
        cut = actions[(actions["action"] == "reduce") &
                      (actions["component_id"] == "headwind")]
        assert len(cut) > 0, "the book-wide rule reduced nothing"

    def test_liquidation_closes_them(self, liquidated):
        actions = liquidated.raw.actions_frame
        closed = actions[(actions["action"] == "close") &
                         (actions["component_id"] == "headwind")]
        assert len(closed) > 0
        assert "liquidate" in closed.iloc[0]["reason"]

    def test_a_forced_close_is_recorded_as_such(self, liquidated):
        """Not as a stop loss. A position cut because the whole book was being closed is
        a different event from one closed because its own thesis broke, and a trade log
        cannot tell them apart without this."""
        assert (liquidated.trades["exit_reason"] == "risk_forced").any()

    def test_the_rule_changes_the_result(self):
        """If the equity curves matched, the rule would be decoration."""
        plain = run(**BASE)
        braked = run(**BASE, risk=TIGHT)
        assert len(braked.trades) != len(plain.trades)

    def test_a_one_contract_position_is_left_alone_rather_than_closed(self):
        """Rounding has to go the safe way: "reduce" must never become "close", or a
        scale-down would quietly liquidate every small position.

        Run, not recomputed. The first version of this re-derived the engine's own
        arithmetic in the test and then asserted on its own result, so it never touched
        the engine and could not have failed.
        """
        braked = run(**(BASE | {"quantity": 1,
                                "sizing": {"method": "fixed", "contracts": 1}}),
                     risk=TIGHT)
        actions = braked.raw.actions_frame
        reduced = actions[(actions["action"] == "reduce") &
                          (actions["component_id"] == "headwind")]
        # With one contract held, `quantity - int(quantity x 0.5)` is 1, which is the whole
        # position -- so the engine must skip it rather than issue a reduction that closes.
        assert reduced.empty or (reduced["quantity"] < 1).all(), (
            "a one-contract position was reduced, which can only mean it was closed by a "
            "rule that said reduce"
        )
        # And nothing was closed by the book-wide rule under a scale-down either, except
        # where the rule actually said liquidate.
        closed = actions[(actions["action"] == "close") &
                         (actions["component_id"] == "headwind")]
        assert all("liquidate" in r for r in closed["reason"]), (
            "the book-wide rule closed a position without saying liquidate"
        )


class TestRefusals:
    def test_the_trade_table_refuses_a_risk_rule(self):
        """Scaling the book down acts on positions that are open, and the trade table
        computes each trade independently with no book to act on."""
        with pytest.raises(StartupError, match="needs the event-driven engine"):
            run(**(BASE | {"engine": "trade_table"}), risk=TIGHT)

    def test_a_partial_rule_is_refused(self):
        with pytest.raises(StartupError, match="risk needs"):
            run(**BASE, risk={"reduce_at": 0.1})

    def test_an_unknown_key_is_refused(self):
        with pytest.raises(StartupError, match="does not take"):
            run(**BASE, risk=dict(TIGHT, nonsense=1))
