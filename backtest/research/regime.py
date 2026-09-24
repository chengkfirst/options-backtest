"""市场状态（regime）分类与分层分析。

**只做规则型，不做统计型。** HMM、贝叶斯这类方法在十年只有三四个完整周期的样本上，
拟合出来的"状态"很难说不是噪音——而且它们的状态没有名字，你没法向自己解释"为什么
这天算高波动"。规则型状态是可解释的、可复核的、参数明摆着的。

分类维度来自跨市场数据：波动率水平、波动率期限结构、趋势、信用。每个维度独立分档，
组合起来就是状态。**维度不可得时整条规则失效并说明原因**，不是用剩下的凑合——
少一个维度的"高波动"和完整的"高波动"不是一回事。

分层分析回答 draft 里列的三个视角：收益分布、执行质量、回撤背景。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Dimension:
    """一个分类维度。"""

    name: str
    source: str                       # 用面板里的哪一列
    thresholds: tuple[float, ...]     # 分档边界
    labels: tuple[str, ...]           # 各档的名字
    rationale: str                    # 为什么这么分

    def __post_init__(self) -> None:
        if len(self.labels) != len(self.thresholds) + 1:
            raise ValueError(
                f"{self.name}：{len(self.thresholds)} 个边界应该对应 "
                f"{len(self.thresholds) + 1} 个标签，给了 {len(self.labels)} 个"
            )

    def classify(self, series: pd.Series) -> pd.Series:
        bins = [-np.inf, *self.thresholds, np.inf]
        return pd.cut(series, bins=bins, labels=list(self.labels), right=False)


#: 默认的维度定义。阈值是**可改的参数**，不是真理——写在这里就是为了让人能改。
DEFAULT_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        name="波动率水平", source="vxn", thresholds=(0.18, 0.28),
        labels=("低波动", "常态", "高波动"),
        rationale="VXN 长期中位约 20%。18 / 28 大致对应三分位，不是拟合出来的。",
    ),
    Dimension(
        name="波动率期限结构", source="term_structure", thresholds=(1.0,),
        labels=("倒挂", "正常"),
        rationale="VIX3M/VIX < 1 表示近端更贵，通常出现在恐慌中。1.0 是结构性分界，不是调出来的。",
    ),
    Dimension(
        name="趋势", source="trend_200", thresholds=(0.0,),
        labels=("200 日均线下方", "200 日均线上方"),
        rationale="最朴素的趋势划分。用 0 而不是某个拟合出来的偏离度，免得过拟合。",
    ),
)


@dataclass(frozen=True, slots=True)
class RegimeSet:
    """每日的状态标签。"""

    frame: pd.DataFrame                 # 每个维度一列，外加 combined
    dimensions: tuple[Dimension, ...]
    unavailable: tuple[str, ...]        # 因数据缺失而未能计算的维度

    def on(self, day: date) -> dict[str, str]:
        ts = pd.Timestamp(day)
        if ts not in self.frame.index:
            return {d.name: "未知" for d in self.dimensions}
        row = self.frame.loc[ts]
        return {k: ("未知" if pd.isna(v) else str(v)) for k, v in row.items()}

    def describe(self) -> pd.DataFrame:
        rows = [{"维度": d.name, "来源": d.source,
                 "分档": " / ".join(d.labels),
                 "边界": " , ".join(f"{t:g}" for t in d.thresholds),
                 "依据": d.rationale,
                 "状态": "缺数据，未计算" if d.name in self.unavailable else "可用"}
                for d in self.dimensions]
        return pd.DataFrame(rows).set_index("维度")

    def coverage(self) -> pd.Series:
        """各状态各占多少天。样本极少的状态，其结论不可外推。"""
        if "combined" not in self.frame:
            return pd.Series(dtype=int)
        return self.frame["combined"].value_counts()


def classify(
    panel: pd.DataFrame, bars=None, dimensions: tuple[Dimension, ...] = DEFAULT_DIMENSIONS
) -> RegimeSet:
    """按规则给每一天打状态标签。

    ``bars`` 给出标的行情，用来算趋势维度（复权价对 200 日均线的偏离）。
    """
    work = panel.copy()
    if bars is not None:
        close = bars.adjusted("close")
        work = work.join(
            (close / close.rolling(200).mean() - 1.0).rename("trend_200"), how="outer"
        ).sort_index()
        work = work.ffill()

    out = pd.DataFrame(index=work.index)
    unavailable: list[str] = []
    for dim in dimensions:
        if dim.source not in work.columns or work[dim.source].notna().sum() == 0:
            unavailable.append(dim.name)
            continue
        out[dim.name] = dim.classify(work[dim.source])

    if len(out.columns):
        # 任一维度缺失则整体为"未知"——用剩下的凑合会让"高波动"的含义悄悄改变
        combined = out.astype(str).agg(" · ".join, axis=1)
        out["combined"] = combined.where(out.notna().all(axis=1), "未知")
    return RegimeSet(frame=out, dimensions=dimensions, unavailable=tuple(unavailable))


def stratify(
    trades: pd.DataFrame, regimes: RegimeSet, *, by: str = "combined", min_trades: int = 15
) -> pd.DataFrame:
    """按开仓当日的市场状态给交易分层。

    三个视角一起给（draft 里列的）：**收益分布**、**执行质量**、**回撤背景**。
    样本不足的状态照常呈现但标注——隐藏比标注更有害。
    """
    if trades.empty:
        return pd.DataFrame()
    labels = []
    for d in pd.to_datetime(trades["entry_date"]):
        state = regimes.on(d.date())
        labels.append(state.get(by) if by != "combined" else state.get("combined", "未知"))
    work = trades.assign(_regime=labels)

    rows = []
    for name, part in work.groupby("_regime", observed=True):
        pnl = part["net_pnl"]
        rows.append({
            "状态": name,
            "笔数": len(part),
            "胜率": float((pnl > 0).mean()),
            "平均每笔": float(pnl.mean()),
            "中位": float(pnl.median()),
            "合计": float(pnl.sum()),
            "最大单笔亏": float(pnl.min()),
            # 执行质量：报价缺口与了结顺延，极端环境下这两项会恶化
            "报价缺口": float(part["quote_gaps"].mean()) if "quote_gaps" in part else np.nan,
            "了结顺延": float(part["deferred_days"].mean()) if "deferred_days" in part else np.nan,
            "样本": "充足" if len(part) >= min_trades else f"不足（<{min_trades}）",
        })
    return pd.DataFrame(rows).set_index("状态").sort_values("笔数", ascending=False)


def drawdown_context(equity: pd.DataFrame, regimes: RegimeSet, top: int = 5) -> pd.DataFrame:
    """最深的几次回撤各自发生在什么状态下。

    这是 draft 里"回撤一般发生在什么 regime 背景下"那一条。
    """
    curve = equity["equity_mid"]
    dd = curve / curve.cummax() - 1
    worst = dd.nsmallest(top * 20)          # 先取一批，再按日期去重成不同的回撤段
    picked, seen = [], set()
    for day, value in worst.items():
        month = (day.year, day.month)
        if month in seen:
            continue
        seen.add(month)
        state = regimes.on(day.date() if hasattr(day, "date") else day)
        picked.append({"日期": day, "回撤": value, **state})
        if len(picked) >= top:
            break
    return pd.DataFrame(picked).set_index("日期")
