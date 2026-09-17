"""策略的声明式描述。

本阶段的策略是**配置**而非代码：入场条件、选约规则、了结规则三段，各自独立。
七插槽框架（路线图阶段 5）会把它们扩展成可替换的插槽，接口保持这个形状。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from backtest.errors import StartupError
from backtest.selection.rules import SelectionRule
from backtest.signals.registry import SignalView
from backtest.types import require

#: 入场条件：读当日信号视图，返回是否入场。取不到信号时应返回 False 而非抛异常。
EntryCondition = Callable[[SignalView], bool]


@dataclass(frozen=True, slots=True)
class EntryRule:
    name: str
    condition: EntryCondition
    #: 该条件用到的信号名，启动时校验，名字打错立刻报错而不是跑到一半失败。
    requires: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExitRule:
    """了结规则。四个条件同时生效，取**最早触发**的那一个。

    - ``hold_days`` / ``dte_floor`` 按交易日计
    - ``take_profit`` / ``stop_loss`` 是相对入场价的比例（0.5 表示 50%）
    """

    hold_days: int | None = 10
    take_profit: float | None = 1.0
    stop_loss: float | None = 0.5
    dte_floor: int | None = 5

    def __post_init__(self) -> None:
        if self.hold_days is not None:
            require(self.hold_days >= 1, "hold_days 至少为 1 个交易日")
        if self.take_profit is not None:
            require(self.take_profit > 0, "take_profit 须为正")
        if self.stop_loss is not None:
            require(0 < self.stop_loss <= 1, "stop_loss 须在 0 与 1 之间")
        if self.dte_floor is not None:
            require(self.dte_floor >= 0, "dte_floor 不得为负")
        require(
            any(x is not None for x in (self.hold_days, self.dte_floor)),
            "必须至少有一个时间类了结条件（hold_days 或 dte_floor），否则仓位会被拖到到期",
        )


@dataclass(frozen=True, slots=True)
class Strategy:
    """一次回测的完整策略描述。"""

    entry: EntryRule
    select: SelectionRule
    exit: ExitRule
    #: 固定张数。成交表引擎各笔独立计算，不存在"当前权益"，因此不支持按权益比例。
    quantity: int = 1
    name: str = "unnamed"

    def __post_init__(self) -> None:
        require(self.quantity >= 1, "quantity 至少为 1 张")


def crossover(fast: str, slow: str) -> EntryCondition:
    """``fast`` 上穿 ``slow`` 的静态条件（当日 fast > slow）。"""

    def condition(view: SignalView) -> bool:
        a, b = view[fast], view[slow]
        if a.is_missing or b.is_missing:
            return False
        return a.unwrap() > b.unwrap()

    return condition


def all_of(*conditions: EntryCondition) -> EntryCondition:
    def condition(view: SignalView) -> bool:
        return all(c(view) for c in conditions)

    return condition


def threshold(name: str, *, below: float | None = None, above: float | None = None) -> EntryCondition:
    if (below is None) == (above is None):
        raise StartupError("threshold 须且只须指定 below 或 above 其一")

    def condition(view: SignalView) -> bool:
        value = view[name]
        if value.is_missing:
            return False
        v = value.unwrap()
        return v < below if below is not None else v > above

    return condition
