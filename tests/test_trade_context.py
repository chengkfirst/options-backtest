"""The trade context, partitioned by slot — requirement S.namespaces.

## The two failures it exists for

Both arrive the moment slots start recording what they decided, and neither announces
itself:

- **one slot overwrites another's value.** Slots naturally want the same key names —
  `level`, `distance`, `reason`, `threshold` — and in a flat dict the last writer wins in
  silence. The stop's level then reads as the target's, and every grouped analysis cutting
  on it cuts on the wrong number.
- **a reader cannot say which slot produced a field.** This is the question attribution
  rests on, and this project got it wrong once: time exits were credited to the stop-loss
  slot, because nothing structural tied a recorded fact to the slot that produced it.

A prefix convention fixes the first and not the second, and holds only while everybody
remembers. The writer being **bound to its slot** is the same idea with no remembering in
it: a component has no path to another slot's namespace.
"""

from __future__ import annotations

import pytest

from backtest.core.errors import InvariantError, StartupError
from backtest.core.maybe import Maybe
from backtest.core.trade_context import TradeContext, market_only

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "0")


def _context() -> TradeContext:
    return market_only({"iv": Maybe.some(0.22), "delta": Maybe.some(0.30)})


class TestTwoSlotsCanHoldTheSameKey:
    """The first failure, which is the one a flat dict actually produces."""

    def test_both_values_survive(self):
        ctx = _context()
        ctx.writer("stop_loss").write("level", 0.5)
        ctx.writer("take_profit").write("level", 1.0)
        assert ctx.of("stop_loss")["level"] == 0.5
        assert ctx.of("take_profit")["level"] == 1.0

    def test_a_reader_can_say_which_slots_wrote_it(self):
        """Every slot, not one.

        Two slots recording `level` is **correct** under namespaces, so an answer of one
        slot would put back the ambiguity the partition removed.
        """
        ctx = _context()
        ctx.writer("stop_loss").write("level", 0.5)
        ctx.writer("take_profit").write("level", 1.0)
        assert set(ctx.wrote("level")) == {"stop_loss", "take_profit"}
        assert ctx.wrote("nothing_wrote_this") == ()

    def test_a_slot_sees_only_its_own(self):
        ctx = _context()
        ctx.writer("stop_loss").write("level", 0.5)
        ctx.writer("take_profit").write("level", 1.0)
        assert dict(ctx.writer("stop_loss").read()) == {"level": 0.5}

    def test_a_slot_cannot_reach_another_namespace(self):
        """The enforcement, stated as the absence of a path.

        `SlotWriter.write` takes a key and a value. There is no argument for which slot,
        so there is nothing to get wrong — which is what makes this a property of the
        type rather than a rule in a docstring.
        """
        writer = ctx = _context().writer("stop_loss")
        import inspect

        params = set(inspect.signature(writer.write).parameters)
        assert params == {"key", "value"}, (
            f"SlotWriter.write takes {sorted(params)}. A parameter naming the target "
            f"namespace is a way for a slot to write into another one"
        )


class TestTheMarketStateIsNotPartitioned:
    """Deliberately flat: nobody writes to it, and it is the same for every slot."""

    def test_reading_market_state_is_unchanged(self):
        ctx = _context()
        assert ctx["iv"].unwrap() == 0.22
        assert ctx.get("delta").unwrap() == 0.30
        assert ctx.get("absent") is None
        assert "iv" in ctx and len(ctx) == 2
        assert sorted(ctx.keys()) == ["delta", "iv"]

    def test_a_market_lookup_does_not_fall_through_to_a_slot(self):
        """Falling through would make a market key and a slot key interchangeable at the
        call site, which is exactly what the partition is for."""
        ctx = _context()
        ctx.writer("stop_loss").write("level", 0.5)
        assert ctx.get("level") is None
        with pytest.raises(KeyError):
            ctx["level"]


class TestTheCandidateRecord:
    """"A candidate context is copied in when the trade opens.\""""

    def test_it_is_copied_and_not_referenced(self):
        """A candidate outlives the trades it produces and arms again with different
        values. A trade holding a live reference would silently acquire the later state,
        and the fact worth keeping is what the candidate saw **when this trade opened**.
        """
        candidate = {"regime": "calm", "armed_on": "2023-01-03"}
        opened = _context().opened_from(candidate)
        candidate["regime"] = "stormy"
        assert opened.candidate["regime"] == "calm"

    def test_copying_a_second_one_in_is_refused(self):
        opened = _context().opened_from({"regime": "calm"})
        with pytest.raises(InvariantError) as caught:
            opened.opened_from({"regime": "stormy"})
        assert "already has a candidate record" in str(caught.value)

    def test_the_slot_records_survive_the_copy(self):
        ctx = _context()
        ctx.writer("stop_loss").write("level", 0.5)
        opened = ctx.opened_from({"regime": "calm"})
        assert opened.of("stop_loss")["level"] == 0.5


@kind("guard")
class TestWhatIsRefused:
    def test_an_unknown_slot_gets_no_namespace(self):
        """A typo would otherwise create a namespace nobody reads, which is
        indistinguishable from a slot that recorded nothing — and recording nothing is a
        legitimate state, so afterwards the two cannot be told apart."""
        with pytest.raises(StartupError) as caught:
            _context().writer("stop-loss")
        assert "is not a slot" in str(caught.value)

    def test_the_slot_list_comes_from_the_registry(self):
        """Not written out again here.

        The document's sentence names six slots and the registry has more. A second
        hand-written copy would drift towards whichever was transcribed.
        """
        from backtest.core.component import SLOTS
        from backtest.core.trade_context import _slot_names

        assert _slot_names() == frozenset(SLOTS)

    def test_a_nameless_key_is_refused(self):
        writer = _context().writer("stop_loss")
        for key in ("", "_hidden"):
            with pytest.raises(StartupError):
                writer.write(key, 1.0)

    def test_the_slot_order_is_the_registrys_not_the_write_order(self):
        """Write order would make this depend on which slot the engine ran first, and a
        reader comparing two runs would see a difference that is not one."""
        from backtest.core.component import SLOTS

        first, second = _context(), _context()
        first.writer("stop_loss").write("a", 1)
        first.writer("take_profit").write("a", 1)
        second.writer("take_profit").write("a", 1)
        second.writer("stop_loss").write("a", 1)
        assert first.slots() == second.slots()
        assert list(first.slots()) == [s for s in SLOTS if s in first.slots()]


class TestWhatTheTradeTableGets:
    def test_each_slot_key_is_prefixed_by_its_slot(self):
        """The prefix is generated here, where the structure knows the slot, rather than
        written by each caller — which is the difference between a format and a
        convention."""
        ctx = _context()
        ctx.writer("stop_loss").write("level", 0.5)
        ctx.writer("take_profit").write("level", 1.0)
        flat = ctx.opened_from({"regime": "calm"}).flat()
        assert flat["stop_loss.level"] == 0.5
        assert flat["take_profit.level"] == 1.0
        assert flat["candidate.regime"] == "calm"
        # Market state keeps its own names: existing readers are unchanged.
        assert flat["iv"].unwrap() == 0.22

    def test_a_context_with_no_slot_records_is_just_the_market(self):
        assert set(_context().flat()) == {"iv", "delta"}
