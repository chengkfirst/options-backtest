"""The fourth risk action — requirement 3.1, the half that was refused.

## Why it was refused and what changed

``pause`` was declared in the vocabulary and refused at the point of use, with the reason
recorded: pausing until a person resumes needs a run that can stop and be restarted, which
needs component state to be saved and restored. A backtest that paused with no way to
resume would be one that **ended early and reported as though it had run to the end**.

That machinery arrived with `0.2.state` (get_state / set_state) and `5.1.resume` (the
checkpoint), so the refusal was lifted rather than carried on out of habit.

## What makes it a fourth action and not a second name for `no_new_positions`

The **run stops**. The result says it paused, carries the checkpoint, and resuming is a
separate deliberate act. Both halves of that matter:

- a pause that let the loop continue would be indistinguishable from the other three
- a pause that ended the run with nothing saying so is the failure the refusal named --
  a paused run and a completed one have the same shape and fewer trades, and every figure
  computed from one would be over a period nobody chose
"""

from __future__ import annotations

import pytest

from backtest.core.component import Param, Variant, get, register
from backtest.core.errors import StartupError
from backtest.portfolio.risk_actions import NOTHING, RiskAction, RiskDecision

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "3")


def _pauser(ctx, *, at: float):
    """Pause once the drawdown passes ``at``."""
    if ctx["drawdown"] <= -at:
        return RiskDecision(action=RiskAction.PAUSE,
                            reason=f"drawdown {ctx['drawdown']:.1%} past {at:.0%}")
    return NOTHING


try:
    register(Variant(
        slot="headwind", name="pauser", version=1,
        describes="pauses the run once the drawdown passes a threshold, so the "
                  "researcher decides whether to continue",
        params=(Param("at", "float", "the drawdown that pauses the run",
                      lo=0.0, hi=1.0),),
        decide=_pauser,
    ))
except StartupError:                 # already registered by an earlier import
    pass


def _run(market, chain, signals, config, *, at: float = 0.03, **over):
    from tests.test_runner import _engine, _strategy

    engine = _engine(market, chain, signals, config)
    engine.brake = get("headwind", "pauser").bind({"at": at})
    return engine.run(_strategy(signals), **over)


@kind("guard")
class TestAPausedRunSaysSo:
    """The failure the refusal named: a run that stopped early looking complete."""

    @pytest.mark.req("3.1.actions")
    def test_it_stops_and_names_the_session_and_the_reason(self, market, chain, signals,
                                                           config):
        result = _run(market, chain, signals, config)
        assert result.paused_on is not None, (
            "the rule returned PAUSE and the run went to the end, so pause is a second "
            "name for no_new_positions"
        )
        assert result.paused_on < config.end, "it paused on the last session, which "\
                                              "proves nothing about stopping"
        assert "drawdown" in result.paused_reason

    def test_the_run_is_shorter_than_the_range(self, market, chain, signals, config):
        """Which is the observable difference, and the thing a reader needs."""
        full = _run(market, chain, signals, config, at=0.99)       # never pauses
        stopped = _run(market, chain, signals, config, at=0.03)
        assert full.paused_on is None
        assert len(stopped.snapshots) < len(full.snapshots)
        assert len(stopped.records) < len(full.records)

    def test_it_carries_a_checkpoint(self, market, chain, signals, config):
        """Without one the pause is a run that ended early. The checkpoint is what makes
        "until a person resumes" a thing the system can actually do."""
        result = _run(market, chain, signals, config)
        assert result.checkpoint is not None
        assert result.checkpoint.through == result.paused_on

    def test_an_unpaused_run_offers_no_checkpoint(self, market, chain, signals, config):
        assert _run(market, chain, signals, config, at=0.99).checkpoint is None

    def test_the_pause_is_in_the_event_log_with_how_to_continue(self, market, chain,
                                                                signals, config):
        from backtest.core.run_log import EventKind

        result = _run(market, chain, signals, config)
        notes = [e for e in result.events.of_kind(EventKind.CANDIDATE_DROPPED)
                 if "paused:" in e.detail]
        assert notes, "the run paused and the log does not say it did"
        assert "resume_from" in notes[0].detail, (
            "the log says the run paused and not how to continue it, which leaves the "
            "reader with a short result and no next step"
        )


class TestResumingAPausedRun:
    @pytest.mark.req("3.1.actions")
    def test_it_continues_from_the_session_after_the_pause(self, market, chain, signals,
                                                           config):
        """"Until a person or a rule resumes it" -- and resuming is deliberate.

        The brake is **not** carried into the continuation: resuming under the same rule
        that paused it would pause again on the next session, which is a loop rather than
        a decision. What to do about the reason is the researcher's, and that is the
        whole content of the action.
        """
        from tests.test_runner import _engine, _strategy

        paused = _run(market, chain, signals, config)
        continued = _engine(market, chain, signals, config).run(
            _strategy(signals), resume_from=paused.checkpoint)

        assert continued.paused_on is None
        assert len(continued.records) > len(paused.records)
        assert len(continued.snapshots) > len(paused.snapshots)
        # The trades from before the pause are still there, unchanged.
        before = [(r.trade_id, r.entry_date, r.exit_date) for r in paused.records]
        after = [(r.trade_id, r.entry_date, r.exit_date) for r in continued.records]
        assert after[:len(before)] == before

    def test_resuming_under_the_same_rule_pauses_again(self, market, chain, signals,
                                                       config):
        """Which is correct and worth pinning: the rule has not changed its mind, so a
        resumed run that ignored it would be running under a rule nobody turned off."""
        paused = _run(market, chain, signals, config)
        again = _run(market, chain, signals, config, resume_from=paused.checkpoint)
        assert again.paused_on is not None
        assert again.paused_on > paused.paused_on


class TestTheVocabularyIsStillClosed:
    def test_all_four_actions_are_built_now(self):
        """The requirement is that a rule may only do one of the four. Three were
        executable and the fourth was refused with a reason; now all four are."""
        from backtest.portfolio.risk_actions import BUILT, require_built

        for action in RiskAction:
            require_built(action)            # raises if any is unbuilt
        assert all(not why for why in BUILT.values()), (
            f"these are still refused: "
            f"{[a.value for a, why in BUILT.items() if why]}"
        )

    def test_an_action_outside_the_four_is_refused(self):
        """The falsifier: a rule doing something outside the vocabulary."""
        with pytest.raises((StartupError, ValueError, KeyError)):
            RiskAction("sell_everything_and_retire")
