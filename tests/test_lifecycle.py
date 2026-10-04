"""The four-state lifecycle — requirement S.lifecycle.

## What was missing and why it mattered

The engine had **no Armed state**. An entry condition becoming true produced an order in
the same step, which collapsed Idle and Armed into one, and two things followed:

- **slot 1 (setup / regime) and slot 2 (entry) were one test.** No ablation could say
  what a regime filter was worth, because removing it also removed the timing. That is a
  direct obstacle to the thing this platform is for -- deciding which of the seven
  components earn their place.
- **a candidate re-entered as soon as the condition was true again**, which is the most
  permissive policy there is and was never chosen. It was an accident of the loop's shape.

## The default reproduces the old behaviour exactly

`setup=None` means always armed; `reentry_policy="immediate"` means no wait. Together
they are what the engine did before any of this existed, which is why the regression
baseline does not move and the two engines stay row-identical. A settings object that
changed results on arrival would make every stored run incomparable with every new one --
and the point of naming these is to find out what they cost, which needs the before.

So the tests below are mostly about the **other** settings: each one has to change the
result, or it is a knob connected to nothing.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.errors import StartupError
from backtest.signals.lifecycle import (
    REENTRY_POLICIES,
    RUNS_IN,
    SCOPE,
    Candidate,
    Lifecycle,
    State,
)

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "2")


class _Calendar:
    """Sessions as consecutive days, which is all the policies need."""

    @staticmethod
    def sessions_between(start: date, end: date) -> int:
        return (end - start).days


class TestTheStatesAndTheirScopes:
    def test_the_four_states_are_the_documents(self):
        assert [s.value for s in State] == ["idle", "armed", "open", "closed"]

    def test_idle_and_armed_are_the_candidates_and_the_others_the_trades(self):
        """The distinction the whole module exists for: one candidate per underlying,
        and one candidate produces several trades over time. Conflate them and "may this
        underlying trade again yet" has nowhere to live."""
        assert SCOPE[State.IDLE] == SCOPE[State.ARMED] == "candidate"
        assert SCOPE[State.OPEN] == SCOPE[State.CLOSED] == "trade"

    def test_setup_runs_in_idle_and_entry_only_in_armed(self):
        """Which is the separation that was missing. Entry in Idle would be the old
        collapsed behaviour written down as if it were the design."""
        assert "setup" in RUNS_IN[State.IDLE]
        assert "entry" not in RUNS_IN[State.IDLE]
        assert "entry" in RUNS_IN[State.ARMED]

    def test_a_candidate_never_holds_a_trade_state(self):
        """`Open` and `Closed` belong to a trade_id, which is what SCOPE says.

        The first version of `opened_trade` set the candidate to Open anyway, and two
        things followed: the next session's arming clobbered it, so the state meant
        nothing; and a candidate allowed two concurrent trades is Armed *and* has one
        open, which one exclusive state cannot express.

        The candidate carries its setup status; the trade count sits beside it.
        """
        candidate = Candidate(symbol="QQQ")
        candidate.arm("setup")
        candidate.opened_trade()
        assert candidate.state is State.ARMED
        assert candidate.open_trades == 1
        candidate.closed_trade(date(2023, 1, 5))
        assert candidate.state is State.ARMED
        assert candidate.open_trades == 0
        assert candidate.last_closed == date(2023, 1, 5)

    def test_the_count_and_the_state_answer_different_questions(self):
        candidate = Candidate(symbol="QQQ")
        candidate.arm("setup")
        candidate.opened_trade()
        candidate.opened_trade()
        candidate.closed_trade(date(2023, 1, 5))
        assert candidate.open_trades == 1 and candidate.opened == 2


