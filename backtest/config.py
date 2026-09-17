"""全局配置。各模块从此读取，禁止在模块内硬编码这些常数。

默认值即 design.md 的「基准配置」。它不是"正确参数"，而是**基线**——研究时每一次
变体对比都相对它而言；没有稳定基线，"改了一处之后差多少"这个问题无法回答。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import date

from backtest.types import ContractSpec, Right, require

#: 波动率年化用的每年交易日数。只用于波动率，不用于定价（见 TimeConventions）。
TRADING_DAYS_PER_YEAR = 252

#: 定价与 theta 衰减用的每年日历日数。期权在周末照常衰减。
CALENDAR_DAYS_PER_YEAR = 365.0


@dataclass(frozen=True, slots=True)
class TimeConventions:
    """两个各司其职的时间口径，强行统一必错其一。

    - 定价的到期时间 T 与 theta：**日历日 / 365**。期权在非交易日同样衰减。
    - 波动率年化：**交易日 / 252**。波动只在交易日产生。
    """

    pricing_basis: str = "calendar/365"
    vol_annualization_basis: str = "trading/252"

    def year_fraction(self, start: date, end: date) -> float:
        """定价口径的年化到期时间。"""
        return max((end - start).days, 0) / CALENDAR_DAYS_PER_YEAR

    def describe(self) -> dict[str, str]:
        return {
            "pricing": self.pricing_basis,
            "vol_annualization": self.vol_annualization_basis,
        }


@dataclass(frozen=True, slots=True)
class CostConfig:
    """成本假设。两条引擎共用，不可分叉。"""

    #: 成交价在中价与不利端之间的位置：0 = 中价，1 = 不利端（买 ask / 卖 bid）。
    adverse_fraction: float = 1.0
    commission_per_contract: float = 0.65
    min_commission_per_order: float = 1.00
    regulatory_fee_per_contract: float = 0.05
    exercise_fee: float = 0.0
    slippage_per_contract: float = 0.0
    #: 相对买卖价差上限，超过即判定为不可成交。
    max_relative_spread: float = 0.20

    def __post_init__(self) -> None:
        require(
            0.0 <= self.adverse_fraction <= 1.0,
            f"adverse_fraction 须在 0 与 1 之间，当前 {self.adverse_fraction}",
        )
        require(self.max_relative_spread > 0, "max_relative_spread 须为正")
        for name in (
            "commission_per_contract",
            "min_commission_per_order",
            "regulatory_fee_per_contract",
            "exercise_fee",
            "slippage_per_contract",
        ):
            require(getattr(self, name) >= 0, f"{name} 不得为负")


@dataclass(frozen=True, slots=True)
class SyntheticChainConfig:
    """合成链生成参数。

    刻意保持粗糙：不建模波动率偏斜、不建模期限结构。一个粗糙且标注清晰的模型，
    比一个看起来精细、实则仍然错误的模型更安全——后者会诱使人相信回测结论。
    """

    #: 行权价覆盖标的价的上下比例。
    strike_range: float = 0.20
    #: 买卖价差为理论价的比例。
    spread_pct: float = 0.02
    #: 买卖价差的绝对下限。
    spread_floor: float = 0.05
    #: 生成的到期节奏。
    include_monthly: bool = True
    include_weekly: bool = True
    #: 周度到期在该日期之后才存在（周度期权是逐步推出的）。
    weekly_start: date = date(2010, 6, 4)
    max_dte: int = 120

    def __post_init__(self) -> None:
        require(0 < self.strike_range < 1, "strike_range 须在 0 与 1 之间")
        require(self.spread_pct > 0 and self.spread_floor > 0, "价差参数须为正")
        require(self.max_dte > 0, "max_dte 须为正")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """一次回测的完整配置。随报告留存，使结果可被重新执行。"""

    symbol: str = "QQQ"
    start: date = date(2015, 1, 1)
    end: date = date(2024, 12, 31)
    #: 名义资金。成交表引擎不做资金约束，该值仅用于把盈亏折算为比例、
    #: 以及判断并发持仓的资金可行性。
    notional_capital: float = 100_000.0
    #: 无风险利率序列不可得时的回退常数。回退会被记入事件流并在报告中标注。
    fallback_risk_free_rate: float = 0.02
    #: 执行延迟（交易日）。T 日收盘产生意图，T+delay 收盘执行。
    execution_lag_days: int = 1
    #: 合约选择的最小剩余期限（交易日），不得小于执行延迟。
    min_days_to_expiry: int = 5
    random_seed: int = 20260917

    contract: ContractSpec = field(default_factory=lambda: ContractSpec(symbol="QQQ"))
    costs: CostConfig = field(default_factory=CostConfig)
    synthetic: SyntheticChainConfig = field(default_factory=SyntheticChainConfig)
    time: TimeConventions = field(default_factory=TimeConventions)

    def __post_init__(self) -> None:
        require(self.start < self.end, "回测起始日须早于结束日")
        require(self.notional_capital > 0, "名义资金须为正")
        require(self.execution_lag_days >= 1, "执行延迟至少为一个交易日")
        require(
            self.contract.symbol == self.symbol,
            f"合约规格的标的 {self.contract.symbol} 与回测标的 {self.symbol} 不一致",
        )
        # 执行延迟一天意味着：T 日下的了结意图在 T+1 执行，合约届时必须仍可交易。
        # 下限小于 lag+1 时，仓位会被静默拖到到期结算，而报告看上去一切正常。
        require(
            self.min_days_to_expiry >= self.execution_lag_days + 1,
            f"min_days_to_expiry 至少为 {self.execution_lag_days + 1}"
            f"（执行延迟 {self.execution_lag_days} 加一），当前 {self.min_days_to_expiry}",
        )

    def describe(self) -> dict[str, object]:
        """完整配置的可打印形式，随报告留存。"""
        return dataclasses.asdict(self)
