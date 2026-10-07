"""The conflict policy, applied by the engine rather than fixed in the platform.

The document's "Override" influence channel: "two slots issue conflicting actions in the
same period", with the mechanism given as **conflict policy in the lifecycle config**. The
policy existed and was a constant in `core/types.py`, so 6.3's research tool -- "compare
Open-state run orders and conflict policies" -- had nothing to compare.

## Why it takes two components to test

With the built-in rules the conflict **cannot happen**. Both test the same daily premium
ratio, one for `>= target` and one for `<= -stop`, and one number cannot satisfy both. So
a test on real trades alone would pass under either policy and prove nothing -- which is
exactly the trap the attribution sign test fell into.

Two always-firing component variants force the case, which is also the real one: a
`stop_loss/underlying` and a `take_profit/premium` read different inputs, and an IV spike
moves them in opposite directions on one session.
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


def _always_fire() -> dict:
    """Register an always-firing variant in both Open-state exit slots."""
    from backtest.core.component import Param, Variant, register, variants

    for slot in ("take_profit", "stop_loss"):
        if "always" not in {v.name for v in variants(slot)}:
            register(Variant(
                slot=slot, name="always", version=1,
                describes="fires every session; exists so the conflict policy has a "
                          "conflict to resolve",
                params=(Param("x", "float", "unused", lo=0.0, hi=1.0),),
                decide=lambda ctx, *, x: True,
            ))
    return {s: {"slot": s, "variant": "always", "params": {"x": 0.5}}
            for s in ("take_profit", "stop_loss")}


class TestTheEngineAppliesIt:
    @kind("limit")
    def test_the_recorded_exit_follows_the_policy(self):
        from backtest.api.sdk import run

        components = _always_fire()
        stop_first = run(**(RUN | {"components": components}))
        tp_first = run(**(RUN | {"components": components,
                                  "lifecycle": {"conflict_policy": "take_profit_first"}}))

        assert len(stop_first.trades) > 10, "too few trades to be worth comparing"
        assert set(stop_first.trades["exit_reason"]) == {"stop_loss"}
        assert set(tp_first.trades["exit_reason"]) == {"take_profit"}
        assert len(stop_first.trades) == len(tp_first.trades), (
            "the policy decides which reason is **recorded**, not whether the trade "
            "closes; a different trade count means it is changing more than it should"
        )

    @kind("guard")
    def test_the_losing_reason_is_kept_as_suppressed(self):
        """Both policies record the other one, so the precedence stays visible."""
        from backtest.api.sdk import run

        result = run(**(RUN | {"components": _always_fire()}))
        suppressed = result.trades["suppressed_reasons"].astype(str)
        assert (suppressed.str.contains("take_profit")).all()

    @kind("guard")
    def test_two_policies_do_not_share_a_run_id(self):
        from backtest.api.sdk import run

        components = _always_fire()
        a = run(**(RUN | {"components": components})).manifest()["run_id"]
        b = run(**(RUN | {"components": components,
                           "lifecycle": {"conflict_policy": "take_profit_first"}})
                ).manifest()["run_id"]
        assert a != b

    @kind("guard")
    def test_the_policy_survives_the_per_session_reset(self):
        """**The bug this test exists for.** The policy was first stored in the method
        that resets per-session state, which runs *after* `run()` reads it from the
        strategy -- so every run silently used the default and the setting looked inert.
        """
        import inspect

        from backtest.engine.loop import EventDrivenEngine

        source = inspect.getsource(EventDrivenEngine)
        assert source.count("self._conflict_policy = ") == 2, (
            "the policy is assigned somewhere other than the constructor default and "
            "run(); a third assignment is most likely a reset that undoes run()"
        )


class TestWithTheBuiltInRulesItCannotBind:
    @kind("limit")
    def test_a_stop_and_a_target_never_fire_on_the_same_session(self):
        """Stated as a test rather than left to be discovered, because a sweep over the
        policy on built-in rules reports no difference and reads as "it does not
        matter"."""
        from backtest.api.sdk import run

        result = run(**(RUN | {"exit": {"hold_days": 20, "dte_floor": 7,
                                         "take_profit": 0.02, "stop_loss": 0.02}}))
        both = result.trades["suppressed_reasons"].astype(str).str.contains(
            "take_profit") & (result.trades["exit_reason"] == "stop_loss")
        assert not both.any(), (
            "a built-in stop and target fired on one session, which the single daily "
            "premium ratio should make impossible. If this is now reachable the "
            "lifecycle note about it is out of date"
        )
