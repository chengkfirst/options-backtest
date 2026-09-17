"""成交价与费用。**两条引擎共用同一套成本假设，不可分叉。**

成交表引擎不能走"用中价简化成本"的捷径——否则它与事件驱动引擎的结果就不可比，
双引擎一致性校验也就失去意义。

默认取不利端成交（买 ask、卖 bid）。对期权来说买卖价差是主要成本，远大于佣金；
用中价成交会得到一个账面漂亮但实际拿不到的结果。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from backtest.config import CostConfig
from backtest.maybe import Maybe
from backtest.types import Side


@dataclass(frozen=True, slots=True)
class Tradability:
    """某张合约在某时点是否可成交，以及不可成交的原因。"""

    ok: bool
    reason: str | None = None
    relative_spread: float | None = None

    @classmethod
    def yes(cls, spread: float) -> "Tradability":
        return cls(True, None, spread)

    @classmethod
    def no(cls, reason: str, spread: float | None = None) -> "Tradability":
        return cls(False, reason, spread)


def check_tradable(bid, ask, costs: CostConfig) -> Tradability:
    """报价缺失、价差超限即不可成交。

    绝不以估算价格替代缺失报价——那会让回测在一张根本没有报价的合约上成交。
    """
    try:
        bid_f, ask_f = float(bid), float(ask)
    except (TypeError, ValueError):
        return Tradability.no("报价缺失")
    if math.isnan(bid_f) or math.isnan(ask_f):
        return Tradability.no("报价缺失")
    if ask_f <= 0 or bid_f < 0:
        return Tradability.no("报价非正")
    if ask_f < bid_f:
        return Tradability.no("卖价低于买价")
    mid = (bid_f + ask_f) / 2.0
    if mid <= 0:
        return Tradability.no("中价非正")
    spread = (ask_f - bid_f) / mid
    if spread > costs.max_relative_spread:
        return Tradability.no(
            f"相对价差 {spread:.1%} 超过上限 {costs.max_relative_spread:.1%}", spread
        )
    return Tradability.yes(spread)


def fill_price(bid: float, ask: float, side: Side, costs: CostConfig) -> float:
    """成交价。

    ``adverse_fraction`` 为 0 时取中价、为 1 时取不利端；中间值按比例插值。
    滑点叠加在其上，方向**始终不利于下单方**。
    """
    mid = (bid + ask) / 2.0
    adverse = ask if side is Side.BUY else bid
    price = mid + (adverse - mid) * costs.adverse_fraction
    direction = 1.0 if side is Side.BUY else -1.0
    price += direction * costs.slippage_per_contract
    return max(price, 0.0)


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """费用分项。合起来看不出问题，分开看才知道钱被谁吃掉了。"""

    commission: float
    regulatory: float
    slippage: float

    @property
    def total(self) -> float:
        return self.commission + self.regulatory + self.slippage

    def __add__(self, other: "FeeBreakdown") -> "FeeBreakdown":
        return FeeBreakdown(
            self.commission + other.commission,
            self.regulatory + other.regulatory,
            self.slippage + other.slippage,
        )


ZERO_FEES = FeeBreakdown(0.0, 0.0, 0.0)


def order_fees(quantity: int, costs: CostConfig, multiplier: int) -> FeeBreakdown:
    """一笔订单的费用分项。

    滑点以现金形式单列，便于在报告里与佣金、监管费并排比较——它通常是三者中最大的
    一项，混在成交价里就看不见了。
    """
    if quantity <= 0:
        raise ValueError("数量须为正")
    commission = max(
        quantity * costs.commission_per_contract, costs.min_commission_per_order
    )
    regulatory = quantity * costs.regulatory_fee_per_contract
    slippage = quantity * costs.slippage_per_contract * multiplier
    return FeeBreakdown(commission, regulatory, slippage)


def exercise_fees(quantity: int, costs: CostConfig) -> FeeBreakdown:
    return FeeBreakdown(quantity * costs.exercise_fee, 0.0, 0.0)


def execute(
    *, bid: float, ask: float, side: Side, quantity: int, multiplier: int, costs: CostConfig
) -> tuple[float, FeeBreakdown]:
    """成交价 + 费用分项。调用前须先通过 :func:`check_tradable`。"""
    price = fill_price(bid, ask, side, costs)
    return price, order_fees(quantity, costs, multiplier)
