"""指标正确性、预热期、以及最重要的一项：无前视。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.config import TRADING_DAYS_PER_YEAR
from backtest.data.underlying import UnderlyingBars
from backtest.errors import StartupError
from backtest.signals import indicators
from backtest.signals.registry import SignalSet

#: 截断重算抽查的日期数。前视偏差最致命且最难靠肉眼发现，这是唯一可靠的防线。
PROBE_COUNT = 12


class TestIndicators:
    def test_sma_matches_manual(self, market):
        close = market.bars.adjusted("close")
        got = indicators.sma(close, 20).dropna()
        want = close.rolling(20).mean().dropna()
        assert np.allclose(got, want)

    def test_warmup_is_missing_not_filled(self, market):
        """预热期绝不能用更短窗口或填充值代替——那是另一套参数算出来的。"""
        got = indicators.sma(market.bars.adjusted("close"), 20)
        assert got.iloc[:19].isna().all() and not np.isnan(got.iloc[19])

    def test_rsi_bounded(self, market):
        rsi = indicators.rsi(market.bars.adjusted("close"), 14).dropna()
        assert (rsi >= 0).all() and (rsi <= 100).all()

    def test_atr_positive(self, market):
        b = market.bars
        atr = indicators.atr(b.adjusted("high"), b.adjusted("low"), b.adjusted("close"), 14)
        assert (atr.dropna() > 0).all()

    def test_realized_vol_uses_trading_day_annualisation(self):
        """波动率按交易日 / 252 年化；定价的日历日口径是另一回事。"""
        idx = pd.date_range("2022-01-03", periods=60, freq="B")
        series = pd.Series(np.exp(np.linspace(0, 0.1, 60)), index=idx)
        manual = np.log(series).diff().rolling(20).std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
        assert np.allclose(
            indicators.realized_vol(series, 20).dropna(), manual.dropna()
        )

    def test_bollinger_width_positive(self, market):
        bb = indicators.bollinger(market.bars.adjusted("close"), 20)
        assert (bb["width"].dropna() > 0).all()
        assert (bb["upper"].dropna() > bb["lower"].dropna()).all()

    @pytest.mark.parametrize("window", [0, -1])
    def test_rejects_bad_window(self, window):
        with pytest.raises(ValueError):
            indicators.sma(pd.Series([1.0, 2.0]), window)


class TestSignalSet:
    def test_registers_defaults(self, signals):
        assert {"sma_20", "sma_50", "rsi_14", "rv_20"} <= set(signals.names)

    def test_missing_is_explicit_not_zero(self, signals, market):
        """The warm-up reads as an explicit absence, not as the number 0."""
        value = signals.on(market.bars.dates[0])["sma_200"]
        assert value.is_missing and "no value" in value.reason

    def test_unknown_signal_fails_at_startup(self, signals):
        with pytest.raises(StartupError, match="unregistered signals"):
            signals.require(["sma_20", "typo"])

    def test_unknown_signal_raises_on_access(self, signals, market):
        with pytest.raises(KeyError):
            signals.on(market.sessions()[10])["nope"]

    def test_off_session_day_is_all_missing(self, signals):
        from datetime import date

        view = signals.on(date(2023, 1, 1))
        assert all(view[n].is_missing for n in view.names())


class TestNoLookahead:
    """截断重算：把数据截到 t 重算，日期 t 的每个信号必须逐位一致。

    代码审阅会漏掉前视，这类测试不会。
    """

    def test_truncated_recomputation_matches(self, market, signals):
        sessions = market.sessions()
        probes = sessions[:: max(1, len(sessions) // PROBE_COUNT)]
        for t in probes:
            truncated = UnderlyingBars(
                market.bars.symbol, market.bars.frame.loc[: pd.Timestamp(t)]
            )
            partial = SignalSet.from_bars(truncated)
            full_view, part_view = signals.on(t), partial.on(t)
            for name in signals.names:
                a, b = full_view[name], part_view[name]
                assert a.is_missing == b.is_missing, f"{name} 于 {t} 缺失状态不一致"
                if a.is_present:
                    assert a.unwrap() == pytest.approx(b.unwrap(), abs=1e-12), (
                        f"{name} 于 {t} 取值不一致"
                    )

    def test_detects_injected_lookahead(self, market):
        """故意引入 shift(-1) 时，同样的检查必须失败——否则这道防线是假的。"""
        leaky = {"leak": lambda b: b.adjusted("close").shift(-1)}
        full = SignalSet.from_bars(market.bars, extra=leaky)
        t = market.sessions()[40]
        truncated = UnderlyingBars(
            market.bars.symbol, market.bars.frame.loc[: pd.Timestamp(t)]
        )
        partial = SignalSet.from_bars(truncated, extra=leaky)
        assert full.on(t)["leak"].is_present
        assert partial.on(t)["leak"].is_missing
