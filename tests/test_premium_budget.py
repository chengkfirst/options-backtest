"""The premium budget: the document's "core budget measure", and what it refuses.

## Why this limit and not the greeks limits

Delta and vega were limited before this and premium at risk was not, and the two answer
different questions. Delta says how the book moves on the next dollar of underlying.
Premium at risk says what the book loses if every option in it expires worthless.

For a long-options book the second has a floor under it and the first does not, and it is
the only limit in the layer whose breach is a **certainty** rather than a scenario: an
option held to expiry out of the money loses all of its premium with probability one.

## The test that justifies the per-expiry limit existing

``test_the_total_budget_cannot_see_a_concentration`` is the reason the budget has three
numbers rather than one. Ten percent of equity spread over six expiries loses at most a
sixth of it on any one Friday; the same ten percent on one expiry is all of it at once,
and a total limit passes both identically.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.accounting.ledger import Position
from backtest.core.types import ContractId, Right
from backtest.portfolio.constraints import PremiumBudget
from tests.kinds import kind, labels

pytestmark = labels("unit", "3")

EQUITY = 100_000.0


def _held(premium: float, expiry: date, *, quantity: int = 1) -> Position:
    """A long position whose cost basis is exactly ``premium``."""
    return Position(
        contract=ContractId("QQQ", expiry, Right.CALL, 400.0),
        quantity=quantity,
        avg_price=premium / (quantity * 100),
        opened=date(2024, 1, 2),
        trade_id=1,
    )


class TestTheTotalBudget:
    def test_an_entry_inside_the_budget_is_allowed(self):
        budget = PremiumBudget(max_premium_pct=0.10)
        allowed, why = budget.check([], 0.0, EQUITY, adding=4_000.0)
        assert allowed and why == ""

    def test_an_entry_that_would_cross_it_is_refused(self):
        budget = PremiumBudget(max_premium_pct=0.10)
        allowed, why = budget.check([], 0.0, EQUITY, adding=12_000.0)
        assert not allowed
        assert "12.0%" in why and "10.0%" in why, (
            f"the refusal has to name both the figure and the limit, or a reader cannot "
            f"tell by how much: {why!r}"
        )

    def test_what_is_already_open_counts(self):
        """**The check is on the book as it would be.** A limit tested only against what
        is already held is a limit the next trade always passes."""
        budget = PremiumBudget(max_premium_pct=0.10)
        open_premium = [_held(8_000.0, date(2024, 3, 15))]
        assert budget.check(open_premium, 0.0, EQUITY, adding=1_000.0)[0]
        assert not budget.check(open_premium, 0.0, EQUITY, adding=3_000.0)[0]

    def test_a_short_position_is_not_premium_at_risk(self):
        """Matching ``Snapshot.premium_at_risk``: a short's collateral is capital tied up
        and comes back unless the option is exercised, so counting it as premium at risk
        would overstate what the book can lose and block trades for the wrong reason."""
        short = Position(
            contract=ContractId("QQQ", date(2024, 3, 15), Right.PUT, 400.0),
            quantity=-1, avg_price=90.0, opened=date(2024, 1, 2), trade_id=2,
        )
        budget = PremiumBudget(max_premium_pct=0.10)
        allowed, why = budget.check([short], 0.0, EQUITY, adding=9_000.0)
        assert allowed, (
            f"a $9,000 short premium was counted against a long-premium budget: {why}"
        )


class TestThePerExpiryBudget:
    @kind("guard")
    def test_the_total_budget_cannot_see_a_concentration(self):
        """**Why there are three numbers and not one.**

        Two books, identical total premium at 10% of equity. One is spread over five
        expiries, the other is all on one Friday. The total limit passes both; only the
        per-expiry limit can tell them apart, and the difference between them is whether
        a single expiry can take the whole budget to zero at once.
        """
        spread = [_held(2_000.0, date(2024, m, 15)) for m in (3, 4, 5, 6, 7)]
        concentrated = [_held(10_000.0, date(2024, 3, 15))]

        total_only = PremiumBudget(max_premium_pct=0.11)
        assert total_only.check(spread, 0.0, EQUITY, adding=0.0,
                                expiry=date(2024, 3, 15))[0]
        assert total_only.check(concentrated, 0.0, EQUITY, adding=0.0,
                                expiry=date(2024, 3, 15))[0], (
            "the total-only budget is supposed to pass both -- that is the point being "
            "made. If it refuses one of them this test proves nothing"
        )

        with_per_expiry = PremiumBudget(max_premium_pct=0.11,
                                        max_premium_per_expiry_pct=0.05)
        assert with_per_expiry.check(spread, 0.0, EQUITY, adding=0.0,
                                     expiry=date(2024, 3, 15))[0]
        allowed, why = with_per_expiry.check(concentrated, 0.0, EQUITY, adding=0.0,
                                             expiry=date(2024, 3, 15))
        assert not allowed and "one day" in why

    def test_only_the_candidates_own_expiry_is_counted(self):
        """A position on another expiry is irrelevant to this one's concentration, and
        counting it would turn the per-expiry limit into a second total."""
        elsewhere = [_held(9_000.0, date(2024, 9, 20))]
        budget = PremiumBudget(max_premium_per_expiry_pct=0.05)
        assert budget.check(elsewhere, 0.0, EQUITY, adding=4_000.0,
                            expiry=date(2024, 3, 15))[0]

    def test_without_an_expiry_the_per_expiry_limit_does_not_fire(self):
        """It cannot: there is nothing to group by. Said explicitly because the silent
        alternative -- grouping everything into one bucket -- would refuse trades under a
        limit nobody set."""
        budget = PremiumBudget(max_premium_per_expiry_pct=0.05)
        assert budget.check([_held(9_000.0, date(2024, 3, 15))], 0.0, EQUITY,
                            adding=0.0)[0]


class TestTheCashFloor:
    def test_cash_below_the_floor_after_the_entry_refuses_it(self):
        """The other side of the same budget: a book can be inside its premium limit and
        still have nothing left to pay a fee or meet an assignment with."""
        budget = PremiumBudget(min_cash_pct=0.20)
        assert budget.check([], 30_000.0, EQUITY, adding=5_000.0)[0]
        allowed, why = budget.check([], 30_000.0, EQUITY, adding=15_000.0)
        assert not allowed and "floor" in why


class TestWhatItRefusesToBeConfiguredAs:
    @pytest.mark.parametrize("value", [0.0, -0.1, 1.5])
    def test_a_share_outside_zero_to_one_is_refused(self, value):
        """A budget above equity is not a budget; one at or below zero forbids every
        trade. Both are configuration errors that would otherwise show up as a strategy
        that mysteriously never trades, or one that is never constrained."""
        with pytest.raises(ValueError, match="share of equity"):
            PremiumBudget(max_premium_pct=value)

    def test_equity_that_is_not_positive_refuses_rather_than_dividing(self):
        allowed, why = PremiumBudget(max_premium_pct=0.10).check([], 0.0, 0.0,
                                                                 adding=1.0)
        assert not allowed and "equity" in why

    def test_a_budget_with_no_limits_set_is_inactive(self):
        """So the engine can skip it rather than run three None checks per candidate --
        and so "no budget" and "a budget of nothing" are different states."""
        assert not PremiumBudget().active
        assert PremiumBudget(min_cash_pct=0.01).active


class TestTheEngineReadsIt:
    """The unit tests prove the budget binds. These prove the engine asks it before
    placing an order, which is the half a limit living in a report never has."""

    RUN = dict(
        symbol="QQQ", start="2023-01-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    @kind("limit")
    def test_a_tight_budget_blocks_trades_and_a_loose_one_does_not(self):
        """Both halves, because each is meaningless alone: a budget that blocks
        everything is not enforcement, and one that blocks nothing is not a budget.

        An ordering rather than counts -- how many trades a given budget removes on this
        data is a market outcome, and that research has not been done.
        """
        from backtest.api.sdk import run

        base = len(run(**self.RUN).trades)
        assert base > 3, "too few trades for a budget to have anything to block"

        loose = len(run(**(self.RUN | {
            "premium_budget": {"max_premium_pct": 1.0}})).trades)
        assert loose == base, (
            "a budget of the whole account changed the result, so it is not the budget "
            "being read"
        )

        tight = len(run(**(self.RUN | {
            "premium_budget": {"max_premium_pct": 0.001}})).trades)
        assert tight < base, (
            "a budget of a tenth of a percent blocked nothing, so the engine is not "
            "consulting it before placing an order"
        )

    @kind("limit")
    def test_a_blocked_entry_says_what_the_figure_and_the_limit_were(self):
        from backtest.api.sdk import run
        from backtest.core.run_log import EventKind

        result = run(**(self.RUN | {"premium_budget": {"max_premium_pct": 0.001}}))
        blocked = [e for e in result.raw.events.of_kind(EventKind.CANDIDATE_DROPPED)
                   if "premium budget" in e.detail]
        assert blocked, "entries were blocked and nothing said why"
        assert "of equity" in blocked[0].detail and "budget" in blocked[0].detail

    @kind("guard")
    def test_two_budgets_do_not_share_a_run_id(self):
        """It blocks entries, so it changes which trades happen. A setting that moves the
        result and does not reach the fingerprint makes a search look smaller than it
        was -- the sixth time this class of gap has had to be checked for."""
        from backtest.api.sdk import run

        def run_id(**over):
            return run(**(self.RUN | over)).manifest()["run_id"]

        assert run_id() != run_id(premium_budget={"max_premium_pct": 0.01})

    @kind("guard")
    def test_the_trade_table_engine_refuses_it(self):
        """It evaluates each trade independently with no book, and a total is a total of
        a book."""
        from backtest.api.sdk import run
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError) as caught:
            run(**(self.RUN | {"engine": "trade_table",
                                "premium_budget": {"max_premium_pct": 0.1}}))
        assert "no book" in str(caught.value)

    def test_an_unknown_setting_is_refused(self):
        """A misspelled key accepted silently is a researcher who believes the book is
        budgeted and is reading the unbudgeted result."""
        from backtest.api.sdk import run
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError) as caught:
            run(**(self.RUN | {"premium_budget": {"max_premium": 0.1}}))
        assert "max_premium" in str(caught.value)

    def test_an_empty_budget_is_refused_rather_than_ignored(self):
        from backtest.api.sdk import run
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError, match="no budget"):
            run(**(self.RUN | {"premium_budget": {}}))

    def test_a_share_outside_the_range_is_refused_at_startup(self):
        """Not at the first candidate. The ValueError the dataclass raises would
        otherwise surface mid-run as a traceback rather than as a setting that is wrong.
        """
        from backtest.api.sdk import run
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError, match="share of equity"):
            run(**(self.RUN | {"premium_budget": {"max_premium_pct": 2.0}}))
