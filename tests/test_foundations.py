"""Maybe, contract ids, config validation, the error hierarchy."""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.config import BacktestConfig, CALENDAR_DAYS_PER_YEAR, TimeConventions
from backtest.core.errors import StartupError
from backtest.core.maybe import Maybe, MissingValueError
from backtest.core.types import ContractId, ExitReason, Right, resolve_exit_reason


class TestMaybe:
    def test_some_and_unwrap(self):
        assert Maybe.some(3.5).unwrap() == 3.5

    def test_missing_carries_reason(self):
        m = Maybe.missing("no quote")
        assert m.is_missing and m.reason == "no quote"

    def test_unwrap_missing_raises(self):
        with pytest.raises(MissingValueError):
            Maybe.missing("no quote").unwrap()

    def test_some_rejects_none(self):
        with pytest.raises(ValueError):
            Maybe.some(None)

    def test_missing_requires_reason(self):
        with pytest.raises(ValueError):
            Maybe.missing("")

    def test_bool_is_blocked(self):
        """Guards against `if maybe:`, which would read absence as falsy."""
        with pytest.raises(TypeError):
            bool(Maybe.some(1.0))

    def test_or_else_does_not_hide_missing(self):
        m = Maybe.missing("warm-up period")
        assert m.or_else(0.0) == 0.0 and m.is_missing


class TestContractId:
    def test_format(self):
        c = ContractId("QQQ", date(2024, 3, 15), Right.CALL, 400.0)
        assert str(c) == "QQQ_20240315_C_00400000"

    def test_round_trip(self):
        c = ContractId("SPY", date(2023, 12, 15), Right.PUT, 412.5)
        assert ContractId.parse(str(c)) == c

    def test_deterministic_across_constructions(self):
        a = ContractId("QQQ", date(2024, 1, 19), Right.CALL, 380.0)
        b = ContractId("QQQ", date(2024, 1, 19), Right.CALL, 380.0)
        assert str(a) == str(b) and a == b

    @pytest.mark.parametrize("bad", ["qqq", ""])
    def test_symbol_must_be_upper(self, bad):
        with pytest.raises(ValueError):
            ContractId(bad, date(2024, 1, 19), Right.CALL, 400.0)

    def test_rejects_non_positive_strike(self):
        with pytest.raises(ValueError):
            ContractId("QQQ", date(2024, 1, 19), Right.CALL, 0.0)


class TestExitPriority:
    def test_stop_loss_beats_take_profit(self):
        """Daily bars do not show intraday order; when both are touched on one day
        take the losing side, so performance is not flattered."""
        assert (
            resolve_exit_reason([ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS])
            is ExitReason.STOP_LOSS
        )

    def test_order_independent(self):
        pair = [ExitReason.TIME_EXIT, ExitReason.DTE_FLOOR]
        assert resolve_exit_reason(pair) is resolve_exit_reason(list(reversed(pair)))


class TestConfigValidation:
    def test_min_dte_must_exceed_execution_lag(self):
        """Below lag+1 an exit intent comes to nothing and the position is silently
        dragged into expiry."""
        with pytest.raises(StartupError, match="min_days_to_expiry"):
            BacktestConfig(execution_lag_days=1, min_days_to_expiry=1)

    def test_symbol_must_match_contract_spec(self):
        with pytest.raises(StartupError):
            BacktestConfig(symbol="SPY")

    def test_start_before_end(self):
        with pytest.raises(StartupError):
            BacktestConfig(start=date(2024, 1, 1), end=date(2023, 1, 1))

    def test_describe_is_serialisable(self):
        assert BacktestConfig().describe()["symbol"] == "QQQ"


class TestTimeConventions:
    def test_pricing_uses_calendar_days(self):
        """30 calendar days (8 of them non-trading) are 30/365, not converted to
        trading days."""
        t = TimeConventions()
        got = t.year_fraction(date(2024, 3, 1), date(2024, 3, 31))
        assert got == pytest.approx(30 / CALENDAR_DAYS_PER_YEAR)

    def test_weekend_decays(self):
        """Friday to Monday is three calendar days, not one trading day."""
        t = TimeConventions()
        friday, monday = date(2024, 3, 1), date(2024, 3, 4)
        expiry = date(2024, 4, 1)
        assert t.year_fraction(friday, expiry) - t.year_fraction(monday, expiry) == pytest.approx(
            3 / CALENDAR_DAYS_PER_YEAR
        )

    def test_two_bases_are_distinct(self):
        d = TimeConventions().describe()
        assert d["pricing"] != d["vol_annualization"]
