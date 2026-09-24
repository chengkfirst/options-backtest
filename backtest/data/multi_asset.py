"""跨市场数据：美债、美元、信用利差、波动率期限结构。

这些不是为了多几条曲线看，是为了两件事：

1. **市场状态分类（regime）**——VIX 水平、波动率期限结构、信用利差共同刻画
   "现在是什么环境"，而策略在不同环境下的表现通常差很远。
2. **跨市场确认**——一个看涨判断如果同时有美债收益率、美元、信用利差的配合，
   与孤立的技术信号是两回事。

全部来自同一个数据源（Yahoo），因为多一个源就多一个失败点。口径与局限写在各项旁边。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtest.data.cache import CacheKey, ParquetCache
from backtest.data.sources import fetch_close_series
from backtest.events import EventKind, EventLog

#: 跨市场序列的定义。``transform`` 说明怎么从原始收盘价得到要用的量。
SERIES: dict[str, dict[str, str]] = {
    "vix": {
        "symbol": "^VIX", "scale": "0.01",
        "meaning": "标普 500 的 30 天隐含波动率",
        "caveat": "它是标普的，不是纳指的；QQQ 策略请同时看 VXN",
    },
    "vix3m": {
        "symbol": "^VIX3M", "scale": "0.01",
        "meaning": "标普 500 的 3 个月隐含波动率",
        "caveat": "与 VIX 之比构成期限结构；2011 年之前没有数据",
    },
    "vxn": {
        "symbol": "^VXN", "scale": "0.01",
        "meaning": "纳斯达克 100 的 30 天隐含波动率",
        "caveat": "QQQ 策略应以它为准",
    },
    "ust10y": {
        "symbol": "^TNX", "scale": "0.01",
        "meaning": "10 年期美债收益率",
        "caveat": "Yahoo 给的是百分数，已除以 100",
    },
    "ust3m": {
        "symbol": "^IRX", "scale": "0.01",
        "meaning": "3 个月期美债收益率",
        "caveat": "与 10 年期之差即期限利差，负值常被视为衰退信号",
    },
    "dollar": {
        "symbol": "DX-Y.NYB", "scale": "1",
        "meaning": "美元指数",
        "caveat": "对历史较早的区间可能有缺口",
    },
    "hyg": {
        "symbol": "HYG", "scale": "1",
        "meaning": "高收益债 ETF",
        "caveat": "与 LQD 之比是信用利差的代理，不是真的利差",
    },
    "lqd": {
        "symbol": "LQD", "scale": "1",
        "meaning": "投资级债 ETF",
        "caveat": "同上",
    },
}


@dataclass(frozen=True, slots=True)
class CrossMarket:
    """跨市场面板，以及每个序列的可得性。"""

    frame: pd.DataFrame
    unavailable: dict[str, str]        # 名字 → 为什么没拿到

    def on(self, day: date) -> pd.Series | None:
        ts = pd.Timestamp(day)
        prior = self.frame.loc[:ts]
        return None if prior.empty else prior.iloc[-1]

    def describe(self) -> pd.DataFrame:
        """每个序列的含义、局限、可得性。想改口径先看这张表。"""
        rows = []
        for name, meta in SERIES.items():
            rows.append({
                "序列": name, "代码": meta["symbol"], "含义": meta["meaning"],
                "局限": meta["caveat"],
                "状态": "缺失：" + self.unavailable[name] if name in self.unavailable
                else f"{int(self.frame[name].notna().sum())} 个观测",
            })
        for derived, why in (
            ("term_structure", "VIX3M / VIX。小于 1 表示近端更贵，通常出现在恐慌中"),
            ("credit_proxy", "HYG / LQD。下降表示信用利差走阔"),
            ("yield_curve", "10 年期减 3 个月期。负值为倒挂"),
        ):
            rows.append({"序列": derived, "代码": "（派生）", "含义": why, "局限": "代理量，不是真实利差",
                         "状态": f"{int(self.frame[derived].notna().sum())} 个观测"
                         if derived in self.frame else "无法计算"})
        return pd.DataFrame(rows).set_index("序列")


def load_cross_market(
    start: date, end: date, *, cache: ParquetCache | None = None,
    events: EventLog | None = None, refresh: bool = False,
) -> CrossMarket:
    """拉取跨市场序列。

    某个序列拿不到时**记录原因并继续**，不中止——跨市场数据是辅助，缺一两个不该让
    整个回测跑不了。但缺了什么必须说清楚，否则 regime 分类会在使用者不知情的情况下
    少用了一个维度。
    """
    cache = cache or ParquetCache()
    events = events or EventLog()
    # 预热期必须与 MarketData 对齐（400 天）。取少了，200 日均线这类长窗口维度在区间
    # 开头算不出来，regime 会有一大段"未知"——那不是数据缺失，是我们自己没取够。
    fetch_start = start - timedelta(days=400)
    columns: dict[str, pd.Series] = {}
    unavailable: dict[str, str] = {}

    for name, meta in SERIES.items():
        key = CacheKey("cross", meta["symbol"], {"start": fetch_start, "end": end, "source": "yfinance"})
        try:
            frame = cache.get_or_load(
                key,
                lambda s=meta["symbol"]: fetch_close_series(s, fetch_start, end).to_frame("close"),
                refresh=refresh,
            )
            series = frame["close"].astype(float) * float(meta["scale"])
            series.index = pd.to_datetime(series.index)
            columns[name] = series
        except Exception as exc:  # noqa: BLE001
            unavailable[name] = f"{type(exc).__name__}: {exc}"
            events.record(EventKind.RANGE_TRUNCATED, start,
                          f"跨市场序列 {name}（{meta['symbol']}）不可得：{exc}")

    panel = pd.DataFrame(columns).sort_index()
    if "vix3m" in panel and "vix" in panel:
        panel["term_structure"] = panel["vix3m"] / panel["vix"]
    if "hyg" in panel and "lqd" in panel:
        panel["credit_proxy"] = panel["hyg"] / panel["lqd"]
    if "ust10y" in panel and "ust3m" in panel:
        panel["yield_curve"] = panel["ust10y"] - panel["ust3m"]
    return CrossMarket(frame=panel, unavailable=unavailable)
