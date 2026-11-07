"""The strategy's return, the account's return, and the excess — three numbers, three names.

## What was wrong

The ledger keeps interest as its own term of the accounting identity, deliberately: a book
that sat in cash all year would earn it, and folding it into `realised` would credit the
strategy with the risk-free rate. The README states the size of the effect — three years
on $100,000 at 4.5% is about $9,300 of interest against a fixture strategy P&L of −473.

The reporting layer then folded it back in. `equity_mid` is cash plus holdings and the cash
carries every dollar of interest earned, so `total_return` was the account's return being
read as the strategy's, and the exported equity frame did not even carry the `interest`
column to separate them with.

Sharpe was worse than inconsistent: `annualised / vol` on a curve that *included* the
interest is the risk-free rate added once and subtracted never — flattering twice over. At
a 4.5% rate against a 20–40% vol that is 0.1 to 0.2 of Sharpe.
"""

from __future__ import annotations

import numpy as np
import pytest

from tests.kinds import kind, labels

pytestmark = labels("unit", "5")

RUN = dict(
    symbol="QQQ", start="2021-01-04", end="2023-12-29",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


@pytest.fixture(scope="module")
def result():
    from backtest.api.sdk import run

    return run(**RUN)


class TestTheEquityFrameCarriesWhatSeparatesThem:
    @kind("guard")
    def test_interest_and_premium_at_risk_are_columns(self):
        """Both are on the snapshots and both were dropped from the exported frame, which
        left every reader of the curve unable to do this separation at all."""
        from backtest.api.sdk import run

        equity = run(**RUN).raw.equity
        for column in ("interest", "premium_at_risk", "strategy_equity"):
            assert column in equity.columns, f"the equity frame has no {column}"

    def test_the_strategy_curve_is_the_account_curve_less_the_interest(self, result):
        equity = result.raw.equity
        difference = equity["equity_mid"] - equity["strategy_equity"]
        assert np.allclose(difference.to_numpy(), equity["interest"].to_numpy())

    def test_the_interest_is_not_nothing_on_this_fixture(self, result):
        """Otherwise every test here passes trivially and proves nothing."""
        assert result.raw.equity["interest"].iloc[-1] > 1_000


class TestThreeReturnsWithThreeNames:
    def test_the_strategy_return_is_below_the_account_return(self, result):
        """Interest is positive, so the account always does better than the trading. A
        test for the direction, not for a figure."""
        m = result.equity_metrics()
        assert m["strategy_total_return"] < m["account_total_return"]
        assert m["total_return"] == m["account_total_return"], (
            "total_return keeps its old name and its old meaning — it was always the "
            "account's figure. The new name is an alias, not a redefinition"
        )

    def test_the_gap_is_the_interest(self, result):
        m = result.equity_metrics()
        equity = result.raw.equity
        opening = float(equity["equity_mid"].iloc[0])
        gap = (m["account_total_return"] - m["strategy_total_return"]) * opening
        # Not exact: the two returns have different denominators by construction (the
        # strategy curve's opening value equals the account's only on day one, where no
        # interest has accrued yet). Within a percent of the interest is the claim.
        assert abs(gap - m["interest_earned"]) < abs(m["interest_earned"]) * 0.01

    @kind("guard")
    def test_sharpe_is_on_the_excess_return(self, result):
        """The definition, and the thing that was wrong: the rate was added once through
        the interest and subtracted never."""
        m = result.equity_metrics()
        assert m["excess_annualised"] == pytest.approx(
            m["annualised"] - m["risk_free_rate"])
        assert m["sharpe"] == pytest.approx(m["excess_annualised"] / m["annualised_vol"])
        assert m["sharpe"] < m["annualised"] / m["annualised_vol"], (
            "the excess Sharpe must be below the raw one whenever the rate is positive; "
            "if it is not, the deduction is not happening"
        )

    @kind("guard")
    def test_the_rate_deducted_is_the_rate_the_interest_was_earned_at(self, result):
        """Deducting the config's fallback while accruing at the market's series would
        subtract a different number from the one that was added — the mismatch this whole
        separation exists to remove."""
        m = result.equity_metrics()
        config_fallback = result.raw.config.fallback_risk_free_rate
        assert m["risk_free_rate"] != pytest.approx(config_fallback), (
            "the rate used equals the config fallback, so the real series is not being "
            "read (on this fixture the two differ)"
        )
        assert 0.0 < m["risk_free_rate"] < 0.15

    def test_the_basis_is_stated_in_words(self, result):
        """A reader should not have to know which of three returns a ratio is on."""
        basis = result.equity_metrics()["ratio_basis"]
        assert "excess" in basis and "risk-free" in basis and "strategy" in basis


class TestSortinoToo:
    @kind("guard")
    def test_sortino_is_on_the_excess_return_and_calmar_is_not(self, result):
        """Sortino is a risk-adjusted ratio and takes the excess. Calmar is return over
        the worst drawdown — and the drawdown is the account's, so its numerator is the
        account's return. Different definitions, not an inconsistency."""
        view = result.investor_view()
        assert view["Sortino"] == pytest.approx(
            view["excess_annualised"] / view["downside_vol"])
        assert view["Calmar"] != pytest.approx(
            view["excess_annualised"] / abs(result.equity_metrics()["max_drawdown"]))

    def test_the_investor_view_reports_the_strategy_figure_too(self, result):
        view = result.investor_view()
        assert view["strategy_annualised"] < view["annualised"]
        assert view["interest_earned"] > 0


class TestTheCashRateIsAnAssumption:
    @kind("guard")
    def test_the_share_of_the_rate_the_cash_earns_is_in_the_register(self):
        """Paying the full Treasury rate on every dollar is a claim about the broker. A
        retail sweep usually pays below Treasury and pledged cash pays nothing, so this
        belongs in group 1 — sensitivity-tested, never tuned."""
        from backtest.core.thresholds import by_group, get

        assert get("cash_interest_share") == 1.0
        names = [t.name for t in by_group("realism")]
        assert "cash_interest_share" in names

    def test_a_lower_share_books_less_interest(self):
        """The mechanism, on the ledger directly — a sensitivity test has to be able to
        move it."""
        from datetime import date

        from backtest.accounting.ledger import Ledger
        from backtest.core.types import ContractSpec

        def earned(share: float) -> float:
            import backtest.core.thresholds as thresholds

            original = thresholds.get
            thresholds.get = lambda name: (share if name == "cash_interest_share"
                                           else original(name))
            try:
                ledger = Ledger(100_000.0, ContractSpec(symbol="QQQ", vol_proxy="^VXN"))
                return ledger.accrue_interest(date(2024, 1, 2), 0.05, days=365)
            finally:
                thresholds.get = original

        full = earned(1.0)
        half = earned(0.5)
        assert full == pytest.approx(5_000.0)
        assert half == pytest.approx(2_500.0)
        assert earned(0.0) == 0.0
