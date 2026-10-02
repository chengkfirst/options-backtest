"""The data layer's front door: construct once, then everything is in memory.

Collects the underlying's bars (both price sets), the trading calendar, the
risk-free rate series, dividend events and the volatility proxy. Every external
request happens at construction; after that the backtest can query repeatedly
without touching the network.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig, TRADING_DAYS_PER_YEAR
from backtest.data.cache import CacheKey, ParquetCache
from backtest.data.calendar import TradingCalendar, expiry_dates
from backtest.data.sources import fetch_close_series, fetch_dividends, fetch_ohlcv
from backtest.data.underlying import UnderlyingBars, build_bars
from backtest.errors import StartupError
from backtest.events import EventKind, EventLog
from backtest.maybe import Maybe

#: Each underlying's own volatility index. Nasdaq volatility runs systematically
#: above the S&P's, so reusing one proxy for both understates QQQ option prices
#: across the board.
VOL_PROXY_BY_SYMBOL = {"QQQ": "^VXN", "SPY": "^VIX", "IWM": "^RVX", "DIA": "^VXD"}

#: The three-month Treasury yield. Yahoo gives it as a percentage (4.5 = 4.5%).
RATE_SYMBOL = "^IRX"

#: Realised-volatility window (trading days) used when the proxy is unavailable.
FALLBACK_RV_WINDOW = 20


class MarketData:
    """All the market data a backtest needs."""

    def __init__(
        self,
        config: BacktestConfig,
        *,
        cache: ParquetCache | None = None,
        events: EventLog | None = None,
        refresh: bool = False,
        bars: UnderlyingBars | None = None,
        rates: pd.Series | None = None,
        vol_proxy: pd.Series | None = None,
        dividends: pd.Series | None = None,
    ) -> None:
        """Passing ``bars`` / ``rates`` / ``vol_proxy`` / ``dividends`` explicitly
        means nothing touches the network.

        This path is for offline use and for tests: a test has to pass reliably on
        a machine with no network, or it is not a regression defence but one more
        source of non-determinism.
        """
        self.config = config
        self.events = events or EventLog()
        self._cache = cache or ParquetCache()
        symbol = config.symbol

        expected = VOL_PROXY_BY_SYMBOL.get(symbol)
        if expected is not None and config.contract.vol_proxy != expected:
            raise StartupError(
                f"the volatility proxy for {symbol} should be {expected}, but the "
                f"config says {config.contract.vol_proxy}. Implied volatility levels "
                f"differ systematically between underlyings, and borrowing another "
                f"index shifts the whole synthetic chain."
            )

        # Indicators need a warm-up, so reach further back.
        fetch_start = config.start - timedelta(days=400)
        self._offline = bars is not None
        self._bars = bars if bars is not None else self._load_bars(symbol, fetch_start, config.end, refresh)
        self.calendar = TradingCalendar(self._bars.dates[0], self._bars.dates[-1])
        if self._offline:
            self._rates = rates if rates is not None else pd.Series(dtype=float)
            self._vol_proxy = vol_proxy if vol_proxy is not None else pd.Series(dtype=float)
            self._dividends = dividends if dividends is not None else pd.Series(dtype=float)
        else:
            self._rates = self._load_rate_series(fetch_start, config.end, refresh)
            self._vol_proxy = self._load_vol_proxy(
                config.contract.vol_proxy, fetch_start, config.end, refresh
            )
            self._dividends = self._load_dividends(symbol, refresh)
        self._note_truncation()
        self._rv = self._realized_vol()
        self._expiries = expiry_dates(
            self.calendar,
            include_monthly=config.synthetic.include_monthly,
            include_weekly=config.synthetic.include_weekly,
            weekly_start=config.synthetic.weekly_start,
        )

    def _note_truncation(self) -> None:
        """Say so when the available range is narrower than the one requested;
        never return incomplete data silently."""
        first, last = self._bars.dates[0], self._bars.dates[-1]
        if first > self.config.start:
            self.events.record(
                EventKind.RANGE_TRUNCATED,
                first,
                f"start requested {self.config.start}, available from {first}",
            )
        if last < self.config.end:
            self.events.record(
                EventKind.RANGE_TRUNCATED,
                last,
                f"end requested {self.config.end}, available to {last}",
            )

    # ---- loading ----
    def _load_bars(self, symbol: str, start: date, end: date, refresh: bool) -> UnderlyingBars:
        key = CacheKey("ohlcv", symbol, {"start": start, "end": end, "source": "yfinance", "prices": "raw+adj"})
        raw = self._cache.get_or_load(key, lambda: fetch_ohlcv(symbol, start, end), refresh=refresh)
        return build_bars(symbol, raw)

    def _load_series(self, kind: str, symbol: str, start: date, end: date, refresh: bool) -> pd.Series:
        key = CacheKey(kind, symbol, {"start": start, "end": end, "source": "yfinance", "prices": "close"})
        frame = self._cache.get_or_load(
            key, lambda: fetch_close_series(symbol, start, end).to_frame("close"), refresh=refresh
        )
        series = frame["close"].astype(float)
        series.index = pd.to_datetime(series.index)
        return series

    def _load_rate_series(self, start: date, end: date, refresh: bool) -> pd.Series:
        try:
            return self._load_series("rate", RATE_SYMBOL, start, end, refresh) / 100.0
        except Exception as exc:  # noqa: BLE001
            self.events.record(
                EventKind.RATE_FALLBACK, start,
                f"no rate series available; falling back to the constant throughout: {exc}"
            )
            return pd.Series(dtype=float)

    def _load_vol_proxy(self, symbol: str, start: date, end: date, refresh: bool) -> pd.Series:
        try:
            return self._load_series("volproxy", symbol, start, end, refresh) / 100.0
        except Exception as exc:  # noqa: BLE001
            self.events.record(
                EventKind.VOL_PROXY_FALLBACK, start,
                f"no volatility proxy available; falling back to realised "
                f"volatility throughout: {exc}"
            )
            return pd.Series(dtype=float)

    def _load_dividends(self, symbol: str, refresh: bool) -> pd.Series:
        key = CacheKey("dividends", symbol, {"source": "yfinance"})
        frame = self._cache.get_or_load(
            key, lambda: fetch_dividends(symbol).to_frame("amount"), refresh=refresh
        )
        if frame.empty:
            reason = frame.attrs.get("unavailable_reason")
            self.events.record(
                EventKind.DIVIDEND_UNAVAILABLE,
                self.config.start,
                f"could not fetch dividends for {symbol} ({reason}); treating as "
                f"no dividend"
                if reason
                else f"{symbol} has no dividend records in this range; treating as "
                     f"no dividend",
            )
            return pd.Series(dtype=float)
        series = frame["amount"].astype(float)
        series.index = pd.to_datetime(series.index)
        return series

    def _realized_vol(self) -> pd.Series:
        """Realised volatility, annualised on trading days / 252 — a separate job
        from pricing's calendar-day convention."""
        log_ret = np.log(self._bars.adjusted("close")).diff()
        return log_ret.rolling(FALLBACK_RV_WINDOW).std() * np.sqrt(TRADING_DAYS_PER_YEAR)

    # ---- queries ----
    @property
    def bars(self) -> UnderlyingBars:
        return self._bars

    @property
    def expiries(self) -> tuple[date, ...]:
        return self._expiries

    def sessions(self) -> tuple[date, ...]:
        """The trading days inside the backtest range (not the warm-up)."""
        return self.calendar.sessions_in(self.config.start, self.config.end)

    def spot(self, day: date) -> Maybe[float]:
        """The underlying's **traded** close. Everything option-related uses this
        and never the adjusted price."""
        return self._bars.traded_close_on(day)

    def risk_free_rate(self, day: date) -> float:
        """The risk-free rate for this session; the most recent earlier value when
        the series lacks the day, and the constant when there is none."""
        if self._rates.empty:
            return self.config.fallback_risk_free_rate
        ts = pd.Timestamp(day)
        prior = self._rates.loc[:ts]
        if prior.empty or pd.isna(prior.iloc[-1]):
            self.events.record(EventKind.RATE_FALLBACK, day,
                               "no rate data on or before this day; using the constant")
            return self.config.fallback_risk_free_rate
        return float(prior.iloc[-1])

    def dividends_between(self, start: date, end: date) -> float:
        """Total dividend per share in the range, excluding the start, summed
        from the actual ex-dividend events."""
        if self._dividends.empty:
            return 0.0
        mask = (self._dividends.index > pd.Timestamp(start)) & (
            self._dividends.index <= pd.Timestamp(end)
        )
        return float(self._dividends.loc[mask].sum())

    def dividend_yield(self, day: date, expiry: date, spot: float) -> float:
        """The continuous dividend yield pricing needs, derived from the actual
        ex-dividend events over the contract's life.

        A constant annual figure instead would erase the ex-dividend dates, and it
        is exactly their timing that decides whether an in-the-money call has
        early-exercise value.
        """
        years = self.config.time.year_fraction(day, expiry)
        if years <= 0 or spot <= 0:
            return 0.0
        cash = self.dividends_between(day, expiry)
        if cash <= 0:
            return 0.0
        return float(cash / (spot * years))

    @property
    def vol_proxy(self) -> pd.Series:
        """The full volatility-proxy series (QQQ -> VXN, SPY -> VIX).

        **This is real market data**, not synthetic. So judgements built on IV
        levels (IV rank, the IV minus RV spread) hold even on a synthetic option
        chain — what is synthetic is the option prices, not this.
        """
        return self._vol_proxy

    def volatility(self, day: date) -> Maybe[float]:
        """The volatility proxy for this day; falls back to the underlying's own
        realised volatility when unavailable."""
        ts = pd.Timestamp(day)
        if not self._vol_proxy.empty:
            prior = self._vol_proxy.loc[:ts]
            if not prior.empty and not pd.isna(prior.iloc[-1]):
                return Maybe.some(float(prior.iloc[-1]))
        prior_rv = self._rv.loc[:ts]
        if prior_rv.empty or pd.isna(prior_rv.iloc[-1]):
            return Maybe.missing(f"neither the volatility proxy nor realised "
                                 f"volatility is available on {day}")
        self.events.record(
            EventKind.VOL_PROXY_FALLBACK, day,
            "the volatility proxy lacks this day; using realised volatility"
        )
        return Maybe.some(float(prior_rv.iloc[-1]))
