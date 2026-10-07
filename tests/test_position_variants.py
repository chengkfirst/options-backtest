"""Slot 6 as a slot: the four sizing methods, registered as variants.

The methods existed and were selectable through `sizing={"method": ...}`, and none was a
registered variant — so slot 6 read as "0 forms" and none of the tools built for slots
reached it: no variant swap, no slot sweep, no ablation, and nothing in the composer that
offers a slot's forms with their parameters.

**The arithmetic is unchanged.** Each variant is a thin call into `contracts_for`, which
stays the one implementation — two copies of a sizing rule is a drift nobody notices until
they disagree on one trade. The test that matters most here is the equivalence one.
"""

from __future__ import annotations

import pytest

from backtest.core.component import variants
from backtest.portfolio.positioning import REQUIRES
from tests.kinds import kind, labels

pytestmark = labels("unit", "3")

BASE = dict(
    symbol="QQQ", start="2022-06-01", end="2023-06-30",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven",
)


class TestEverySelectableMethodHasAForm:
    @kind("guard")
    def test_the_two_lists_agree(self):
        """`REQUIRES` is the one list of methods. A method selectable there with no
        registered form is a slot the research tools cannot reach — which is the state
        all four were in."""
        registered = {v.name for v in variants("position")}
        assert registered == set(REQUIRES), (
            f"selectable: {sorted(REQUIRES)}; registered: {sorted(registered)}"
        )

    @kind("guard")
    def test_each_form_declares_every_parameter_its_method_needs(self):
        """**`scale_by_vol` needs two and was listed with one**, which is how it and
        `fixed_notional` came to be offered by the interface and raise a TypeError the
        first time anyone picked them. The form and the method read the same list now."""
        for variant in variants("position"):
            names = {p.name for p in variant.params}
            assert names == set(REQUIRES[variant.name]), (
                f"{variant.name} needs {sorted(REQUIRES[variant.name])} and declares "
                f"{sorted(names)}"
            )

    def test_each_form_says_what_it_does(self):
        for variant in variants("position"):
            assert len(variant.describes) > 40, (
                f"{variant.name} is offered in the composer with no description"
            )


class TestTheArithmeticIsTheSame:
    @kind("consistency")
    @pytest.mark.parametrize("method,params,spec", [
        ("fixed", {"contracts": 3}, {"method": "fixed"}),
        ("risk_pct", {"risk_pct": 0.02}, {"method": "risk_pct", "risk_pct": 0.02}),
        ("fixed_notional", {"notional": 5000.0},
         {"method": "fixed_notional", "notional": 5000.0}),
        ("scale_by_vol", {"target_vol": 0.2, "risk_pct": 0.02},
         {"method": "scale_by_vol", "target_vol": 0.2, "risk_pct": 0.02}),
    ])
    def test_a_variant_and_the_spec_produce_the_same_run(self, method, params, spec):
        """**The test this refactor rests on.** A registered form that sizes differently
        from the spec it wraps would be a second sizing rule wearing the first one's
        name."""
        from backtest.api.sdk import run

        quantity = params.get("contracts", 1)
        by_spec = run(**(BASE | {"sizing": spec, "quantity": quantity}))
        by_variant = run(**(BASE | {
            "sizing": {"method": "fixed"}, "quantity": 1,
            "components": {"position": {"slot": "position", "variant": method,
                                         "params": params}}}))

        assert len(by_spec.trades) == len(by_variant.trades)
        assert (list(by_spec.trades["quantity"])
                == list(by_variant.trades["quantity"])), (
            f"{method} sized differently through the variant than through the spec"
        )


