"""The event bus: the order within a timestamp, and the chain back from a fill.

## Why the order is the subject and not the delivery

That a handler receives what it subscribed to is barely worth asserting -- it is one
dictionary lookup, and nothing about it could be subtly wrong. What can be subtly wrong is
**which event is delivered first when several land on the same day**, because every
answer to that produces a complete-looking run and only one of them is the document's.

The failures this file is for all look like a working backtest:

- accounting delivered before fills marks the book without the day's trades in it, which
  is the divergence the audit found in the engine's eight-step loop
- a handler registered twice doubles every position it opens and raises nothing
- an event published after its phase has passed is delivered a day late
- a fill whose causal chain is broken still reports a fill
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.errors import InvariantError, StartupError
from backtest.core.events import (
    ORDER_AS_WRITTEN,
    PHASE,
    Event,
    EventBus,
    EventType,
)

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "0")

DAY = date(2023, 1, 3)
NEXT = date(2023, 1, 4)


def _bus_recording() -> tuple[EventBus, list[EventType]]:
    """A bus with one recorder on every type, so the drain order is observable."""
    bus = EventBus()
    seen: list[EventType] = []
    for type_ in EventType:
        bus.subscribe(type_, lambda e: seen.append(e.type), name=f"recorder/{type_.value}")
    return bus, seen


class TestTheDocumentsThirteen:
    """The event list, held to the document rather than to a transcription.

    An earlier version of ``core/events.py`` said the document named eight types and that
    five were "derived rather than quoted" -- with the derivation written out so nobody
    would read a guess as a specification. Reading the document itself showed the
    derivation was **ten right, two invented and three missed**.

    So the list is pinned here, by name, against the document's own event table. Two
    extra types are ours and are named as such; a third appearing without being added
    here fails, which is the point.
    """

    #: The document's own table, pages 10 and 11. Thirteen.
    DOCUMENTS_OWN = (
        "market", "quote", "corporate_action", "contract_adjust", "universe", "timer",
        "signal", "target_portfolio", "risk", "order", "fill", "order_status", "expiry",
    )

    #: Not in the document's table, and here for reasons recorded in the module.
    #: ``accounting`` is a **stage** of the processing order rather than an event there;
    #: ``state`` is what 0.2's get_state and 5.1's resume snapshot.
    OURS = ("accounting", "state")

    @pytest.mark.req("0.1.bus")
    def test_all_thirteen_of_the_documents_types_exist(self):
        present = {t.value for t in EventType}
        missing = [n for n in self.DOCUMENTS_OWN if n not in present]
        assert not missing, (
            f"these are in the document's event table and not here: {missing}. The count "
            f"is thirteen and the names are the document's"
        )
        assert len(self.DOCUMENTS_OWN) == 13

    def test_nothing_else_is_here_without_being_named_as_ours(self):
        """A fourteenth type smuggled in among the document's would make the list a
        guess again, which is how the first version went wrong."""
        extra = sorted({t.value for t in EventType}
                       - set(self.DOCUMENTS_OWN) - set(self.OURS))
        assert not extra, (
            f"{extra} are neither in the document's table nor listed as ours. Add them "
            f"to OURS with a reason, or take them out"
        )

    def test_every_type_has_a_place_in_the_order(self):
        """A type with no phase would be delivered in publication order, which is the
        dependence the phase table exists to remove."""
        unplaced = sorted(t.value for t in EventType if t not in PHASE)
        assert not unplaced, f"these have no phase: {unplaced}"


class TestTheOrderWithinATimestamp:
    """The document's order, which is the whole reason a bus exists here."""

    @pytest.mark.req("0.1.order")
    def test_the_document_order_is_what_phase_says(self):
        """The order read off the table, against the document's sentence.

        Asserted as a sequence rather than by spot-checking pairs: the sentence is eight
        stages and a table of thirteen numbers, and any pair of them can agree while the
        whole disagrees.
        """
        # **Nine stages.** The transcription in `requirements.md` had eight: it dropped
        # "contract adjustments" from the first and "end-of-day snapshot" from the last,
        # and this test was written from the transcription rather than the document.
        stages = [
            (EventType.CORPORATE_ACTION, "corporate actions"),
            (EventType.CONTRACT_ADJUST, "contract adjustments"),
            (EventType.MARKET, "market data"),
            (EventType.SIGNAL, "signals"),
            (EventType.TARGET_PORTFOLIO, "portfolio and risk"),
            (EventType.ORDER, "orders"),
            (EventType.FILL, "fills"),
            (EventType.EXPIRY, "expiry settlement"),
            (EventType.ACCOUNTING, "accounting"),
            (EventType.STATE, "end-of-day snapshot"),
        ]
        numbers = [PHASE[t] for t, _ in stages]
        assert numbers == sorted(numbers), (
            f"the phase table does not run in the document's order.\n"
            f"  document: {ORDER_AS_WRITTEN}\n"
            f"  table:    {[(n, w) for (_, w), n in zip(stages, numbers)]}"
        )
        # And the words are the document's, so a renamed stage is caught here too.
        for _, word in stages:
            assert word in ORDER_AS_WRITTEN

    @pytest.mark.req("0.1.bus")
    def test_events_are_delivered_in_phase_order_not_publication_order(self):
        """Published backwards, delivered forwards.

        If the bus delivered in publication order the result would depend on which layer
        happened to publish first, which is the dependence a bus is supposed to remove.
        """
        bus, seen = _bus_recording()
        for type_ in reversed(list(EventType)):
            bus.emit(type_, DAY, "test")
        bus.drain(DAY)
        assert seen == sorted(seen, key=lambda t: PHASE[t]), (
            f"delivered in {[t.value for t in seen]}, which is not the document's order"
        )

    @pytest.mark.req("0.1.order")
    def test_accounting_is_delivered_after_fills(self):
        """The specific divergence the audit found, named in its own test.

        The engine marked to market at step 5 of eight, before the strategy ran. That is
        equivalent under a one-day execution lag and wrong the moment a fill lands on its
        signal's own day -- the mark would be taken before the fill it should include. A
        general ordering test would cover it; this one fails with the reason attached.
        """
        bus, seen = _bus_recording()
        bus.emit(EventType.ACCOUNTING, DAY, "ledger")
        bus.emit(EventType.FILL, DAY, "broker")
        bus.drain(DAY)
        assert seen.index(EventType.FILL) < seen.index(EventType.ACCOUNTING), (
            "the book was marked before the day's fills were delivered, so the mark does "
            "not include them"
        )

    def test_two_events_in_one_phase_keep_their_publication_order(self):
        """The only part left to chance, and the part nothing depends on."""
        bus = EventBus()
        seen: list[str] = []
        bus.subscribe(EventType.SIGNAL, lambda e: seen.append(e.reason), name="r")
        for reason in ("first", "second", "third"):
            bus.emit(EventType.SIGNAL, DAY, "test", reason=reason)
        bus.drain(DAY)
        assert seen == ["first", "second", "third"]

    def test_only_the_timestamp_being_drained_is_delivered(self):
        bus, seen = _bus_recording()
        bus.emit(EventType.MARKET, DAY, "data")
        bus.emit(EventType.MARKET, NEXT, "data")
        bus.drain(DAY)
        assert seen == [EventType.MARKET]
        assert len(bus.pending()) == 1


