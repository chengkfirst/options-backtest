"""The second baseline configuration — requirement 0.4.regression.

## The blindness this answers to

`tools/baseline.py` builds its trades with `TradeTableEngine`, and
`backtest/engine/vectorized.py:108-116` **refuses a strategy with components outright**.
So the recorded configuration has never had a component, and **no change to any component
can ever move it.** Both engine defects fixed on `fix/engine-state` were component
changes: the baseline was checked row for row across three states and came back identical
in all of them, 230 trades and net -11,822.29 — and that agreement carried no information.
See `docs/findings/baseline-change-2026-11-06.md`.

The one guard whose stated job is "an unintended change in results" was structurally
unable to see the class of change this project exists to make.

## What is asserted here, and in what order

1. **The configuration is the kind of configuration that can see it.** Event-driven
   engine, a stateful component swapped into a slot. Asserted directly rather than
   inferred from the trade log, because a configuration that quietly lost its component
   would still produce a stable file and still pass the comparison.
2. **The component actually fires.** A trailing stop that never triggers gives a baseline
   as blind as the one it was added to fix — stable, and stable for a reason that has
   nothing to do with the component.
3. **The recorded file moves when the component's rule changes.** Demonstrated, not
   asserted: the same configuration is re-run with the mark started where
   `stop_loss/trailing` **version 1** started it, and the rows are required to differ.
   That is one of the two defects from `fix/engine-state`, replayed.
4. **The trade-table configuration cannot be made to see any of this.** A `limit` test,
   so that when the gap closes the test fails and says to delete it.
5. **The file has not moved.** The baseline comparison itself.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pytest

from backtest.core.component import Variant, get
from backtest.core.errors import StartupError
from backtest.engine.vectorized import TradeTableEngine
from backtest.signals.stop_loss import TRAILING

from tests.baseline_configurations import (
    EVENT_DRIVEN_FILE,
    TRAILING_FRACTION,
    build_event_driven_trades,
    build_with_stop,
    compare,
    event_driven_inputs,
    event_driven_strategy,
    read_recorded,
)
from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``. The module's kind
#: is ``baseline`` -- a failure says the result moved, without saying what kind of thing
#: changed -- and the classes that check the configuration's *shape* override it, because
#: those are defences going dark rather than results moving.
pytestmark = labels("baseline", "cross")

#: The least share of closes the component may account for before this configuration
#: stops being a test of the component. Not a cut the engine applies to anything -- it is
#: a property of this fixture configuration, asserted so that a later change to the bars,
#: the selection or the fraction cannot quietly leave the stop never firing.
#:
#: A fifth is well below what was measured (see ``TRAILING_FRACTION``), so the assertion
#: has room to be true without being vacuous.
LEAST_SHARE_CLOSED_BY_THE_COMPONENT = 0.20


def _baseline_stop():
    """The stop the recorded configuration uses, built the way the builder builds it."""
    return get("stop_loss", "trailing").bind({"fraction": TRAILING_FRACTION})


@kind("guard")
class TestTheConfigurationCanSeeAComponentChange:
    """The shape of the configuration, checked without running it.

    These are the preconditions for the comparison below to mean anything. Each one, if
    it broke, would leave a stable file and a passing guard.
    """

    @pytest.mark.req("0.4.regression")
    def test_a_component_is_swapped_into_a_slot(self):
        strategy = event_driven_strategy(_baseline_stop())
        assert strategy.components, (
            "the second baseline configuration has no component, which is the whole "
            "reason it exists. A componentless configuration here would duplicate the "
            "blindness it was added to close"
        )

    def test_the_component_is_stateful(self):
        """State across days is what the trade-table engine cannot express at all, and
        what both `fix/engine-state` defects were about."""
        stop = event_driven_strategy(_baseline_stop()).components["stop_loss"]
        assert stop.variant.stateful
        assert stop.variant.initial_state is not None

    def test_the_rule_is_not_also_given_on_the_exit(self):
        """`Strategy.__post_init__` refuses both, so this is really a check that the
        configuration gives the component rather than the plain stop -- if it gave the
        plain stop the run would still work and the component would be absent."""
        strategy = event_driven_strategy(_baseline_stop())
        assert strategy.exit.stop_loss is None
        assert "stop_loss" in strategy.components


@kind("limit")
class TestTheFirstConfigurationIsStructurallyBlind:
    """The gap that made a second configuration necessary, asserted as still being there.

    **When this fails, delete it** — and then say in the finding whether the second
    configuration is still needed, because its whole justification is this assertion.
    """

    @pytest.mark.req("0.4.regression")
    def test_the_trade_table_engine_refuses_this_configuration(self):
        inputs = event_driven_inputs()
        engine = TradeTableEngine(inputs.market, inputs.chain, inputs.signals,
                                  inputs.config)
        with pytest.raises(StartupError) as raised:
            engine.run(event_driven_strategy(_baseline_stop()))
        assert "stop_loss" in str(raised.value)
        assert "event_driven" in str(raised.value)


@kind("guard")
class TestTheComponentFires:
    """A baseline that is stable because nothing happens is not a baseline."""

    @pytest.mark.req("0.4.regression")
    def test_the_recorded_file_closes_a_fifth_of_its_trades_by_the_stop(self):
        recorded = read_recorded()
        closes = recorded["exit_reason"].value_counts()
        by_the_stop = int(closes.get("stop_loss", 0))
        share = by_the_stop / len(recorded)
        assert share >= LEAST_SHARE_CLOSED_BY_THE_COMPONENT, (
            f"only {by_the_stop} of {len(recorded)} recorded trades closed by the "
            f"stop ({share:.1%}), under the {LEAST_SHARE_CLOSED_BY_THE_COMPONENT:.0%} "
            f"this configuration is meant to hold. The fraction "
            f"{TRAILING_FRACTION} may need rechoosing: a trailing stop that rarely "
            f"fires makes this file as blind as the one it was added to fix.\n"
            f"closes: {closes.to_dict()}"
        )


def _version_ones_mark(ctx: Mapping[str, Any]) -> dict:
    """Version 1's `initial_state`, copied verbatim -- fallback included.

    From `git show c59fc61^:backtest/signals/stop_loss/__init__.py`, `_trailing_initial`.
    Cited so the next reader can check the replica against the thing it replays without
    doing the archaeology, and copied rather than paraphrased because an approximation of
    a defect is a third form -- see
    `test_the_replica_starts_the_mark_where_version_one_started_it` below for what
    dropping the fallback alone would do.
    """
    ratio = ctx.get("premium_ratio")
    return {"high_water": float(ratio) if ratio is not None and math.isfinite(ratio)
                          else 0.0}


@kind("guard")
class TestAChangeToTheComponentMovesTheFile:
    """The demonstration. Not "this file could move" — this file does move, under the
    exact change that moved nothing last time.

    `stop_loss/trailing` version 1 started the high-water mark at the fill day's
    **exit-side** ratio rather than at the entry, so the stop was looser than its own
    rule by the round-trip cost. Replayed here as an unregistered variant: a form built
    and bound without `register`, so the session's registry is untouched and no other
    test can see it.

    **The two run-comparing tests below are not independent, and the first is the
    load-bearing one.** Had the file been recorded with version 1's mark, the two runs
    would be identical: `moved` would be empty and
    `test_version_ones_mark_produces_different_trades` would fail -- but `later` would be
    empty too, so the directional test would pass **vacuously**. All of the protection
    against the file quietly holding version 1's answer therefore rests on the first of
    them. They are complementary the other way round -- a third form, neither version,
    could produce a difference the first test accepts and still be caught by the second,
    which says *which way* the difference must run -- so the pair is sufficient. A reader
    who counts two independent guards here would trust it more than it earns.
    """

    #: Version 1's `initial_state` -- :func:`_version_ones_mark`, verbatim -- and
    #: nothing else: the params and the decision are version 2's, so the one difference
    #: between the two runs is where the mark starts. Bound unregistered -- registering it
    #: would put a form nobody should use into the registry the interface offers.
    VERSION_ONE = Variant(
        slot="stop_loss", name="trailing-mark-at-the-fill", version=1,
        describes="stop_loss/trailing as it was at version 1: the give-back measured "
                  "from the fill day's exit-side ratio rather than from the entry. Here "
                  "only to prove the baseline file is sensitive to this difference",
        params=TRAILING.params,
        decide=TRAILING.decide,
        stateful=True,
        initial_state=_version_ones_mark,
    )

    #: What version 1 did with a `premium_ratio` that is not a number: `(the context,
    #: the mark it started at)`. The one part of version 1 a replica can get wrong while
    #: still producing a plausible run, so it is checked rather than read.
    VERSION_ONE_MARKS = (
        ({"premium_ratio": -0.0198}, -0.0198),
        ({"premium_ratio": float("nan")}, 0.0),
        ({"premium_ratio": float("-inf")}, 0.0),
        # Cannot occur today -- `premium_ratio` is a required keyword of `exit_context`
        # -- and version 1 handled it, so the replica does too. A replica that is
        # faithful only on the inputs that happen to arrive is not a replica.
        ({}, 0.0),
    )

    @pytest.mark.req("0.4.regression")
    def test_the_replica_starts_the_mark_where_version_one_started_it(self):
        """The replica has to be version 1, not an approximation of it.

        Version 1 fell back to **0.0** when the ratio was absent or non-finite. A replica
        that drops the fallback starts the mark at NaN, and then `ratio > high_water` is
        always False and `(high_water - ratio) >= fraction` is always False
        (`backtest/signals/stop_loss/__init__.py`), so **the stop never fires again for
        that trade's whole life.** Version 1 would have used 0.0 and fired normally.

        Nothing in the two tests below would notice: they only require the two runs to
        differ, and a form that stops exiting differs plenty. The demonstrated
        sensitivity figure would then be measuring a third form that never stops, and a
        reader would trust it as version 1's.

        Latent rather than live: `premium_ratio` is
        `(exit_price / entry_price - 1.0)` from a raw quote with no finiteness guard, and
        no bid is NaN on today's synthetic chain. A fixture change, a data gap or a real
        chain makes it live.
        """
        for ctx, mark in self.VERSION_ONE_MARKS:
            assert self.VERSION_ONE.initial_state(ctx) == {"high_water": mark}, (
                f"the version 1 replica starts its high-water mark at "
                f"{self.VERSION_ONE.initial_state(ctx)} for premium_ratio="
                f"{ctx.get('premium_ratio')!r}, where version 1 started it at {mark}. "
                f"See `git show c59fc61^:backtest/signals/stop_loss/__init__.py`, "
                f"`_trailing_initial`. A mark that is not a number can never be passed "
                f"and can never be given back from, so the stop would silently never "
                f"fire again -- and the two tests below would still pass, measuring a "
                f"form that is not the defect this file claims to replay"
            )

    @pytest.fixture(scope="class")
    def runs(self):
        """The recorded file and one run under version 1's mark, keyed by `trade_id`.

        Class-scoped: both tests below need the same pair, and the run takes about ten
        seconds. **Joined on `trade_id`, not on row number** -- the trade log is ordered
        by exit date, so one trade exiting earlier shifts every row after it, and the
        same twelve trades show up as twenty-three differing rows. The baseline
        comparison is positional and that is right for it (any difference is a
        difference), but a count of *what moved* has to be a count of logical trades or
        it overstates itself.
        """
        recorded = read_recorded().set_index("trade_id")
        under_version_one = build_with_stop(
            self.VERSION_ONE.bind({"fraction": TRAILING_FRACTION})
        ).set_index("trade_id")
        return recorded, under_version_one

    @pytest.mark.req("0.4.regression")
    def test_version_ones_mark_produces_different_trades(self, runs):
        recorded, under_version_one = runs
        shared = recorded.index.intersection(under_version_one.index)
        moved = [
            int(trade_id) for trade_id in shared
            if (recorded.loc[trade_id, "exit_date"],
                recorded.loc[trade_id, "exit_reason"],
                round(recorded.loc[trade_id, "net_pnl"], 8))
            != (under_version_one.loc[trade_id, "exit_date"],
                under_version_one.loc[trade_id, "exit_reason"],
                round(under_version_one.loc[trade_id, "net_pnl"], 8))
        ]
        assert moved or len(recorded) != len(under_version_one), (
            "the recorded baseline is identical under version 1 of the trailing stop's "
            "mark, so this configuration cannot see the very defect it was built to "
            "see. Either the fraction is too wide for the difference to matter, or the "
            "component is not reaching the exits at all"
        )

    @pytest.mark.req("0.4.regression")
    def test_the_recorded_file_is_the_version_two_side_of_that_difference(self, runs):
        """Which way round it moved, so the file cannot quietly be recording version 1's
        answer. Version 2's mark starts 0.0198 higher, so `high_water - ratio >= fraction`
        is reached at a ratio 0.0198 higher: **never later, sometimes earlier.** The
        direction is forced by the arithmetic, so a trade that exited *later* in the
        recorded file than under version 1 would mean the file is on the wrong side.
        """
        recorded, under_version_one = runs
        shared = recorded.index.intersection(under_version_one.index)
        later = [int(i) for i in shared
                 if recorded.loc[i, "exit_date"] > under_version_one.loc[i, "exit_date"]]
        assert not later, (
            f"{len(later)} trades exit later in the recorded file than under version 1 "
            f"of the mark: {later[:8]}. Version 2's mark is never below version 1's, so "
            f"no trade can exit later under it. Either the file was recorded with the "
            f"wrong form, or the give-back is no longer measured from the entry"
        )


@pytest.mark.req("0.4.regression")
def test_the_event_driven_baseline_has_not_moved():
    """The comparison. A failure says the result moved and nothing more; what moved and
    whether it should have is the researcher's to read."""
    assert EVENT_DRIVEN_FILE.exists(), (
        f"no event-driven baseline at {EVENT_DRIVEN_FILE}; record one with "
        f"`python -m tools.baseline update --configuration event_driven`"
    )
    diff = compare()
    assert diff is None, (
        "the event-driven baseline moved:\n"
        + diff.to_string(index=False)
        + "\nIf the change really should move the results, run explicitly: "
          "python -m tools.baseline update --configuration event_driven"
    )


@kind("guard")
def test_the_two_configurations_are_recorded_in_separate_files():
    """One file each, so a failure names the configuration that moved in its path rather
    than in a column a reader has to know to look at."""
    import tools.baseline as baseline

    assert EVENT_DRIVEN_FILE != baseline.BASELINE_FILE
    assert EVENT_DRIVEN_FILE.parent == baseline.BASELINE_FILE.parent


@kind("guard")
def test_the_builder_takes_no_arguments():
    """A baseline builder with a knob is a baseline with more than one value. The
    sensitivity test above goes through `build_with_stop`, which is explicitly not the
    recorded configuration."""
    import inspect

    assert inspect.signature(build_event_driven_trades).parameters == {}
