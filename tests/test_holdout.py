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
from backtest.research.holdout import HoldOut, check, touches

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
        would be the quietest way to lose the distinction."""
        import json
        import subprocess
        import sys

        from backtest.core.run_id import GUARD_RECORD

        # The writer is what decides this, so the writer is what is checked.
        source = (pathlib.Path("tools") / "check.py").read_text(encoding="utf-8")
        assert '"passed": bool(passed and not partial)' in source, (
            "a --fast run must not be recorded as passing"
        )

    def test_a_run_reports_which_state_it_was_in(self):
        """It belongs in the manifest, beside the commit, not in a log nobody reads."""
        result = run(**(BASE | {"end": "2023-03-31"}))
        assert "guards" in result.manifest()
        assert result.manifest()["guards"]["state"] in (
            "green", "red", "stale", "unrecorded")
