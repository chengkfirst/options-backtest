"""Requirement 0.6: a fill traced back through order -> target -> signal -> component.

## Why this is a file of its own

The audit's note on 0.6 was that two of the five links did not exist -- orders and
targets were not events -- so the chain could not be followed even in principle. They
exist now, and that creates a failure the gap could not have: **a chain that is short
because a link was never set, read as a complete one**. A list of one is a perfectly good
list, and a run where no fill can be explained produces the same trade table as one where
every fill can.

So the assertions here are about completeness over a whole run rather than about one
hand-built chain. The hand-built one is in ``test_events_bus.py``, where the bus is the
subject; here the subject is whether the engine actually sets the links on every fill it
produces.
"""

from __future__ import annotations

import pytest

from backtest.api.sdk import run
from backtest.core.events import EventType

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("consistency", "5")

RUN = dict(
    symbol="QQQ", start="2023-01-01", end="2023-06-30",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


@pytest.fixture(scope="module")
def result():
    return run(**RUN)


class TestEveryFillIsExplainable:
    @pytest.mark.req("0.6.chain")
    @pytest.mark.req("0.1.bus")
    def test_every_fill_in_a_real_run_reaches_the_component_that_decided_it(self, result):
        """The whole requirement, asserted over the run rather than over an example.

        One hand-built chain proves the bus can carry one. This proves the engine sets
        the links on all of them -- which is the half that rots, because a new code path
        that publishes a fill without naming its order breaks nothing else.
        """
        bus = result.raw.bus
        fills = bus.of_type(EventType.FILL)
        assert fills, "the run produced no fills, so this asserts nothing"

        broken = [bus.trace(f.id) for f in fills]
        broken = [t for t in broken if not t.complete]
        assert not broken, (
            f"{len(broken)} of {len(fills)} fills cannot be traced to a component:\n"
            + "\n\n".join(t.describe() for t in broken[:3])
        )

    def test_each_fill_names_its_own_order_and_not_another(self, result):
        """The hop the chain exists for.

        Matching a fill to an order by contract and quantity would be right almost
        always, and wrong exactly when two orders for the same contract are in flight --
        which is the case anybody asking "why did this fill happen" is asking about. So
        the id is carried on the intent across the day that separates them, and this is
        what checks it arrived.
        """
        bus = result.raw.bus
        for fill in bus.of_type(EventType.FILL):
            trace = bus.trace(fill.id)
            order = next(e for e in trace.links if e.type is EventType.ORDER)
            assert order.data["contract"] == fill.data["contract"], (
                f"fill #{fill.id} on {fill.data['contract']} names order #{order.id}, "
                f"which was for {order.data['contract']}"
            )
            assert order.when <= fill.when, "a fill cannot precede its order"
            assert fill.trade_id == order.trade_id

    def test_the_order_carries_the_day_it_was_due_and_the_fill_honours_it(self, result):
        """The execution lag, visible in the stream rather than inferred.

        An order says which session it is due on; the fill either lands there or lands
        later with the deferral recorded. A fill *earlier* than its order's due day would
        be look-ahead, and nothing else in the suite would see it.
        """
        from datetime import date

        bus = result.raw.bus
        for fill in bus.of_type(EventType.FILL):
            order = next(e for e in bus.trace(fill.id).links
                         if e.type is EventType.ORDER)
            due = date.fromisoformat(order.data["execute_on"])
            assert fill.when >= due, (
                f"fill #{fill.id} landed on {fill.when}, before the {due} its order "
                f"said it was due -- which is a fill on information not yet available"
            )


class TestTheChainDistinguishesWhatMustBeDistinguished:
    def test_a_rejected_order_is_a_rejection_and_not_a_missing_fill(self, result):
        """4.2 asks for every rejected order to be logged, and the count is read.

        Publishing a rejection as an order that never filled would make it
        indistinguishable from a bug in the fill path -- the two have identical evidence
        and opposite causes.
        """
        bus = result.raw.bus
        rejects = bus.of_type(EventType.REJECT)
        orders = bus.of_type(EventType.ORDER)
        fills = bus.of_type(EventType.FILL)
        assert rejects, "this run used to reject two intents; if it no longer does, " \
                        "find a configuration that does rather than deleting this"

        # An order either fills or is still in flight at the end of the range. A
        # rejection is neither, and is not counted among them.
        assert len(fills) <= len(orders)
        reject_ids = {e.id for e in rejects}
        for fill in fills:
            assert not reject_ids & {e.id for e in bus.trace(fill.id).links}, (
                "a fill's chain passes through a rejection"
            )

    def test_a_rejection_still_names_the_slot_that_asked(self, result):
        """Which slot is being refused is the question a researcher asks of a rejection
        count, and it is lost if the rejection carries only the contract."""
        for event in result.raw.bus.of_type(EventType.REJECT):
            assert event.component_id, f"{event.describe()} does not name a slot"
            assert event.trade_id is not None


@kind("guard")
class TestTheTwoRecordsOfAFillAgree:
    """One test, against a fork that has already happened once.

    There are two records of every fill: the **decision log** (`actions_frame`, one
    `TradeAction` per fill with the slot that asked for it) and the **event stream**
    (`FILL` events, with the causal chain). They were written in two places, and within
    hours of the second one existing they disagreed -- 22 rows against 34 events.

    The cause is the shape of the failure rather than the arithmetic. The FILL event was
    published by the caller, before `_execute` ran, and `_execute` has four paths that
    fill nothing: no quote on the execution day, the position already gone, a reduction
    that would take all of it, an untradable quote. Each left an event with no fill behind
    it -- and an intent carried forward republished one every day it was retried, so one
    close of one trade produced five events.

    **Nothing failed.** The trade table was right, the ledger was right, the accounting
    identity held every day, and the stream said twelve things had happened that had not.
    The fix was to put the event on the line after `apply_fill`, beside the log's own row;
    this is what keeps them there.
    """

    def test_one_fill_event_for_every_row_of_the_decision_log(self, result):
        from backtest.core.events import EventType

        log = result.raw.actions
        fills = result.raw.bus.of_type(EventType.FILL)
        assert len(log) == len(fills), (
            f"the decision log has {len(log)} rows and the stream has {len(fills)} fill "
            f"events. Two records of the same fact have diverged, and neither the trade "
            f"table nor the accounting identity can see it"
        )

    def test_they_agree_row_by_row_and_not_only_in_count(self, result):
        """Equal counts with mismatched contents is the next way to be wrong, and it is
        the one a count test teaches you to stop looking for."""
        from backtest.core.events import EventType

        log = sorted(((a.trade_id, a.when, a.action.value, a.component_id)
                      for a in result.raw.actions))
        stream = sorted(((e.trade_id, e.when, e.reason.split()[0], e.component_id)
                         for e in result.raw.bus.of_type(EventType.FILL)))
        assert log == stream, (
            "the two records hold the same number of fills and describe different ones:\n"
            f"  only in the decision log: {sorted(set(log) - set(stream))[:3]}\n"
            f"  only in the stream:       {sorted(set(stream) - set(log))[:3]}"
        )

    def test_the_price_and_quantity_in_the_event_are_the_filled_ones(self, result):
        """A reduction is resized at the fill when the position has shrunk, and a close
        takes whatever is held. The event has to carry what was filled, not what was
        asked for -- which is the field that drifts when an event is published before the
        fill rather than after it."""
        from backtest.core.events import EventType

        by_key = {(a.trade_id, a.when, a.action.value): a for a in result.raw.actions}
        for event in result.raw.bus.of_type(EventType.FILL):
            action = by_key[(event.trade_id, event.when, event.reason.split()[0])]
            assert event.data["quantity"] == action.values["quantity"]
            assert event.data["price"] == pytest.approx(action.values["price"])


@kind("guard")
class TestTheStreamIsComplete:
    def test_nothing_is_left_undelivered(self, result):
        """An event published and never drained is a decision the run did not act on,
        and it leaves no other trace."""
        assert not result.raw.bus.pending(), (
            f"{len(result.raw.bus.pending())} events were published and never delivered"
        )

    @pytest.mark.req("0.1.order")
    def test_the_days_events_are_in_the_documents_order(self, result):
        """Within each session, the stream must read in the document's order.

        Checked on the stream rather than on the loop: the loop's own order is "settle
        yesterday, then decide today", which is equivalent under the enforced one-day
        lag but is not the document's sentence. What a reader of the stream sees has to
        be the sentence.
        """
        from backtest.core.events import PHASE

        by_day: dict = {}
        for event in result.raw.bus:
            by_day.setdefault(event.when, []).append(PHASE[event.type])
        for day, phases in by_day.items():
            assert phases == sorted(phases), (
                f"{day}'s events are not in phase order: {phases}"
            )

    @pytest.mark.req("0.1.order")
    def test_the_equivalence_rests_on_the_lag_being_at_least_one_day(self):
        """**Why the loop's order is allowed to differ from the document's.**

        The loop marks to market before the strategy runs. That includes every fill of
        the day, because a signal decided today cannot fill today -- the config refuses
        an execution lag below one. Relax that and the mark would be taken before a fill
        it should include, which is the divergence the audit recorded.

        Tied together here so the two cannot drift apart: a change to the lag bound fails
        this test, with the reason, rather than silently making the accounting wrong.
        """
        from backtest.core.config import BacktestConfig
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError):
            BacktestConfig(symbol="QQQ", execution_lag_days=0)

    def test_the_run_can_say_why_a_fill_happened(self, result):
        """The requirement from a user's side: `result.why()`.

        A chain nobody can reach answers 0.6 for nobody, which is why this asserts the
        public method and not only the bus.
        """
        text = result.why()
        assert "traceable to a component" in text
        assert "cannot be traced" not in text
        trade = result.raw.bus.of_type(EventType.FILL)[0].trade_id
        assert f"trade {trade}" in result.why(trade)