class TestEachPolicyRefusesSomething:
    """A policy that refuses nothing is a knob connected to nothing."""

    @staticmethod
    def _armed(*, closed_on: date | None = None, opened: int = 0,
               open_trades: int = 0) -> Candidate:
        candidate = Candidate(symbol="QQQ", opened=opened, open_trades=open_trades,
                              last_closed=closed_on)
        candidate.arm("setup said so")
        return candidate

    def test_immediate_allows_a_trade_the_day_the_last_one_closed(self):
        """The default, and the old behaviour. Asserted so that a change to it is a
        failing test rather than a quietly different trade count."""
        candidate = self._armed(closed_on=date(2023, 1, 5))
        assert candidate.may_enter(date(2023, 1, 5), Lifecycle(),
                                   calendar=_Calendar()) == ""

    def test_next_session_refuses_the_closing_day_and_allows_the_next(self):
        policy = Lifecycle(reentry_policy="next_session")
        candidate = self._armed(closed_on=date(2023, 1, 5))
        assert "next_session" in candidate.may_enter(date(2023, 1, 5), policy,
                                                     calendar=_Calendar())
        assert candidate.may_enter(date(2023, 1, 6), policy, calendar=_Calendar()) == ""

    def test_cooldown_counts_sessions_and_says_how_many_are_left(self):
        policy = Lifecycle(reentry_policy="cooldown", cooldown_sessions=5)
        candidate = self._armed(closed_on=date(2023, 1, 5))
        refusal = candidate.may_enter(date(2023, 1, 8), policy, calendar=_Calendar())
        assert "3 session(s) ago" in refusal and "5" in refusal
        assert candidate.may_enter(date(2023, 1, 10), policy,
                                   calendar=_Calendar()) == ""

    def test_once_never_allows_a_second_trade(self):
        policy = Lifecycle(reentry_policy="once")
        candidate = self._armed(closed_on=date(2023, 1, 5), opened=1)
        assert "already opened 1 trade(s)" in candidate.may_enter(
            date(2024, 1, 5), policy, calendar=_Calendar())

    def test_a_candidate_that_has_never_traded_is_not_held_by_any_policy(self):
        """Every waiting policy turns on the last close, and there is not one yet.
        Refusing here would mean a strategy with a cooldown could never open its first
        trade -- which is the shape of a bug that looks like a strategy that does not
        trade."""
        for policy in REENTRY_POLICIES:
            lifecycle = (Lifecycle(reentry_policy=policy, cooldown_sessions=5)
                         if policy == "cooldown" else Lifecycle(reentry_policy=policy))
            assert self._armed().may_enter(date(2023, 1, 5), lifecycle,
                                           calendar=_Calendar()) == "", policy

    def test_max_concurrent_trades_is_per_candidate(self):
        """What `allow_stacking` could not express: it is a boolean, and the engine's
        `max_concurrent` is book-wide."""
        policy = Lifecycle(max_concurrent_trades=2)
        assert self._armed(open_trades=1).may_enter(
            date(2023, 1, 5), policy, calendar=_Calendar()) == ""
        refusal = self._armed(open_trades=2).may_enter(
            date(2023, 1, 5), policy, calendar=_Calendar())
        assert "already holds 2 open trade(s)" in refusal


class TestArmingIsNotEntering:
    def test_an_idle_candidate_may_not_enter_whatever_the_policy_says(self):
        """The state comes first. A candidate the setup slot has not armed is not
        waiting on a policy -- it has no setup, which is a different answer and a
        different sentence."""
        candidate = Candidate(symbol="QQQ")
        refusal = candidate.may_enter(date(2023, 1, 5), Lifecycle(),
                                     calendar=_Calendar())
        assert "is idle" in refusal and "no setup has been seen" in refusal

    def test_disarming_says_why(self):
        candidate = Candidate(symbol="QQQ")
        candidate.arm("vix below 20")
        candidate.disarm("vix above 30")
        assert candidate.state is State.IDLE
        assert candidate.reason == "vix above 30"

    def test_arming_opens_nothing(self):
        """The separation, stated as an absence: `arm` changes a state and touches no
        counter. If arming incremented anything it would be entering."""
        candidate = Candidate(symbol="QQQ")
        candidate.arm("setup")
        assert candidate.opened == 0 and candidate.open_trades == 0


