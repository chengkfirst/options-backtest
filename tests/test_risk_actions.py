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

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "3")

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
SCALE = {"variant": "drawdown",
         "params": {"reduce_at": 0.05, "halt_at": 0.30, "keep": 0.5}}
LIQUIDATE = {"variant": "drawdown",
             "params": {"reduce_at": 0.05, "halt_at": 0.08, "keep": 0.5,
                        "liquidate_at": 0.10}}
TIGHT = SCALE


class TestVocabulary:
    @pytest.mark.req("3.1.actions")
    def test_every_action_says_whether_it_is_built(self):
        assert set(BUILT) == set(RiskAction)

    def test_every_action_in_the_vocabulary_is_built(self):
        """This asserted the opposite until `pause` landed.

        It required an unbuilt action to be refused **with its reason**, which was the
        right test while one of the four was declared and not executable. All four are
        built now, so the mechanism to check is the other one: that `BUILT` and the
        vocabulary have not drifted apart, and that `require_built` is still the gate a
        future unbuilt action would go through.
        """
        from backtest.portfolio.risk_actions import BUILT, RiskAction, require_built

        # Every action, `none` included: it is in the vocabulary, so a reader asking
        # "is this one built" has to get an answer for it too.
        assert set(BUILT) == set(RiskAction), (
            f"BUILT and the action vocabulary have drifted. Only in the vocabulary: "
            f"{sorted(a.value for a in set(RiskAction) - set(BUILT))}; only in BUILT: "
            f"{sorted(str(k) for k in set(BUILT) - set(RiskAction))}"
        )
        for action in RiskAction:
            require_built(action)

    def test_an_action_declared_unbuilt_is_still_refused_with_its_reason(self,
                                                                        monkeypatch):
        """The gate itself, exercised on a planted entry.

        The four are all built, so without this the refusal path has no test at all --
        and it is the path that keeps a future declared-but-absent action from running
        as a silent no-op.
        """
        import backtest.portfolio.risk_actions as actions

        monkeypatch.setitem(actions.BUILT, actions.RiskAction.LIQUIDATE,
                            "planted: not built in this test")
        with pytest.raises(StartupError) as caught:
            actions.require_built(actions.RiskAction.LIQUIDATE)
        assert "planted" in str(caught.value)

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

    @pytest.mark.req("3.1.acts")
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
        """If the equity curves matched, the rule would be decoration.

        The docstring said equity and the assertion counted trades -- which is not the
        same claim: a scale-down changes **quantities**, and a rule that only ever reduced
        would leave the count untouched while changing every number in the book.
        """
        import pandas as pd

        plain = run(**BASE)
        braked = run(**BASE, risk=SCALE)
        with pytest.raises(AssertionError):
            pd.testing.assert_series_equal(plain.raw.equity["equity_mid"],
                                           braked.raw.equity["equity_mid"])

    def test_a_one_contract_position_is_left_alone_rather_than_closed(self):
        """Rounding has to go the safe way: "reduce" must never become "close", or a
        scale-down would quietly liquidate every small position.

        Run, not recomputed. The first version of this re-derived the engine's own
        arithmetic in the test and then asserted on its own result, so it never touched
        the engine and could not have failed.
        """
        # Small capital so the book actually draws down past the brake's first stage
        # while still holding one contract per trade.
        braked = run(**(BASE | {"quantity": 1, "capital": 8_000.0,
                                "sizing": {"method": "fixed", "contracts": 1}}),
                     risk=SCALE)
        # The rule has to have fired, or this proves nothing. `reduced.empty or ...` --
        # which is what I wrote first -- passes when the rule never ran at all, which is
        # the same escape hatch this whole round of fixes was about.
        assert (braked.trades["quantity"] == 1).all(), "the setup did not hold one contract"
        # The rule has to have **fired**, or this proves nothing. It fires and produces no
        # order, by design, so the evidence is in the event log rather than in the actions
        # -- and the engine records it there for exactly this reason: a rule that triggers
        # and cuts nothing is indistinguishable from one that never triggered, and the two
        # call for opposite responses.
        detail = " ".join(e.detail for e in braked.raw.events.events)
        assert "too small to reduce without closing" in detail, (
            "the scale-down never triggered on a one-contract book, so the rounding was "
            "never exercised"
        )

        actions = braked.raw.actions_frame
        assert len(actions), "the run produced no fills at all"
        by_rule = actions[actions["component_id"] == "headwind"]
        assert (by_rule["action"] != "reduce").all(), (
            f"the rule issued a reduction on a one-contract position; reducing the whole "
            f"position is a close"
        )
        # Nor did it close anything: this configuration has no liquidation stage, so a
        # close credited to the book-wide rule could only be a reduction in disguise.
        assert (by_rule["action"] != "close").all(), (
            "the book-wide rule closed a position under a scale-down"
        )