class TestAHandlerThatPublishes:
    """The ordinary case -- a signal handler producing an order -- and its edge."""

    def test_an_event_published_during_a_drain_joins_it(self):
        """A decision carried through to its fill inside one timestamp.

        Without this a signal on Monday could not reach a fill until Tuesday's drain,
        which would build the execution lag into the bus rather than into the engine
        where it is a configured choice.
        """
        bus = EventBus()
        seen: list[EventType] = []

        def on_signal(event: Event) -> None:
            seen.append(event.type)
            bus.emit(EventType.ORDER, event.when, "execution", caused_by=event.id)

        bus.subscribe(EventType.SIGNAL, on_signal, name="exec")
        bus.subscribe(EventType.ORDER, lambda e: seen.append(e.type), name="r")
        bus.emit(EventType.SIGNAL, DAY, "signals")
        bus.drain(DAY)
        assert seen == [EventType.SIGNAL, EventType.ORDER]

    def test_an_event_published_after_its_phase_has_passed_raises(self):
        """A day late with nothing saying so is the failure; raising is the fix.

        An order published while fills are being delivered cannot be filled today. The
        permissive behaviour -- leave it pending, deliver it tomorrow -- produces a run
        where some orders take one day and some take two, with the difference invisible.
        """
        bus = EventBus()
        bus.subscribe(
            EventType.FILL,
            lambda e: bus.emit(EventType.ORDER, e.when, "late", reason="too late"),
            name="latecomer")
        bus.emit(EventType.FILL, DAY, "broker")
        with pytest.raises(InvariantError) as caught:
            bus.drain(DAY)
        assert "phase" in str(caught.value) and ORDER_AS_WRITTEN in str(caught.value)

    def test_publishing_for_a_later_day_during_a_drain_is_fine(self):
        """Which is how an execution lag is expressed: decided today, due tomorrow."""
        bus = EventBus()
        bus.subscribe(
            EventType.SIGNAL,
            lambda e: bus.emit(EventType.ORDER, NEXT, "execution", caused_by=e.id),
            name="lagged")
        bus.emit(EventType.SIGNAL, DAY, "signals")
        bus.drain(DAY)
        assert [e.type for e in bus.pending()] == [EventType.ORDER]
        bus.drain(NEXT)
        assert not bus.pending()

    def test_draining_from_inside_a_handler_raises(self):
        bus = EventBus()
        bus.subscribe(EventType.MARKET, lambda e: bus.drain(e.when), name="reentrant")
        bus.emit(EventType.MARKET, DAY, "data")
        with pytest.raises(InvariantError) as caught:
            bus.drain(DAY)
        assert "already draining" in str(caught.value)


