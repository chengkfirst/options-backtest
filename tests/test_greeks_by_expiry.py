"""Greeks grouped by expiry — the axis the book-wide total cannot see.

The document's RiskModel row asks for "Greeks aggregation by underlying and expiry
bucket". The aggregate was one book-wide total, and for an options book the expiry axis is
the one that carries the concentration: a book with all its vega on one Friday is a
different book from one spreading it over six, and the total passes both identically.

The same argument as the premium budget's per-expiry limit, which was built first and left
the greeks behind.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.accounting.ledger import Position
from backtest.core.types import ContractId, Right
from backtest.portfolio.scenarios import greeks_by_expiry
from tests.kinds import kind, labels

pytestmark = labels("unit", "3")

DAY = date(2024, 1, 2)


def _position(expiry: date, *, quantity: int = 1, premium: float = 5.0,
              trade_id: int = 1) -> Position:
    return Position(
        contract=ContractId("QQQ", expiry, Right.CALL, 400.0 + trade_id),
        quantity=quantity, avg_price=premium, opened=DAY, trade_id=trade_id,
    )


class _Chain:
    """A chain returning fixed greeks, so the arithmetic is checkable by hand."""

    def __init__(self, greeks: dict, missing: set = ()):
        self.greeks, self.missing = greeks, set(missing)

    def series(self, contract, days):
        if contract.expiry in self.missing:
            return pd.DataFrame()
        return pd.DataFrame([self.greeks])


class TestTheGrouping:
    @kind("known_answer")
    def test_each_expiry_sums_its_own_positions(self):
        chain = _Chain({"delta": 0.5, "gamma": 0.01, "vega": 10.0, "theta": -2.0})
        march, june = date(2024, 3, 15), date(2024, 6, 21)
        out = greeks_by_expiry(
            [_position(march, trade_id=1), _position(march, trade_id=2),
             _position(june, trade_id=3)],
            chain, DAY, 400.0, 100_000.0)

        assert list(out["expiry"]) == [march, june]
        # Two positions x 0.5 delta x 100 multiplier.
        assert out.loc[0, "delta"] == pytest.approx(100.0)
        assert out.loc[1, "delta"] == pytest.approx(50.0)
        assert out.loc[0, "positions"] == 2 and out.loc[1, "positions"] == 1

    def test_days_to_expiry_is_on_each_row(self):
        chain = _Chain({"delta": 0.5, "gamma": 0.0, "vega": 1.0, "theta": 0.0})
        out = greeks_by_expiry([_position(date(2024, 2, 1))], chain, DAY, 400.0, 1e5)
        assert out.loc[0, "days_to_expiry"] == 30

    def test_premium_at_risk_is_bucketed_too(self):
        """The same figure the budget limits, per expiry, so the two agree on what a
        concentration is."""
        chain = _Chain({"delta": 0.0, "gamma": 0.0, "vega": 1.0, "theta": 0.0})
        out = greeks_by_expiry(
            [_position(date(2024, 3, 15), premium=4.0, trade_id=1),
             _position(date(2024, 3, 15), premium=6.0, trade_id=2)],
            chain, DAY, 400.0, 1e5)
        assert out.loc[0, "premium_at_risk"] == pytest.approx(1000.0)

    def test_a_short_position_is_not_premium_at_risk(self):
        chain = _Chain({"delta": 0.0, "gamma": 0.0, "vega": 1.0, "theta": 0.0})
        out = greeks_by_expiry([_position(date(2024, 3, 15), quantity=-1)],
                               chain, DAY, 400.0, 1e5)
        assert out.loc[0, "premium_at_risk"] == 0.0


class TestTheConcentrationItExistsToShow:
    @kind("guard")
    def test_one_expiry_and_six_look_different(self):
        """**What the book-wide total cannot say.** Same total vega either way."""
        chain = _Chain({"delta": 0.0, "gamma": 0.0, "vega": 10.0, "theta": 0.0})
        one = greeks_by_expiry(
            [_position(date(2024, 3, 15), trade_id=i) for i in range(1, 7)],
            chain, DAY, 400.0, 1e5)
        spread = greeks_by_expiry(
            [_position(date(2024, m, 15), trade_id=m) for m in range(3, 9)],
            chain, DAY, 400.0, 1e5)

        assert one["vega"].sum() == pytest.approx(spread["vega"].sum()), (
            "the two books must have the same total, or this is not the comparison "
            "the total is supposed to be blind to"
        )
        assert float(one["vega_share"].max()) == pytest.approx(1.0)
        assert float(spread["vega_share"].max()) < 0.2

    @kind("guard")
    def test_the_share_is_of_absolute_vega(self):
        """Two expiries with opposite vega net to nothing, and that is a calendar spread
        rather than a book without volatility risk. A signed denominator would report it
        as the second."""
        chain_long = _Chain({"delta": 0.0, "gamma": 0.0, "vega": 10.0, "theta": 0.0})

        class _Signed(_Chain):
            def series(self, contract, days):
                sign = 1.0 if contract.expiry.month == 3 else -1.0
                return pd.DataFrame([{**self.greeks, "vega": 10.0 * sign}])

        out = _Signed(chain_long.greeks).__class__(chain_long.greeks)
        frame = greeks_by_expiry(
            [_position(date(2024, 3, 15), trade_id=1),
             _position(date(2024, 6, 21), trade_id=2)],
            out, DAY, 400.0, 1e5)
        assert frame["vega"].sum() == pytest.approx(0.0)
        assert frame["vega_share"].sum() == pytest.approx(1.0)
        assert np.isfinite(frame["vega_share"]).all()


class TestAbsentGreeks:
    @kind("guard")
    def test_a_position_with_no_quote_is_counted_not_zeroed(self):
        """The same rule as the book-wide aggregate: a vega read as 0 makes a bucket look
        free of volatility exposure, which is the most dangerous misreading available."""
        march = date(2024, 3, 15)
        chain = _Chain({"delta": 0.5, "gamma": 0.0, "vega": 10.0, "theta": 0.0},
                       missing={march})
        out = greeks_by_expiry([_position(march)], chain, DAY, 400.0, 1e5)
        assert out.loc[0, "missing"] == 1
        assert out.loc[0, "positions"] == 1
        assert out.loc[0, "vega"] == 0.0, (
            "the sum of no readable positions is 0; what must not be lost is that it is "
            "a sum of nothing, which `missing` says"
        )

    def test_a_nan_greek_counts_as_missing(self):
        chain = _Chain({"delta": 0.5, "gamma": float("nan"), "vega": 10.0, "theta": 0.0})
        out = greeks_by_expiry([_position(date(2024, 3, 15))], chain, DAY, 400.0, 1e5)
        assert out.loc[0, "missing"] == 1

    def test_an_empty_book_returns_the_columns_rather_than_nothing(self):
        """A caller charting this should get an empty frame with the right shape, not a
        KeyError on the first column access."""
        out = greeks_by_expiry([], _Chain({}), DAY, 400.0, 1e5)
        assert out.empty
        for column in ("expiry", "vega", "vega_share", "premium_at_risk", "missing"):
            assert column in out.columns


class TestThroughTheResult:
    @kind("limit")
    def test_the_trade_table_engine_says_it_has_no_book(self):
        from backtest.api.sdk import run

        flat = run(symbol="QQQ", start="2022-06-01", end="2023-06-30",
                   entry="close < sma_20 * 0.98",
                   select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                           "delta_lo": 0.25, "delta_hi": 0.45},
                   exit={"hold_days": 20, "dte_floor": 7},
                   engine="trade_table", sizing={"method": "fixed"}, quantity=1)
        with pytest.raises(AttributeError, match="no book"):
            flat.greeks_by_expiry()