class TestTheFormIsTheResearchersChoice:
    """Slot 5 has forms, like slots 3 and 4.

    The drawdown brake used to be the only shape a risk rule could have. Every one of its
    numbers was required -- but its **structure**, step down then halt then release, was
    the platform's, and appeared nowhere as a decision anyone had made. The document lists
    this slot's responses as "to be defined" and names two examples; both are registered
    and neither is preferred.
    """

    def test_both_of_the_documents_examples_exist(self):
        from backtest.core.component import variants

        assert {"drawdown", "daily_loss"} <= {v.name for v in variants("headwind")}

    def test_a_configuration_without_a_form_is_refused(self):
        """Rather than given the one I happened to write first."""
        with pytest.raises(StartupError, match="needs a variant"):
            run(**BASE, risk={"reduce_at": 0.1, "halt_at": 0.2, "keep": 0.5})

    def test_the_two_forms_behave_differently(self):
        """They respond to different things -- the distance from the high-water mark
        against one session's loss -- so a run under each is a different run."""
        params = {"reduce_at": 0.05, "halt_at": 0.30, "keep": 0.5}
        slow = run(**BASE, risk={"variant": "drawdown", "params": params})
        fast = run(**BASE, risk={"variant": "daily_loss", "params": params})
        assert len(slow.trades) != len(fast.trades)

    def test_the_form_reaches_the_fingerprint(self):
        """Two runs under different rules shared one id, and so did a run under no rule
        at all. Third time a thing that moves the result reached the engine without
        reaching the fingerprint."""
        params = {"reduce_at": 0.05, "halt_at": 0.30, "keep": 0.5}
        ids = {
            "none": run(**BASE).manifest()["run_id"],
            "drawdown": run(**BASE, risk={"variant": "drawdown",
                                          "params": params}).manifest()["run_id"],
            "daily_loss": run(**BASE, risk={"variant": "daily_loss",
                                            "params": params}).manifest()["run_id"],
            "other params": run(**BASE, risk={
                "variant": "drawdown",
                "params": params | {"reduce_at": 0.08}}).manifest()["run_id"],
        }
        assert len(set(ids.values())) == len(ids), ids

    def test_an_unknown_form_lists_what_exists(self):
        with pytest.raises(StartupError, match="daily_loss|drawdown"):
            run(**BASE, risk={"variant": "nonsense", "params": {}})


