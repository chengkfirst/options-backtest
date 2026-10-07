"""SET_STOP: a stop that moves, as a logged action rather than private state.

## The recorded reason for its absence was wrong

`BUILT` said SET_STOP could not be built because there was "nowhere to record a stop that
moves during the trade". The stateful component protocol is exactly that place, and
`stop_loss/trailing` has been using it since it was written — a per-trade high-water mark
that updates every session.

What was actually missing is smaller and different: a stop move that is **logged and
attributable**. A component keeping its level in its own state moves the stop invisibly;
nothing in the decision log says the stop moved, when, or which slot asked. "Up 30%, stop
to breakeven" — the commonest real stop rule there is — could not be written as a rule a
reader could audit afterwards.

## What this buys that `trailing` does not

A trailing stop moves every session and gives back a fixed fraction of the best. A ratchet
moves once, to a level the researcher chose, and then stops. On a trade that runs up and
then chops sideways the first exits and the second does not. Which is right is the
researcher's question; having both is what makes it askable.
"""

from __future__ import annotations

import pytest

from tests.kinds import kind, labels

pytestmark = labels("unit", "2")

RUN = dict(
    symbol="QQQ", start="2022-06-01", end="2023-06-30",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


def _with(variant: str, **params) -> dict:
    return {"components": {"stop_loss": {"slot": "stop_loss", "variant": variant,
                                          "params": params}}}


class TestTheActionIsBuilt:
    def test_set_stop_is_no_longer_refused(self):
        from backtest.core.actions import Action, BUILT, require_built

        assert BUILT[Action.SET_STOP] == ""
        require_built(Action.SET_STOP)

    def test_the_four_that_remain_unbuilt_still_say_why(self):
        """ROLL, HEDGE_ADD and HEDGE_REMOVE share one structural blocker — a trade
        keyed to one contract — and it is not a data problem. Said here so the count
        does not drift without the reasons drifting with it."""
        from backtest.core.actions import Action, BUILT

        unbuilt = {a for a, why in BUILT.items() if why}
        assert unbuilt == {Action.ROLL, Action.HEDGE_ADD, Action.HEDGE_REMOVE}
        for action in unbuilt:
            assert len(BUILT[action]) > 50, f"{action.value} gives no reason"


class TestTheRatchetMovesTheStop:
    @kind("limit")
    def test_the_move_is_logged_with_the_slot_and_the_reason(self):
        from backtest.api.sdk import run

        result = run(**(RUN | _with("ratchet", fraction=0.5, trigger=0.3, to=0.0)))
        actions = result.raw.actions_frame
        moves = actions[actions["action"] == "set_stop"]
        assert len(moves) > 3, f"only {len(moves)} stop moves; the trigger may be unreachable"
        assert set(moves["component_id"]) == {"stop_loss"}
        assert (moves["level"] == 0.0).all()
        assert moves["reason"].str.contains("trigger").all(), (
            "a logged stop move whose reason does not say what triggered it is not "
            "auditable, which is the whole point of it being an action"
        )

    @kind("limit")
    def test_it_changes_the_result_against_the_same_fixed_stop(self):
        """A stop that moves to breakeven exits trades a 50% fixed stop holds."""
        from backtest.api.sdk import run

        fixed = run(**(RUN | _with("premium", fraction=0.5)))
        ratchet = run(**(RUN | _with("ratchet", fraction=0.5, trigger=0.3, to=0.0)))

        fixed_stops = (fixed.trades["exit_reason"] == "stop_loss").sum()
        ratchet_stops = (ratchet.trades["exit_reason"] == "stop_loss").sum()
        assert ratchet_stops > fixed_stops, (
            f"the ratchet stopped out {ratchet_stops} trades against the fixed stop's "
            f"{fixed_stops}; moving a stop to breakeven cannot make it fire less often"
        )

    @kind("guard")
    def test_a_trade_whose_stop_never_moved_behaves_like_the_fixed_stop(self):
        """The mechanism must be inert where it does not fire, or every comparison
        against it measures the ratchet plus a side effect.

        Per trade rather than per run, and the first version of this was per run with
        ``trigger=5.0`` on the assumption that nothing reaches +500%. Four trades did --
        a cheap option can multiply several times over -- so the assumption was the
        test's rather than the system's. Comparing the trades that did **not** move
        states the property directly and does not depend on the data being tame.
        """
        from backtest.api.sdk import run

        ratchet = run(**(RUN | _with("ratchet", fraction=0.5, trigger=5.0, to=0.0)))
        fixed = run(**(RUN | _with("premium", fraction=0.5)))

        actions = ratchet.raw.actions_frame
        moved = set(actions.loc[actions["action"] == "set_stop", "trade_id"])
        still = ratchet.trades[~ratchet.trades["trade_id"].isin(moved)]
        assert len(still) > 5, "too few unmoved trades to compare"

        same = fixed.trades.set_index("trade_id")["exit_reason"]
        for _, trade in still.iterrows():
            assert trade["exit_reason"] == same[trade["trade_id"]], (
                f"trade {trade['trade_id']} never moved its stop and still exited "
                f"differently from the fixed stop: {trade['exit_reason']} against "
                f"{same[trade['trade_id']]}"
            )

    @kind("guard")
    def test_it_moves_once_and_not_every_session(self):
        """The difference from the trailing stop. A move per session would make it a
        trailing stop with extra steps and a misleading name."""
        from backtest.api.sdk import run

        result = run(**(RUN | _with("ratchet", fraction=0.5, trigger=0.1, to=0.0)))
        moves = result.raw.actions_frame
        moves = moves[moves["action"] == "set_stop"]
        assert moves["trade_id"].is_unique, (
            "a trade moved its stop more than once, so this is trailing rather than "
            "ratcheting"
        )


class TestWhatTheSetterRefuses:
    def test_a_negative_level_is_refused(self):
        """A stop level is a loss fraction. -0.3 would read as "exit when the trade is
        30% up", which is a take-profit wearing the wrong name."""
        from datetime import date

        from backtest.core.errors import InvariantError
        from backtest.engine.loop import EventDrivenEngine, OpenTrade

        setter = EventDrivenEngine._stop_setter(
            object.__new__(EventDrivenEngine),
            type("T", (), {"trade_id": 1, "stop_level": None, "stop_moves": []})(),
            date(2024, 1, 2), [])
        with pytest.raises(InvariantError, match="not negative"):
            setter(-0.3)

    def test_a_nan_level_is_refused(self):
        from datetime import date

        from backtest.core.errors import InvariantError
        from backtest.engine.loop import EventDrivenEngine

        setter = EventDrivenEngine._stop_setter(
            object.__new__(EventDrivenEngine),
            type("T", (), {"trade_id": 1, "stop_level": None, "stop_moves": []})(),
            date(2024, 1, 2), [])
        with pytest.raises(InvariantError, match="finite"):
            setter(float("nan"))


class TestTheContextOffersIt:
    def test_a_variant_that_wants_it_can_tell_it_is_absent(self):
        """None rather than a no-op, so a variant built on it refuses under an engine
        that does not supply it instead of silently never moving anything."""
        from backtest.signals.exit_context import exit_context

        ctx = exit_context(premium_ratio=0.0, underlying_move=0.0, days_held=1,
                           days_to_expiry=30)
        assert ctx["set_stop"] is None
        assert ctx["stop_level"] is None

    @kind("limit")
    def test_the_level_in_force_is_readable(self):
        """A slot ratcheting a stop needs to know what it is ratcheting from.

        Observed from the **take-profit** slot, because a component in the stop slot and
        a stop on the rule cannot both be given -- the engine refuses that, and rightly.
        The context is the same object both slots are handed, which is the pass-through
        channel the figure describes.
        """
        from backtest.api.sdk import run
        from backtest.core.component import Param, Variant, register, variants

        seen: list = []
        if "reads_stop" not in {v.name for v in variants("take_profit")}:
            register(Variant(
                slot="take_profit", name="reads_stop", version=1,
                describes="records the stop level it was shown; never exits",
                params=(Param("x", "float", "unused", lo=0.0, hi=1.0),),
                decide=lambda ctx, *, x: bool(seen.append(ctx.get("stop_level"))),
            ))
        run(**(RUN | {"exit": {"hold_days": 20, "dte_floor": 7, "stop_loss": 0.4},
                       "components": {"take_profit": {"slot": "take_profit",
                                                       "variant": "reads_stop",
                                                       "params": {"x": 0.5}}}}))
        assert seen and all(level == 0.4 for level in seen), (
            f"the rule's own stop is 0.4 and the slot was shown {set(seen)}"
        )
