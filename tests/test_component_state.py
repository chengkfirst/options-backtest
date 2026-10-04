"""`get_state` / `set_state`, and the first form that needed them — requirement 0.2.state.

## Why they were absent until now

They were deliberately not built while nothing kept state, and the reason is recorded in
`core/component.py`: an empty pair of methods reads as built and does nothing. A variant
declaring itself `stateful` was refused, naming what was missing.

The trailing stop is what made them worth building. Its high-water mark is a fact about
the trade's **history**, and no amount of context about today reconstructs it.

## The failure the protocol has to prevent

A stateful form whose state is per *component* instead of per *trade*: two open trades
under one trailing stop would share a high-water mark, so the second trade's stop would
move because the first one's did. Nothing about the result would look wrong — the trades
would exit at plausible levels for a strategy nobody wrote.
"""

from __future__ import annotations

import pytest

from backtest.core.component import Param, Variant, get, register
from backtest.core.errors import InvariantError, StartupError

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "0")


def _bound(fraction: float = 0.5):
    return get("stop_loss", "trailing").bind({"fraction": fraction})


def _ctx(trade_id: int, ratio: float) -> dict:
    return {"trade_id": trade_id, "premium_ratio": ratio}


class TestTheTrailingStop:
    """A different rule from a fixed stop, not a better one."""

    def test_it_fires_on_a_fall_from_the_best_and_not_from_entry(self):
        """The case that separates the two rules, with the arithmetic.

        A trade that doubled and then halved: +100% to +49% is a give-back of 0.51 of the
        entry premium, so a 0.5 trailing stop fires. A 0.5 **fixed** stop does not — the
        premium is still above what was paid.
        """
        stop = _bound(0.5)
        assert not stop(_ctx(1, 0.0))
        assert not stop(_ctx(1, 1.00))
        assert not stop(_ctx(1, 0.80))          # given back 0.20
        assert stop(_ctx(1, 0.49))              # given back 0.51

        fixed = get("stop_loss", "premium").bind({"fraction": 0.5})
        assert not fixed({"premium_ratio": 0.49}), (
            "the fixed stop fires on the same path, so this test is not separating the "
            "two rules"
        )

    def test_the_mark_starts_at_entry_and_not_at_zero(self):
        """A mark starting at zero would let the premium halve before it caught up,
        making the rule fixed for the first part of the trade and trailing afterwards —
        two rules wearing one name."""
        stop = _bound(0.5)
        stop(_ctx(1, 0.0))
        assert stop.state_for(1, _ctx(1, 0.0))["high_water"] == 0.0
        # Straight down from entry: a 0.5 give-back from a mark of 0 is -0.5.
        assert not stop(_ctx(1, -0.49))
        assert stop(_ctx(1, -0.50))

    def test_the_mark_never_falls(self):
        stop = _bound(1.5)
        for ratio in (0.0, 0.9, 0.3, 0.7):
            stop(_ctx(1, ratio))
        assert stop.state_for(1, _ctx(1, 0.7))["high_water"] == pytest.approx(0.9)

    def test_a_missing_quote_does_not_move_the_mark_or_fire(self):
        """Treating a gap as a fall exits on missing data, which is the commonest way a
        trailing stop in a backtest beats one in an account."""
        stop = _bound(0.5)
        stop(_ctx(1, 1.0))
        assert not stop(_ctx(1, float("nan")))
        assert not stop({"trade_id": 1, "premium_ratio": None})
        assert stop.state_for(1, _ctx(1, 1.0))["high_water"] == 1.0


@kind("guard")
class TestTheStateIsPerTrade:
    """The failure that produces a plausible result for a strategy nobody wrote."""

    def test_two_trades_keep_two_marks(self):
        stop = _bound(0.5)
        stop(_ctx(1, 1.00))      # trade 1 doubles
        stop(_ctx(2, 0.10))      # trade 2 barely moves
        assert stop.state_for(1, _ctx(1, 0))["high_water"] == 1.00
        assert stop.state_for(2, _ctx(2, 0))["high_water"] == 0.10

    def test_one_trades_peak_does_not_fire_anothers_stop(self):
        """Shared state would exit trade 2 here, at a level nothing about trade 2
        reached."""
        stop = _bound(0.5)
        stop(_ctx(1, 1.00))
        assert not stop(_ctx(2, 0.10)), (
            "trade 2 was stopped out on a give-back from trade 1's high-water mark"
        )

    def test_a_stateful_form_called_without_a_trade_id_raises(self):
        with pytest.raises(InvariantError) as caught:
            _bound()({"premium_ratio": 0.5})
        assert "no trade_id" in str(caught.value)

    def test_a_closed_trade_is_forgotten(self):
        """Without this a long run keeps one entry per trade for ever, a snapshot grows
        without bound, and a trade_id reused later inherits a closed trade's mark."""
        stop = _bound(0.5)
        stop(_ctx(1, 1.00))
        stop.forget(1)
        assert "1" not in stop.get_state()
        # And the next trade with that id starts fresh rather than at 1.00.
        assert not stop(_ctx(1, 0.49))