@kind("guard")
class TestWhatTheSettingsRefuse:
    def test_a_policy_that_is_not_one_is_refused(self):
        with pytest.raises(StartupError) as caught:
            Lifecycle(reentry_policy="whenever")
        assert "is not a reentry policy" in str(caught.value)

    def test_a_cooldown_with_no_length_is_refused(self):
        """How long to wait is the whole content of the policy, so a default would be
        the platform choosing a research parameter."""
        with pytest.raises(StartupError) as caught:
            Lifecycle(reentry_policy="cooldown")
        assert "needs cooldown_sessions" in str(caught.value)

    def test_a_cooldown_length_under_a_policy_that_does_not_wait_is_refused(self):
        """A setting silently ignored is one somebody believes is in force."""
        with pytest.raises(StartupError) as caught:
            Lifecycle(reentry_policy="immediate", cooldown_sessions=5)
        assert "silently ignored" in str(caught.value)

    def test_a_concurrency_limit_of_zero_is_refused(self):
        with pytest.raises(StartupError):
            Lifecycle(max_concurrent_trades=0)

    def test_the_defaults_are_the_old_behaviour(self):
        """Pinned, because this is what makes every stored run comparable with every new
        one. A changed default here moves every result in the system at once."""
        default = Lifecycle()
        assert default.reentry_policy == "immediate"
        assert default.max_concurrent_trades is None
        assert default.cooldown_sessions is None


