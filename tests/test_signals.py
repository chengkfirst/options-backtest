"""Indicator correctness, the warm-up period, and the most important one: no
look-ahead."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.core.config import TRADING_DAYS_PER_YEAR
from backtest.data.underlying import UnderlyingBars
from backtest.core.errors import StartupError
from backtest.signals import indicators
from backtest.signals.registry import SignalSet

#: How many dates the truncate-and-recompute check samples. Look-ahead is the most fatal
#: bias and the hardest to see by eye, and this is the only reliable defence against it.
PROBE_COUNT = 12


class TestIndicators:
    def test_sma_matches_manual(self, market):
        close = market.bars.adjusted("close")
        got = indicators.sma(close, 20).dropna()
        want = close.rolling(20).mean().dropna()
        assert np.allclose(got, want)

    def test_warmup_is_missing_not_filled(self, market):
        """The warm-up is never replaced by a shorter window or a filled value — that
        would have been computed with different parameters."""
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
        """Volatility is annualised on trading days / 252; pricing's calendar-day
        convention is a separate thing."""
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
    """Truncate and recompute: cut the data at t, recompute, and every signal on date t
    must match bit for bit.

    A code review misses look-ahead; a test like this does not.
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
                assert a.is_missing == b.is_missing, \
                    f"{name} disagrees on absence at {t}"
                if a.is_present:
                    assert a.unwrap() == pytest.approx(b.unwrap(), abs=1e-12), (
                        f"{name} disagrees in value at {t}"
                    )

    def test_detects_injected_lookahead(self, market):
        """With a shift(-1) planted deliberately, the same check must fail — otherwise
        this defence is fake."""
        leaky = {"leak": lambda b: b.adjusted("close").shift(-1)}
        full = SignalSet.from_bars(market.bars, extra=leaky)
        t = market.sessions()[40]
        truncated = UnderlyingBars(
            market.bars.symbol, market.bars.frame.loc[: pd.Timestamp(t)]
        )
        partial = SignalSet.from_bars(truncated, extra=leaky)
        assert full.on(t)["leak"].is_present
        assert partial.on(t)["leak"].is_missing
