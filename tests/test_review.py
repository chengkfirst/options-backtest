"""The daily review: dynamic exits, scaling in and out.

The danger here is not that it fails to run, it is that **the P&L comes out quietly
wrong**. After a reduction, the final record computes the price difference on the
remaining quantity only, and the P&L of the contracts sold earlier vanishes — the
run finishes and the numbers look fine. So these tests are about the trade log and
the ledger agreeing exactly.
"""

from __future__ import annotations

import pytest

from backtest.signals.review import Add, Close, PositionView, Reduce, Roll
from backtest.core.errors import StartupError

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "2")


class TestActions:
    def test_close_kind_validated(self):
        with pytest.raises(StartupError, match="unknown exit kind"):
            Close("x", kind="anything")

    @pytest.mark.parametrize("bad", [0.0, 1.0, 1.5, -0.2])
    def test_reduce_fraction_bounds(self, bad):
        with pytest.raises(StartupError, match="reduction must lie between"):
            Reduce(bad)

    def test_add_at_least_one(self):
        with pytest.raises(StartupError, match="at least 1 contract"):
            Add(0)

    def test_roll_refuses_loudly(self):
        """Rolling is unimplemented. Quietly treating it as a close would distort the
        win rate and the holding period, so it refuses."""
        with pytest.raises(StartupError, match="rolling is not implemented"):
            Roll(to_contract=None)


class TestEngineIntegration:
    BASE = dict(
        symbol="QQQ", start="2022-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 30, "take_profit": 1.0, "stop_loss": 0.5, "dte_floor": 7},
        engine="event_driven", sizing={"method": "risk_pct", "risk_pct": 0.03},
    )

    def _run(self, **extra):
        from backtest import run

        return run(**self.BASE, **extra)

    def test_reduce_keeps_one_logical_trade(self):
        """A reduction must not turn one trade into two — counted by position,
        something rolled three times becomes four and the win rate is distorted.

        The criterion is that **trade_id does not repeat**, not "the same total count
        as without reductions": size depends on equity, a reduction changes the equity
        path, and later trades' quantities change with it (computing to 0 means no
        trade), so the two variants' totals differ by construction.
        """
        def review(ctx, pos):
            return (Reduce(0.5, "cut half")
                    if (pos.days_held == 5 and pos.quantity >= 2) else None)

        r = self._run(review=review)
        t = r.trades
        assert len(t) == t.trade_id.nunique(), \
            "a reduction split one logical trade across several rows"
        events = r.events
        assert events.detail.astype(str).str.contains("reduced by").any(), \
            "no reduction actually happened in this run"

    def test_partial_close_pnl_is_not_lost(self):
        """The final record computes on the remaining quantity and naturally omits
        the contracts sold earlier — no error, just P&L that is short."""
        def review(ctx, pos):
            return (Reduce(0.5, "cut half")
                    if (pos.days_held == 5 and pos.quantity >= 2) else None)

        r = self._run(review=review)
        fees = sum(x.fees for x in r.raw.records)
        assert r.trades.net_pnl.sum() == pytest.approx(r.raw.ledger.realised - fees, abs=0.01)

    def test_dynamic_exit_changes_outcome(self):
        """The review really changes the result rather than being silently ignored."""
        def review(ctx, pos):
            return (Close("trailing take profit", kind="take_profit")
                    if pos.ratio > 0.2 else None)

        plain, dynamic = self._run(), self._run(review=review)
        assert dynamic.trades.holding_days.mean() < plain.trades.holding_days.mean()

    def test_static_rules_still_apply(self):
        """The static rules are a safety net a review cannot get round — otherwise a
        review written wrong drags a position into expiry."""
        r = self._run(review=lambda ctx, pos: None)
        assert (r.trades.holding_days <= 30).all()

    def test_bad_return_is_rejected(self):
        with pytest.raises(StartupError, match="must return one of Close"):
            self._run(review=lambda ctx, pos: "close it")

    def test_exception_carries_the_day(self):
        def review(ctx, pos):
            raise ValueError("I am broken")

        with pytest.raises(StartupError, match="the review function raised"):
            self._run(review=review)

    def test_trade_table_refuses_review(self):
        """The trade table computes exits vectorised and cannot review day by day —
        it refuses rather than approximating."""
        from backtest import run

        opts = {**self.BASE, "engine": "trade_table"}
        with pytest.raises(StartupError):
            run(**opts, review=lambda ctx, pos: Close("x"))
