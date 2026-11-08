"""The component protocol: one slot, several named forms, swappable by name.

## What this is for

The first of the two uses this platform exists for is **settling the seven components**.
Settling one means comparing the candidate forms of it on equal footing -- a stop measured
on the premium against a stop measured on the underlying, say, which the architecture
document lists by name as a strategy choice to research.

Until now a slot had exactly one form and only its parameters could be varied. A parameter
sweep over a premium stop answers "which premium stop", never "should the stop be on the
premium at all". So the comparison the first use needs could not be run.

## What a component is

A named, versioned, parameterised object that decides one thing and returns a decision. It
reads the trade's context and nothing else; it never touches the ledger, and it never calls
another slot -- both of which are what make per-slot ablation mean anything.

``name`` and ``version`` travel into the run fingerprint, so a result produced by version 1
of a stop cannot be mistaken for one produced by version 2. A version that does not change
when the logic changes is worse than no version, so :func:`register` refuses to re-register
a name at the same version with different code.

## What this is not, and why

The architecture document's protocol has ``on_event(event, ctx) -> list[Event]``. There is
no event bus in this system -- layers call each other directly -- so there is nothing for
such a method to receive, and defining an empty one would be a shape that reads as built
and does nothing. What is here instead is the part the engine actually calls: ``decide``.
When the bus lands, ``decide`` is what ``on_event`` will dispatch to.

``get_state`` / ``set_state`` are here, on :class:`Bound`, and they were built when the
first form needed them: a trailing stop, which remembers its high-water mark. They were
deliberately absent before that, because an empty pair of methods reads as built and does
nothing -- and a component that quietly kept state would have made a resumed run differ
from a continuous one with nothing raising.

**A stateful form is per trade, not per run.** Two open trades under one trailing stop
have two different high-water marks, and a single state on the component would make them
share one -- the second trade's stop moving because the first one's did. So the engine
asks the component for a fresh state per trade and hands the right one back on each call;
see :meth:`Bound.state_for`.

An earlier version of this docstring claimed both methods were "here and are real" when
they were not, and the next version said they were absent after they had been built. Both
are recorded rather than deleted: a file that describes machinery it does not have is the
precise failure this project refuses, the mirror of it sends the next reader to build what
exists, and both are easier to make in a docstring than in code. ``tools/check_doc_claims.py``
now holds the project's documents to the code; nothing holds a docstring, so this one says
what it is.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from backtest.core.errors import InvariantError, StartupError

def _slot_names() -> tuple[str, ...]:
    """The slots a component may fill, from the one place they are defined.

    Taken from :data:`backtest.core.slots.COMPONENTS` rather than written out again here.
    A second hand-written list of the same thing drifts -- and this one already had, by
    one entry, within an hour of being written.

    ``review`` is added: it is a hook rather than one of the document's seven, and it is
    swappable in the same way, so a component may fill it.
    """
    from backtest.core.slots import COMPONENTS

    # ``review`` is a hook rather than one of the document's seven, and ``time_exit`` is
    # the holding cap and the days-to-expiry floor -- not a document slot either, and a
    # distinct owner of exits that would otherwise be credited to the stop. Both are
    # swappable in the same way, so a component may fill them.
    return tuple(c.key for c in COMPONENTS) + ("review", "time_exit")


SLOTS: tuple[str, ...] = _slot_names()


@dataclass(frozen=True, slots=True)
class Param:
    """One parameter a component takes: its type, its range, and what it means.

    The schema is not decoration. It is what lets the interface build a form for a
    component it has never seen, what validates a configuration before a run starts rather
    than halfway through, and what tells a sweep which values are even legal.
    """

    name: str
    kind: str                      # "float" / "int" / "bool"
    #: What this parameter decides, in one line, for a person reading the form.
    means: str
    lo: float | None = None
    hi: float | None = None
    #: Whether the researcher must supply it. **There are no defaults here**: a component
    #: parameter is a strategy choice, and the platform does not pick one. See
    #: ``tools/check_defaults.py``.
    required: bool = True

    def validate(self, value: Any) -> float | int | bool:
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise StartupError(f"{self.name} must be true or false, got {value!r}")
            return value
        try:
            out = int(value) if self.kind == "int" else float(value)
        except (TypeError, ValueError):
            raise StartupError(
                f"{self.name} must be a {self.kind}, got {value!r}"
            ) from None
        if self.lo is not None and out < self.lo:
            raise StartupError(f"{self.name} must be at least {self.lo}, got {out}")
        if self.hi is not None and out > self.hi:
            raise StartupError(f"{self.name} must be at most {self.hi}, got {out}")
        return out


@runtime_checkable
class Decider(Protocol):
    """What a component's logic has to be: a function of the context and its parameters.

    ``ctx`` is a :class:`~backtest.signals.lifecycle.TradeContext`-shaped mapping of what
    the trade looks like today. The return is the component's own kind -- an exit
    component returns ``True`` to fire -- so the protocol stays narrow enough to be
    checked rather than general enough to be anything.
    """

    def __call__(self, ctx: Mapping[str, Any], **params: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class Variant:
    """One registered form of one slot."""

    slot: str
    name: str
    version: int
    #: One line: what this form decides, and how it differs from the others in its slot.
    #: Shown wherever the variant is offered, because "stop_loss/underlying v1" tells a
    #: reader nothing on its own.
    describes: str
    params: tuple[Param, ...]
    decide: Decider
    #: Whether this form keeps state across days. A stateful component cannot be used by
    #: an engine that does not step day by day, and declaring it is what lets that be
    #: refused rather than silently producing a different answer.
    stateful: bool = False
    #: ``f(ctx) -> dict``: the state this form starts a **trade** with, called once when
    #: the trade opens. Required when ``stateful``, and refused when not -- see
    #: :func:`register` for why each half is refused.
    initial_state: "Callable[[Mapping[str, Any]], dict] | None" = None

    @property
    def ref(self) -> str:
        """``slot/name@version`` -- what goes into the run fingerprint and the trade log."""
        return f"{self.slot}/{self.name}@{self.version}"

    def bind(self, params: Mapping[str, Any]) -> "Bound":
        """Validate a parameter set against the schema and return a callable component.

        Unknown parameters raise. A misspelled parameter that was quietly ignored would
        leave the component running on something other than what the configuration says,
        and the configuration is what a person reads back when asking what the run did.
        """
        given = dict(params)
        unknown = sorted(set(given) - {p.name for p in self.params})
        if unknown:
            raise StartupError(
                f"{self.ref} got {', '.join(unknown)}, which it does not take. It takes "
                f"{', '.join(p.name for p in self.params) or '(nothing)'}"
            )
        out: dict[str, Any] = {}
        for p in self.params:
            if p.name not in given:
                if p.required:
                    raise StartupError(
                        f"{self.ref} needs {p.name}: {p.means}. It is a strategy choice "
                        f"and the platform does not pick one"
                    )
                continue
            out[p.name] = p.validate(given[p.name])
        return Bound(variant=self, params=out)


@dataclass(slots=True)
class Bound:
    """A variant with its parameters fixed: the thing the engine actually calls."""

    variant: Variant
    params: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return self.variant.ref

    @property
    def slot(self) -> str:
        return self.variant.slot

    #: trade_id -> that trade's state. Per trade and not per component: two open trades
    #: under one trailing stop have two high-water marks, and sharing one would move the
    #: second trade's stop because the first one's moved.
    #:
    #: Not a frozen field, because this is the one part of a component that is *about*
    #: changing. Everything else here is fixed at bind time.
    _state: dict[int, dict] = field(default_factory=dict, repr=False)

    def __call__(self, ctx: Mapping[str, Any]) -> Any:
        """Decide. A stateful form is given its trade's state as ``ctx["state"]``.

        The state is a mutable mapping the form may write to, which is how a trailing
        stop raises its high-water mark. Handing it the dict rather than taking a returned
        one is deliberate: a form that forgot to return its state would silently lose it,
        and losing a trailing stop's high-water mark turns it into a fixed stop -- a
        plausible result from a different strategy.
        """
        if not self.variant.stateful:
            return self.variant.decide(ctx, **self.params)
        trade_id = ctx.get("trade_id")
        if trade_id is None:
            raise InvariantError(
                f"{self.ref} is stateful and was called with no trade_id in the context, "
                f"so its state cannot be told from another trade's"
            )
        return self.variant.decide(
            {**ctx, "state": self.state_for(trade_id, ctx)}, **self.params)

    def state_for(self, trade_id: int, ctx: Mapping[str, Any]) -> dict:
        """This trade's state, created from ``initial_state`` the first time."""
        if trade_id not in self._state:
            self._state[trade_id] = dict(self.variant.initial_state(ctx))
        return self._state[trade_id]

    def get_state(self) -> dict[str, Any]:
        """Everything this component is carrying, for a snapshot.

        Keys are strings because a snapshot is written to a file: a ``dict`` keyed by int
        round-trips through JSON as a ``dict`` keyed by string, and a resumed run would
        then find no state for any trade and start every stop again from its entry --
        which looks like a run, and is a different one.
        """
        return {str(trade): dict(state) for trade, state in self._state.items()}

    def set_state(self, state: Mapping[str, Any]) -> None:
        """Restore what :meth:`get_state` returned.

        Replaces rather than merges. A merge would let a resumed run carry state from two
        different runs at once, and nothing downstream could tell.
        """
        self._state.clear()
        for trade, held in state.items():
            self._state[int(trade)] = dict(held)

    def forget(self, trade_id: int) -> None:
        """Drop a closed trade's state.

        Without this a long run accumulates one entry per trade for ever, and a snapshot
        of it grows without bound -- and a trade_id reused by a later run would inherit a
        closed trade's high-water mark.
        """
        self._state.pop(trade_id, None)

    def describe(self) -> str:
        shown = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.ref}({shown})" if shown else self.ref


