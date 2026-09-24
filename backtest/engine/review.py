"""逐日复查持仓：③④⑥⑤ 的动态版本。

## 为什么这四件事是同一个机制

在入场那一刻把止盈止损定成固定比例，隐含了一个假设：**这笔交易的"对"与"错"
在开仓时就已经知道了，而且不会变。** 真实交易里不成立：

- **③ 止盈**不一定是固定比例。跟踪止盈（回吐已得利润的三分之一就走）、
  按标的波动定（走到 2 倍 ATR）、按论据定（等的那件事发生了就走）——
  这些都要每天重算。
- **④ 止损**同理。"亏 50%"是个数字，"论据失效"是个判断，后者往往先发生。
- **⑥ 仓位**也不一定是一次性的。先建半仓、确认了再加，是很常见的做法。
- **⑤ headwind** 本来就是持仓期间的事。

四件事都要求同一个东西：**每天对每个持仓问一遍，然后可以做点什么。**
所以它们是一个钩子，不是四个。

## 用法

    def review(ctx, pos):
        # 跟踪止盈：从最高点回吐三分之一就走
        if pos.mfe_so_far > 0.5 and pos.ratio < pos.mfe_so_far * 0.67:
            return Close("回吐已得利润的三分之一")

        # 论据失效（这比固定止损更早触发）
        if ctx.signal("close") < ctx.signal("sma_50"):
            return Close("跌破 50 日线，当初看多的理由没了")

        # 中间地带：先减一半，不是非黑即白
        if pos.days_held > 10 and pos.ratio < 0:
            return Reduce(0.5, "持有十天还没动静，先减一半")

        return None      # 继续持有

## 边界

- **静态出场规则依然生效**，复查是在它之上加判断，不是替代。时间类了结
  （``hold_days`` / ``dte_floor``）是安全网，不允许被复查绕过——否则一个写错的
  复查函数会把仓位拖到到期。
- 只有事件驱动引擎支持。成交表引擎向量化算出场，逐日复查它做不到。
- **部分了结不产生新的交易记录**：展期三次的头寸按持仓统计会变成四笔，
  胜率与持有期全部失真。减仓后仍是同一笔逻辑交易，直到彻底平掉。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from backtest.errors import StartupError
from backtest.types import require


@dataclass(frozen=True, slots=True)
class Close:
    """平掉整个持仓。"""

    reason: str = ""
    #: 归到哪个了结原因下。影响统计分组，所以要老实填。
    #: ``take_profit`` / ``stop_loss`` / ``headwind`` / ``signal``
    kind: str = "signal"

    def __post_init__(self) -> None:
        allowed = {"take_profit", "stop_loss", "headwind", "signal"}
        require(self.kind in allowed, f"未知的了结类别 {self.kind!r}，可选 {sorted(allowed)}")


@dataclass(frozen=True, slots=True)
class Reduce:
    """减仓。``fraction`` 是减掉的**比例**，0.5 表示减一半。

    减完仍是**同一笔逻辑交易**，不会在明细里多出一行。
    """

    fraction: float
    reason: str = ""

    def __post_init__(self) -> None:
        require(0 < self.fraction < 1, f"减仓比例须在 0 与 1 之间（0.5 = 减一半），给的是 {self.fraction}")


@dataclass(frozen=True, slots=True)
class Add:
    """加仓。同一张合约、同一笔逻辑交易，按加权平均更新成本。"""

    contracts: int
    reason: str = ""

    def __post_init__(self) -> None:
        require(self.contracts >= 1, "加仓至少 1 张")


@dataclass(frozen=True, slots=True)
class Roll:
    """展期——**还没实现**。

    不是"先平掉再开一笔"那么简单：按本项目的口径，展期后仍是**同一笔逻辑交易**
    （见 ``需求.md`` 的底线约束）。当成两笔统计，胜率和持有期都会失真。
    要正确支持，``TradeRecord`` 得能容纳"一笔交易经历过多张合约"，那是架构级改动。

    现在返回它会直接报错，而不是悄悄当成平仓——后者会给出一个看起来正常的错结果。
    """

    to_contract: Any
    reason: str = ""

    def __post_init__(self) -> None:
        raise StartupError(
            "展期还没实现。它要求一笔逻辑交易能跨多张合约，而 TradeRecord 目前一行一张，"
            "当成'平掉再开一笔'会让胜率与持有期失真。"
            "现在可以用 Close() + 让计划在下一天重新开仓来近似，但那在统计上是两笔。"
        )


@dataclass(frozen=True, slots=True)
class PositionView:
    """复查时看到的这个持仓的状态。

    ``*_so_far`` 一律是**截至今天**的，不是事后回看的——复查函数不能看见未来。
    """

    contract: Any
    entry_date: date
    entry_price: float
    quantity: int
    days_held: int
    #: 剩余到期（交易日）
    dte: int
    #: 当前中价
    mark: float
    #: 相对入场价的盈亏比例。0.2 表示浮盈 20%
    ratio: float
    #: 截至今天的最好 / 最差。跟踪止盈止损靠它们
    mfe_so_far: float
    mae_so_far: float
    iv: float
    delta: float
    #: 下单时打的标签，来自 Plan
    tag: str = ""


#: 复查函数：拿市场上下文与这个持仓的状态，返回一个动作或 ``None``（继续持有）。
Reviewer = Callable[[Any, PositionView], "Close | Reduce | Add | None"]
