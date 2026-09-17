"""可缺失值、合约标识、配置校验、错误分层。"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.config import BacktestConfig, CALENDAR_DAYS_PER_YEAR, TimeConventions
from backtest.errors import StartupError
from backtest.maybe import Maybe, MissingValueError
from backtest.types import ContractId, ExitReason, Right, resolve_exit_reason


class TestMaybe:
    def test_some_and_unwrap(self):
        assert Maybe.some(3.5).unwrap() == 3.5

    def test_missing_carries_reason(self):
        m = Maybe.missing("无报价")
        assert m.is_missing and m.reason == "无报价"

    def test_unwrap_missing_raises(self):
        with pytest.raises(MissingValueError):
            Maybe.missing("无报价").unwrap()

    def test_some_rejects_none(self):
        with pytest.raises(ValueError):
            Maybe.some(None)

    def test_missing_requires_reason(self):
        with pytest.raises(ValueError):
            Maybe.missing("")

    def test_bool_is_blocked(self):
        """防止 `if maybe:` —— 那会把缺失悄悄当成 falsy。"""
        with pytest.raises(TypeError):
            bool(Maybe.some(1.0))

    def test_or_else_does_not_hide_missing(self):
        m = Maybe.missing("预热期")
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
        """日线看不到盘中先后，同日两者都触及时取亏损一侧，不高估表现。"""
        assert (
            resolve_exit_reason([ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS])
            is ExitReason.STOP_LOSS
        )

    def test_order_independent(self):
        pair = [ExitReason.TIME_EXIT, ExitReason.DTE_FLOOR]
        assert resolve_exit_reason(pair) is resolve_exit_reason(list(reversed(pair)))


class TestConfigValidation:
    def test_min_dte_must_exceed_execution_lag(self):
        """下限小于 lag+1 时，了结意图会落空，仓位被静默拖到到期。"""
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
        """30 个日历日（含 8 个非交易日）按 30/365，不折算成交易日。"""
        t = TimeConventions()
        got = t.year_fraction(date(2024, 3, 1), date(2024, 3, 31))
        assert got == pytest.approx(30 / CALENDAR_DAYS_PER_YEAR)

    def test_weekend_decays(self):
        """周五到周一是三个日历日，不是一个交易日。"""
        t = TimeConventions()
        friday, monday = date(2024, 3, 1), date(2024, 3, 4)
        expiry = date(2024, 4, 1)
        assert t.year_fraction(friday, expiry) - t.year_fraction(monday, expiry) == pytest.approx(
            3 / CALENDAR_DAYS_PER_YEAR
        )

    def test_two_bases_are_distinct(self):
        d = TimeConventions().describe()
        assert d["pricing"] != d["vol_annualization"]
