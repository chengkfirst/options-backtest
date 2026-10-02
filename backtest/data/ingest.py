"""Fetching from external sources, with retries.

All market data comes from Yahoo Finance: underlying bars, the volatility index,
and the three-month Treasury yield (^IRX). One source rather than adding FRED as
well means one less point of failure, and ^IRX *is* the three-month bill, as the
design states.

Rate limits and network flakiness are retried with backoff; past the limit this
**raises**, rather than returning empty data — a market-data call that silently
returns nothing makes the backtest conclude "there were no signals in this
period".
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from backtest.core.errors import DataQualityError

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 3.0)


def _with_retry(fetch, what: str) -> pd.DataFrame:
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            frame = fetch()
            if frame is not None and not frame.empty:
                return frame
            last = DataQualityError(f"{what} returned no data")
        except Exception as exc:  # noqa: BLE001 - external APIs raise anything
            last = exc
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    raise DataQualityError(
        f"could not fetch {what} after {MAX_ATTEMPTS} attempts: {last}")


def _yf():
    import yfinance  # imported late: offline tests must not fail on it

    return yfinance


def fetch_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Daily OHLCV, carrying both the unadjusted prices and the adjusted close.

    ``auto_adjust=False`` is the point: we need **both sets of prices** —
    adjusted for signals, actually-traded for lining up with option strikes. Take
    only one and the other cannot be recovered.
    """

    def _fetch() -> pd.DataFrame:
        raw = _yf().download(
            symbol,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
        )
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        return raw

    frame = _with_retry(_fetch, f"{symbol} daily bars")
    required = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
    missing = required - set(frame.columns)
    if missing:
        raise DataQualityError(f"{symbol} bars are missing columns: {sorted(missing)}")
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame


def fetch_dividends(symbol: str) -> pd.Series:
    """Dividend events (date -> amount per share).

    When unavailable this returns an empty series but **records why in the
    series' ``attrs``**, which is what the caller logs. Swallowing the error
    would make "this underlying pays no dividend" indistinguishable from "the
    dividend call is down", and those two mean very different things for pricing.
    """

    def _fetch() -> pd.DataFrame:
        series = _yf().Ticker(symbol).dividends
        return series.to_frame("amount") if series is not None else pd.DataFrame()

    try:
        frame = _with_retry(_fetch, f"{symbol} dividends")
    except DataQualityError as exc:
        empty = pd.Series(dtype=float, name="amount")
        empty.attrs["unavailable_reason"] = str(exc)
        return empty
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame["amount"]


def fetch_close_series(symbol: str, start: date, end: date) -> pd.Series:
    """A close series, for things like the vol index and rates where only the
    close is needed."""
    frame = fetch_ohlcv(symbol, start, end)
    return frame["Close"].rename(symbol)