@kind("guard")
class TestEachSlotsRecordIsItsOwn:
    """Requirement S.namespaces, over a real run rather than a constructed context.

    The unit tests in ``test_trade_context.py`` prove the structure cannot be made to
    cross namespaces. This proves the engine uses it — which is the half that rots, since
    a slot recording into the wrong namespace, or into none, breaks nothing else.
    """

    @pytest.mark.req("S.namespaces")
    def test_the_exit_reason_agrees_with_the_slot_that_recorded_firing(self, result):
        """The failure this requirement exists for, asserted over every trade.

        Time exits were once credited to the stop-loss slot. Nothing structural tied a
        recorded fact to the slot that produced it, so the two could disagree and only a
        person reading both would notice.
        """
        from backtest.core.types import ExitReason

        owner = {
            ExitReason.TAKE_PROFIT: "take_profit",
            ExitReason.STOP_LOSS: "stop_loss",
            ExitReason.DTE_FLOOR: "time_exit",
            ExitReason.TIME_EXIT: "time_exit",
        }
        checked = 0
        for record in result.raw.records:
            slot = owner.get(record.exit_reason)
            if slot is None:
                continue          # expiry and risk-forced exits are not these slots'
            fired = record.slot_record.get(f"{slot}.fired")
            assert fired, (
                f"trade {record.trade_id} closed on {record.exit_reason.value} and the "
                f"{slot} slot's own record says it did not fire: {fired!r}. The exit "
                f"reason and the slot that produced it disagree"
            )
            checked += 1
        assert checked >= 5, "too few trades with a slot-owned exit for this to mean much"

    def test_every_slot_that_ran_recorded_something(self, result):
        """A slot that ran and recorded nothing is indistinguishable from one that did
        not run, and "did not run" is a legitimate state — so the two must not look
        alike."""
        for record in result.raw.records:
            for slot in ("take_profit", "stop_loss", "time_exit"):
                assert f"{slot}.fired" in record.slot_record, (
                    f"trade {record.trade_id} has no record from {slot}, which runs on "
                    f"every open day"
                )

    def test_the_two_exit_slots_record_the_same_ratio_under_their_own_names(self, result):
        """Both slots turn on the premium ratio and both record it. Under a flat dict
        one would have overwritten the other; here both are present and each is
        attributable."""
        for record in result.raw.records[:10]:
            take = record.slot_record.get("take_profit.premium_ratio")
            stop = record.slot_record.get("stop_loss.premium_ratio")
            assert take is not None and stop is not None
            assert take == stop, "the two slots saw one ratio and recorded two"

    def test_the_trade_table_says_which_slot_each_column_came_from(self, result):
        """The prefix, over a real record. A column a reader cannot attribute is the
        second of the two failures, and it is the one a prefix convention leaves open."""
        record = result.raw.records[0]
        assert record.slot_record, "no slot recorded anything"
        for key in record.slot_record:
            slot = key.split(".")[0]
            assert slot in ("take_profit", "stop_loss", "time_exit", "candidate"), (
                f"{key!r} is not attributable to a slot"
            )
