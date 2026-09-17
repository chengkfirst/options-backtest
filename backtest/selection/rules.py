"""合约选择：把"约 30 天到期、delta 约 0.30 的看涨"翻译成唯一确定的一张合约。

两个口径在这里并存，必须分清：

- **目标到期天数**按**日历日**计（30 DTE 就是 30 个日历日，与市场惯例一致）。
- **可执行性下限**按**交易日**计，因为它对应的是执行延迟——T 日下的单在 T+1 执行，
  合约届时必须仍可交易。

每次选择都返回完整的筛选过程：选中了哪张、每一步淘汰了多少、为什么失败。没有这个，
"为什么这天没开仓"就只能靠猜。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.data.calendar import TradingCalendar
from backtest.data.chain import relative_spread
from backtest.maybe import Maybe
from backtest.types import ContractId, Right, require


@dataclass(frozen=True, slots=True)
class SelectionRule:
    """选约规则。``target_delta`` 与 ``moneyness`` 二选一。"""

    right: Right = Right.CALL
    target_dte: int = 30
    dte_window: tuple[int, int] = (21, 45)
    target_delta: float | None = 0.30
    #: 相对标的的价值状态，正数为虚值（看涨则行权价更高）。
    moneyness: float | None = None
    min_volume: float | None = None
    min_open_interest: float | None = None
    max_relative_spread: float | None = None

    def __post_init__(self) -> None:
        lo, hi = self.dte_window
        require(0 < lo <= hi, f"dte_window 非法：{self.dte_window}")
        require(lo <= self.target_dte <= hi, f"target_dte {self.target_dte} 不在 {self.dte_window} 内")
        require(
            (self.target_delta is None) != (self.moneyness is None),
            "target_delta 与 moneyness 必须且只能指定一个",
        )
        if self.target_delta is not None:
            require(0 < abs(self.target_delta) < 1, f"target_delta 须在 0 与 1 之间：{self.target_delta}")


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """选中的合约 + 完整筛选过程。"""

    contract: Maybe[ContractId]
    quote: dict | None
    trace: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    skipped_filters: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.contract.is_present

    def describe(self) -> str:
        steps = " → ".join(f"{name}:{n}" for name, n in self.trace)
        if self.ok:
            return f"选中 {self.contract.unwrap()} | {steps}"
        return f"未选中（{self.contract.reason}） | {steps}"


def select_contract(
    chain: pd.DataFrame,
    rule: SelectionRule,
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
) -> SelectionResult:
    """从某日的链中选出一张合约。"""
    trace: list[tuple[str, int]] = [("链上合约", len(chain))]
    skipped: list[str] = []

    if chain.empty:
        return SelectionResult(Maybe.missing("该日链为空"), None, tuple(trace))

    pool = chain.loc[chain["right"] == rule.right.value]
    trace.append(("按期权类型", len(pool)))
    if pool.empty:
        return SelectionResult(Maybe.missing("链上没有该类型的合约"), None, tuple(trace))

    pool = _filter_expiry(pool, rule, config, calendar, asof, trace)
    if pool is None or pool.empty:
        return SelectionResult(
            Maybe.missing("没有满足到期条件且可执行的到期日"), None, tuple(trace)
        )

    pool = _filter_liquidity(pool, rule, config, trace, skipped)
    if pool.empty:
        return SelectionResult(
            Maybe.missing("流动性过滤后无候选"), None, tuple(trace), tuple(skipped)
        )

    row = _pick_strike(pool, rule, trace)
    if row is None:
        return SelectionResult(
            Maybe.missing("无法确定行权价（delta 不可用）"), None, tuple(trace), tuple(skipped)
        )

    contract = ContractId(
        symbol=str(row["symbol"]),
        expiry=_as_date(row["expiry"]),
        right=Right(row["right"]),
        strike=float(row["strike"]),
    )
    return SelectionResult(Maybe.some(contract), row.to_dict(), tuple(trace), tuple(skipped))


def _as_date(value) -> date:
    return value if isinstance(value, date) else pd.Timestamp(value).date()


def _filter_expiry(
    pool: pd.DataFrame,
    rule: SelectionRule,
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
    trace: list[tuple[str, int]],
) -> pd.DataFrame | None:
    expiries = sorted({_as_date(v) for v in pool["expiry"].unique()})
    lo, hi = rule.dte_window

    in_window = [e for e in expiries if lo <= (e - asof).days <= hi]
    trace.append(("到期在窗口内", len(in_window)))
    if not in_window:
        return None

    # 可执行性下限：执行日之前或当日到期的合约不可用。下限按交易日计，
    # 且配置层已保证它不小于执行延迟加一。
    executable = [
        e for e in in_window if calendar.sessions_between(asof, e) >= config.min_days_to_expiry
    ]
    trace.append(("满足可执行性下限", len(executable)))
    if not executable:
        return None

    # 最接近目标天数；并列时取较近的那个（确定性规则）
    chosen = min(executable, key=lambda e: (abs((e - asof).days - rule.target_dte), e))
    subset = pool.loc[pool["expiry"].map(_as_date) == chosen]
    trace.append(("选定到期日后", len(subset)))
    return subset


def _filter_liquidity(
    pool: pd.DataFrame,
    rule: SelectionRule,
    config: BacktestConfig,
    trace: list[tuple[str, int]],
    skipped: list[str],
) -> pd.DataFrame:
    """流动性过滤。字段不可用时**跳过该约束并标明**，不因缺字段判为不合格。"""
    checks = (
        ("volume", rule.min_volume, "最小成交量"),
        ("open_interest", rule.min_open_interest, "最小未平仓"),
    )
    for column, threshold, label in checks:
        if threshold is None:
            continue
        if pool[column].isna().all():
            skipped.append(f"{label}（数据源不提供 {column}）")
            continue
        pool = pool.loc[pool[column] >= threshold]
        trace.append((label, len(pool)))
        if pool.empty:
            return pool

    cap = rule.max_relative_spread or config.costs.max_relative_spread
    spread = relative_spread(pool["bid"], pool["ask"])
    pool = pool.loc[spread.notna() & (spread <= cap)]
    trace.append(("价差上限", len(pool)))
    return pool


def _pick_strike(pool: pd.DataFrame, rule: SelectionRule, trace: list[tuple[str, int]]):
    if rule.target_delta is not None:
        usable = pool.loc[pool["delta"].notna()]
        trace.append(("delta 可用", len(usable)))
        if usable.empty:
            return None
        target = abs(rule.target_delta)
        distance = (usable["delta"].abs() - target).abs()
        # 并列时取更虚值的一张：看涨取更高行权价，看跌取更低
        tie_break = -usable["strike"] if rule.right is Right.CALL else usable["strike"]
        order = np.lexsort((tie_break.to_numpy(), distance.to_numpy()))
        return usable.iloc[order[0]]

    spot = float(pool["underlying_price"].iloc[0])
    sign = 1.0 if rule.right is Right.CALL else -1.0
    target_strike = spot * (1 + sign * rule.moneyness)
    distance = (pool["strike"] - target_strike).abs()
    order = np.lexsort((pool["strike"].to_numpy(), distance.to_numpy()))
    trace.append(("按价值状态定位", len(pool)))
    return pool.iloc[order[0]]
