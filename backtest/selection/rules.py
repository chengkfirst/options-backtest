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
from typing import Callable

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.data.calendar import TradingCalendar
from backtest.data.chain import relative_spread
from backtest.errors import StartupError
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

    # ── 可行域：硬约束，不满足就不考虑 ──────────────────────
    #
    # 专业做法不是"选一个点"，是"划一个可行域，然后在里面按目标排序"。
    # 盯死 delta=0.30 有两个问题：链上未必有那一张（于是选到一张离得很远的），
    # 以及**只在那一个点成立的结论是噪音**——实盘参数不可能刚好落在那里。
    #: delta 的可接受区间（绝对值）。给了它，``target_delta`` 退化为排序用的目标。
    delta_band: tuple[float, float] | None = None
    #: 该合约自身 IV 的可接受区间。买方想要低、卖方想要高。
    iv_band: tuple[float, float] | None = None


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
        for name, band in (("delta_band", self.delta_band), ("iv_band", self.iv_band)):
            if band is not None:
                lo, hi = band
                require(lo < hi, f"{name} 的下界须小于上界：{band}")


#: 选约函数拿到的链，除 ``CHAIN_COLUMNS`` 外还有这几列。都是算好的——同样的推导
#: 每次自己写一遍，迟早有一次符号弄反。
DERIVED_COLUMNS: tuple[str, ...] = (
    "dte", "sessions_left", "mid", "spread", "moneyness", "is_call", "is_put",
)

#: 自定义选约：拿当日链与**决策日**的信号视图，返回选中的那一行（或 None 表示不开仓）。
#:
#: 为什么链是执行日的、视图却是决策日的——见 ``enrich_chain`` 的说明。
ContractChooser = Callable[[pd.DataFrame, object], object]


def enrich_chain(
    chain: pd.DataFrame,
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
) -> pd.DataFrame:
    """把当日链整理成"可以直接按 greek 和 iv 挑"的样子。

    做两件事：

    1. **加派生列**。``dte`` 按日历日（30DTE 就是 30 个日历日，与市场惯例一致），
       ``sessions_left`` 按交易日（可执行性用的是这个口径，两者不可混用）；
       ``mid``、``spread``（相对买卖价差）；``moneyness`` **正数一律表示虚值**，
       看涨看跌同号，不用每次重新想方向。

    2. **先剔除执行不了的合约**。T 日决策、T+1 成交，届时仍可交易的才留下。
       在这里剔掉而不是事后校验，是为了让"按 greek 和 iv 随便挑"**由构造保证安全**：
       挑不到开不了的，就不需要记得去检查。

    **关于前视**：这张链是**执行日**的，传给选约函数的信号视图却是**决策日**的。
    这不是笔误。真实过程是——T 日收盘你根据当时看到的东西决定"买一张什么样的"，
    单子在 T+1 收盘成交，具体是哪张由规则在成交时点解析。所以规则可以用 T 的信息，
    解析可以用 T+1 的链；但规则本身不许看 T+1 的行情，否则就是拿成交日的涨跌
    反过来挑行权价。
    """
    if chain.empty:
        return chain

    out = chain.copy()
    expiry = out["expiry"].map(_as_date)
    out["dte"] = [(e - asof).days for e in expiry]
    out["sessions_left"] = [calendar.sessions_between(asof, e) for e in expiry]
    out["mid"] = (out["bid"] + out["ask"]) / 2.0
    out["spread"] = relative_spread(out["bid"], out["ask"])

    spot = out["underlying_price"].astype(float)
    # ``right`` 列的取值是 "C" / "P"（与合约代码一致）。但写 chain.right == "call"
    # 是最自然的直觉，而那会静默匹配到 0 行。所以直接给布尔列，让人不必记这件事。
    is_call = out["right"] == Right.CALL.value
    out["is_call"] = is_call
    out["is_put"] = ~is_call
    # 看涨：行权价越高越虚值。看跌：行权价越低越虚值。统一成"正数 = 虚值"。
    out["moneyness"] = np.where(is_call, out["strike"] / spot - 1.0, 1.0 - out["strike"] / spot)

    return out.loc[out["sessions_left"] >= config.min_days_to_expiry].reset_index(drop=True)


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
    rule: "SelectionRule | ContractChooser",
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
    view: object = None,
) -> SelectionResult:
    """从某日的链中选出一张合约。

    ``rule`` 两种形态：

    - ``SelectionRule``——声明式，"约 30 天、delta 约 0.30"。快，可以扫参数，
      但表达不了"按当时情况调整"。
    - **函数**——``f(chain, view)``，拿整张链自己筛，返回一行或 ``None``。
      链已加好派生列并剔除不可执行的合约，见 ``enrich_chain``。
    """
    if callable(rule):
        return _choose_by_function(chain, rule, config, calendar, asof, view)
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
    if not pool.empty:
        pool = _apply_bands(pool, rule, trace, skipped)
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


