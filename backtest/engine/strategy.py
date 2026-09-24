"""策略的声明式描述。

本阶段的策略是**配置**而非代码：入场条件、选约规则、了结规则三段，各自独立。
七插槽框架（路线图阶段 5）会把它们扩展成可替换的插槽，接口保持这个形状。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from backtest.errors import StartupError
from backtest.engine.plan import Planner
from backtest.engine.review import Reviewer
from backtest.selection.rules import ContractChooser, SelectionRule
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
class Sizing:
    """仓位规模的确定方式。

    对期权买方，**最大损失就是付出的全部权利金**，所以"每笔风险"是个干净的概念：
    风险金额 ÷ 每张权利金 = 张数。卖方不成立（最大损失远大于收到的权利金），
    所以卖方启用时会被拒绝——那需要保证金模型。

    ``method`` 取值：

    ``"fixed"``        固定张数。最朴素，但随着权益变化，每笔的风险占比会漂移。
    ``"risk_pct"``     每笔风险占权益固定比例。**买方的默认选择**。
    ``"fixed_notional"`` 每笔投入固定金额。
    ``"vol_target"``   按标的波动率反向缩放：波动大时少买。
    ``"kelly"``        按历史胜率与盈亏比的分数 Kelly。需要足够样本，否则拒绝。
    """

    method: str = "fixed"
    contracts: int = 1                 # fixed
    risk_pct: float = 0.02             # risk_pct：每笔最多亏权益的 2%
    notional: float = 5_000.0          # fixed_notional
    target_vol: float = 0.20           # vol_target：目标年化波动
    kelly_fraction: float = 0.25       # kelly：用几分之一 Kelly
    max_contracts: int = 50            # 任何方法都不得超过

    def __post_init__(self) -> None:
        allowed = {"fixed", "risk_pct", "fixed_notional", "vol_target", "kelly"}
        require(self.method in allowed, f"未知的仓位方法 {self.method!r}，可选 {sorted(allowed)}")
        require(self.contracts >= 1, "contracts 至少为 1")
        require(0 < self.risk_pct <= 1, "risk_pct 须在 0 与 1 之间")
        require(self.notional > 0, "notional 须为正")
        require(self.target_vol > 0, "target_vol 须为正")
        require(0 < self.kelly_fraction <= 1, "kelly_fraction 须在 0 与 1 之间")
        require(self.max_contracts >= 1, "max_contracts 至少为 1")


@dataclass(frozen=True, slots=True)
class Strategy:
    """一次回测的完整策略描述。"""

    entry: EntryRule
    #: 选约。``SelectionRule`` 是声明式（快、可扫参数）；也可以直接给一个函数
    #: ``f(chain, view)``，拿整张链按 greek / iv 自己筛，见 ``selection.rules``。
    select: SelectionRule | ContractChooser
    exit: ExitRule
    #: 固定张数。成交表引擎各笔独立计算，不存在"当前权益"，因此只支持这个。
    quantity: int = 1
    #: 仓位方法。除 ``fixed`` 外都需要组合状态，只有事件驱动引擎支持。
    sizing: "Sizing" = field(default_factory=lambda: Sizing())
    #: 信号再次触发且选出同一张合约时，是否允许再开一笔。
    #:
    #: 默认不允许——连续几天信号成立、选约又选到同一张是很常见的，默认允许会让
    #: 同一个判断被重复下注。**两个引擎必须遵守同一个取值**，否则它们会在这件事上
    #: 悄悄分歧：成交表各笔独立、天然允许；事件驱动一个合约一个持仓、天然不允许。
    allow_stacking: bool = False
    #: **计划式策略**：一个函数同时决定买什么、买多少、怎么了结。
    #: 给了它就绕过 entry / select / sizing 三段——因为那三段表达不了它们之间的联动。
    #: 只有事件驱动引擎支持；成交表引擎会响亮拒绝。见 ``backtest.engine.plan``。
    plan: "Planner | None" = None
    #: **逐日复查持仓**：动态止盈止损、分批加减仓、headwind 都靠它。
    #: 静态 ExitRule 依然是安全网，复查是在它之上加判断，不是替代。
    #: 只有事件驱动引擎支持。见 ``backtest.engine.review``。
    review: "Reviewer | None" = None
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
