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

``get_state`` / ``set_state`` are here and are real: a component that keeps state across
days (a trailing stop remembering its high-water mark) has to be able to hand that state
over for a resumed run, and a component that silently keeps state without declaring it
would make a resumed run differ from a continuous one.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from backtest.core.errors import StartupError

#: The slots a component may fill. Taken from the architecture document's seven, plus the
#: daily review, which is a hook rather than a slot but is swappable in the same way.
#:
#: A slot not listed here cannot be registered. The list is short and explicit because a
#: typo in a slot name would otherwise create a slot nobody fills and nobody notices.
SLOTS: tuple[str, ...] = (
    "setup", "entry", "select", "take_profit", "stop_loss", "headwind", "position",
    "hedge", "review",
)


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


@dataclass(frozen=True, slots=True)
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

    def __call__(self, ctx: Mapping[str, Any]) -> Any:
        return self.variant.decide(ctx, **self.params)

    def describe(self) -> str:
        shown = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.ref}({shown})" if shown else self.ref


#: Everything registered, by ``slot/name``.
#:
#: **Who fills it.** This module defines the protocol and nothing else; the shipped forms
#: live in ``backtest.signals.variants``, which is Layer 2, and Layer 0 importing Layer 2
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
    return get(str(spec["slot"]), str(spec["variant"])).bind(spec.get("params") or {})