#: Everything registered, by ``slot/name``.
#:
#: **Who fills it.** This module defines the protocol and nothing else; the shipped forms
#: live under ``backtest.signals.<slot>``, which is Layer 2, and Layer 0 importing Layer 2
#: would invert the dependency direction the whole architecture rests on (the guard refuses
#: it, which is how this arrangement was found). So the package's own ``__init__`` imports
#: them once, at Layer 7 where composition belongs, and a test asserts the registry is
#: populated after ``import backtest`` -- otherwise "registered" would depend on whether
#: some other module happened to be imported first.
_REGISTRY: dict[str, Variant] = {}


def register(variant: Variant) -> Variant:
    """Add a variant, refusing anything that would make two runs indistinguishable.

    Two guards here, both about the fingerprint. A name re-registered at the same version
    with different code would make yesterday's result and today's share a reference; a
    name re-registered with the *same* code is a harmless reload (a module imported twice)
    and is allowed.
    """
    if variant.slot not in SLOTS:
        raise StartupError(
            f"{variant.slot!r} is not a slot; one of {', '.join(SLOTS)}"
        )
    if variant.stateful and variant.initial_state is None:
        raise StartupError(
            f"{variant.ref} declares itself stateful and supplies no `initial_state`. "
            f"The state a form starts a trade with is part of the form -- a trailing "
            f"stop's high-water mark starts at the trade's entry, and not at whatever "
            f"price the form happens to be shown first -- and a platform-chosen default "
            f"would be the platform deciding a research parameter.\n\n"
            f"Give `initial_state=lambda ctx: {{...}}`, which is called once per trade."
        )
    if variant.initial_state is not None and not variant.stateful:
        raise StartupError(
            f"{variant.ref} supplies an `initial_state` and does not declare itself "
            f"stateful. The flag is what the engine reads to decide whether to keep and "
            f"snapshot state at all, so a form with state and no flag would have its "
            f"state discarded between days with nothing saying so"
        )
    key = f"{variant.slot}/{variant.name}"
    existing = _REGISTRY.get(key)
    if existing is not None:
        same_code = (existing.version == variant.version
                     and _source(existing.decide) == _source(variant.decide))
        if existing.version == variant.version and not same_code:
            raise StartupError(
                f"{variant.ref} is already registered with different logic. Raise the "
                f"version: a result produced by the old code and one produced by the new "
                f"would otherwise carry the same reference and look like the same run"
            )
        if same_code:
            return existing
    _REGISTRY[key] = variant
    return variant


