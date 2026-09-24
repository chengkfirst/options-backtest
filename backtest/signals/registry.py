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

@dataclass(frozen=True, slots=True)
class SignalMeta:
    """一个信号的自述：它量的是什么、窗口多长、单位是什么、读的时候要注意什么。

    有元信息和没有，差别不在"界面好看一点"。没有的时候界面只能把名字按字母表倒出来，
    使用者得自己去猜 ``rv_20`` 是什么口径年化的、``mom_20`` 是比率还是差值。
    猜错了不会报错，只会得到一个讲不通的结论。
    """

    family: str        # 归类：价格 / 趋势 / 动量 / 波动
    measures: str      # 量的是什么
    unit: str          # 单位或取值范围
    window: int | None # 回看窗口（交易日），无窗口的为 None
    caveat: str = ""   # 读的时候容易弄错的地方


#: 每个信号的自述。**新增信号必须同时在这里登记**，否则 ``describe()`` 会直接报错——
#: 一个没人说得清含义的信号，比没有这个信号更糟。
SIGNAL_META: dict[str, SignalMeta] = {
    "close":       SignalMeta("价格", "复权收盘价", "美元", None,
                              "复权价，不是当天实际成交价；对齐期权合约请用实际价"),
    "sma_20":      SignalMeta("趋势", "20 日简单均线", "美元", 20),
    "sma_50":      SignalMeta("趋势", "50 日简单均线", "美元", 50),
    "sma_200":     SignalMeta("趋势", "200 日简单均线", "美元", 200,
                              "区间前 200 个交易日内不可用，不是 0"),
    "ema_20":      SignalMeta("趋势", "20 日指数均线", "美元", 20),
    "rsi_14":      SignalMeta("动量", "相对强弱，超买超卖", "0–100", 14,
                              "70/30 是惯例阈值，不是统计结论"),
    "mom_20":      SignalMeta("动量", "20 日累计收益率", "比率（0.05 = 涨 5%）", 20,
                              "是比率不是差值"),
    "atr_14":      SignalMeta("波动", "14 日平均真实波幅", "美元", 14,
                              "绝对金额，跨标的比较前要先除以价格"),
    "rv_20":       SignalMeta("波动", "20 日已实现波动率", "年化比率（0.20 = 20%）", 20,
                              "按**交易日 252** 年化；与按日历日年化的 IV 直接相减不可解释"),
    "bb_width_20": SignalMeta("波动", "布林带宽度，衡量波动收敛/扩张", "比率", 20),
}


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


def describe_signals(names: "tuple[str, ...] | None" = None) -> pd.DataFrame:
    """所有信号的自述表，按类别排。界面和 notebook 用的是同一份。

    自定义信号没有登记元信息时，如实标成"自定义（未登记说明）"而不是留空——
    留空看起来像是系统没查到，标出来才说明是使用者自己没写。
    """
    names = names or tuple(DEFAULT_SIGNALS)
    rows = []
    for name in names:
        m = SIGNAL_META.get(name)
        rows.append({
            "信号": name,
            "类别": m.family if m else "自定义",
            "量的是什么": m.measures if m else "自定义（未登记说明）",
            "单位": m.unit if m else "—",
            "回看窗口": m.window if m else None,
            "注意": m.caveat if m else "",
        })
    order = {"价格": 0, "趋势": 1, "动量": 2, "波动": 3, "自定义": 9}
    frame = pd.DataFrame(rows)
    return (frame.assign(_o=frame["类别"].map(lambda c: order.get(c, 9)))
                 .sort_values(["_o", "回看窗口", "信号"], na_position="first")
                 .drop(columns="_o").reset_index(drop=True))


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
