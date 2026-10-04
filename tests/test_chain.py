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
class TestTheStreamIsComplete:
    def test_nothing_is_left_undelivered(self, result):
        """An event published and never drained is a decision the run did not act on,
        and it leaves no other trace."""
        assert not result.raw.bus.pending(), (
            f"{len(result.raw.bus.pending())} events were published and never delivered"
        )

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