def _choose_by_function(
    chain: pd.DataFrame,
    chooser: "ContractChooser",
    config: BacktestConfig,
    calendar: TradingCalendar,
    asof: date,
    view: object,
) -> SelectionResult:
    """执行用户写的选约函数，并把返回值校验成一张确实存在、确实开得了的合约。

    校验是必须的。用户函数返回什么都有可能——空的 DataFrame、自己拼的行、
    一个不在链上的行权价。这些如果不查，回测会拿着一张不存在的合约照常跑完，
    最后给出一个完全可信的假结果。
    """
    trace: list[tuple[str, int]] = [("链上合约", len(chain))]
    pool = enrich_chain(chain, config, calendar, asof)
    trace.append(("可执行（T+1 仍可交易）", len(pool)))
    if pool.empty:
        return SelectionResult(Maybe.missing("当日没有可执行的合约"), None, tuple(trace))

    try:
        picked = chooser(pool, view)
    except Exception as exc:  # noqa: BLE001
        # 不吞。用户函数里的错必须带着是哪天、错在哪一起抛出来，否则调试无从下手。
        raise StartupError(
            f"选约函数在 {asof} 抛出异常：{type(exc).__name__}: {exc}"
        ) from exc

    if picked is None:
        # 返回 None 可能是"今天确实不想开"，也可能是筛选条件写错了一直筛空。
        # 两者从结果上分不出来，所以把当日**实际**能选的样貌一并报出——
        # 写错 right 取值、delta 全是缺失这类问题，看一眼事件就知道了。
        return SelectionResult(
            Maybe.missing(f"选约函数返回 None（当日不开仓）｜当日可选：{_profile(pool)}"),
            None, tuple(trace),
        )

    row = _as_single_row(picked, asof)
    if row is None:
        return SelectionResult(Maybe.missing("选约函数返回了空结果"), None, tuple(trace))

    key = (str(row["symbol"]), _as_date(row["expiry"]), str(row["right"]), float(row["strike"]))
    match = pool.loc[
        (pool["symbol"].astype(str) == key[0])
        & (pool["expiry"].map(_as_date) == key[1])
        & (pool["right"].astype(str) == key[2])
        & (pool["strike"].astype(float) == key[3])
    ]
    if match.empty:
        raise StartupError(
            f"选约函数在 {asof} 返回的合约不在当日可执行的链上：{key}。"
            "请从传进去的 chain 里挑一行返回，不要自己拼。"
        )

    row = match.iloc[0]
    trace.append(("函数选中", 1))
    contract = ContractId(
        symbol=str(row["symbol"]),
        expiry=_as_date(row["expiry"]),
        right=Right(row["right"]),
        strike=float(row["strike"]),
    )
    return SelectionResult(Maybe.some(contract), row.to_dict(), tuple(trace))


def _profile(pool: pd.DataFrame) -> str:
    """当日可选合约的一句话画像。用于让"选不出来"这件事自己解释自己。"""
    def rng(col: str, fmt: str = "{:.2f}") -> str:
        v = pool[col].dropna()
        if v.empty:
            return f"{col}=全缺失"
        return f"{col} {fmt.format(v.min())}–{fmt.format(v.max())}"

    rights = "/".join(sorted(pool["right"].astype(str).unique()))
    return (f"{len(pool)} 张，right 取值 {rights}，"
            f"{rng('dte', '{:.0f}')}，{rng('delta')}，{rng('iv')}，{rng('spread')}")


def _as_single_row(picked, asof: date):
    """把选约函数的返回值收敛成一行。接受 Series、单行 DataFrame、以及空结果。"""
    if isinstance(picked, pd.DataFrame):
        if picked.empty:
            return None
        if len(picked) > 1:
            raise StartupError(
                f"选约函数在 {asof} 返回了 {len(picked)} 行。一次只能选一张合约——"
                "需要多腿请等多腿支持，现在自己挑一行返回。"
            )
        return picked.iloc[0]
    if isinstance(picked, pd.Series):
        return picked
    raise StartupError(
        f"选约函数在 {asof} 返回了 {type(picked).__name__}，"
        "应当返回 chain 里的一行（Series）或 None。"
    )


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


def _apply_bands(pool: pd.DataFrame, rule: SelectionRule, trace, skipped):
    """可行域过滤。字段不可用时**跳过该约束并标明**，不因缺字段判为不合格。"""
    if rule.delta_band is not None:
        lo, hi = rule.delta_band
        if pool["delta"].isna().all():
            skipped.append("delta 区间（数据源不提供 delta）")
        else:
            pool = pool.loc[pool["delta"].abs().between(lo, hi)]
            trace.append((f"delta∈[{lo},{hi}]", len(pool)))
    if rule.iv_band is not None:
        lo, hi = rule.iv_band
        if pool["iv"].isna().all():
            skipped.append("IV 区间（数据源不提供 iv）")
        else:
            pool = pool.loc[pool["iv"].between(lo, hi)]
            trace.append((f"IV∈[{lo},{hi}]", len(pool)))
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