class TestTheEngineConsultsIt:
    @kind("limit")
    def test_a_variant_overrides_the_sizing_spec(self):
        """Instead of, never as well — the same rule as a swapped-in exit component."""
        from backtest.api.sdk import run

        result = run(**(BASE | {
            "sizing": {"method": "fixed"}, "quantity": 1,
            "components": {"position": {"slot": "position", "variant": "fixed",
                                         "params": {"contracts": 4}}}}))
        assert set(result.trades["quantity"]) == {4}, (
            "the spec said 1 and the variant said 4; the variant decides"
        )

    @kind("guard")
    def test_a_size_of_zero_drops_the_candidate_and_says_so(self):
        """Never rounded up to 1, which would turn a budget into a position — and the
        drop is recorded, because a trade that silently did not happen is the hardest
        kind of absence to notice."""
        from backtest.api.sdk import run
        from backtest.core.run_log import EventKind

        result = run(**(BASE | {
            "sizing": {"method": "fixed"}, "quantity": 1,
            "components": {"position": {"slot": "position",
                                         "variant": "fixed_notional",
                                         "params": {"notional": 1.0}}}}))
        assert result.trades.empty, "a $1 budget bought a contract"
        dropped = [e for e in result.raw.events.of_kind(EventKind.CANDIDATE_DROPPED)
                   if "position component sized" in e.detail]
        assert dropped, "nothing was sized and nothing said why"

    @kind("limit")
    def test_the_slot_can_be_swept_like_any_other(self):
        """The point of registering them: the slot tools now apply. Two parameter
        values, two different books."""
        from backtest.api.sdk import run

        small = run(**(BASE | {
            "sizing": {"method": "fixed"}, "quantity": 1,
            "components": {"position": {"slot": "position", "variant": "fixed_notional",
                                         "params": {"notional": 2000.0}}}}))
        large = run(**(BASE | {
            "sizing": {"method": "fixed"}, "quantity": 1,
            "components": {"position": {"slot": "position", "variant": "fixed_notional",
                                         "params": {"notional": 20000.0}}}}))
        # **Per trade, not in total.** The larger budget opens fewer trades, because
        # the capital constraint binds sooner -- 8 against 65 on this fixture -- so the
        # totals are close while the per-trade sizes differ by ten times. Asserting the
        # total would have been asserting the capital constraint.
        small_each = float(small.trades["quantity"].median())
        large_each = float(large.trades["quantity"].median())
        assert large_each > small_each * 5, (
            f"a ten-times budget bought {large_each} contracts a trade against "
            f"{small_each}"
        )


class TestTwoMethodsHadNeverRun:
    """**Found by registering them as variants**, which is the argument for doing it.

    `contracts_for` computed the risk-based budget *above* the branch that uses it, and
    `risk_pct` is None for `fixed_notional` — so two of the four advertised methods
    raised `TypeError: unsupported operand type(s) for *: 'float' and 'NoneType'` the
    moment anyone selected them. Offered by the interface, listed in `REQUIRES`, never
    run. "A feature that fails only when someone picks that one option" is the failure
    `REQUIRES` was written to prevent, and it had it.
    """

    @kind("guard")
    @pytest.mark.parametrize("method,params", [
        ("fixed", {"contracts": 2}),
        ("risk_pct", {"risk_pct": 0.02}),
        ("fixed_notional", {"notional": 5000.0}),
        ("scale_by_vol", {"target_vol": 0.2, "risk_pct": 0.02}),
    ])
    def test_every_method_sizes_a_position(self, method, params):
        from backtest.portfolio.positioning import Sizing, contracts_for

        count = contracts_for(Sizing(method=method, **params), equity=100_000.0,
                              premium=500.0, stop_loss=0.5, volatility=0.25)
        assert count > 0, f"{method} sized nothing on a plainly sizeable trade"

    def test_a_method_missing_a_parameter_names_it(self):
        """Named rather than multiplied. The TypeError said 'float and NoneType', which
        tells a reader about the arithmetic instead of about their configuration."""
        from backtest.core.errors import StartupError
        from backtest.portfolio.positioning import Sizing, contracts_for

        with pytest.raises(StartupError, match="needs risk_pct"):
            contracts_for(Sizing(method="scale_by_vol", target_vol=0.2),
                          equity=1e5, premium=500.0, stop_loss=0.5, volatility=0.25)

    def test_the_spec_refuses_it_at_startup_rather_than_at_the_first_trade(self):
        from backtest.core.errors import StartupError
        from backtest.portfolio.positioning import Sizing

        with pytest.raises(StartupError, match="risk_pct"):
            Sizing(method="scale_by_vol", target_vol=0.2)


class TestWhatTheVariantRefuses:
    def test_scale_by_vol_with_no_volatility_sizes_nothing(self):
        """Rather than inventing a figure — the rule `contracts_for` already applies."""
        from backtest.portfolio.positioning import _size_with

        assert _size_with("scale_by_vol", {"equity": 1e5, "premium": 500.0,
                                            "stop_loss": 0.5, "volatility": None},
                          target_vol=0.2, risk_pct=0.02) == 0

    def test_a_missing_premium_sizes_nothing(self):
        from backtest.portfolio.positioning import _size_with

        assert _size_with("risk_pct", {"equity": 1e5, "premium": None,
                                        "stop_loss": 0.5}, risk_pct=0.02) == 0

    def test_fixed_needs_neither_equity_nor_premium(self):
        """It is the one method that ignores both, which is why it is the one to use
        when the question is about the rule rather than about compounding."""
        from backtest.portfolio.positioning import _size_with

        assert _size_with("fixed", {}, contracts=3) == 3
