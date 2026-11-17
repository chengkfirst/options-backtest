"""The locked hold-out: the document's research protocol, rule one.

"Lock a final hold-out period at the start and use it once, at the end." Nothing enforced
it. A run could be scored on data already looked at, repeatedly, and neither the result nor
the record of it would say so.

It needs a mechanism rather than discipline for a reason sharper than most rules here:
looking at the hold-out does not fail, does not warn, and returns a number **better** than
the honest one. The incentive points the wrong way and the feedback is silent, which is the
combination nothing survives by intention.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest

from backtest import run
from backtest.core.errors import StartupError
from backtest.research.holdout import HoldOut, ReservedSymbol, check, touches

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("guard", "6")

BASE = dict(
    symbol="QQQ", start="2022-06-01", end="2023-12-31",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)
RESERVED = ("2023-07-01", "2023-12-31")


class TestThePeriodItself:
    def test_a_backwards_period_is_refused(self):
        with pytest.raises(StartupError, match="ends before it begins"):
            HoldOut(date(2023, 12, 31), date(2023, 1, 1))

    def test_an_overlap_is_the_overlapping_stretch(self):
        held = HoldOut(date(2023, 7, 1), date(2023, 12, 31))
        assert held.overlaps(date(2023, 1, 1), date(2023, 9, 30)) == (
            date(2023, 7, 1), date(2023, 9, 30))

    def test_no_overlap_is_none_not_an_empty_range(self):
        """An empty range would compare as touching nothing and still be a range."""
        held = HoldOut(date(2023, 7, 1), date(2023, 12, 31))
        assert held.overlaps(date(2022, 1, 1), date(2023, 6, 30)) is None

    def test_a_single_shared_day_counts(self):
        """The boundary, because a hold-out that begins the day a run ends has been
        touched."""
        held = HoldOut(date(2023, 7, 1), date(2023, 12, 31))
        assert held.overlaps(date(2023, 1, 1), date(2023, 7, 1)) is not None


class TestTheRefusal:
    @pytest.mark.req("6.4.holdout")
    def test_a_run_reaching_into_the_hold_out_is_refused(self):
        with pytest.raises(StartupError, match="inside the hold-out"):
            run(**BASE, hold_out=RESERVED)

    def test_the_refusal_says_what_overlapped_and_what_to_do(self):
        with pytest.raises(StartupError) as caught:
            run(**BASE, hold_out=RESERVED)
        message = str(caught.value)
        assert "2023-07-01" in message and "unlock" in message

    def test_a_run_that_stops_short_proceeds(self):
        result = run(**(BASE | {"end": "2023-06-30"}), hold_out=RESERVED)
        assert result.provenance()["hold_out"]["touched"] is False

    def test_unlocking_requires_a_reason_not_a_flag(self):
        """A boolean would be ticked without thought and would say nothing in the record
        six months later."""
        import inspect

        signature = inspect.signature(run)
        assert signature.parameters["unlock"].default == ""
        assert signature.parameters["unlock"].annotation in (str, "str")

    def test_an_unlocked_run_proceeds_and_records_the_reason(self):
        result = run(**BASE, hold_out=RESERVED, unlock="final evaluation")
        held = result.provenance()["hold_out"]
        assert held["touched"] is True
        assert held["reason"] == "final evaluation"


class TestThreeStatesNotTwo:
    """"No hold-out declared" and "declared and respected" are different answers.

    They would otherwise look identical to a reader comparing two results -- one of which
    was protected and one of which simply never had anything reserved.
    """

    def test_no_hold_out_says_nothing_was_reserved(self):
        note = run(**BASE).provenance()["hold_out"]
        assert note["hold_out"] is None
        assert "nothing was reserved" in note["note"]

    def test_respected_says_so(self):
        note = run(**(BASE | {"end": "2023-06-30"}),
                   hold_out=RESERVED).provenance()["hold_out"]
        assert note["hold_out"] == "2023-07-01 to 2023-12-31"
        assert note["touched"] is False

    def test_touched_says_every_later_result_is_in_sample(self):
        note = run(**BASE, hold_out=RESERVED,
                   unlock="final evaluation").provenance()["hold_out"]
        assert "in sample" in note["note"]


class TestCountingTheTouches:
    """A hold-out touched once is the protocol working. Touched five times is a validation
    period that has quietly become a training one."""

    def test_only_the_touches_are_counted(self):
        records = [
            {"run_id": "a", "hold_out": {"touched": False}},
            {"run_id": "b", "hold_out": {"touched": True, "reason": "final"}},
            {"run_id": "c", "hold_out": {}},
            {"run_id": "d", "hold_out": {"touched": True, "reason": "again"}},
        ]
        found = touches(records)
        assert [t["run_id"] for t in found] == ["b", "d"]
        assert [t["reason"] for t in found] == ["final", "again"]

    def test_a_record_with_no_hold_out_field_does_not_break_it(self):
        assert touches([{"run_id": "x"}]) == []


class TestWhatItDeliberatelyDoesNot:
    def test_it_does_not_choose_the_period(self):
        """How much to hold back and from when is a research-method choice -- decided
        before the research, not after seeing what helped."""
        note = check(None, start=date(2022, 1, 1), end=date(2023, 1, 1))
        assert note["hold_out"] is None

    def test_it_does_not_block_a_second_use(self):
        """A tool that silently refused would push the work somewhere it cannot be seen.
        What the record carries is how many times, and why."""
        first = run(**BASE, hold_out=RESERVED, unlock="final evaluation")
        second = run(**BASE, hold_out=RESERVED, unlock="again, after a fix")
        assert first.provenance()["hold_out"]["touched"]
        assert second.provenance()["hold_out"]["reason"] == "again, after a fix"


class TestTheGuardRecord:
    """A result from a commit whose guards were failing is a different kind of result.

    The suite is the reason to believe the engine computed what it claims. A figure
    produced while it was red has no such backing, and six months later nobody can tell
    the two apart from the number alone.
    """

    def _record(self, tmp_path, **fields):
        import json

        from backtest.core.run_id import GUARD_RECORD

        target = tmp_path / GUARD_RECORD
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(fields), encoding="utf-8")
        return tmp_path

    def test_no_record_is_its_own_state(self, tmp_path):
        """The ordinary state on a machine where the guards have not been run. Reporting
        it as either green or red would be a claim nobody made."""
        from backtest.core.run_id import guards_were_green

        assert guards_were_green(tmp_path)["state"] == "unrecorded"

    def test_a_record_for_other_code_is_stale_not_green(self, tmp_path):
        """A guard run on a different commit says nothing about this one, and treating it
        as green is how a red result acquires a green badge."""
        from backtest.core.run_id import guards_were_green

        self._record(tmp_path, code="abc123", passed=True, when="2026-01-01")
        verdict = guards_were_green(tmp_path)
        assert verdict["state"] == "stale"
        assert verdict["recorded_for"] == "abc123"

    def test_a_failing_run_is_recorded_as_red(self, tmp_path, monkeypatch):
        from backtest.core import run_id as module

        monkeypatch.setattr(module, "code_version", lambda root=None: "deadbee")
        self._record(tmp_path, code="deadbee", passed=False, when="2026-01-01")
        verdict = module.guards_were_green(tmp_path)
        assert verdict["state"] == "red"
        assert "no backing" in verdict["note"]

    def test_a_fast_run_does_not_count_as_green(self, tmp_path, monkeypatch):
        """It skipped the slow guards, so it cannot say the code is green -- and saying so
        would be the quietest way to lose the distinction.

        The writer is called and the record read back. The first version searched
        `tools/check.py` for a string, which is the same thing I had just replaced in
        test_layer0 -- it passes while the line sits in a branch that never runs.
        """
        import tools.check as check
        from backtest.core import run_id as module

        monkeypatch.setattr(check, "ROOT", tmp_path)
        monkeypatch.setattr(module, "code_version", lambda root=None: "deadbee")

        check._record(passed=True, partial=True)
        assert module.guards_were_green(tmp_path)["state"] == "red", (
            "a --fast run that passed was recorded as green, so a result produced on it "
            "would claim backing from guards that never ran"
        )

        check._record(passed=True, partial=False)
        assert module.guards_were_green(tmp_path)["state"] == "green"

    def test_a_run_reports_which_state_it_was_in(self):
        """It belongs in the manifest, beside the commit, not in a log nobody reads."""
        result = run(**(BASE | {"end": "2023-03-31"}))
        assert "guards" in result.manifest()
        assert result.manifest()["guards"]["state"] in (
            "green", "red", "stale", "unrecorded")


class TestAReservedSymbol:
    """The other half of the hold-out: an underlying no run may touch until the end.

    Decision 0001 reserves SPY and records that `HoldOut` could not express it -- it takes
    two dates and has no concept of a symbol. The mechanism now exists, which is a
    narrower claim than the reservation being enforced: enforcement depends on the caller
    passing `hold_out_symbols=`, and `run(symbol="SPY")` without it still proceeds.
    Nothing in this repository declares SPY reserved to every caller.
    """

    SHORT = BASE | {"end": "2023-03-31"}

    @pytest.mark.req("6.4.holdout")
    def test_a_run_on_the_reserved_symbol_is_refused(self):
        with pytest.raises(StartupError, match="SPY, which is reserved"):
            run(**(self.SHORT | {"symbol": "SPY"}), hold_out_symbols=("SPY",))

    def test_the_refusal_says_what_was_reserved_and_what_to_do(self):
        with pytest.raises(StartupError) as caught:
            run(**(self.SHORT | {"symbol": "SPY"}), hold_out_symbols=("SPY",))
        message = str(caught.value)
        assert "reserved" in message and "unlock" in message

    def test_a_run_on_another_symbol_proceeds_and_the_reservation_is_recorded(self):
        held = run(**self.SHORT, hold_out_symbols=("SPY",)).provenance()["hold_out"]
        assert held["symbols"] == ["SPY"]
        assert held["symbol_touched"] is False

    @pytest.mark.req("6.4.holdout")
    def test_an_unlocked_run_proceeds_and_records_the_reason(self):
        held = run(**self.SHORT, hold_out_symbols=("QQQ",),
                   unlock_symbols="final evaluation").provenance()["hold_out"]
        assert held["symbol_touched"] is True
        assert held["symbol_reason"] == "final evaluation"

    def test_a_bare_string_is_one_symbol_and_not_three_letters(self):
        """Iterating "SPY" would reserve S, P and Y, match nothing, and raise nothing."""
        held = run(**self.SHORT, hold_out_symbols="SPY").provenance()["hold_out"]
        assert held["symbols"] == ["SPY"]

    def test_the_note_names_which_underlying_is_still_out_of_sample(self):
        """**The flattering reading this wording is written against.** The note hung on a
        QQQ run said "a result on another underlying is out of sample in the underlying
        and in sample in time" -- whose natural subject is the result it is attached to,
        which is false in the direction that makes the number look better than it is."""
        held = run(**self.SHORT, hold_out_symbols=("SPY",)).provenance()["hold_out"]
        assert "SPY stays out of sample" in held["note"]
        assert "this result, on QQQ, is in sample in both" in held["note"]


class TestOneSpellingPerSymbol:
    """A hold-out that misses because the symbol was lowercase is the silent failure this
    module exists to prevent, so case and surrounding space cannot make a second
    reservation."""

    @pytest.mark.parametrize("spelling", ["spy", "SPY ", " Spy"])
    def test_a_loosely_spelled_reservation_still_catches_the_run(self, spelling):
        with pytest.raises(StartupError, match="SPY, which is reserved"):
            run(**(BASE | {"symbol": "SPY", "end": "2023-03-31"}),
                hold_out_symbols=(spelling,))

    @pytest.mark.parametrize("spelling", ["spy", "SPY ", " Spy"])
    def test_every_spelling_is_the_same_reservation(self, spelling):
        assert ReservedSymbol(spelling) == ReservedSymbol("SPY")
        assert ReservedSymbol(spelling).matches(" spy ")

    def test_a_symbol_with_nothing_in_it_is_refused(self):
        """It reserves nothing while reading, in a configuration, as a reservation."""
        with pytest.raises(StartupError, match="reserves nothing"):
            ReservedSymbol("   ")

    def test_a_reservation_of_something_that_is_not_a_symbol_is_refused(self):
        """`ReservedSymbol(None)` raised `AttributeError` from inside `strip()`, which
        names neither the parameter nor what was wanted. Every other refusal in this
        module explains itself."""
        with pytest.raises(StartupError, match="ticker as text"):
            ReservedSymbol(None)


class TestAnEmptyReservationIsRefused:
    """"Not passed" and "passed and empty" are different declarations.

    `hold_out_symbols=[]` reserved nothing, refused nothing, and wrote provenance
    identical to omitting the parameter -- so a full backtest on the reserved underlying
    completed and its record read "no symbol was reserved". The realistic route is not a
    literal `[]`: it is `[s for s in reserved if ...]` that filtered everything out, or a
    config key read as absent.

    `symbols: []` instead of `symbols: None` would not be enough, because nothing
    downstream reads the difference. The precedent is `engine/runner.py`'s empty batch --
    "an empty batch is almost always a list comprehension that filtered everything out"
    -- which raises for the same reason.
    """

    SHORT = BASE | {"end": "2023-03-31"}
    SPAN = dict(start=date(2022, 1, 1), end=date(2022, 12, 31))

    @pytest.mark.parametrize("empty", [(), [], set()])
    def test_an_empty_collection_of_symbols_is_refused(self, empty):
        with pytest.raises(StartupError, match="reserves nothing"):
            run(**(self.SHORT | {"symbol": "SPY"}), hold_out_symbols=empty)

    def test_the_refusal_names_the_likely_cause(self):
        with pytest.raises(StartupError) as caught:
            check(None, symbol="QQQ", reserved=(), **self.SPAN)
        assert "filtered everything out" in str(caught.value)

    def test_omitting_it_reserves_nothing_and_says_so(self):
        """The other side of the distinction: absence is allowed, and is a state of its
        own rather than an error."""
        held = run(**self.SHORT).provenance()["hold_out"]
        assert held["symbols"] is None

    def test_a_plain_string_reservation_is_refused_rather_than_crashing(self):
        """`reserved=("SPY",)` died with `AttributeError` on `.symbol`. Accepting it
        would be worse: normalisation lives in `ReservedSymbol`, so a raw `" spy"` would
        compare unnormalised and miss."""
        with pytest.raises(StartupError, match="ReservedSymbol"):
            check(None, symbol="QQQ", reserved=("SPY",), **self.SPAN)


class TestTheSymbolHalfHasThreeStatesToo:
    """"No symbol reserved" and "reserved and respected" are different answers, for the
    same reason the period half distinguishes them: a reader comparing two results cannot
    otherwise tell the protected one from the one that reserved nothing."""

    SPAN = dict(start=date(2022, 1, 1), end=date(2022, 12, 31))

    def test_nothing_reserved_says_no_symbol_was_reserved(self):
        note = check(None, symbol="QQQ", **self.SPAN)
        assert note["symbols"] is None
        assert "no symbol was reserved" in note["symbol_note"]

    def test_reserved_and_respected_says_so(self):
        note = check(None, symbol="QQQ", reserved=(ReservedSymbol("SPY"),), **self.SPAN)
        assert note["symbols"] == ["SPY"] and note["symbol_touched"] is False
        assert "stays reserved" in note["symbol_note"]

    def test_unlocked_says_the_underlying_is_spent(self):
        note = check(None, symbol="SPY", reserved=(ReservedSymbol("SPY"),),
                     unlock_symbols="final evaluation", **self.SPAN)
        assert note["symbol_touched"] is True
        assert note["symbol_reason"] == "final evaluation"
        assert "in sample" in note["symbol_note"]

    def test_a_reservation_with_no_run_symbol_is_refused_rather_than_missed(self):
        """A caller that declares a reservation and forgets to say what the run is on
        would get a reservation that matches nothing and warns nobody."""
        with pytest.raises(StartupError, match="which symbol"):
            check(None, reserved=(ReservedSymbol("SPY"),), **self.SPAN)

    def test_a_touch_by_symbol_is_counted_as_a_touch(self):
        """`touches` is the number a reader needs when deciding what a final figure is
        worth, and a spent underlying counts as much as a spent period."""
        note = check(None, symbol="SPY", reserved=(ReservedSymbol("SPY"),),
                     unlock_symbols="final evaluation", **self.SPAN)
        found = touches([{"run_id": "a", "hold_out": note}])
        assert [t["run_id"] for t in found] == ["a"]
        assert found[0]["reason"] == "final evaluation"

    def test_only_the_underlying_that_matched_is_reported_as_spent(self):
        """**IWM was never run.** `touches` joined the whole declaration, so a reader saw
        "IWM, SPY" spent, concluded an intact hold-out was gone, and the rational next
        move is to stop protecting it. The matched symbol lived only in the prose of
        `symbol_note`, and facts do not live in prose."""
        note = check(None, symbol="SPY",
                     reserved=(ReservedSymbol("SPY"), ReservedSymbol("IWM")),
                     unlock_symbols="final evaluation", **self.SPAN)
        assert note["symbol_spent"] == "SPY"
        assert note["symbols"] == ["IWM", "SPY"], "the declaration is unchanged"
        assert touches([{"run_id": "a", "hold_out": note}])[0]["reserved"] == "SPY"

    def test_an_untouched_reservation_records_no_spent_symbol(self):
        """Absent rather than empty: a key holding "" would be read as a symbol named
        nothing by anything that only checks for the key."""
        note = check(None, symbol="QQQ", reserved=(ReservedSymbol("SPY"),), **self.SPAN)
        assert "symbol_spent" not in note


class TestEachHalfIsUnlockedByItsOwnAct:
    """Under decision 0001's own configuration -- SPY reserved **and** everything after a
    date reserved -- one `unlock=` spent both halves. No refusal naming the symbol ever
    fired, and the recorded reason for spending SPY was a sentence about dates, while two
    comments in the code asserted that the halves are spent separately.
    """

    HELD = HoldOut(date(2023, 7, 1), date(2023, 12, 31))
    BOTH = dict(start=date(2023, 1, 1), end=date(2023, 9, 30), symbol="SPY",
                reserved=(ReservedSymbol("SPY"),))

    def test_a_period_unlock_does_not_spend_the_symbol(self):
        with pytest.raises(StartupError, match="SPY, which is reserved"):
            check(self.HELD, unlock="final evaluation", **self.BOTH)

    def test_a_symbol_unlock_does_not_spend_the_period(self):
        with pytest.raises(StartupError, match="inside the hold-out"):
            check(self.HELD, unlock_symbols="final evaluation", **self.BOTH)

    def test_both_halves_reached_at_once_need_both_acts_and_record_both_reasons(self):
        """No existing test exercised both halves at once:
        `test_the_period_half_is_untouched_while_a_symbol_is_unlocked` is arranged so the
        period does not overlap."""
        note = check(self.HELD, unlock="the final evaluation",
                     unlock_symbols="the underlying is the last thing held back",
                     **self.BOTH)
        assert note["touched"] is True and note["symbol_touched"] is True
        assert note["reason"] == "the final evaluation"
        assert note["symbol_reason"] == (
            "the underlying is the last thing held back")

    def test_touches_reports_both_dimensions_and_both_reasons(self):
        note = check(self.HELD, unlock="the final evaluation",
                     unlock_symbols="the underlying too", **self.BOTH)
        found = touches([{"run_id": "a", "hold_out": note}])[0]
        assert found["reserved"] == "2023-07-01 to 2023-12-31; SPY"
        assert found["reason"] == "the final evaluation; the underlying too"


class TestThePeriodFormIsUnchanged:
    """The regression this item risks most.

    The module's docstring, the SDK's wiring and `tools/check_mutations.py` all rest on the
    period half behaving exactly as it did. A symbol half that quietly altered it would be
    worse than no symbol half at all, so the old mapping is asserted key by key.
    """

    def test_a_period_only_hold_out_returns_what_it_always_did(self):
        note = check(HoldOut(date(2023, 7, 1), date(2023, 12, 31)),
                     start=date(2023, 1, 1), end=date(2023, 6, 30))
        assert note["hold_out"] == "2023-07-01 to 2023-12-31"
        assert note["touched"] is False
        assert note["note"] == (
            "the run ends before the hold-out (2023-07-01 to 2023-12-31) begins")

    def test_no_declaration_at_all_still_says_nothing_was_reserved(self):
        note = check(None, start=date(2022, 1, 1), end=date(2023, 1, 1))
        assert note["hold_out"] is None and note["touched"] is False
        assert note["note"] == (
            "no hold-out was declared, so nothing was reserved from this research and "
            "no result here is out of sample")

    def test_the_period_refusal_does_not_need_a_symbol(self):
        """`check` is called with no symbol by every existing caller of the period form,
        and the refusal has to arrive from the dates alone."""
        with pytest.raises(StartupError, match="inside the hold-out"):
            check(HoldOut(date(2023, 7, 1), date(2023, 12, 31)),
                  start=date(2023, 1, 1), end=date(2023, 9, 30))

    def test_the_period_half_is_untouched_while_a_symbol_is_unlocked(self):
        """The two halves are reserved separately and spent separately: unlocking the
        symbol must not report the period as covered, or a result would claim an
        out-of-sample period it never ran on."""
        note = check(HoldOut(date(2024, 1, 1), date(2024, 12, 31)),
                     start=date(2023, 1, 1), end=date(2023, 6, 30),
                     symbol="SPY", reserved=(ReservedSymbol("SPY"),),
                     unlock_symbols="final evaluation")
        assert note["touched"] is False
        assert note["symbol_touched"] is True
