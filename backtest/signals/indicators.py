"""技术指标。

全部是 ``pd.Series -> pd.Series`` 的纯函数，且**只依赖截至当日的数据**。预热期返回
``NaN``，由上层转为显式缺失——绝不用更短的窗口或填充值代替，因为那会让预热期的信号
看起来可用，实际是另一套参数算出来的。

指标一律吃**复权价**：除息日与拆股日的跳空是价格体系变化，不是市场波动，用实际成交价
会让均线与波动率在这些日子出现虚假跃变。
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
    """区间收益率。"""
    _check_window(window)
    return series.pct_change(window)


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    _check_window(window)
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder 平滑；min_periods 保证预热期不产生取值
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - 100 / (1 + rs)
    # 全程无下跌时 RSI 为 100，这是定义而非缺失
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
    """已实现波动率，按**交易日 / 252** 年化。

    波动只在交易日产生，所以这里用交易日口径；而定价的到期时间用日历日 / 365，
    因为期权在周末照常衰减。两个口径分工明确，强行统一必错其一。
    """
    _check_window(window)
    log_ret = np.log(series).diff()
    return log_ret.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(
        TRADING_DAYS_PER_YEAR
    )


def _check_window(window: int) -> None:
    if window < 1:
        raise ValueError(f"窗口须为正整数，当前 {window}")


def rank_in_window(series: pd.Series, window: int) -> pd.Series:
    """当前值在过去 ``window`` 天的**区间**里的位置，0–1。

    这就是交易台上说的 "IV rank"：``(今天 − 区间最低) / (区间最高 − 区间最低)``。
    它回答的是"相对自己的历史，现在贵还是便宜"——绝对的 IV 数字没有可比性，
    20% 对某些标的是高位，对另一些是低位。
    """
    lo = series.rolling(window, min_periods=window // 2).min()
    hi = series.rolling(window, min_periods=window // 2).max()
    span = hi - lo
    return ((series - lo) / span.where(span > 0)).astype(float)


def percentile_in_window(series: pd.Series, window: int) -> pd.Series:
    """当前值在过去 ``window`` 天里的**分位**，0–1。

    与 ``rank_in_window`` 的区别常被忽略：IV rank 只看区间两端，一次极端尖峰
    就会把它长期压低；IV percentile 看的是"有多少天比今天低"，对尖峰不敏感。
    两个一起看才知道是真便宜还是被一次意外拉低了。
    """
    return series.rolling(window, min_periods=window // 2).apply(
        lambda w: (w[:-1] < w[-1]).mean(), raw=True
    ).astype(float)
