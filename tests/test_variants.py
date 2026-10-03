"""The component protocol: one slot, several named forms.

The first of this platform's two uses is settling the seven components. Settling one means
choosing which *form* of it to keep -- a stop on the premium against a stop on the
underlying -- which the architecture document names in its register as a choice to
research. A parameter sweep over one form cannot answer it.

What is checked here is the part that would fail quietly: a form that is configured and
does not run, two forms that hash alike, and a comparison that reports a difference it did
not actually measure.
"""

from __future__ import annotations

import pytest

from backtest.core.component import Bound, Param, Variant, build, get, register, variants
from backtest.core.errors import StartupError

BASE = dict(
    symbol="QQQ", start="2023-01-01", end="2023-06-30",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


def _run(**over):
    from backtest import run

    return run(**(BASE | over))


class TestRegistry:
    def test_the_shipped_forms_are_registered_by_importing_the_package(self):
        """Registration happens at the composition layer, not by whoever imports first.

        The registry is Layer 0 and the forms are Layer 2, so Layer 0 cannot reach them
        without inverting the dependency direction. If this were left to an import at the
        call site, a variant would be "not registered" from one entry point and fine from
        another.
        """
        import backtest  # noqa: F401

        refs = {v.ref for v in variants()}
        assert {"stop_loss/premium@1", "stop_loss/underlying@1",
                "take_profit/premium@1", "take_profit/underlying@1"} <= refs

    def test_both_sides_of_the_documented_choice_exist(self):
        """The register names "stop loss on premium vs on the underlying" as a choice to
        research. A choice needs both sides to exist and neither to be picked."""
        names = {v.name for v in variants("stop_loss")}
        assert {"premium", "underlying"} <= names

    def test_an_unknown_slot_is_refused(self):
        with pytest.raises(StartupError, match="is not a slot"):
            register(Variant(slot="nonsense", name="x", version=1, describes="",
                             params=(), decide=lambda ctx: True))

    def test_re_registering_with_different_logic_raises(self):
        """Two runs would otherwise carry the same reference and look like the same run."""
        def first(ctx, **p):
            return True

        def second(ctx, **p):
            return False

        register(Variant(slot="headwind", name="probe", version=1, describes="a",
                         params=(), decide=first))
        with pytest.raises(StartupError, match="already registered with different logic"):
            register(Variant(slot="headwind", name="probe", version=1, describes="a",
                             params=(), decide=second))

    def test_an_unknown_variant_lists_what_exists(self):
        with pytest.raises(StartupError, match="Registered: premium, underlying"):
            get("stop_loss", "trailing")


class TestParameters:
    def test_a_missing_parameter_is_refused_not_defaulted(self):
        """A component parameter is a strategy choice, so the platform does not pick one."""
        with pytest.raises(StartupError, match="needs fraction"):
            get("stop_loss", "premium").bind({})

    def test_a_misspelled_parameter_raises(self):
        """Ignored, it would leave the component running on something other than what the
        configuration says -- and the configuration is what a person reads back."""
        with pytest.raises(StartupError, match="fractoin"):
            get("stop_loss", "premium").bind({"fractoin": 0.4})

    def test_a_value_outside_the_range_raises(self):
        with pytest.raises(StartupError, match="at most 1"):
            get("stop_loss", "premium").bind({"fraction": 5.0})

    def test_no_shipped_parameter_carries_a_default(self):
        """The same rule as tools/check_defaults.py, for the component schema."""
        defaulted = [f"{v.ref}.{p.name}" for v in variants() for p in v.params
                     if not p.required]
        assert not defaulted, f"these component parameters are optional: {defaulted}"

    def test_build_round_trips_a_spec(self):
        bound = build({"slot": "stop_loss", "variant": "underlying",
                       "params": {"fraction": 0.03}})
        assert isinstance(bound, Bound)
        assert bound.ref == "stop_loss/underlying@1"
        assert bound.describe() == "stop_loss/underlying@1(fraction=0.03)"


class TestItActuallyRuns:
    """A component that is configured and does not run is the failure this whole module
    exists to avoid."""

    def test_the_two_stop_forms_give_different_trades(self):
        a = _run(components={"stop_loss": {"variant": "premium",
                                           "params": {"fraction": 0.4}}})
        b = _run(components={"stop_loss": {"variant": "underlying",
                                           "params": {"fraction": 0.03}}})
        stopped = lambda r: int((r.trades["exit_reason"] == "stop_loss").sum())  # noqa: E731
        assert stopped(a) != stopped(b)

    def test_a_component_replaces_the_rule_rather_than_adding_to_it(self):
        """Running both would mean a trade could be closed by the form that was supposed
        to have been replaced, and a comparison of two forms would measure neither."""
        with_component = _run(components={"stop_loss": {"variant": "premium",
                                                        "params": {"fraction": 0.99}}})
        # A 99% premium stop almost never fires, so with the rule replaced there should be
        # far fewer stop exits than the rule alone would give.
        assert int((with_component.trades["exit_reason"] == "stop_loss").sum()) == 0

    def test_giving_both_a_component_and_the_rule_field_raises(self):
        with pytest.raises(StartupError, match="given both as a component"):
            _run(exit={"hold_days": 20, "dte_floor": 7, "stop_loss": 0.4},
                 components={"stop_loss": {"variant": "premium",
                                           "params": {"fraction": 0.4}}})

    def test_the_trade_table_refuses_rather_than_approximating(self):
        """It computes exits vectorised over the whole window, so it cannot apply a
        per-day decision function. The same refusal it already gives for a plan."""
        with pytest.raises(StartupError, match="cannot run a swapped-in component"):
            _run(engine="trade_table",
                 components={"stop_loss": {"variant": "premium",
                                           "params": {"fraction": 0.4}}})


class TestFingerprint:
    """Two different strategies sharing one fingerprint makes a search look smaller than
    it was, which makes the deflated result look better than it earned."""

    def _id(self, components):
        return _run(components=components).manifest()["run_id"]

    def test_two_forms_of_one_slot_do_not_hash_alike(self):
        assert self._id({"stop_loss": {"variant": "premium",
                                       "params": {"fraction": 0.4}}}) != \
               self._id({"stop_loss": {"variant": "underlying",
                                       "params": {"fraction": 0.03}}})

    def test_two_parameters_of_one_form_do_not_hash_alike(self):
        assert self._id({"stop_loss": {"variant": "premium",
                                       "params": {"fraction": 0.4}}}) != \
               self._id({"stop_loss": {"variant": "premium",
                                       "params": {"fraction": 0.5}}})

    def test_a_component_run_does_not_hash_like_a_plain_one(self):
        assert self._id({"stop_loss": {"variant": "premium",
                                       "params": {"fraction": 0.4}}}) != self._id(None)

    def test_the_reference_carries_the_version(self):
        manifest = _run(components={"stop_loss": {"variant": "premium",
                                                  "params": {"fraction": 0.4}}}).manifest()
        assert manifest["strategy"]["components"]["stop_loss"]["ref"].endswith("@1")


class TestComparison:
    def test_the_forms_to_compare_are_required(self):
        """Which forms, at which parameters, is the research question. A default list
        would answer it and then report its winner as a finding."""
        from backtest.research.forms import compare

        r = _run()
        with pytest.raises(StartupError, match="needs the forms to run"):
            compare(r.raw.strategy, "stop_loss", [], lambda s: (0.0, 0))

    def test_a_comparison_states_what_it_cannot_settle(self):
        from backtest.research.forms import report

        r = _run()
        c = r.compare_forms("stop_loss", [
            {"variant": "premium", "params": {"fraction": 0.4}},
            {"variant": "premium", "params": {"fraction": 0.6}},
            {"variant": "underlying", "params": {"fraction": 0.03}},
            {"variant": "underlying", "params": {"fraction": 0.05}},
        ])
        text = report(c, synthetic=r.synthetic)
        assert "Ranked by nothing" in text
        assert "under its own parameter" in text
        # The boundary comes before the numbers.
        assert text.index("!!") < text.index("net P&L")

    def test_one_form_at_one_parameter_cannot_settle_anything(self):
        """Without a second parameter there is no ruler for the between-form gap."""
        r = _run()
        c = r.compare_forms("stop_loss", [
            {"variant": "premium", "params": {"fraction": 0.4}},
            {"variant": "underlying", "params": {"fraction": 0.03}},
        ])
        separated, why = c.distinguishes()
        assert not separated
        assert "no ruler" in why

    def test_the_verdict_always_carries_the_multiple(self):
        """A gap of 3,576 against a within-form spread of 3,566 passes the test and
        separates nothing. The word alone would hide that; the multiple cannot."""
        r = _run()
        c = r.compare_forms("stop_loss", [
            {"variant": "premium", "params": {"fraction": 0.4}},
            {"variant": "premium", "params": {"fraction": 0.6}},
            {"variant": "underlying", "params": {"fraction": 0.03}},
            {"variant": "underlying", "params": {"fraction": 0.05}},
        ])
        _, why = c.distinguishes()
        assert "x the most any single form" in why or "covers by changing its own" in why

    def test_the_comparison_is_labelled_mixed_not_option_side(self):
        """A premium form against an underlying form is the one comparison a synthetic
        chain biases rather than merely blurs: the bias does not cancel."""
        from backtest.research.forms import SCOPE
        from backtest.research.boundary import Scope

        assert SCOPE is Scope.MIXED


class TestExitContext:
    def test_both_measures_are_signed_for_the_position(self):
        """A variant that worked out the direction itself would get a put wrong sooner or
        later, and getting it wrong inverts the stop and the target without raising."""
        from backtest.signals.exit_context import exit_context

        ctx = exit_context(premium_ratio=-0.4, underlying_move=-0.02,
                           days_held=3, days_to_expiry=20)
        assert get("stop_loss", "premium").bind({"fraction": 0.3}).__call__(ctx)
        assert get("stop_loss", "underlying").bind({"fraction": 0.01}).__call__(ctx)
        assert not get("take_profit", "premium").bind({"fraction": 0.3}).__call__(ctx)

    def test_a_missing_measure_does_not_fire(self):
        """NaN must read as "cannot tell", never as a level crossed."""
        from backtest.signals.exit_context import exit_context

        ctx = exit_context(premium_ratio=float("nan"), underlying_move=float("nan"),
                           days_held=3, days_to_expiry=20)
        assert not get("stop_loss", "premium").bind({"fraction": 0.3}).__call__(ctx)
        assert not get("stop_loss", "underlying").bind({"fraction": 0.01}).__call__(ctx)
