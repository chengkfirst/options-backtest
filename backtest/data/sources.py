"""外部数据源的取数与重试。

全部行情来自 Yahoo Finance：标的日线、波动率指数、三个月期国债收益率（^IRX）。
用同一个来源而非再引入 FRED，是为了少一处失败点；口径上 ^IRX 就是三个月期国债，
与设计中写的一致。

限流与网络抖动按退避重试；超过上限后**明确报错**，而不是返回空数据——一个静默
返回空的行情接口会让回测跑出"这段时间没有信号"的假结论。
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from backtest.errors import DataQualityError

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 3.0)


def _with_retry(fetch, what: str) -> pd.DataFrame:
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            frame = fetch()
            if frame is not None and not frame.empty:
                return frame
            last = DataQualityError(f"{what} 返回空数据")
        except Exception as exc:  # noqa: BLE001 - 外部接口异常类型不稳定
            last = exc
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    raise DataQualityError(f"获取 {what} 失败（已重试 {MAX_ATTEMPTS} 次）：{last}")


def _yf():
    import yfinance  # 延迟导入：离线测试不应因为它而失败

    return yfinance


def fetch_ohlcv(symbol: str, start: date, end: date) -> pd.DataFrame:
    """日线 OHLCV，同时带回未复权价与复权收盘价。

    取 ``auto_adjust=False`` 是关键：我们需要**两套价格**——复权价用于信号，
    实际成交价用于与期权行权价对齐。只要其中一套，另一套就补不回来了。
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

    frame = _with_retry(_fetch, f"{symbol} 日线")
    required = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
    missing = required - set(frame.columns)
    if missing:
        raise DataQualityError(f"{symbol} 日线缺少字段：{sorted(missing)}")
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame


def fetch_dividends(symbol: str) -> pd.Series:
    """除息事件（日期 → 每股金额）。"""

    def _fetch() -> pd.DataFrame:
        series = _yf().Ticker(symbol).dividends
        return series.to_frame("amount") if series is not None else pd.DataFrame()

    try:
        frame = _with_retry(_fetch, f"{symbol} 分红")
    except DataQualityError:
        return pd.Series(dtype=float, name="amount")
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame["amount"]


def fetch_close_series(symbol: str, start: date, end: date) -> pd.Series:
    """收盘价序列。用于波动率指数与利率这类只需要收盘的序列。"""
    frame = fetch_ohlcv(symbol, start, end)
    return frame["Close"].rename(symbol)
