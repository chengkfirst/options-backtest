"""Resume and the batch runner — requirement 5.1.

## The correctness criterion, and why it is the whole test

A resumed run must not differ from a continuous one. That is hard to establish by
inspection: a run's state is a ledger, a queue of orders in flight, the open trades, each
candidate's lifecycle, every stateful component's per-trade state, the event stream, and a
handful of equity counters. **Omit any one and the resumed run produces a plausible result
that is not the same result** -- a trade missing, a stop starting again from its entry, a
candidate re-arming when it should be waiting, a drawdown brake released by the act of
resuming.

So the test is the mechanism. The same range is run continuously and then as two halves
across a checkpoint, and the trade tables must be row-identical. It found a real omission
on the first attempt: an order in flight carries the id of the ORDER event it was
published as, and a resumed run with a fresh event bus restarted ids at 1, so the carried
order pointed at an event that had not happened.

## Where the cuts are

Four, chosen for what is **in flight** at each rather than for the dates: one with four
open trades, one with an order queued and not yet filled, one with nothing open at all,
and one with six open and six live high-water marks. A cut on a quiet day is the easy
case and proves least.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.errors import InvariantError, StartupError
from backtest.engine.runner import Batch, BatchEntry, Checkpoint, run_batch

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("consistency", "5")


def _strategy(signals, *, name: str = "resume-probe", trailing: bool = True):
    """A fresh strategy each time.

    Fresh deliberately: a bound component holds its own per-trade state, so two runs
    sharing one would share high-water marks -- and this file's whole subject is state
    crossing a boundary correctly.
    """
    from backtest.core.component import get
    from backtest.core.types import Right
    from backtest.engine.strategy import Strategy
    from backtest.signals.contracts import SelectionRule
    from backtest.signals.entry.expression import parse_condition
    from backtest.signals.strategy import EntryRule, ExitRule

    condition, requires = parse_condition("close < sma_20 * 0.98", signals.names)
    components = ({"stop_loss": get("stop_loss", "trailing").bind({"fraction": 0.5})}
                  if trailing else {})
    return Strategy(
        name=name,
        entry=EntryRule("dip", condition, requires),
        select=SelectionRule(right=Right.CALL, target_dte=30, dte_window=(21, 45),
                             target_delta=0.30),
        exit=ExitRule(hold_days=10, dte_floor=7),
        components=components,
    )


def _engine(market, chain, signals, config):
    from backtest.engine.loop import EventDrivenEngine

    return EventDrivenEngine(market, chain, signals, config)


def _rows(result) -> list[tuple]:
    """Everything about a trade that a difference could hide in."""
    return [(r.trade_id, r.entry_date, r.exit_date, r.contract, r.quantity,
             round(r.entry_price, 10), round(r.exit_price, 10), round(r.net_pnl, 10),
             r.exit_reason.value, r.holding_days, r.deferred_days)
            for r in result.records]


@pytest.fixture(scope="module")
def continuous(market, chain, signals, config):
    return _engine(market, chain, signals, config).run(_strategy(signals))


@kind("guard")
class TestAResumedRunIsTheSameRun:
    #: Chosen for what is in flight, not for the dates. See the module docstring.
    CUTS = (date(2022, 8, 15), date(2022, 10, 17), date(2023, 3, 15), date(2023, 7, 17))

    @pytest.mark.req("5.1.resume")
    @pytest.mark.parametrize("cut", CUTS)
    def test_the_trade_table_is_row_identical(self, cut, continuous, market, chain,
                                              signals, config):
        """**A fresh strategy for the second half**, which is the realistic case and the
        only one that tests anything.

        The first version reused one strategy object across both runs, so its bound
        components still held their state in memory and `set_state` restored what had
        never left. The mutation check caught it: deleting the restore entirely left this
        test green. A resumed run is a new process, and this now matches.
        """
        first = _engine(market, chain, signals, config).run(_strategy(signals), until=cut)
        second = _engine(market, chain, signals, config).run(
            _strategy(signals), resume_from=first.checkpoint)

        expected, got = _rows(continuous), _rows(second)
        assert got == expected, (
            f"a run cut at {cut} and resumed produced a different trade table.\n"
            f"  only in the continuous run: "
            f"{[r for r in expected if r not in got][:2]}\n"
            f"  only in the resumed run:    "
            f"{[r for r in got if r not in expected][:2]}"
        )

    def test_the_cuts_cover_something_in_flight(self, market, chain, signals, config):
        """A cut on a quiet day is the easy case and proves least.

        Asserted so the set of cuts cannot quietly become four quiet days: between them
        they have to leave open trades, an order queued, live component state, and one
        cut with nothing open at all.
        """
        seen = {"open": 0, "queued": 0, "state": 0, "empty": 0}
        for cut in self.CUTS:
            checkpoint = _engine(market, chain, signals, config).run(
                _strategy(signals), until=cut).checkpoint
            seen["open"] += bool(checkpoint.open_trades)
            seen["queued"] += bool(checkpoint.queue)
            seen["state"] += bool(checkpoint.component_state.get("stop_loss"))
            seen["empty"] += not checkpoint.open_trades
        for what, count in seen.items():
            assert count, f"no cut leaves {what}, so that case is untested"

    def test_the_equity_curve_is_continuous_across_the_cut(self, continuous, market,
                                                           chain, signals, config):
        """The counters, which are the group easiest to forget.

        A resumed run whose peak equity restarted at the initial capital reports no
        drawdown until it exceeds the old peak -- so a drawdown brake would be released
        by the act of resuming, and nothing else in a result would show it.
        """
        cut = date(2023, 7, 17)
        strategy = _strategy(signals)
        first = _engine(market, chain, signals, config).run(strategy, until=cut)
        second = _engine(market, chain, signals, config).run(
            strategy, resume_from=first.checkpoint)

        assert len(second.snapshots) == len(continuous.snapshots)
        for mine, theirs in zip(second.snapshots, continuous.snapshots):
            assert mine.day == theirs.day
            assert mine.equity_mid == pytest.approx(theirs.equity_mid, rel=1e-12)

    @pytest.mark.req("5.1.resume")
    def test_the_equity_counters_are_restored_and_not_restarted(self, market, chain,
                                                                signals, config):
        """Asserted on the counters themselves, not on the trades they move.

        `peak_equity` reaches a result only through the drawdown, and the drawdown only
        through a rule that reads it -- so a version of this that compared trade tables
        under a drawdown brake **passed with the restore deleted**, because the peak at
        the cut happened to sit a few dollars above the starting capital and the
        difference could not cross a 5% threshold. That is data luck standing in for a
        mechanism.

        What matters is mechanical and is checked directly: every counter the engine
        carries comes back as it was. The consequence -- a drawdown brake released by the
        act of resuming, with nothing else in a result showing it -- follows from that
        and does not need a market to demonstrate it.
        """
        cut = date(2023, 7, 17)
        first = _engine(market, chain, signals, config).run(_strategy(signals), until=cut)
        counters = first.checkpoint.counters
        assert counters["peak_equity"] > config.notional_capital, (
            "the book never rose above its starting capital, so a restored peak and a "
            "restarted one are the same number and this test proves nothing"
        )

        # **One session only.** Resuming to the end of the range let the second half
        # push the peak up again, so the figure ended above the checkpoint's whatever it
        # was seeded with -- and the test passed with the restore deleted. One session
        # cannot outrun a peak reached over the previous year.
        next_session = market.calendar.next_session(cut)
        resumed = _engine(market, chain, signals, config).run(
            _strategy(signals), resume_from=first.checkpoint, until=next_session)
        carried = resumed.checkpoint.counters

        assert carried["peak_equity"] >= counters["peak_equity"], (
            f"after one further session the peak equity is "
            f"{carried['peak_equity']:,.2f} and the checkpoint's was "
            f"{counters['peak_equity']:,.2f}. A peak cannot fall, so the counter was "
            f"restarted rather than restored -- and a drawdown measured against a lower "
            f"peak releases a brake that should still be on"
        )
        # The others come straight through: one session changes the equity figures, and
        # the brake's own state is not a thing a quiet session moves.
        assert carried["brake_state"] == counters["brake_state"]

    def test_a_drawdown_brake_trades_the_same_across_a_resume(self, market, chain,
                                                              signals, config):
        """The consequence, end to end. Separate from the test above because it depends
        on the brake actually reaching a threshold on this data, which is a property of
        the data rather than of the mechanism."""
        from backtest.core.component import get

        def brake():
            return get("headwind", "drawdown").bind(
                {"reduce_at": 0.05, "halt_at": 0.15, "keep": 0.5, "liquidate_at": 0.30})

        cut = date(2023, 7, 17)
        whole = _engine(market, chain, signals, config)
        whole.brake = brake()
        continuous_rows = _rows(whole.run(_strategy(signals)))

        first_engine = _engine(market, chain, signals, config)
        first_engine.brake = brake()
        first = first_engine.run(_strategy(signals), until=cut)

        second_engine = _engine(market, chain, signals, config)
        second_engine.brake = brake()
        second = second_engine.run(_strategy(signals), resume_from=first.checkpoint)
        assert _rows(second) == continuous_rows

    def test_the_event_stream_follows_on(self, market, chain, signals, config):
        """The omission this test found.

        An order in flight names the ORDER event it was published as, so the fill can
        name it back. A fresh bus hands out ids from 1 and the carried order then points
        at an event that has not happened.
        """
        cut = date(2022, 10, 17)
        strategy = _strategy(signals)
        first = _engine(market, chain, signals, config).run(strategy, until=cut)
        assert first.checkpoint.queue, "this cut has no order in flight, so it tests less"
        second = _engine(market, chain, signals, config).run(
            strategy, resume_from=first.checkpoint)

        # Every fill in the resumed half still traces to a component, including the one
        # whose order was placed in the first half.
        from backtest.core.events import EventType

        fills = second.bus.of_type(EventType.FILL)
        assert fills
        assert all(second.bus.trace(f.id).complete for f in fills)


@kind("guard")
class TestWhatResumeRefuses:
    def test_a_checkpoint_from_another_strategy_is_refused(self, market, chain, signals,
                                                           config):
        """Continuing one strategy's book under another builds a book nobody ran."""
        first = _engine(market, chain, signals, config).run(
            _strategy(signals, name="one"), until=date(2022, 10, 17))
        with pytest.raises(StartupError) as caught:
            _engine(market, chain, signals, config).run(
                _strategy(signals, name="two"), resume_from=first.checkpoint)
        assert "under" in str(caught.value)

    def test_a_checkpoint_whose_state_has_nowhere_to_go_is_refused(self, market, chain,
                                                                   signals, config):
        """A trailing stop resumed into a strategy with no stop component would have its
        high-water marks discarded, making it a fixed stop for the rest of every trade."""
        first = _engine(market, chain, signals, config).run(
            _strategy(signals, trailing=True), until=date(2023, 7, 17))
        assert first.checkpoint.component_state.get("stop_loss")
        with pytest.raises(StartupError) as caught:
            _engine(market, chain, signals, config).run(
                _strategy(signals, trailing=False), resume_from=first.checkpoint)
        assert "no component there" in str(caught.value)

    def test_a_resume_with_no_sessions_left_is_refused(self, market, chain, signals,
                                                       config):
        """A run over no sessions returns a result that looks like a backtest."""
        strategy = _strategy(signals)
        whole = _engine(market, chain, signals, config).run(strategy, until=config.end)
        with pytest.raises(StartupError) as caught:
            _engine(market, chain, signals, config).run(
                _strategy(signals), resume_from=whole.checkpoint)
        assert "no sessions left" in str(caught.value)

    def test_a_whole_run_offers_no_checkpoint(self, continuous):
        """A checkpoint of a finished run is a thing nobody can continue, and offering
        one would invite the attempt."""
        assert continuous.checkpoint is None

    def test_two_streams_cannot_be_merged_into_one_bus(self):
        """Two runs' causal chains in one history cannot be told apart by a trace."""
        from backtest.core.events import EventBus, EventType

        bus = EventBus()
        bus.emit(EventType.MARKET, date(2023, 1, 3), "data")
        bus.drain(date(2023, 1, 3))
        with pytest.raises(InvariantError) as caught:
            bus.resume(bus.history, 99)
        assert "cannot be told apart" in str(caught.value)