def _source(fn: Callable) -> str:
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        # A callable with no retrievable source (a built-in, a lambda from an interactive
        # session). Treated as "cannot tell", which makes the re-registration check fall
        # through to raising rather than quietly allowing it.
        return repr(fn)


def get(slot: str, name: str) -> Variant:
    key = f"{slot}/{name}"
    found = _REGISTRY.get(key)
    if found is None:
        offered = sorted(v.name for v in _REGISTRY.values() if v.slot == slot)
        raise StartupError(
            f"there is no {name!r} for {slot}. Registered: "
            f"{', '.join(offered) if offered else '(none yet)'}"
        )
    return found


def variants(slot: str | None = None) -> tuple[Variant, ...]:
    """Everything registered, optionally for one slot, in a stable order."""
    found = [v for v in _REGISTRY.values() if slot is None or v.slot == slot]
    return tuple(sorted(found, key=lambda v: (v.slot, v.name, v.version)))


def build(spec: Mapping[str, Any]) -> Bound:
    """Turn ``{"variant": "...", "params": {...}}`` into a component.

    This is what a configuration file and the interface's form both go through, so a
    component configured from either side is validated the same way.
    """
    if "slot" not in spec or "variant" not in spec:
        raise StartupError(
            'a component spec needs "slot" and "variant", for example '
            '{"slot": "stop_loss", "variant": "underlying", "params": {"fraction": 0.03}}'
        )
    variant = get(str(spec["slot"]), str(spec["variant"]))
    # A version given -- which is what a written config file carries -- has to match.
    # Replaying against a later version would give a different result from a file that
    # looks unchanged, and a config file whose replay is not the same run is worse than
    # no config file.
    wanted = spec.get("version")
    if wanted is not None and int(wanted) != variant.version:
        raise StartupError(
            f"this config asks for {variant.slot}/{variant.name}@{wanted} and version "
            f"{variant.version} is what is registered. The form's logic changed, so this "
            f"code cannot reproduce that run. Check out the commit the run was made on, "
            f"or re-run under the current version knowing it is a different run"
        )
    return variant.bind(spec.get("params") or {})
