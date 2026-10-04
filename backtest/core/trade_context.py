"""The trade context, partitioned by slot.

## What the document asks for

"Each slot writes only to its own namespace in the trade context (setup, entry,
take_profit, stop_loss, headwind, hedge), and a candidate context is copied in when the
trade opens."

## What was there, and why a flat dict is not enough

The context has been a flat snapshot of **market state** -- the contract's IV and delta,
the underlying's signals -- built once per trade at entry and once at exit. Every value is
a ``Maybe``, nothing overwrites anything, and it works.

It also has no room for the thing the document is actually asking about: what each slot
**decided**, and on what. The moment slots start recording that, a flat dict has two
failures waiting in it, and neither announces itself:

- **one slot overwrites another's value.** Two slots naturally want the same key names --
  ``level``, ``distance``, ``reason``, ``threshold`` -- and the last writer wins silently.
  The stop's level then reads as the target's and every grouped analysis that cuts on it
  is cutting on the wrong number.
- **a reader cannot say which slot produced a field.** This is the question attribution
  rests on, and it has been got wrong here once already: time exits were credited to the
  stop-loss slot, because nothing structural tied a recorded fact to the slot that
  produced it.

A prefix convention -- ``stop_loss_level`` -- fixes the first and not the second, and
holds only while everyone remembers it. Namespaces are the same idea enforced by the
thing that hands out the write access.

## How it is enforced

A slot does not get the context. It gets a :class:`SlotWriter` **bound to its own name**,
whose ``write`` has nowhere else to put anything. There is no path from a component to
another slot's namespace, so the convention is not a convention.

## The candidate context

Copied in when the trade opens, and copied rather than referenced. A candidate outlives
the trades it produces -- it arms again later, with different values -- and a trade
holding a live reference to it would silently acquire the state of a later arming. The
record of what the candidate saw **when this trade opened** is the thing worth keeping,
and it is only worth keeping if it cannot change afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from backtest.core.errors import InvariantError, StartupError


def _slot_names() -> frozenset[str]:
    """The slots that may own a namespace, from the one place slots are defined.

    ``core.component.SLOTS`` rather than a list written out again here, for the reason
    given in that module: a second hand-written copy of the same thing drifts, and the
    copy in this file would drift towards whatever the document's sentence happened to
    name when it was transcribed -- the document lists six, the registry has more.
    """
    from backtest.core.component import SLOTS

    return frozenset(SLOTS)


@dataclass(frozen=True, slots=True)
class SlotWriter:
    """One slot's write access to the context, and to nothing else.

    Held by a component for the duration of a decision. ``write`` is the only way into a
    namespace, and this object knows which namespace it is for -- so "a slot writes only
    to its own" is a property of the type rather than a rule someone follows.
    """

    slot: str
    _into: dict[str, Any]

    def write(self, key: str, value: Any) -> None:
        """Record one fact this slot decided or used.

        Keys are this slot's own, so ``level`` here and ``level`` in another slot are
        different facts and both survive.
        """
        if not key or key.startswith("_"):
            raise StartupError(
                f"{self.slot!r} tried to write the key {key!r}. A context key is read by "
                f"a person grouping results, so it has a name"
            )
        self._into[key] = value

    def update(self, values: Mapping[str, Any]) -> None:
        for key, value in values.items():
            self.write(key, value)

    def read(self) -> Mapping[str, Any]:
        """What this slot has written so far. Its own, which is all it can see."""
        return MappingProxyType(dict(self._into))


@dataclass(frozen=True, slots=True)
class TradeContext:
    """The market state the decisions saw, plus what each slot recorded.

    ``market`` is what the context has always been and is unchanged: a flat mapping of
    ``Maybe`` values, built from the chain and the signal layer. Flat deliberately --
    nobody writes to it, it is the same for every slot on a given day, and partitioning a
    read-only snapshot by who read it would answer a question nobody asks.
    """

    market: Mapping[str, Any]
    #: slot -> what that slot wrote. Only the slot's own :class:`SlotWriter` can add to it.
    _slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: What the candidate had recorded when this trade opened. Empty until a trade opens
    #: from a candidate -- see the module docstring on why it is a copy.
    candidate: Mapping[str, Any] = field(default_factory=dict)

    # ── writing ───────────────────────────────────────────
    def writer(self, slot: str) -> SlotWriter:
        """The write handle for one slot.

        An unknown slot is refused rather than given a namespace of its own. A typo
        would otherwise create a namespace nobody reads, which is indistinguishable from
        a slot that recorded nothing -- and the second is a legitimate state, so the two
        cannot be told apart afterwards.
        """
        if slot not in _slot_names():
            known = ", ".join(sorted(_slot_names()))
            raise StartupError(
                f"{slot!r} is not a slot, so it has no namespace in the trade context. "
                f"One of: {known}"
            )
        return SlotWriter(slot=slot, _into=self._slots.setdefault(slot, {}))

    # ── reading ───────────────────────────────────────────
    def of(self, slot: str) -> Mapping[str, Any]:
        """What one slot recorded. Empty when it recorded nothing."""
        return MappingProxyType(dict(self._slots.get(slot, {})))

    def slots(self) -> tuple[str, ...]:
        """The slots that recorded anything, in slot order rather than write order.

        Write order would make this depend on which slot the engine happened to run
        first, and a reader comparing two runs would see a difference that is not one.
        """
        from backtest.core.component import SLOTS

        return tuple(s for s in SLOTS if self._slots.get(s))

    def wrote(self, key: str) -> tuple[str, ...]:
        """Which slots recorded this key. The question attribution rests on.

        Returns every slot rather than one: two slots recording ``level`` is **correct**
        under namespaces, and a function that answered with one of them would reintroduce
        the ambiguity the namespaces removed.
        """
        return tuple(s for s in self.slots() if key in self._slots[s])

    def get(self, key: str, default: Any = None) -> Any:
        """Market state by key, which is what every existing reader wants.

        Slot namespaces are **not** searched. A lookup that fell through to them would
        make a market key and a slot key interchangeable at the call site, and the whole
        point of the partition is that they are not.
        """
        return self.market.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.market[key]

    def __contains__(self, key: str) -> bool:
        return key in self.market

    def __iter__(self) -> Iterator[str]:
        return iter(self.market)

    def __len__(self) -> int:
        return len(self.market)

    def keys(self):
        return self.market.keys()

    def items(self):
        return self.market.items()

    def values(self):
        return self.market.values()

    # ── carrying it out ───────────────────────────────────
    def flat(self) -> dict[str, Any]:
        """One mapping for the trade table, with each slot's keys prefixed by its slot.

        The prefix exists **here**, where it is generated, rather than at the call sites
        that write -- which is the difference between a convention and a format. A reader
        of the table can still say which slot produced a column, and no writer had to
        remember to say so.
        """
        out = dict(self.market)
        for slot in self.slots():
            for key, value in self._slots[slot].items():
                out[f"{slot}.{key}"] = value
        for key, value in self.candidate.items():
            out[f"candidate.{key}"] = value
        return out

    def opened_from(self, candidate: Mapping[str, Any]) -> "TradeContext":
        """This context with a candidate's record copied in, as the document asks.

        A new context rather than a mutation: the candidate's values at the moment the
        trade opened are a fact about that moment, and a structure that can be updated
        later is a structure where that fact can be lost.
        """
        if self.candidate:
            raise InvariantError(
                "this trade context already has a candidate record. Copying a second one "
                "in would overwrite what the candidate saw when the trade opened, which "
                "is the one moment the copy exists to preserve"
            )
        return TradeContext(market=self.market, _slots=self._slots,
                            candidate=dict(candidate))


def market_only(values: Mapping[str, Any]) -> TradeContext:
    """A context with market state and no slot records.

    The shape every existing caller produced before namespaces existed, so the engines'
    construction of the context did not have to change in the same step as this landed.
    """
    return TradeContext(market=dict(values))