class TestTheSnapshot:
    def test_what_get_state_returns_set_state_restores(self):
        first = _bound(0.5)
        first(_ctx(1, 1.00))
        first(_ctx(2, 0.40))

        second = _bound(0.5)
        second.set_state(first.get_state())
        assert second.get_state() == first.get_state()
        # Restored mid-trade: the mark is 1.00, so this fires where a fresh one would not.
        assert second(_ctx(1, 0.49))

    def test_the_keys_are_strings_so_a_snapshot_survives_json(self):
        """A dict keyed by int comes back from JSON keyed by string. A resumed run would
        then find no state for any trade and start every stop again from its entry —
        which looks like a run, and is a different one."""
        import json

        stop = _bound(0.5)
        stop(_ctx(7, 1.00))
        restored = _bound(0.5)
        restored.set_state(json.loads(json.dumps(stop.get_state())))
        assert restored.state_for(7, _ctx(7, 0))["high_water"] == 1.00

    def test_set_state_replaces_rather_than_merges(self):
        """A merge would let a resumed run carry state from two runs at once, and nothing
        downstream could tell."""
        stop = _bound(0.5)
        stop(_ctx(1, 1.00))
        stop.set_state({"2": {"high_water": 0.25}})
        assert set(stop.get_state()) == {"2"}

    def test_the_snapshot_is_a_copy(self):
        stop = _bound(0.5)
        stop(_ctx(1, 1.00))
        snapshot = stop.get_state()
        snapshot["1"]["high_water"] = 99.0
        assert stop.state_for(1, _ctx(1, 0))["high_water"] == 1.00


@kind("guard")
class TestWhatTheRegistryRefuses:
    def test_a_stateful_form_with_no_initial_state_is_refused(self):
        """The state a form starts a trade with is part of the form — a trailing stop
        starts at the entry price, not at zero — so a platform-chosen default would be
        the platform deciding a research parameter."""
        with pytest.raises(StartupError) as caught:
            register(Variant(slot="stop_loss", name="_stateless_claim", version=1,
                             describes="x", params=(), decide=lambda ctx: False,
                             stateful=True))
        assert "initial_state" in str(caught.value)

    def test_an_initial_state_without_the_flag_is_refused(self):
        """The flag is what the engine reads to decide whether to keep and snapshot state
        at all, so a form with state and no flag would have its state discarded between
        days with nothing saying so."""
        with pytest.raises(StartupError) as caught:
            register(Variant(slot="stop_loss", name="_unflagged", version=1,
                             describes="x", params=(), decide=lambda ctx: False,
                             initial_state=lambda ctx: {}))
        assert "declare itself stateful" in str(caught.value)


@kind("guard")
class TestTheEngineUsesIt:
    """The unit tests prove the protocol works when called. These prove the engine calls
    it -- which is the half that rots, because a stateful form whose state is dropped
    between days quietly becomes a fixed stop and produces an ordinary-looking result.
    """

    RUN = dict(
        symbol="QQQ", start="2021-01-01", end="2023-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 10, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    @staticmethod
    def _with(variant: str):
        from backtest.api.sdk import run

        return run(**(TestTheEngineUsesIt.RUN | {
            "components": {"stop_loss": {"variant": variant,
                                          "params": {"fraction": 0.5}}}}))

    @pytest.mark.req("0.2.state")
    def test_the_mark_survives_between_days_in_a_real_run(self):
        """**What is asserted is the mechanism, not the outcome.**

        A trailing stop whose state was dropped between days would behave exactly like a
        fixed stop at the same fraction, so the thing to check is that the two forms are
        *not* the same -- and that the trailing one fires at least as often, which follows
        from the rule rather than from this data: the give-back from a mark that never
        falls is never smaller than the fall from entry.

        An earlier version of this pinned the two counts at 70 and 120. Those are figures
        about what QQQ did in 2021-2023, and a test is the wrong place to record a market
        outcome: it would go red on a data revision, and it reads as a finding when the
        research behind it has not been done.
        """
        fixed = self._with("premium").trades
        trailing = self._with("trailing").trades

        fixed_stops = int((fixed["exit_reason"] == "stop_loss").sum())
        trailing_stops = int((trailing["exit_reason"] == "stop_loss").sum())
        assert fixed_stops > 0, "the fixed stop never fired, so there is nothing to compare"
        assert trailing_stops > fixed_stops, (
            f"the trailing stop fired {trailing_stops} times against the fixed stop's "
            f"{fixed_stops}. A give-back measured from a mark that never falls is never "
            f"smaller than a fall measured from entry, so this cannot be an artefact of "
            f"the data -- the high-water mark is not surviving between days, which makes "
            f"the trailing stop a fixed one"
        )

    @pytest.mark.req("0.5.hash")
    def test_the_two_forms_do_not_share_a_run_id(self):
        assert (self._with("premium").manifest()["run_id"]
                != self._with("trailing").manifest()["run_id"])

    def test_no_state_is_left_for_a_closed_trade(self):
        """Every trade in this run closed, so nothing should still be held. A run that
        kept them all would snapshot a mapping that grows with the trade count."""
        result = self._with("trailing")
        stop = result.raw.strategy.components["stop_loss"]
        closed = {r.trade_id for r in result.raw.records}
        held = {int(k) for k in stop.get_state()}
        assert not (held & closed), (
            f"state is still held for closed trades {sorted(held & closed)[:5]}"
        )
