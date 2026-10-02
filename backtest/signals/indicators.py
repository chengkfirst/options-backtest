"""Technical indicators.

All of them are pure ``pd.Series -> pd.Series`` functions that **depend only on
data up to the day in question**. The warm-up period returns ``NaN``, which the
layer above turns into an explicit absence — never a shorter window or a filled
value, because that makes a warm-up signal look usable when it was in fact
computed with different parameters.

Indicators always take **adjusted prices**: the gaps on ex-dividend and split days
are changes of price system, not market movement, and traded prices make moving
averages and volatility jump spuriously on those days.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.config import TRADING_DAYS_PER_YEAR


def sma(series: pd.Series, window: int) -> pd.Series:
    _check_window(window)
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    _check_window(window)
    out = series.ewm(span=window, adjust=False, min_periods=window).mean()
    return out


def momentum(series: pd.Series, window: int) -> pd.Series:
    """Return over the window."""
    _check_window(window)
    return series.pct_change(window)


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    _check_window(window)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing; min_periods keeps the warm-up from producing a value.
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    # With no down day at all, RSI is 100 by definition, not absent.
    return out.mask(avg_loss.eq(0.0) & avg_gain.gt(0.0), 100.0)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    _check_window(window)
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0) -> dict[str, pd.Series]:
    _check_window(window)
    mid = sma(series, window)
    sd = series.rolling(window, min_periods=window).std(ddof=0)
    return {
        "mid": mid,
        "upper": mid + num_std * sd,
        "lower": mid - num_std * sd,
        "width": (2 * num_std * sd) / mid,
    }


def realized_vol(series: pd.Series, window: int = 20) -> pd.Series:
    """Realised volatility, annualised on **trading days / 252**.

    Volatility is only realised on trading days, hence this convention; pricing's
    time to expiry uses calendar days / 365, because an option decays over the
    weekend. The two conventions have different jobs, and forcing them into one
    makes one of them wrong.
    """
    _check_window(window)
    log_ret = np.log(series).diff()
    return log_ret.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(
        TRADING_DAYS_PER_YEAR
    )


def _check_window(window: int) -> None:
    if window < 1:
        raise ValueError(f"the window must be a positive integer, got {window}")


def rank_in_window(series: pd.Series, window: int) -> pd.Series:
    """Where the current value sits in the **range** of the last ``window`` days,
    0 to 1.

    This is what a trading desk calls IV rank:
    ``(today − range low) / (range high − range low)``. It answers "dear or cheap
    relative to its own history" — absolute IV numbers are not comparable, and 20%
    is high for some underlyings and low for others.
    """
    lo = series.rolling(window, min_periods=window // 2).min()
    hi = series.rolling(window, min_periods=window // 2).max()
    span = hi - lo
    return ((series - lo) / span.where(span > 0)).astype(float)


def percentile_in_window(series: pd.Series, window: int) -> pd.Series:
    """The current value's **percentile** among the last ``window`` days, 0 to 1.

    The difference from ``rank_in_window`` is routinely overlooked: IV rank sees
    only the two ends of the range, so one extreme spike depresses it for a long
    time, while IV percentile asks "how many days were below today" and is
    insensitive to spikes. Reading both together is what tells you whether
    something is genuinely cheap or merely dragged down by one outlier.
    """
    return series.rolling(window, min_periods=window // 2).apply(
        lambda w: (w[:-1] < w[-1]).mean(), raw=True
    ).astype(float)