@kind("guard")
class TestWhatWouldOtherwiseBeSilent:
    """Four ways to be wrong that produce a complete-looking run."""

    @pytest.mark.req("0.1.bus")
    def test_a_handler_cannot_be_registered_twice(self):
        """It would run twice on every event: a double position on a fill, a double
        trade on a signal, and no error anywhere."""
        bus = EventBus()
        handler = lambda e: None
        bus.subscribe(EventType.FILL, handler, name="ledger")
        with pytest.raises(StartupError) as caught:
            bus.subscribe(EventType.FILL, handler, name="ledger")
        assert "twice" in str(caught.value)

        # A different name is a different subscriber and is allowed: two layers listening
        # to fills is the normal case.
        bus.subscribe(EventType.FILL, handler, name="tradebook")
        assert bus.subscribers(EventType.FILL) == ("ledger", "tradebook")

    def test_a_handler_that_raises_names_itself_and_the_event(self):
        """A bus that swallowed it would drop a layer out of the run silently."""
        bus = EventBus()

        def broken(event: Event) -> None:
            raise ZeroDivisionError("the stop level was zero")

        bus.subscribe(EventType.SIGNAL, broken, name="stop_loss")
        bus.emit(EventType.SIGNAL, DAY, "signals", reason="exit now")
        with pytest.raises(InvariantError) as caught:
            bus.drain(DAY)
        text = str(caught.value)
        assert "stop_loss" in text and "ZeroDivisionError" in text
        assert "the stop level was zero" in text and "exit now" in text

    def test_time_does_not_run_backwards(self):
        bus = EventBus()
        bus.drain(NEXT)
        with pytest.raises(InvariantError) as caught:
            bus.drain(DAY)
        assert "backwards" in str(caught.value)

    def test_an_event_cannot_be_published_twice(self):
        bus = EventBus()
        published = bus.emit(EventType.MARKET, DAY, "data")
        with pytest.raises(InvariantError):
            bus.publish(published)

    def test_an_effect_cannot_precede_its_cause(self):
        """``caused_by`` pointing at an id that does not exist yet would make a trace
        read backwards, and a trace is the only evidence of why a fill happened."""
        bus = EventBus()
        with pytest.raises(InvariantError) as caught:
            bus.emit(EventType.FILL, DAY, "broker", caused_by=99)
        assert "has not been published yet" in str(caught.value)


