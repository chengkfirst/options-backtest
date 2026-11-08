"""The event bus: the only way layers are allowed to talk to each other.

The architecture document opens Layer 0 with this and the audit called it the largest
structural gap in the system. Five other requirements wait on it -- the component
protocol's ``on_event``, the target portfolio, the scheduler and resume, and running the
same component code against a live feed. None of them can be built properly first,
because each is defined as something that happens *in response to an event*.

## What a bus buys that direct calls do not

Today the engine calls the selection rule, which calls the chain, which calls the pricer.
Every one of those calls is correct and none of them is **observable**: there is no
moment at which a third party can see what one layer told another. Three things the
document asks for are all that same missing moment.

- **Traceability** (0.6): a fill traced back through order -> target -> signal ->
  component. A call stack is gone by the time anyone wants to read it; a chain of events
  with ids is still there.
- **Swapping a slot** (2.1, 6.3): a component that receives events can be replaced by
  another that receives the same events. A component reached by a direct call can only be
  replaced by something with the same signature in the same place.
- **The same code live** (7.2): "the same components, the same events, a different
  source". Without a bus there is nothing to change the source of.

## The fixed order within a timestamp

Events carry a timestamp, and several land on the same one. The order they are processed
in is **not** the order they were published in -- that would make the result depend on
which layer happened to run first, which is the thing a bus is supposed to remove. It is
the document's order:

    corporate actions -> market data -> signals -> portfolio -> orders -> fills
    -> expiry -> accounting

``PHASE`` below assigns each event type its place in it. Two events on the same timestamp
in the same phase keep the order they were published in, which is the only part left to
chance and is the part where nothing depends on it.

**Accounting is last.** The engine marked to market at step 5 of its eight, before the
strategy ran. That is equivalent under a one-day execution lag -- nothing changes the
portfolio after step 5 -- and it is wrong the moment an intraday fill lands on the same
day as its signal, because the mark would then be taken before the fill it should include.
The audit recorded it as a divergence; putting the order in one table is what makes it not
recur.

## What this is not

It is not ``core/run_log.py``. That is a **diagnostic** stream -- a quote was missing, a
selection failed, an exit was deferred -- written for a person reading afterwards, and it
has no bearing on what the engine does next. This is the engine's own plumbing: removing
an event changes the result. The two have similar names and opposite purposes, which is
why this says so here.

It is also not a priority queue over wall-clock time. A backtest's timestamps come from
the calendar, not from the machine, and a queue that ordered by arrival would reintroduce
exactly the dependence on publication order that ``PHASE`` exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Callable, Iterator, Mapping, Sequence

from backtest.core.errors import InvariantError, StartupError


class EventType(str, Enum):
    """The architecture document's thirteen, plus two of our own.

    **This list was wrong and the correction is worth recording.** An earlier version
    said the document named eight and that the other five were "derived rather than
    quoted", with the derivation written out so nobody would read a guess as a
    specification. Reading the document itself -- rather than the transcription in
    ``docs/requirements.md`` -- showed the derivation was ten right, two invented and three
    missed.

    The document's thirteen, from its own event table (pages 10 and 11):

    ======================  ==========  =============================================
    Document                Here        Note
    ======================  ==========  =============================================
    MarketEvent             MARKET      the underlying's bar
    QuoteEvent              QUOTE       the option chain snapshot
    CorporateActionEvent    CORPORATE_  dividend, split, rights
                            ACTION
    ContractAdjustEvent     CONTRACT_   an OCC adjustment: new multiplier, new strike
                            ADJUST
    UniverseEvent           UNIVERSE    the tradable set for a date
    TimerEvent              TIMER       start of day, snapshot, rebalance, end of day
    SignalEvent             SIGNAL      what a component decided
    TargetPortfolioEvent    TARGET_     target quantity per contract plus reasons
                            PORTFOLIO
    RiskEvent               RISK        a risk rule triggered
    OrderEvent              ORDER       side, quantity, type, limit, time in force
    FillEvent               FILL        filled quantity, price, fee, slippage
    OrderStatusEvent        ORDER_      partial fill, reject, cancel, **and the reason**
                            STATUS
    ExpiryEvent             EXPIRY      settlement price, exercise or lapse
    ======================  ==========  =============================================

    ``ORDER_STATUS`` replaces what was called ``REJECT``. A rejection is one of its four
    cases and the narrow name lost the other three -- a partial fill is not a rejection
    and is the figure 4.2 asks to be reported.

    Two are **ours and not the document's**, named here as such rather than smuggled into
    the thirteen:

    - ``ACCOUNTING`` -- the processing order's penultimate stage. The document has it as a
      stage and not as an event; it is one here because the mark is what the order exists
      to put last, and a stage nothing announces cannot be checked.
    - ``STATE`` -- 0.2's ``get_state``/``set_state`` and 5.1's resume. A snapshot is a
      point in the stream rather than a side channel.
    """

    # ── the document's thirteen ──
    CORPORATE_ACTION = "corporate_action"
    CONTRACT_ADJUST = "contract_adjust"
    MARKET = "market"
    QUOTE = "quote"
    UNIVERSE = "universe"
    TIMER = "timer"
    SIGNAL = "signal"
    RISK = "risk"
    TARGET_PORTFOLIO = "target_portfolio"
    ORDER = "order"
    ORDER_STATUS = "order_status"
    FILL = "fill"
    EXPIRY = "expiry"
    # ── ours, for the reasons above ──
    STATE = "state"
    ACCOUNTING = "accounting"


#: Where each type sits in the document's order within one timestamp. Lower runs first.
#:
#: The gaps are deliberate: a type added later gets a number between two existing ones
#: without renumbering the rest, and renumbering is how an order like this comes to be
#: subtly different from the document it was copied from.
PHASE: Mapping[EventType, int] = {
    EventType.CORPORATE_ACTION: 10,    # splits and dividends, before anything is priced
    EventType.CONTRACT_ADJUST: 15,     # an OCC adjustment, in the same first stage
    EventType.MARKET: 20,              # the underlying's bar
    EventType.QUOTE: 25,               # the option chain for the day
    EventType.UNIVERSE: 27,            # the tradable set, after the data it is drawn from
    EventType.TIMER: 30,               # scheduled work, before decisions are made
    EventType.SIGNAL: 40,              # what the components decided
    EventType.RISK: 45,                # the portfolio's own brake, after the signals
    EventType.TARGET_PORTFOLIO: 50,    # what the book should hold
    EventType.ORDER: 60,               # the gap between target and actual
    EventType.ORDER_STATUS: 65,        # partial fill, reject or cancel, with the reason
    EventType.FILL: 70,                # what actually happened
    EventType.EXPIRY: 80,              # settlement
    EventType.ACCOUNTING: 85,          # the mark, after everything that moves the book
    EventType.STATE: 90,               # the end-of-day snapshot, last
}

def sort_key(event: "Event") -> tuple:
    """The document's three sort keys: timestamp, processing phase, sequence.

    Exposed as a function because the ordering is a property of the **stream**, not of
    this object: a stream written to disk and read back has to be re-sorted, and sorting
    it by anything else is a different run. The figure on page 2 names all three.
    """
    return (event.when, event.phase, event.id)


#: The document's order, in its own words (page 11).
#:
#: **Nine stages, not eight.** The transcription dropped two: "contract adjustments" from
#: the first stage, and "end-of-day snapshot" from the last. Both matter to the table
#: above -- an OCC adjustment changes a contract's multiplier and strike, so it belongs
#: with the corporate actions and before anything is priced; and the snapshot is what a
#: resumed run restores from, so it comes after the mark rather than with it.
ORDER_AS_WRITTEN = ("corporate actions and contract adjustments -> market data -> "
                    "signals -> portfolio and risk -> orders -> fills -> expiry "
                    "settlement -> accounting -> end-of-day snapshot")


@dataclass(frozen=True, slots=True)
class Event:
    """One thing that happened, at one timestamp, published by one layer.

    ``caused_by`` is what makes requirement 0.6 possible: a fill names the order it came
    from, the order names the target, the target names the signal, the signal names the
    component. The chain is only as good as the discipline of setting it, so
    :meth:`EventBus.trace` reports where it breaks rather than returning a short chain as
    though it were complete.
    """

    type: EventType
    when: date
    #: Which layer published it, as a short name. Not the module path: the point of a bus
    #: is that a subscriber does not know what is on the other side of it.
    source: str
    #: The event's own identifier, unique within a run. Assigned by the bus.
    id: int = 0
    #: The id of the event this one is a consequence of, or 0 for a root.
    caused_by: int = 0
    #: The trade this concerns, where there is one.
    trade_id: int | None = None
    #: ``slot/name@version`` for an event a component produced. The reason this is not
    #: merely ``source``: two components can fill the same slot, and attribution that
    #: cannot tell them apart makes a variant comparison meaningless.
    component_id: str = ""
    #: Why. A sentence, not a code: it is read by a person deciding whether a run is
    #: worth anything.
    reason: str = ""
    #: The event's own data. A mapping rather than a subclass per type, deliberately --
    #: see the module docstring in ``core/actions.py`` for the same argument: thirteen
    #: dataclasses with one field each is thirteen places for a field name to drift.
    data: Mapping[str, Any] = field(default_factory=dict)

    @property
    def phase(self) -> int:
        return PHASE[self.type]

    def describe(self) -> str:
        who = f"{self.source}"
        if self.component_id:
            who = f"{self.source}[{self.component_id}]"
        trade = f" trade {self.trade_id}" if self.trade_id is not None else ""
        return f"#{self.id} {self.when} {self.type.value} from {who}{trade}: {self.reason}"


@dataclass(frozen=True, slots=True)
class Trace:
    """A causal chain, and what is missing from it.

    The reason this is a type rather than a list: a trace that stops early looks exactly
    like a trace that is complete, and the whole value of requirement 0.6 is being able
    to tell a fill nobody can explain from one anybody can.
    """

    #: Effect first, cause last -- the order someone asks the question in ("where did
    #: this fill come from").
    links: tuple[Event, ...]

    @property
    def types(self) -> tuple[EventType, ...]:
        return tuple(e.type for e in self.links)

    @property
    def component(self) -> str:
        """The component the chain reaches, or "" if it does not reach one."""
        for event in self.links:
            if event.component_id:
                return event.component_id
        return ""

    def missing(self) -> tuple[EventType, ...]:
        """Which of the document's four hops this chain does not contain.

        Only asked of a chain that starts at a fill: the hops below a signal are not
        expected above it, and reporting them missing for a signal's own trace would
        make every trace look broken.
        """
        if not self.links or self.links[0].type is not EventType.FILL:
            return ()
        found = set(self.types)
        return tuple(t for t in EventBus.CHAIN if t not in found)

    @property
    def complete(self) -> bool:
        """A fill that reaches a component through the target and the order."""
        return not self.missing() and bool(self.component)

    def describe(self) -> str:
        lines = [e.describe() for e in self.links]
        gaps = self.missing()
        if gaps:
            lines.append(f"  ** the chain is missing {[t.value for t in gaps]}: this "
                         f"fill cannot be traced to a decision **")
        elif not self.component:
            lines.append("  ** no event in this chain names a component, so the fill "
                         "cannot be attributed to a slot **")
        return "\n".join(lines)


class EventBus:
    """Publish, subscribe, and drain one timestamp in the document's order.

    Deliberately small. A bus that also scheduled, retried or filtered would be a second
    engine, and the point of this one is that the engine's order is visible in one table
    rather than distributed through the code that happens to call what.
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[tuple[str, Callable[[Event], None]]]] = {}
        self._pending: list[Event] = []
        self._history: list[Event] = []
        self._next_id = 1
        self._draining = False
        self._last_drained: date | None = None

    # ── subscribing ───────────────────────────────────────
    def subscribe(self, type_: EventType, handler: Callable[[Event], None], *,
                  name: str = "") -> None:
        """Register a handler. ``name`` is for the error message when one raises.

        A handler is not allowed to be registered twice for the same type under the same
        name: a double registration makes every event in that type happen twice, which
        on a fill means a double position and on a signal means a double trade -- and it
        produces no error of its own anywhere.
        """
        who = name or getattr(handler, "__qualname__", repr(handler))
        existing = self._handlers.setdefault(type_, [])
        if any(n == who for n, _ in existing):
            raise StartupError(
                f"{who!r} is already subscribed to {type_.value}. A handler registered "
                f"twice runs twice on every event, which on a fill is a double position "
                f"and raises nothing of its own"
            )
        existing.append((who, handler))

    def subscribers(self, type_: EventType) -> tuple[str, ...]:
        return tuple(n for n, _ in self._handlers.get(type_, ()))

    # ── publishing ────────────────────────────────────────
    def publish(self, event: Event) -> Event:
        """Queue an event for this timestamp's drain. Returns it with its id set.

        Nothing is delivered here. Delivering on publish would run handlers in
        publication order, which is the dependence on who-ran-first that ``PHASE``
        exists to remove.
        """
        if event.id:
            raise InvariantError(
                f"event #{event.id} has already been published once. Publishing it again "
                f"would put the same thing in the history twice with one id, and a trace "
                f"through it would fork"
            )
        if event.caused_by and event.caused_by >= self._next_id:
            raise InvariantError(
                f"{event.type.value} says it was caused by #{event.caused_by}, which has "
                f"not been published yet. An effect cannot precede its cause, and a trace "
                f"built from this would read backwards"
            )
        stamped = Event(
            type=event.type, when=event.when, source=event.source, id=self._next_id,
            caused_by=event.caused_by, trade_id=event.trade_id,
            component_id=event.component_id, reason=event.reason, data=event.data,
        )
        self._next_id += 1
        self._pending.append(stamped)
        return stamped

    def emit(self, type_: EventType, when: date, source: str, **kwargs: Any) -> Event:
        """``publish`` without writing ``Event(...)`` out, which is most call sites."""
        return self.publish(Event(type=type_, when=when, source=source, **kwargs))

    # ── draining ──────────────────────────────────────────
    def drain(self, when: date) -> tuple[Event, ...]:
        """Deliver everything queued for ``when``, in the document's order.

        A handler may publish while being delivered to -- a signal handler producing an
        order is the ordinary case -- and the new event joins this drain if its phase has
        not passed, which is what makes a single timestamp able to carry a decision
        through to its fill. One that arrives **late for its phase** is the interesting
        case and it raises: an order published while fills are being delivered would be
        filled on the next timestamp with nothing saying it slipped a day.
        """
        if self._draining:
            raise InvariantError(
                "drain() was called while already draining. A handler that drains the "
                "bus re-enters the order it is in the middle of, and the result then "
                "depends on how deep the nesting went"
            )
        if self._last_drained is not None and when < self._last_drained:
            raise InvariantError(
                f"a drain for {when} follows one for {self._last_drained}. Time does not "
                f"run backwards in a backtest, and an event delivered out of order is "
                f"look-ahead with no trace of itself"
            )

        self._draining = True
        delivered: list[Event] = []
        try:
            phase = -1
            while True:
                due = [e for e in self._pending if e.when == when]
                ready = [e for e in due if e.phase >= phase]
                if not ready:
                    break
                phase = min(e.phase for e in ready)
                # **(timestamp, phase, sequence)** -- the figure's three sort keys.
                #
                # Within a phase this used to be publication order, held by the list
                # rather than written on the event. That is the same order while the
                # stream lives in this list and stops being the same order the moment
                # one is persisted and read back, when it would depend on the storage.
                # `id` is the sequence: assigned once, at publish, and monotonic.
                batch = sorted((e for e in ready if e.phase == phase),
                               key=lambda e: e.id)
                for event in batch:
                    self._pending.remove(event)
                    self._history.append(event)
                    delivered.append(event)
                    self._deliver(event)
                late = [e for e in self._pending
                        if e.when == when and e.phase < phase]
                if late:
                    first = late[0]
                    raise InvariantError(
                        f"{first.source} published a {first.type.value} event for {when} "
                        f"while phase {phase} was being delivered, and "
                        f"{first.type.value} belongs to phase {first.phase}.\n\n"
                        f"The order within a timestamp is {ORDER_AS_WRITTEN}. An event "
                        f"published after its phase has passed would be delivered on the "
                        f"next timestamp instead, a day late, with nothing saying so"
                    )
                phase += 1
        finally:
            self._draining = False
            self._last_drained = when
        return tuple(delivered)

    def _deliver(self, event: Event) -> None:
        for name, handler in self._handlers.get(event.type, ()):
            try:
                handler(event)
            except Exception as exc:
                raise InvariantError(
                    f"the {event.type.value} handler {name!r} raised on "
                    f"{event.describe()}: {type(exc).__name__}: {exc}"
                ) from exc

    # ── reading afterwards ────────────────────────────────
    @property
    def history(self) -> tuple[Event, ...]:
        return tuple(self._history)

    def pending(self) -> tuple[Event, ...]:
        """Anything queued and not delivered. Should be empty at the end of a run."""
        return tuple(self._pending)

    def of_type(self, type_: EventType) -> tuple[Event, ...]:
        return tuple(e for e in self._history if e.type is type_)

    # ── carrying a stream across a checkpoint ─────────────
    def resume(self, history: Sequence[Event], next_id: int) -> None:
        """Restore a stream so a continued run's ids follow on from the first part's.

        An order still in flight across a checkpoint carries the id of the ORDER event it
        was published as. A resumed run with a fresh bus hands out ids from 1, so the
        carried order points at an event that has not happened -- which this refuses, and
        the refusal is why the gap was found rather than producing a trace that reads
        backwards.

        Only into an empty bus: merging two streams would put two runs' causal chains in
        one history with nothing in a trace saying which run a link came from.
        """
        if self._history or self._pending:
            raise InvariantError(
                f"this bus already holds {len(self._history)} event(s), and a resumed "
                f"stream is being loaded into it. Two runs' chains in one history cannot "
                f"be told apart by a trace"
            )
        if next_id <= len(history):
            raise StartupError(
                f"the next id is {next_id} and {len(history)} events are being restored, "
                f"so the next event would reuse an id that is already in the stream"
            )
        self._history = list(history)
        self._next_id = next_id

    #: The chain requirement 0.6 asks for, in the order a reader follows it: a fill back
    #: through the order, the target, the signal, to the component that decided.
    CHAIN = (EventType.FILL, EventType.ORDER, EventType.TARGET_PORTFOLIO,
             EventType.SIGNAL)

    def trace(self, event_id: int) -> "Trace":
        """Follow ``caused_by`` back to a root, and say where the chain breaks.

        Requirement 0.6 is "a fill traced back through order -> target -> signal ->
        component, with the data values used", and the audit's note on it was that two of
        the five links did not exist. They exist now, which creates a new way to be wrong:
        a chain that is **short** because a link was never set, returned as though it were
        complete.

        So this does not return a list. It returns a :class:`Trace` that knows which of
        the four hops it found and which it did not, and whose ``complete`` is False when
        a fill cannot be followed to a component. A caller that wants the list can have
        it; a caller that prints the trace prints the gap with it.
        """
        by_id = {e.id: e for e in self._history}
        if event_id not in by_id:
            raise StartupError(
                f"no event #{event_id} in this run's history, so there is nothing to "
                f"trace. Ids are per run and are not stable across runs"
            )
        links: list[Event] = []
        seen: set[int] = set()
        cursor = by_id[event_id]
        while True:
            links.append(cursor)
            if not cursor.caused_by:
                break
            if cursor.caused_by in seen:
                raise InvariantError(
                    f"the causal chain from #{event_id} loops at #{cursor.caused_by}. An "
                    f"event cannot be its own cause, and a loop here would make `trace` "
                    f"run forever rather than return something wrong"
                )
            seen.add(cursor.caused_by)
            parent = by_id.get(cursor.caused_by)
            if parent is None:
                break
            cursor = parent
        return Trace(links=tuple(links))

    def __len__(self) -> int:
        return len(self._history)

    def __iter__(self) -> Iterator[Event]:
        return iter(self._history)
