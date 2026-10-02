"""The component definitions, which are now the interface's single source of truth.

An error here does not turn a test red by itself — the interface draws a wrong form
from it, and the form looks perfectly normal. So what is checked is not whether it
runs, but whether the definition is self-consistent and matches the real code.
"""

from __future__ import annotations

import pytest

from backtest.components import (
    COMPONENTS,
    COUPLINGS,
    LAYERS,
    as_form_spec,
    describe,
    gaps,
)


class TestDefinition:
    def test_every_component_says_what_it_decides(self):
        """Without a role a component is just a name."""
        for c in COMPONENTS:
            assert len(c.role) > 10, f"{c.name} does not say what it decides in a trade"
            assert c.output, f"{c.name} does not say what it outputs"

    def test_unfinished_components_say_what_is_missing(self):
        for c in COMPONENTS:
            if c.status != "done":
                assert c.gap, f"{c.name} is {c.status} but does not say what is missing"

    def test_discovered_components_say_how_to_find(self):
        """Marked as discovered without saying how is an invitation to guess."""
        for c in COMPONENTS:
            if c.origin == "discovered":
                assert len(c.how_to_find) > 50, f"{c.name} does not say how to find it"

    def test_every_component_sits_in_a_known_layer(self):
        known = {name for name, _ in LAYERS}
        for c in COMPONENTS:
            assert c.layer in known, f"{c.name}'s layer {c.layer!r} is not in LAYERS"

    def test_couplings_reference_real_components(self):
        keys = {c.key for c in COMPONENTS}
        for a, b, why in COUPLINGS:
            assert a in keys and b in keys, \
                f"a coupling references a component that does not exist: {a} / {b}"
            assert len(why) > 10

    def test_keys_unique(self):
        keys = [c.key for c in COMPONENTS]
        assert len(keys) == len(set(keys))


class TestFields:
    def test_field_names_are_unique_across_components(self):
        """A field name becomes the form's name directly, and a duplicate sends the
        backend the wrong value."""
        seen: dict[str, str] = {}
        for c in COMPONENTS:
            for f in c.inputs:
                assert f.name not in seen, \
                    f"{f.name} belongs to both {seen[f.name]} and {c.name}"
                seen[f.name] = c.name

    def test_select_fields_have_options_and_valid_default(self):
        for c in COMPONENTS:
            for f in c.inputs:
                if f.kind == "select":
                    assert f.options, f"{f.name} is a select with no options"
                    values = [v for v, _ in f.options]
                    assert f.default in values, f"{f.name}'s default is not an option"

    def test_depends_points_at_an_existing_field(self):
        """A wrong dependency makes the field **never appear**, and raises nothing."""
        names = {f.name for c in COMPONENTS for f in c.inputs}
        for c in COMPONENTS:
            for f in c.inputs:
                if f.depends:
                    src, _, want = f.depends.partition("=")
                    assert src in names, f"{f.name} depends on a field that does not exist: {src}"
                    assert want, f"{f.name}'s depends states no value"

    def test_depends_value_is_a_real_option(self):
        by_name = {f.name: f for c in COMPONENTS for f in c.inputs}
        for c in COMPONENTS:
            for f in c.inputs:
                if not f.depends:
                    continue
                src, _, want = f.depends.partition("=")
                opts = [v for v, _ in by_name[src].options]
                if opts:
                    assert want in opts, \
                        f"{f.name} depends on {src}={want}, which is not one of its options"

    def test_numeric_bounds_contain_the_default(self):
        for c in COMPONENTS:
            for f in c.inputs:
                if f.kind in ("number", "ratio", "int") and f.default is not None:
                    if f.lo is not None:
                        assert f.default >= f.lo, f"{f.name}'s default is below its floor"
                    if f.hi is not None:
                        assert f.default <= f.hi, f"{f.name}'s default is above its ceiling"


class TestFormSpec:
    def test_covers_every_component_exactly_once(self):
        spec = as_form_spec()
        listed = [c["key"] for L in spec for c in L["components"]]
        assert sorted(listed) == sorted(c.key for c in COMPONENTS)

    def test_layers_in_trading_order(self):
        """The order of the layers is the order things happen in a trade."""
        assert [L["layer"] for L in as_form_spec()] == [n for n, _ in LAYERS]

    def test_serialisable(self):
        """The interface fetches this definition as JSON — unserialisable means the
        interface cannot have it."""
        import json

        json.dumps(as_form_spec(), ensure_ascii=False)

    def test_gaps_generated_not_handwritten(self):
        """The gap list has to agree with the component statuses. A hand-written list
        goes stale once the feature lands."""
        assert len(gaps()) == sum(1 for c in COMPONENTS if c.status != "done" and c.gap)


class TestDescribe:
    def test_describe_lists_all(self):
        assert len(describe()) == len(COMPONENTS)

    def test_fields_match_real_run_parameters(self):
        """A form field has to be something backtest.run really knows, or filling it
        in has no effect and raises nothing."""
        import inspect

        from backtest import run

        sig = set(inspect.signature(run).parameters)
        # The front end assembles these under different names; they do not correspond
        # to a run() parameter directly.
        indirect = {
            "entry_mode", "entry_code", "review_code", "exit_mode", "delta", "right",
            "dte_lo", "dte_hi", "delta_lo", "delta_hi",
            "max_spread", "min_open_interest", "target_vol",
            "take_profit", "stop_loss", "hold_days", "dte_floor",
            "sizing_method", "risk_pct", "notional", "target_vol",
        }
        for c in COMPONENTS:
            for f in c.inputs:
                assert f.name in sig or f.name in indirect, (
                    f"{f.name} is neither a run() parameter nor registered as an "
                    f"indirect field — filling it in on the interface has no effect"
                )