class TestTheChainBackFromAFill:
    """Requirement 0.6: fill -> order -> target -> signal -> component."""

    @staticmethod
    def _full_chain(bus: EventBus) -> Event:
        signal = bus.emit(EventType.SIGNAL, DAY, "signals",
                          component_id="entry/dip@1", trade_id=1, reason="close < sma")
        target = bus.emit(EventType.TARGET_PORTFOLIO, DAY, "portfolio",
                          caused_by=signal.id, trade_id=1, reason="hold 1 contract")
        order = bus.emit(EventType.ORDER, DAY, "execution",
                         caused_by=target.id, trade_id=1, reason="buy 1")
        fill = bus.emit(EventType.FILL, DAY, "broker",
                        caused_by=order.id, trade_id=1, reason="filled at 5.10")
        bus.drain(DAY)
        return fill

    @pytest.mark.req("0.6.chain")
    def test_a_fill_reaches_the_component_that_decided_it(self):
        bus = EventBus()
        fill = self._full_chain(bus)
        trace = bus.trace(fill.id)
        assert trace.types == (EventType.FILL, EventType.ORDER,
                               EventType.TARGET_PORTFOLIO, EventType.SIGNAL)
        assert trace.component == "entry/dip@1"
        assert trace.complete

    @pytest.mark.req("0.6.chain")
    def test_a_broken_chain_says_which_links_are_missing(self):
        """The new way to be wrong once the links exist: a short chain returned as
        though it were a complete one.

        This is why `trace` returns a type rather than a list. A list of one is a
        perfectly good list.
        """
        bus = EventBus()
        orphan = bus.emit(EventType.FILL, DAY, "broker", reason="from nowhere")
        bus.drain(DAY)
        trace = bus.trace(orphan.id)
        assert not trace.complete
        assert set(trace.missing()) == {EventType.ORDER, EventType.TARGET_PORTFOLIO,
                                        EventType.SIGNAL}
        assert "cannot be traced to a decision" in trace.describe()

    def test_a_chain_that_reaches_a_signal_with_no_component_is_not_complete(self):
        """All four hops present and nothing says which slot decided.

        The hop count and the attribution are separate failures: a chain can have every
        link and still not answer "which component", which is the question a variant
        comparison rests on.
        """
        bus = EventBus()
        signal = bus.emit(EventType.SIGNAL, DAY, "signals", reason="no component named")
        target = bus.emit(EventType.TARGET_PORTFOLIO, DAY, "portfolio",
                          caused_by=signal.id)
        order = bus.emit(EventType.ORDER, DAY, "execution", caused_by=target.id)
        fill = bus.emit(EventType.FILL, DAY, "broker", caused_by=order.id)
        bus.drain(DAY)
        trace = bus.trace(fill.id)
        assert not trace.missing(), "every hop is there"
        assert not trace.complete, "and it still cannot say which slot decided"
        assert "cannot be attributed to a slot" in trace.describe()

    def test_a_signals_own_trace_is_not_reported_as_missing_hops(self):
        """The hops below a signal are not expected above it. Reporting them would make
        every trace look broken and train people to ignore the field."""
        bus = EventBus()
        signal = bus.emit(EventType.SIGNAL, DAY, "signals", component_id="entry/dip@1")
        bus.drain(DAY)
        assert bus.trace(signal.id).missing() == ()

    def test_tracing_an_unknown_id_is_refused(self):
        bus = EventBus()
        with pytest.raises(StartupError) as caught:
            bus.trace(42)
        assert "no event #42" in str(caught.value)

    def test_a_causal_loop_raises_rather_than_hanging(self):
        """Constructed by hand because `publish` refuses a forward reference, so a loop
        can only arrive through a history assembled some other way -- a resumed run, or
        a merged one. Hanging is the behaviour to rule out."""
        bus = EventBus()
        a = bus.emit(EventType.ORDER, DAY, "x")
        b = bus.emit(EventType.FILL, DAY, "y", caused_by=a.id)
        bus.drain(DAY)
        bus._history[0] = Event(type=a.type, when=a.when, source=a.source, id=a.id,
                                caused_by=b.id)
        with pytest.raises(InvariantError) as caught:
            bus.trace(b.id)
        assert "loops" in str(caught.value)


class TestTheHistory:
    def test_delivered_events_are_kept_and_pending_ones_are_not(self):
        bus = EventBus()
        bus.emit(EventType.MARKET, DAY, "data")
        bus.emit(EventType.MARKET, NEXT, "data")
        bus.drain(DAY)
        assert len(bus) == 1
        assert len(bus.pending()) == 1

    def test_ids_are_unique_and_ascending(self):
        bus = EventBus()
        ids = [bus.emit(EventType.MARKET, DAY, "data").id for _ in range(5)]
        assert ids == sorted(set(ids)) == [1, 2, 3, 4, 5]