@kind("guard")
class TestItChangesARealRun:
    """A setting that cannot be seen in a result is a knob connected to nothing.

    The unit tests above prove each policy refuses something. These prove the engine asks
    it -- which is the half that rots, because a lifecycle gate the loop never consults
    breaks no test and produces an ordinary-looking backtest.
    """

    #: Three years and a ten-day hold, deliberately.
    #:
    #: The six-month window used first could not demonstrate a waiting policy at all: all
    #: eleven of its entries fell in May and early June and the first exit was 8 June, so
    #: nothing had closed while every entry was being decided and a cooldown measured
    #: from the last close had nothing to bite on. The test failed and the gate was
    #: working -- which is the shape of a premise built on a window rather than on the
    #: mechanism.
    RUN = dict(
        symbol="QQQ", start="2021-01-01", end="2023-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 10, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )



    @staticmethod
    def _trades(**over) -> int:
        from backtest.api.sdk import run

        return len(run(**(TestItChangesARealRun.RUN | over)).trades)

    @pytest.mark.req("S.lifecycle")
    def test_a_waiting_policy_removes_trades_and_the_default_does_not(self):
        """Both halves in one test, because each is meaningless without the other.

        A policy that removed nothing would be unenforced; a default that removed
        something would make every stored run incomparable with every new one.
        """
        base = self._trades()
        assert base > 20, (
            f"{base} trades is too few for a reentry policy to have much to remove, so "
            f"this test would pass on a run that demonstrates nothing"
        )

        explicit_default = self._trades(lifecycle={"reentry_policy": "immediate"})
        assert explicit_default == base, (
            "naming the default changed the result, so the default is not what the "
            "engine was doing"
        )

        # **What is asserted is what each policy means, not what this data did.**
        #
        # An earlier version pinned these at 115, 63 and 21. Those are facts about QQQ in
        # 2021-2023, and a test is the wrong place for one: it goes red on a data revision
        # and it reads as a finding when no research stands behind it.
        #
        # `once` is the exception and is exact, because its figure follows from its
        # definition rather than from the data -- one trade ever is one trade.
        assert self._trades(lifecycle={"reentry_policy": "once"}) == 1

        # The rest are ordered rather than counted. Each comparison is mechanical:
        # a longer wait cannot admit more trades than a shorter one, and a cap of one
        # cannot admit more than no cap.
        waits_a_day = self._trades(lifecycle={"reentry_policy": "next_session"})
        waits_a_month = self._trades(lifecycle={"reentry_policy": "cooldown",
                                                "cooldown_sessions": 20})
        assert waits_a_day < base, "waiting a session removed nothing"
        assert waits_a_month < waits_a_day, (
            f"a twenty-session cooldown ({waits_a_month}) admitted at least as many "
            f"trades as waiting one session ({waits_a_day}). A longer wait cannot admit "
            f"more, so the length is not being read"
        )
        assert self._trades(lifecycle={"max_concurrent_trades": 1}) < base

    @pytest.mark.req("S.lifecycle")
    def test_a_setup_slot_that_never_arms_produces_no_trades(self):
        """The Armed state, end to end. With setup and entry collapsed into one step
        there was nowhere for this to be true."""
        never = self._trades(setup=lambda ctx: False)
        assert never == 0, (
            f"a setup slot that never arms produced {never} trades, so entry is still "
            f"being asked in the Idle state"
        )

        always = self._trades(setup=lambda ctx: True)
        assert always == self._trades(), (
            "a setup slot that always arms changed the result, so `setup=None` and "
            "'always armed' are not the same thing"
        )

    @pytest.mark.req("0.5.hash")
    def test_two_lifecycle_settings_do_not_share_a_run_id(self):
        """**The fourth time this class of gap has appeared**: something that moves the
        result reaching the engine without reaching the fingerprint.

        Found by a round-trip check reporting two runs identical when one had a
        five-session cooldown and the other had none. A policy that takes 136 trades down
        to 1 and leaves the id unchanged makes a search look smaller than it was, which
        flatters every deflated figure computed from it.
        """
        from backtest.api.sdk import run

        def run_id(**over):
            return run(**(self.RUN | over)).manifest()["run_id"]

        plain = run_id()
        assert plain != run_id(lifecycle={"reentry_policy": "once"})
        assert run_id(lifecycle={"reentry_policy": "once"}) != run_id(
            lifecycle={"reentry_policy": "cooldown", "cooldown_sessions": 5})
        assert plain != run_id(setup=lambda ctx: True), (
            "a run with a setup slot shares its id with one without, and a setup that "
            "does not arm removes every trade"
        )

    @pytest.mark.req("0.5.file")
    def test_the_config_file_reproduces_a_run_with_a_policy(self, tmp_path):
        """The file carries the policy as a **value**, not as a description.

        The whole lifecycle block used to be dropped on load, which was right while
        nothing in it was settable and became wrong the moment `reentry_policy` was: a
        file recording a cooldown would replay without one, reproducing a different run
        while looking like the same one.
        """
        from backtest.api.config_file import load, save, to_mapping
        from backtest.api.sdk import run

        policy = {"reentry_policy": "cooldown", "cooldown_sessions": 5}
        first = run(**(self.RUN | {"lifecycle": policy}))
        written = to_mapping(first)["lifecycle"]
        assert written["reentry_policy"] == "cooldown"
        assert written["cooldown_sessions"] == 5

        back = load(save(first, tmp_path / "with_policy.yaml"))
        assert back["lifecycle"] == policy
        assert run(**back).manifest()["run_id"] == first.manifest()["run_id"]

    def test_a_refusal_is_recorded_only_when_something_wanted_to_trade(self):
        """A refusal on a day nothing wanted to trade is not information, and logging it
        every session would bury the days it mattered."""
        from backtest.api.sdk import run
        from backtest.core.run_log import EventKind

        result = run(**(self.RUN | {"lifecycle": {"reentry_policy": "once"}}))
        refusals = [e for e in result.raw.events.of_kind(EventKind.CANDIDATE_DROPPED)
                    if "reentry_policy" in e.detail]
        assert refusals, "nothing recorded why the later entries did not happen"
        # Bounded rather than exact, and the reason is worth stating: the entry
        # condition is true on **more** days than it opens trades, because selection,
        # the concurrency limit and sizing each refuse some of them afterwards. So the
        # count is above the 135 trades this policy removed and well below the ~750
        # sessions in the range -- one per blocked entry, not one per day.
        #
        # An exact figure here would have to be produced by another run of the same
        # thing, which is comparing the code to itself.
        sessions = len(result.raw.snapshots)
        blocked = self._trades() - 1          # every trade but the one that got through
        assert blocked <= len(refusals) < sessions / 3, (
            f"{len(refusals)} refusals over {sessions} sessions, against {blocked} "
            f"entries this policy removed. Below that figure the refusals are not being "
            f"recorded; near the session count they are being logged every day, which "
            f"buries the days that mattered"
        )
