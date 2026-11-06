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

## The two failures it did not prevent, and now does

Per trade was not enough, because a trade ends and a run ends and the state outlived both.
Two more invariants, each with its own class below and each measured rather than imagined:

- **no state is held for any trade that has a record** — `forget` was called on one of the
  three paths that close a trade, so expiry and early exercise left theirs behind
- **a run starts carrying nothing** — trade ids restart at 1, so a second run of the same
  strategy object read the first run's leftovers as its own, and the `run_id` was the same
  either way

And the mark those invariants are about has to start where the rule says: at the **entry**.
It started at the fill day's exit-side price, below the entry by the round-trip cost, and
the test that pinned the rule fed in 0.0 by hand and so could not see it.
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

    def test_the_mark_starts_at_entry_whatever_the_first_price_seen_is(self):
        """A mark starting below the entry makes the stop looser than its own rule.

        **The first ratio the form is shown is not the entry**, and feeding it 0.0 is
        exactly how that went unnoticed. `initial_state` is called lazily, on the form's
        first call for a trade, and under the one-day execution lag that call happens on
        the fill day with an **exit-side** ratio -- below zero by the round-trip cost, on
        every trade. So the value passed in here is the one the engine passes in, not the
        one the entry would imply.

        A mark starting at the first sellable price is a defensible rule; it is not this
        form's rule. Give-back is measured from the entry, so the mark starts there and
        the trailing stop can never fire later than a fixed stop at the same fraction.
        """
        stop = _bound(0.5)
        fill_day = -0.0198          # the round-trip cost on the fixture, measured
        stop(_ctx(1, fill_day))
        assert stop.state_for(1, _ctx(1, fill_day))["high_water"] == 0.0, (
            "the mark started at the first price the trade could be sold at rather than "
            "at the entry, so the stop is looser than its stated rule by the round-trip "
            "cost"
        )
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
    def _with(variant: str, **over):
        from backtest.api.sdk import run

        return run(**(TestTheEngineUsesIt.RUN | {
            "components": {"stop_loss": {"variant": variant,
                                          "params": {"fraction": 0.5}}}} | over))

    @pytest.mark.req("0.2.state")
    def test_the_mark_a_real_run_starts_a_trade_with_is_the_entry(self):
        """**Driven through a run, and read off the engine's own snapshot.**

        The unit test above hands the form a ratio, and for as long as the value it
        handed in was 0.0 it could not see this: `initial_state` is called lazily, on the
        form's first call for a trade, and under the one-day execution lag that call
        happens on the **fill day** with an exit-side ratio. The engine was therefore
        starting every mark at minus the round-trip cost -- measured at -0.0198 on 33 of
        33 trades -- so the stop was looser than its stated rule by that much, and the
        trade log showed an ordinary exit on an ordinary date.

        Two readings, both the engine's: the marks it holds at a checkpoint taken on the
        first fill day, where nothing has had a chance to rise yet, and the marks it is
        still holding at the end of a whole run.
        """
        full = self._with("trailing")
        first_fill = min(r.entry_date for r in full.raw.records)
        at_the_fill = self._with("trailing", until=str(first_fill))
        marks = at_the_fill.raw.checkpoint.component_state["stop_loss"]
        assert marks, (
            f"the checkpoint on {first_fill} holds no state, so the stop was not asked "
            f"about a trade that had just been filled and this test reads nothing"
        )
        for trade, held in marks.items():
            assert held["high_water"] == 0.0, (
                f"trade {trade} was filled on {first_fill} and its high-water mark "
                f"starts at {held['high_water']:.6f}, below the entry. The give-back is "
                f"measured from the entry, so the mark starts at 0.0 -- what the engine "
                f"showed the form on the fill day is the exit side of the quote, which "
                f"is below the entry by the round-trip cost"
            )

        leftover = full.raw.strategy.components["stop_loss"].get_state()
        below = {t: h["high_water"] for t, h in leftover.items()
                 if h["high_water"] < 0.0}
        assert not below, (
            f"these trades are still open with a mark below the entry: {below}. A mark "
            f"that starts at the entry and never falls cannot be below it"
        )

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
        kept them all would snapshot a mapping that grows with the trade count.

        **Every trade here closes by its stop or its holding cap**, which is one of the
        three ways a trade ends, so this passed while the other two were broken. The
        generalisation is `TestNoStateOutlivesTheTradeItBelongsTo` below.
        """
        result = self._with("trailing")
        stop = result.raw.strategy.components["stop_loss"]
        closed = {r.trade_id for r in result.raw.records}
        held = {int(k) for k in stop.get_state()}
        assert not (held & closed), (
            f"state is still held for closed trades {sorted(held & closed)[:5]}"
        )


@kind("guard")
class TestNoStateOutlivesTheTradeItBelongsTo:
    """The invariant: **no state is held for any trade that has a record.**

    `forget` used to be called from one of the three paths that close a trade -- the exit
    intent. Passive expiry and early exercise each popped the trade and returned a record
    with the component's state left in place. Two consequences, neither of which anything
    in a result shows: a checkpoint's state grows with the expiry count rather than with
    the open-trade count, and a trade id reused by a later run inherits a closed trade's
    high-water mark.

    One run, chosen because it reaches **all three** paths. `test_no_state_is_left_for_a_
    closed_trade` above closes every trade through the exit intent, so it covered one of
    the three and passed while two were broken -- which is why that is a weaker test of
    the same thing rather than the same test twice.
    """

    #: Puts, no holding cap, and the days-to-expiry floor at the lowest the execution lag
    #: allows -- so contracts reach their expiry (`_settle`) and deep in-the-money puts
    #: are worth exercising early (`_early_exercise`). The wide stop is what leaves room
    #: for both: at 0.5 almost everything closes by the stop instead.
    RUN = dict(
        symbol="QQQ", start="2022-01-01", end="2023-12-31",
        entry="close > sma_20 * 1.02",
        select={"right": "put", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"dte_floor": 2},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
        components={"stop_loss": {"variant": "trailing", "params": {"fraction": 0.9}}},
    )

    @pytest.fixture(scope="class")
    def result(self):
        from backtest.api.sdk import run

        return run(**self.RUN)

    def test_the_run_reaches_every_way_a_trade_can_close(self, result):
        """Asserted out loud, because a guard that quietly stops covering two of the
        three paths is the failure this class exists to prevent."""
        reasons = {r.exit_reason.value for r in result.raw.records}
        for path, how in (("stop_loss", "the exit intent"),
                          ("expiry_otm", "passive expiry, `_settle`"),
                          ("early_exercise", "early exercise, `_early_exercise`")):
            assert path in reasons, (
                f"no trade in this run closed by {path} ({how}), so the test below "
                f"cannot see state left behind on that path. Found {sorted(reasons)}"
            )

    @pytest.mark.req("0.2.state")
    def test_no_state_is_held_for_any_trade_that_has_a_record(self, result):
        stop = result.raw.strategy.components["stop_loss"]
        closed = {r.trade_id for r in result.raw.records}
        held = {int(k) for k in stop.get_state()}
        leaked = sorted(held & closed)
        assert not leaked, (
            f"{len(leaked)} trades have a record and still hold state: {leaked[:8]}. "
            f"A record means the trade is over, so its components should have forgotten "
            f"it -- and a trade id reused by a later run inherits what is left"
        )


@kind("guard")
class TestStateDoesNotCrossRuns:
    """Trade ids restart at 1 in every run, so whatever is left over is read as this
    run's.

    Measured before this was fixed: `research.positioning.compare` run with the
    **identical** sizing method three times returned 27 / 29 / 27 trades, reproducibly,
    and the `run_id` was the same for all three -- so nothing in the output distinguished
    a first run from a later one. The exposed callers are the tools that re-run one
    strategy object: `positioning.compare`, `sdk.delay_test` and `sdk.realism_test`,
    which is to say the look-ahead and realism tests themselves.

    A trade still open when the range ends is deliberately left unclosed, so `forget`
    cannot be the whole answer: a run also has to start with nothing inherited.
    """

    #: The range ends with a trade open, which is what leaves state behind for a second
    #: run to find under an id it is about to reuse. Every trade here closes through the
    #: exit intent, so `forget` covers all of them -- what this configuration isolates is
    #: the other half: the leftover of a trade the range never closed.
    RUN = dict(
        symbol="QQQ", start="2022-01-01", end="2023-12-31",
        entry="close > sma_20 * 1.02",
        select={"right": "put", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 10, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
        components={"stop_loss": {"variant": "trailing", "params": {"fraction": 0.5}}},
    )

    @staticmethod
    def _rows(records) -> list[tuple]:
        """Everything about a trade a difference could hide in."""
        return [(r.trade_id, r.entry_date, r.exit_date, str(r.contract),
                 r.exit_reason.value, round(r.net_pnl, 8)) for r in records]

    @staticmethod
    def _again(result):
        """The same strategy object, through a fresh engine over the same range."""
        from backtest.engine.loop import EventDrivenEngine

        return EventDrivenEngine(result._market, result._chain, result._signals,
                                 result.raw.config).run(result.raw.strategy)

    @pytest.fixture(scope="class")
    def first(self):
        from backtest.api.sdk import run

        return run(**self.RUN)

    @pytest.mark.req("0.4.reproducible")
    def test_the_same_strategy_object_run_twice_gives_the_same_trades(self, first):
        stop = first.raw.strategy.components["stop_loss"]
        assert stop.get_state(), (
            "the first run left no state behind, so a second run has nothing to inherit "
            "and this test cannot see the leak. Choose a range that ends with a trade "
            "open"
        )
        second = self._again(first)
        expected, got = self._rows(first.raw.records), self._rows(second.records)
        assert got == expected, (
            f"the second run of one strategy object produced {len(got)} trades against "
            f"the first run's {len(expected)}.\n"
            f"  only in the first run:  {[r for r in expected if r not in got][:3]}\n"
            f"  only in the second run: {[r for r in got if r not in expected][:3]}\n"
            f"The run_id is the same either way, so nothing in the output says which "
            f"figures came from the contaminated run"
        )

    @pytest.mark.req("0.4.reproducible")
    def test_a_run_does_not_inherit_state_set_before_it(self, first):
        """The same leak, driven rather than waited for.

        The test above only bites when the first run happens to leave behind a mark that
        changes an outcome. This one seeds a mark that cannot not change one: a
        high-water of 5.0 on the first trade fires a 0.5 give-back on that trade's first
        session, whatever the data does.
        """
        from backtest.api.sdk import run

        seeded = run(**self.RUN)
        trade = first.raw.records[0].trade_id
        seeded.raw.strategy.components["stop_loss"].set_state(
            {str(trade): {"high_water": 5.0}})
        again = self._again(seeded)
        assert self._rows(again.records) == self._rows(first.raw.records), (
            f"a run inherited state that was set on its components before it started. "
            f"Trade {trade} was stopped out on a give-back from a mark no trade in this "
            f"run ever reached"
        )