class TestTheBatchRunner:
    def test_each_configuration_gets_its_own_result(self):
        batch = run_batch({"a": {"x": 1}, "b": {"x": 2}},
                          runner=lambda **kw: f"ran {kw}")
        assert batch.trials == 2
        assert [e.label for e in batch] == ["a", "b"]
        assert all(e.ran for e in batch)

    def test_a_failure_does_not_discard_the_others(self):
        """One bad entry in forty should not throw away the thirty-nine, and the failure
        belongs in the result rather than a log nobody reads."""
        def runner(**kw):
            if kw.get("bad"):
                raise ValueError("no")
            return "fine"

        batch = run_batch({"good": {}, "bad": {"bad": True}}, runner=runner)
        assert len(batch.ran()) == 1 and len(batch.failed()) == 1
        assert "ValueError: no" in batch.failed()[0].failed

    @pytest.mark.req("5.1.resume")
    def test_the_trial_count_includes_the_failures(self):
        """**The number the deflated Sharpe needs.**

        A configuration that raised is still a thing that was looked at. Counting only
        the ones that ran would deflate by too few trials, which flatters the survivors
        by exactly the number of attempts nobody wrote down.
        """
        def runner(**kw):
            raise ValueError("no")

        batch = run_batch([{}, {}, {}], runner=runner)
        assert batch.trials == 3 and not batch.ran()
        assert "3 trials" in batch.describe()

    def test_an_empty_batch_is_refused(self):
        """Almost always a list comprehension that filtered everything out."""
        with pytest.raises(StartupError) as caught:
            run_batch([], runner=lambda **kw: None)
        assert "nothing to run" in str(caught.value)

    def test_a_sequence_is_labelled_by_index(self):
        batch = run_batch([{"x": 1}, {"x": 2}], runner=lambda **kw: kw)
        assert [e.label for e in batch] == ["0", "1"]


