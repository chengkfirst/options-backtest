"""信号的注册与按日取用。

全部信号在回测开始时**一次性按全序列算完**，之后按日切片取用，而不是逐日增量计算。
这是性能与安全的一次有意识取舍：逐日重算会慢上两个数量级，而一次性计算带来的前视
风险，恰好有可靠的自动检测手段——见 :mod:`backtest.signals.lookahead` 的截断重算
属性测试。用测试换性能，而不是用架构换性能。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from backtest.data.underlying import UnderlyingBars
from backtest.errors import StartupError
from backtest.maybe import Maybe
from backtest.signals import indicators

#: 默认注册的信号。每个是 ``bars -> Series``，只吃复权价。
DEFAULT_SIGNALS: dict[str, Callable[[UnderlyingBars], pd.Series]] = {
    "close": lambda b: b.adjusted("close"),
    "sma_20": lambda b: indicators.sma(b.adjusted("close"), 20),
    "sma_50": lambda b: indicators.sma(b.adjusted("close"), 50),
    "sma_200": lambda b: indicators.sma(b.adjusted("close"), 200),
    "ema_20": lambda b: indicators.ema(b.adjusted("close"), 20),
    "rsi_14": lambda b: indicators.rsi(b.adjusted("close"), 14),
    "atr_14": lambda b: indicators.atr(
        b.adjusted("high"), b.adjusted("low"), b.adjusted("close"), 14
    ),
    "mom_20": lambda b: indicators.momentum(b.adjusted("close"), 20),
    "rv_20": lambda b: indicators.realized_vol(b.adjusted("close"), 20),
    "bb_width_20": lambda b: indicators.bollinger(b.adjusted("close"), 20)["width"],
}


@dataclass(frozen=True, slots=True)
class SignalView:
    """某一日的全部信号取值。缺失是显式的，不会被当成 0。"""

    day: date
    _values: Mapping[str, float]

    def __getitem__(self, name: str) -> Maybe[float]:
        if name not in self._values:
            raise KeyError(f"未注册的信号：{name!r}")
        value = self._values[name]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return Maybe.missing(f"{name} 在 {self.day} 无取值（预热期或数据缺失）")
        return Maybe.some(float(value))

    def get(self, name: str) -> Maybe[float]:
        return self[name]

    def names(self) -> tuple[str, ...]:
        return tuple(self._values)


class SignalSet:
    """一次算完，按日取用。"""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self._by_day = {ts.date(): row for ts, row in frame.iterrows()}

    @classmethod
    def from_bars(
        cls,
        bars: UnderlyingBars,
        extra: Mapping[str, Callable[[UnderlyingBars], pd.Series]] | None = None,
    ) -> "SignalSet":
        definitions = dict(DEFAULT_SIGNALS)
        definitions.update(extra or {})
        columns = {name: fn(bars).astype(float) for name, fn in definitions.items()}
        frame = pd.DataFrame(columns, index=bars.frame.index)
        return cls(frame)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._frame.columns)

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def require(self, names: "list[str] | tuple[str, ...]") -> None:
        """启动期校验订阅的信号名。

        名字打错必须在第一个交易日之前就报出来，而不是跑到中途才失败。
        """
        unknown = [n for n in names if n not in self._frame.columns]
        if unknown:
            raise StartupError(
                f"未注册的信号：{unknown}；可用信号：{list(self._frame.columns)}"
            )

    def on(self, day: date) -> SignalView:
        row = self._by_day.get(day)
        if row is None:
            return SignalView(day, {name: float("nan") for name in self._frame.columns})
        return SignalView(day, row.to_dict())
