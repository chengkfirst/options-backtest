"""The shared action vocabulary and the trade lifecycle.

Both are definitions rather than behaviour, which is exactly why they need tests: an
error here does not turn anything red on its own. The vocabulary would quietly accept an
action the engine cannot execute, and the lifecycle would describe slots that do not
exist — and both look authoritative while being wrong.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.actions import BUILT, Action, TradeAction, require_built
from backtest.core.errors import StartupError
from backtest.core.slots import COMPONENTS
from backtest.signals.lifecycle import RUNS_IN, SCOPE, SETTINGS, State, describe, gaps


class TestActionVocabulary:
    @pytest.mark.req("2.1.vocabulary")
    def test_every_action_declares_whether_it_is_built(self):
        """An action absent from BUILT would pass require_built by accident."""
        assert set(BUILT) == set(Action), \
            f"not declared in BUILT: {sorted(a.value for a in set(Action) - set(BUILT))}"

    def test_unbuilt_actions_are_refused_with_a_reason(self):
        """Refusing is the point; refusing without saying why is not much better than
        accepting."""
        for action, why in BUILT.items():
            if not why:
                continue
            with pytest.raises(StartupError, match="not implemented"):
                require_built(action)
            assert len(why) > 30, f"{action.value} refuses without explaining"

    def test_built_actions_pass(self):
        for action, why in BUILT.items():
            if why:
                continue
            require_built(action)

    def test_trade_action_refuses_an_unbuilt_action_at_construction(self):
        """The envelope checks at construction, so an unbuilt action cannot reach the
        engine and be mapped to the nearest thing that works."""
        with pytest.raises(StartupError):
            TradeAction(Action.ROLL, trade_id=1, when=date(2024, 1, 2))

    def test_review_actions_declare_their_vocabulary_action(self):
        """Close / Reduce / Add are the ergonomic form; each must say which shared
        action it is, or attribution by action cannot group them."""
        from backtest.signals.review import Add, Close, Reduce

        assert Close.action is Action.CLOSE
        assert Reduce.action is Action.REDUCE
        assert Add.action is Action.ADD


class TestLifecycle:
    def test_every_state_has_a_scope(self):
        assert set(SCOPE) == set(State)

    def test_a_candidate_state_never_runs_a_trade_scoped_slot(self):
        """Setup belongs to the underlying and the exits belong to a trade. Mixing them
        would mean an exit rule reading a candidate that has no position."""
        trade_only = {"take_profit", "stop_loss", "headwind", "hedge"}
        for state, slots in RUNS_IN.items():
            if SCOPE[state] == "candidate":
                assert not (set(slots) & trade_only), \
                    f"{state.value} is candidate-scoped but runs {set(slots) & trade_only}"

    def test_every_slot_named_is_a_real_slot(self):
        """A typo here would describe a slot that does not exist, and the table would
        still render."""
        keys = {c.key for c in COMPONENTS}
        for state, slots in RUNS_IN.items():
            for slot in slots:
                assert slot in keys, f"{state.value} names {slot!r}, which is not a slot"

    def test_every_setting_says_what_enforces_it_or_that_nothing_does(self):
        """A setting with neither an enforcer nor a gap is a claim that it is settled,
        which has to be true."""
        for s in SETTINGS:
            assert s.question.endswith("?") or len(s.question) > 20
            assert s.enforced_by or s.gap, f"{s.name} says nothing about its state"

    def test_gaps_are_generated_not_written(self):
        """Every gap in the list traces back to a setting, so the list cannot go stale
        independently."""
        names = {s.name for s in SETTINGS if s.gap}
        assert {g.split(":")[0].removeprefix("lifecycle.") for g in gaps()} == names

    def test_describe_is_serialisable_for_the_interface(self):
        describe().to_json()


class TestPositioning:
    """The sizer, now that it is arithmetic and can be tested without a backtest.

    That is the point of extracting it: these cases were previously only reachable by
    running a whole backtest and inferring the quantity from the trade log.
    """

    def _sizing(self, **kw):
        from backtest.portfolio.positioning import Sizing
        return Sizing(**kw)

    def test_size_is_risk_budget_over_stop_distance(self):
        """The coupling the whole project turns on. With the stop at -50%, a trade's real
        risk is half the premium, so the position is twice what a total-loss assumption
        would give."""
        from backtest.portfolio.positioning import contracts_for

        s = self._sizing(method="risk_pct", risk_pct=0.02)
        tight = contracts_for(s, equity=100_000, premium=500, stop_loss=0.5)
        total = contracts_for(s, equity=100_000, premium=500, stop_loss=None)
        assert tight == 8 and total == 4

    def test_never_rounds_up_to_one(self):
        """Rounding a computed 0 up to 1 would quietly turn 2% at risk into far more."""
        from backtest.portfolio.positioning import contracts_for

        s = self._sizing(method="risk_pct", risk_pct=0.02)
        assert contracts_for(s, equity=10_000, premium=50_000, stop_loss=1.0) == 0

    def test_max_contracts_caps_every_method(self):
        from backtest.portfolio.positioning import contracts_for

        for method, extra in (("risk_pct", {}), ("fixed_notional", {"notional": 1e9}),
                              ("scale_by_vol", {"target_vol": 0.2})):
            s = self._sizing(method=method, risk_pct=0.9, max_contracts=5, **extra)
            got = contracts_for(s, equity=1e9, premium=1.0, stop_loss=1.0, volatility=0.1)
            assert got == 5, f"{method} ignored max_contracts"

    def test_scale_by_vol_refuses_without_a_volatility(self):
        """Returning 0 rather than inventing a figure: a missing volatility must not
        become an arbitrary position."""
        from backtest.portfolio.positioning import contracts_for

        s = self._sizing(method="scale_by_vol", risk_pct=0.02, target_vol=0.20)
        assert contracts_for(s, equity=100_000, premium=500, stop_loss=0.5) == 0

    def test_scale_by_vol_buys_less_when_more_volatile(self):
        from backtest.portfolio.positioning import contracts_for

        s = self._sizing(method="scale_by_vol", risk_pct=0.02, target_vol=0.20)
        calm = contracts_for(s, equity=100_000, premium=500, stop_loss=0.5, volatility=0.10)
        wild = contracts_for(s, equity=100_000, premium=500, stop_loss=0.5, volatility=0.40)
        assert calm > wild

    def test_loss_fraction_treats_an_invalid_stop_as_a_total_loss(self):
        """A stop outside (0, 1] is not a stop. Reading it as one would oversize."""
        from backtest.portfolio.positioning import loss_fraction

        assert loss_fraction(0.5) == 0.5
        for bad in (None, 0.0, -0.2, 1.5):
            assert loss_fraction(bad) == 1.0