@kind("architecture")
class TestTheMutationCheckCannotBeFooledByARename:
    """``tools/check_mutations.py`` read "pytest exited non-zero" as "the mutation was
    caught", and a renamed test makes pytest exit non-zero for a different reason.

    So an entry pointing at a test that no longer exists reported OK for ever -- a
    mechanism nobody was checking, inside the tool whose whole purpose is finding
    mechanisms nobody is checking. Found when an entry named a test that had been renamed
    minutes earlier.
    """

    def test_an_entry_naming_a_missing_test_is_reported(self):
        from tools.check_mutations import Mutation, run_one

        caught, detail = run_one(Mutation(
            "a mechanism whose test was renamed",
            "backtest/engine/runner.py",
            # A unique anchor: the duplicate-anchor check runs first and would report
            # that instead, which would make this test pass for the wrong reason.
            "    entries: tuple[BatchEntry, ...]",
            "    entries: tuple[BatchEntry, ...] = ()",
            ("tests/test_runner.py::TestThatDoesNotExist::test_nor_does_this",),
        ))
        assert not caught
        assert "no such test" in detail

    def test_every_entry_names_tests_that_exist(self):
        """The list held to itself. Cheap, because collection is cheap, and it is the
        check that would have caught the rename on the day it happened."""
        import subprocess
        import sys

        from tools.check_mutations import MUTATIONS, ROOT

        targets = sorted({t for m in MUTATIONS for t in m.tests
                          if not t.endswith(".py")})
        probe = subprocess.run(
            [sys.executable, "-m", "pytest", *targets, "-q", "--no-header",
             "--collect-only"],
            cwd=ROOT, capture_output=True, text=True)
        assert probe.returncode == 0, (
            "some mutation entries name tests that cannot be collected:\n"
            + probe.stdout[-1500:]
        )