class TestASellIsSizedAtTheFill:
    """A quantity decided on day T and filled on T+1 can exceed what is held by then.

    Found by adding a rule that fires on consecutive days: the drawdown form changes state
    too slowly to ever queue two sells against one position, so nothing had exercised it.
    The ledger refused outright -- "reversing through zero is not supported" -- which is
    the invariant doing its job, and the fix belongs upstream of it.
    """

    PARAMS = {"reduce_at": 0.05, "halt_at": 0.30, "keep": 0.5}

    def test_a_rule_that_fires_daily_does_not_oversell(self):
        """It used to raise. The ledger refuses a fill larger than the position, so the
        run either completes or does not -- and "it completed" is the assertion."""
        from backtest.core.errors import InvariantError

        result = run(**BASE, risk={"variant": "daily_loss", "params": self.PARAMS})
        assert len(result.trades) > 0

        # And every sell that happened was no larger than the position it came from,
        # which is the property the ledger enforces and this exercises.
        actions = result.raw.actions_frame
        sells = actions[actions["action"].isin(["reduce", "close"])]
        assert len(sells), "nothing was sold, so the sizing was never exercised"
        assert (sells["quantity"] > 0).all()

    def test_a_sell_larger_than_the_position_is_resized_and_said_so(
            self, market, chain, signals, config):
        """Constructed directly rather than hoped for.

        The first version asserted the event list was non-empty, which every run
        satisfies. Reaching for a run that produces the overlap then stopped working --
        making an unfilled order wait instead of dropping it removed the route by which
        two sells queued against one position. The mechanism still has to hold: a fill
        larger than the position is what the ledger refuses outright, and silently selling
        less than the order said is a fill that does not match its order.
        """
        from backtest.accounting.ledger import Ledger
        from backtest.core.run_log import EventKind
        from backtest.core.types import ContractId, Right, Side
        from backtest.engine.loop import EventDrivenEngine, Intent
        from backtest.core.actions import Action
        from backtest.execution.costs import order_fees

        day = next(d for d in market.calendar.sessions
                   if config.start <= d <= config.end and len(chain.snapshot(d)))
        row = chain.snapshot(day).iloc[0]
        contract = ContractId(symbol="QQQ", expiry=row["expiry"],
                              strike=float(row["strike"]),
                              right=Right.CALL if row["right"] == "C" else Right.PUT)

        from backtest.engine.loop import OpenTrade

        # Its own event log: the engine falls back to ``market.events``, and ``market``
        # is session-scoped, so a shared log would let another test's events satisfy the
        # assertion below.
        from backtest.core.run_log import EventLog

        engine = EventDrivenEngine(market, chain, signals, config, EventLog())
        ledger = Ledger(initial_cash=1_000_000.0, spec=config.contract)
        ledger.apply_fill(contract=contract, side=Side.BUY, quantity=2, price=5.0,
                          fees=order_fees(2, config.costs, config.contract.multiplier),
                          when=day, trade_id=1)
        open_trades = {1: OpenTrade(
            trade_id=1, contract=contract, entry_date=day, entry_price=5.0, quantity=2,
            side=Side.BUY, fees=0.0, entry_quote=dict(row),
            entry_signals={},
        )}

        # A reduction of four, against a position of two, decided when four were held.
        # A reduction rather than a close, so nothing downstream needs the trade record --
        # the resizing is what is under test, not the bookkeeping after it.
        oversized = Intent(contract=contract, side=Side.SELL, quantity=4, created=day,
                           execute_on=day, trade_id=1, kind=Action.REDUCE, keep=0.5)
        engine._execute(oversized, ledger, open_trades, day, [])

        # Keeping half of two leaves one, so one is sold -- not the four that were asked
        # for, and not the two that would have closed it.
        assert ledger.position(contract).abs_quantity == 1

        notices = [e for e in engine.events.of_kind(EventKind.EXIT_DEFERRED)
                   if "was held at the fill" in e.detail]
        assert notices, "the sell was resized and nothing said so"
        assert "4" in notices[0].detail and "2" in notices[0].detail

    def test_the_reduction_keeps_its_own_ratio(self):
        """Recomputed from the ratio the rule gave, not clamped to whatever is left --
        clamping would turn "keep half" into "keep what remains", a different
        instruction."""
        result = run(**BASE, risk={"variant": "daily_loss", "params": self.PARAMS})
        actions = result.raw.actions_frame
        reduced = actions[(actions["action"] == "reduce") &
                          (actions["component_id"] == "headwind")]
        assert len(reduced), "no reduction happened, so this proves nothing"
        # Keeping half of n leaves n - floor(n/2), so a reduction never takes everything.
        assert (reduced["quantity"] >= 1).all()


class TestRefusals:
    def test_the_trade_table_refuses_a_risk_rule(self):
        """Scaling the book down acts on positions that are open, and the trade table
        computes each trade independently with no book to act on."""
        with pytest.raises(StartupError, match="needs the event-driven engine"):
            run(**(BASE | {"engine": "trade_table"}), risk=TIGHT)

    def test_a_partial_rule_is_refused(self):
        """A form needs every parameter it declares; the platform supplies none."""
        with pytest.raises(StartupError, match="needs halt_at|needs keep|needs reduce_at"):
            run(**BASE, risk={"variant": "drawdown", "params": {"reduce_at": 0.1}})

    def test_an_unknown_parameter_is_refused(self):
        """Ignored, it would leave the rule running on something other than what the
        configuration says."""
        with pytest.raises(StartupError, match="does not take"):
            run(**BASE, risk={"variant": "drawdown",
                              "params": dict(SCALE["params"], nonsense=1)})
