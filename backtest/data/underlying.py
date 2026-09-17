"""标的日线行情：**两套价格**。

- **复权价**（``adjusted``）：对拆股与分红调整，用于信号与收益率计算。只有它才能
  让除息日与拆股日不出现虚假跳空。
- **实际成交价**（``traded``）：当时真实交易的价格，用于与期权行权价对齐、计算
  价值状态、以及作为定价模型的标的输入。

两者必须分开取用。期权的行权价是**当时的实际价格**，而复权价会被此后的每一次分红
不断改写——用复权价判断历史合约的价值状态，误差随累计分红单调增大，且不会报错，
只会让所有历史选约系统性偏移。QQQ 十年累计分红超过 5%，不是可忽略的量级。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from backtest.errors import DataQualityError
from backtest.maybe import Maybe

RAW_FIELDS = ("open", "high", "low", "close")
ADJ_PREFIX = "adj_"


@dataclass(frozen=True, slots=True)
class UnderlyingBars:
    """某标的在某区间的日线，两套价格并存。"""

    symbol: str
    frame: pd.DataFrame

    # ---- 取用 ----
    def traded(self, field: str = "close") -> pd.Series:
        """实际成交价。用于期权相关的一切计算。"""
        self._check_field(field)
        return self.frame[field]

    def adjusted(self, field: str = "close") -> pd.Series:
        """复权价。用于信号与收益率。"""
        self._check_field(field)
        return self.frame[ADJ_PREFIX + field]

    @property
    def volume(self) -> pd.Series:
        return self.frame["volume"]

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(d.date() for d in self.frame.index)

    def traded_close_on(self, day: date) -> Maybe[float]:
        """某日的实际成交收盘价。非交易日返回显式缺失，而不是 ``None``。"""
        ts = pd.Timestamp(day)
        if ts not in self.frame.index:
            return Maybe.missing(f"{day} 不是 {self.symbol} 的交易日")
        return Maybe.some(float(self.frame.at[ts, "close"]))

    def slice(self, start: date, end: date) -> "UnderlyingBars":
        mask = (self.frame.index >= pd.Timestamp(start)) & (
            self.frame.index <= pd.Timestamp(end)
        )
        return UnderlyingBars(self.symbol, self.frame.loc[mask])

    @staticmethod
    def _check_field(field: str) -> None:
        if field not in RAW_FIELDS and field != "volume":
            raise KeyError(f"未知字段 {field!r}，可用：{RAW_FIELDS + ('volume',)}")

    def __len__(self) -> int:
        return len(self.frame)


def build_bars(symbol: str, raw: pd.DataFrame) -> UnderlyingBars:
    """由 Yahoo 的原始列构造两套价格。

    复权因子取 ``Adj Close / Close``，并同样施加到开高低上——只复权收盘价会让
    日内区间与收盘价不在同一价格体系里，ATR 这类指标随之失真。
    """
    frame = pd.DataFrame(index=pd.to_datetime(raw.index))
    frame.index.name = "date"
    for src, dst in zip(("Open", "High", "Low", "Close"), RAW_FIELDS):
        frame[dst] = raw[src].astype(float)
    frame["volume"] = raw["Volume"].astype("int64")

    factor = (raw["Adj Close"].astype(float) / raw["Close"].astype(float)).rename("factor")
    for f in RAW_FIELDS:
        frame[ADJ_PREFIX + f] = frame[f] * factor

    validate_bars(symbol, frame)
    return UnderlyingBars(symbol, frame)


def validate_bars(symbol: str, frame: pd.DataFrame) -> None:
    """质量校验。异常数据必须被拒绝，不能静默进入回测。"""
    if frame.empty:
        raise DataQualityError(f"{symbol} 日线为空")
    if frame.index.has_duplicates:
        dupes = frame.index[frame.index.duplicated()].unique()
        raise DataQualityError(f"{symbol} 日线存在重复日期：{list(dupes[:5])}")
    if not frame.index.is_monotonic_increasing:
        raise DataQualityError(f"{symbol} 日线未按日期升序")

    price_cols = list(RAW_FIELDS) + [ADJ_PREFIX + f for f in RAW_FIELDS]
    non_positive = (frame[price_cols] <= 0).any(axis=1)
    if non_positive.any():
        raise DataQualityError(
            f"{symbol} 存在非正价格，首个出现于 {frame.index[non_positive][0].date()}"
        )
    inverted = frame["high"] < frame["low"]
    if inverted.any():
        raise DataQualityError(
            f"{symbol} 存在最高价低于最低价，首个出现于 {frame.index[inverted][0].date()}"
        )
    if frame[price_cols].isna().any().any():
        raise DataQualityError(f"{symbol} 存在缺失价格")
