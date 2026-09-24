"""交易计划：把七个组件当成**一个决定**来表达。

## 为什么需要这个

参数化的写法把策略拆成互不相干的常数：止盈 `1.0`、仓位"1 张"、选约 `delta 0.30`。
但真实交易里这些是联动的——回撤深的时候，你会**同时**买得更久、下注更大、
止损放宽、目标提高。那是一个判断的几个侧面，不是六个独立设定。

拆成常数之后它们就再也不认识彼此了：你没法表达"这一档用这套参数，那一档用那套"。

``Plan`` 把一次完整的交易决定写在一起：

    def plan(ctx):
        dd = ctx.signal("drawdown_60", default=0.0)
        if dd > -0.05:
            return None                          # 不够深，今天不做

        deep = dd < -0.10
        c = ctx.chain[ctx.chain.is_call & (ctx.chain.spread < 0.08)]
        c = c[c.dte.between(45, 75)] if deep else c[c.dte.between(21, 45)]
        if c.empty:
            return None

        return Plan(
            contract    = c.iloc[(c.delta - (0.50 if deep else 0.30)).abs().argmin()],
            risk_pct    = 0.03 if deep else 0.015,   # 深回撤下注更大
            take_profit = 1.00 if deep else 0.60,    # 目标也更高
            stop_loss   = 0.50,
            max_hold    = 40   if deep else 20,      # 给它更多时间
            tag         = "深回撤" if deep else "浅回撤",
        )

``tag`` 会跟着这笔交易一路存进明细。事后可以直接按它分组看两档的表现差异——
这正是"分类研究"要的东西，而且分类口径来自你下单时的真实判断，不是事后硬分的。

## 边界

计划式只有事件驱动引擎支持。成交表引擎各笔独立、向量化算出场，表达不了
"这一笔用这套出场规则"。它会响亮拒绝，不会给个近似结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import pandas as pd

from backtest.errors import StartupError
from backtest.types import require


@dataclass(frozen=True, slots=True)
class Plan:
    """一次完整的交易决定。``None`` 表示今天不做。"""

    #: 选中的合约——从 ``ctx.chain`` 里挑的**一行**。
    contract: pd.Series

    # ── 仓位（⑥）。三选一，都不给则用策略的默认仓位方法 ──
    contracts: int | None = None          # 直接指定张数
    risk_pct: float | None = None         # 本笔最多亏掉权益的百分之几
    notional: float | None = None         # 本笔投入固定金额

    # ── 了结（③④）。不给的项回落到策略的全局 ExitRule ──
    take_profit: float | None = None
    stop_loss: float | None = None
    max_hold: int | None = None           # 最长持有（交易日）
    dte_floor: int | None = None          # 剩余到期下限（交易日）

    # ── 记录 ──
    #: 分组标签。会存进交易明细，事后按它分类研究。
    tag: str = ""
    #: 给人看的一句话：当时为什么这么做。也会存进明细。
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.contract, pd.Series):
            raise StartupError(
                f"Plan.contract 要的是 ctx.chain 里的一行（Series），"
                f"给的是 {type(self.contract).__name__}。"
                "从 chain 里筛完之后用 .iloc[i] 取一行。"
            )
        given = [x is not None for x in (self.contracts, self.risk_pct, self.notional)]
        require(sum(given) <= 1, "仓位只能给一个：contracts / risk_pct / notional 三选一")
        if self.contracts is not None:
            require(self.contracts >= 1, "contracts 至少为 1 张")
        if self.risk_pct is not None:
            require(0 < self.risk_pct <= 1, "risk_pct 须在 0 与 1 之间（0.02 = 2%）")
        if self.notional is not None:
            require(self.notional > 0, "notional 须为正")
        if self.take_profit is not None:
            require(self.take_profit > 0, "take_profit 须为正")
        if self.stop_loss is not None:
            require(0 < self.stop_loss <= 1, "stop_loss 须在 0 与 1 之间（0.5 = 亏 50% 止损）")
        if self.max_hold is not None:
            require(self.max_hold >= 1, "max_hold 至少为 1 个交易日")


@dataclass(frozen=True, slots=True)
class Context:
    """做决定时能看到的全部东西。

    **所有信息都截止到决策日收盘**——看不到明天。链是执行日的（T+1 成交时
    合约才真正确定），但信号与组合状态都是决策日的，见 ``selection.rules.enrich_chain``。
    """

    day: date
    #: 当日可选的全部合约。已加派生列（iv/delta/.../dte/spread/moneyness/is_call）
    #: 并已剔除 T+1 执行不了的。按希腊字母和 IV 随便挑都不会挑到开不了的。
    chain: pd.DataFrame
    #: 标的当日价（实际成交价，不是复权价）。
    spot: float
    #: 组合状态（⑥⑦要用）。
    equity: float
    cash: float
    positions: int
    #: 当前回撤（负数，如 -0.08）。熔断、减仓这类判断靠它。
    drawdown: float
    _view: Any = None

    def signal(self, name: str, default: float | None = None) -> float:
        """取当日信号。

        缺失时：给了 ``default`` 就用它，没给就抛错。**不会悄悄返回 0**——
        一个被当成 0 的回撤会让"跌了 8% 才买"变成"天天买"。
        """
        if self._view is None:
            raise StartupError("这个上下文里没有信号视图")
        got = self._view[name]
        if got.is_missing:
            if default is None:
                raise StartupError(
                    f"信号 {name!r} 在 {self.day} 不可用（{got.reason}）。"
                    f"要么给个默认值 ctx.signal({name!r}, default=...)，"
                    f"要么在计划里判断并返回 None 跳过这天。"
                )
            return default
        return float(got.unwrap())

    @property
    def view(self):
        """原始信号视图，返回 ``Maybe``。需要自己处理缺失时用它。"""
        return self._view

    @property
    def calls(self) -> pd.DataFrame:
        return self.chain[self.chain["is_call"]]

    @property
    def puts(self) -> pd.DataFrame:
        return self.chain[self.chain["is_put"]]


#: 计划函数：拿当时的全部状态，返回一个完整的交易决定，或 ``None`` 表示不做。
Planner = Callable[[Context], "Plan | None"]
