"""The cost section tells the truth about what trading cost.

## What was wrong

A section headed "costs" listed commission, regulatory and slippage and totalled them.
For an option buyer the **spread** is the largest cost by several times, and it is not a
fee — it is paid inside the fill, a buy at the ask and a sell at the bid. With
`slippage_per_contract` at its default of 0, the row read as "trading is nearly free".

Measured on this project's own fixture over 2019–2024: fees 506, spread 3,396. **Six and
a half times.**

The consequence that mattered more than the display: `research/anomaly.py`'s "costs eat
the edge" rule read `fees_pct_of_gross`, so the one automatic check for "this edge will
not survive execution" could never see the dominant cost.

## And the denominator

`total / abs(gross)` is "fees as a share of the size of the loss" when gross is negative,
printed as "% of gross". It is now the premium traded — defined whatever the sign — plus
gross rebuilt at the mid, which is the only gross a spread can be a share of without
appearing on both sides of the division.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.research.metrics import cost_breakdown
from tests.kinds import kind, labels

pytestmark = labels("unit", "6")


def _trade(**over) -> dict:
    base = dict(
        trade_id=1, quantity=1, multiplier=100,
        entry_price=5.00, entry_mid=4.90, exit_price=7.00, exit_mid=7.20,
        gross_pnl=200.0, fee_commission=1.0, fee_regulatory=0.1, fee_slippage=0.0,
        net_pnl=198.9, entry_date=date(2024, 1, 2), exit_date=date(2024, 1, 31),
    )
    return base | over


class TestTheSpreadIsInTheTotal:
    @kind("known_answer")
    def test_the_total_is_fees_plus_spread(self):
        """Hand-computable: $30 of spread (10 in, 20 out) and $1.10 of fees."""
        out = cost_breakdown(pd.DataFrame([_trade()]))
        assert out["spread"] == pytest.approx(30.0)
        assert out["fees"] == pytest.approx(1.1)
        assert out["total"] == pytest.approx(31.1)

    @kind("guard")
    def test_the_fee_slippage_column_is_not_counted_twice(self):
        """`fee_slippage` is the explicit per-contract slippage assumption, a different
        thing from the spread. Adding them as one would double-count for anyone who set
        both."""
        plain = cost_breakdown(pd.DataFrame([_trade()]))
        with_fee = cost_breakdown(pd.DataFrame([_trade(fee_slippage=5.0)]))
        assert with_fee["spread"] == plain["spread"]
        assert with_fee["total"] == pytest.approx(plain["total"] + 5.0)

    @kind("guard")
    def test_a_table_with_no_quote_reports_none_not_zero(self):
        """**A zero here is the exact misreading this exists to remove.** A run recorded
        before the quote was kept beside the fill cannot measure the spread, and must say
        so rather than report that the fills were free."""
        frame = pd.DataFrame([_trade()]).drop(columns=["entry_mid", "exit_mid"])
        out = cost_breakdown(frame)
        assert out["spread"] is None
        assert "not zero" in out["spread_note"]
        assert out["total"] == pytest.approx(out["fees"])


class TestTheDenominator:
    def test_the_premium_denominator_works_whatever_the_sign(self):
        win = cost_breakdown(pd.DataFrame([_trade(gross_pnl=200.0)]))
        lose = cost_breakdown(pd.DataFrame([_trade(gross_pnl=-200.0)]))
        for out in (win, lose):
            assert out["premium_traded"] == pytest.approx(500.0)
            assert out["cost_pct_of_premium"] == pytest.approx(31.1 / 500.0)

    @kind("guard")
    def test_a_losing_run_gets_no_gross_ratio_and_says_why(self):
        """It used to print "2.0% of gross" where gross was a loss. A ratio to a loss is
        not a cost ratio."""
        out = cost_breakdown(pd.DataFrame([_trade(gross_pnl=-200.0)]))
        assert out["cost_pct_of_gross_at_mid"] is None
        assert "not positive" in out["gross_note"]

    @kind("guard")
    def test_the_gross_ratio_uses_gross_rebuilt_at_the_mid(self):
        """The spread is inside every fill price, so a ratio to plain gross has the
        spread on both sides of the division and understates it."""
        out = cost_breakdown(pd.DataFrame([_trade(gross_pnl=200.0)]))
        assert out["gross_pnl_at_mid"] == pytest.approx(230.0)
        assert out["cost_pct_of_gross_at_mid"] == pytest.approx(31.1 / 230.0)


class TestTheAnomalyRuleCanSeeIt:
    @kind("guard")
    def test_a_run_whose_spread_eats_the_edge_is_flagged(self):
        """The rule read `fees_pct_of_gross`. With $1 of fees and $300 of spread against
        $310 of gross at the mid, the old rule saw 0.3% and said nothing."""
        from backtest.research.anomaly import scan

        trades = pd.DataFrame([
            _trade(trade_id=i, entry_price=5.0, entry_mid=4.0, exit_price=5.3,
                   exit_mid=6.3, gross_pnl=30.0, fee_commission=0.1,
                   fee_regulatory=0.0, net_pnl=29.9)
            for i in range(1, 11)
        ])
        costs = cost_breakdown(trades)
        assert costs["spread"] > costs["fees"] * 50, "the fixture does not make the point"

        found = [f for f in scan(trades, metrics=None, costs=costs) if f.kind == "costs"]
        assert found, (
            f"the spread was {costs['spread']:,.0f} against "
            f"{costs['gross_pnl_at_mid']:,.0f} of gross at the mid and nothing was "
            f"reported"
        )
        assert "edge before costs" in found[0].headline

    @kind("guard")
    def test_no_edge_at_all_is_its_own_finding_rather_than_silence(self):
        """When gross at the mid is not positive there is no ratio, and the old rule
        returned nothing — the quietest outcome for the worst case."""
        from backtest.research.anomaly import scan

        trades = pd.DataFrame([
            _trade(trade_id=i, entry_price=5.0, entry_mid=4.9, exit_price=4.0,
                   exit_mid=4.1, gross_pnl=-100.0, net_pnl=-101.1)
            for i in range(1, 11)
        ])
        costs = cost_breakdown(trades)
        assert costs["gross_pnl_at_mid"] <= 0

        found = [f for f in scan(trades, metrics=None, costs=costs) if f.kind == "costs"]
        assert found and "no edge" in found[0].headline
        assert "no cost assumption will change that" in found[0].detail

    def test_a_cheap_run_is_not_flagged(self):
        """A rule that fires on everything is not a rule."""
        from backtest.research.anomaly import scan

        trades = pd.DataFrame([
            _trade(trade_id=i, entry_price=5.0, entry_mid=4.99, exit_price=9.0,
                   exit_mid=9.01, gross_pnl=400.0, net_pnl=398.9)
            for i in range(1, 11)
        ])
        costs = cost_breakdown(trades)
        assert not [f for f in scan(trades, metrics=None, costs=costs)
                    if f.kind == "costs"]


class TestOnARealRun:
    @kind("limit")
    def test_the_summary_names_the_spread_before_the_fees(self):
        from backtest.api.sdk import run

        result = run(
            symbol="QQQ", start="2023-01-01", end="2023-12-31",
            entry="close < sma_20 * 0.98",
            select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                    "delta_lo": 0.25, "delta_hi": 0.45},
            exit={"hold_days": 20, "dte_floor": 7},
            engine="event_driven", sizing={"method": "fixed"}, quantity=1,
        )
        text = result.summary(printout=False)
        section = text[text.index("— costs —"):]
        assert "spread (paid inside the fill)" in section
        assert "premium traded" in section

        costs = result.costs()
        assert costs["spread"] > costs["fees"], (
            f"on this fixture the spread ({costs['spread']:,.0f}) should exceed the fees "
            f"({costs['fees']:,.0f}); if it does not, the cost model changed and this "
            f"test's premise needs rechecking"
        )
