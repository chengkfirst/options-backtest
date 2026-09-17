"""跨模块传递的核心类型。全部不可变。

`TradeRecord` 是本项目最重要的一个契约：成交表引擎与日后的事件驱动引擎都产出
这一种记录，研究层与报告层因此对两条路径完全通用，两者的结果也可以直接逐行比对。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from backtest.errors import StartupError
from backtest.maybe import Maybe


class Right(str, Enum):
    CALL = "C"
    PUT = "P"


class ExerciseStyle(str, Enum):
    AMERICAN = "american"
    EUROPEAN = "european"


class SettlementStyle(str, Enum):
    PHYSICAL = "physical"
    CASH = "cash"


class QuoteSource(str, Enum):
    SYNTHETIC = "synthetic"
    VENDOR = "vendor"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ExitReason(str, Enum):
    """了结原因的统一取值集合；两条引擎必须使用同一套取值与同一优先级。"""

    RISK_FORCED = "risk_forced"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    DTE_FLOOR = "dte_floor"
    TIME_EXIT = "time_exit"
    EXPIRY_ITM = "expiry_itm"
    EXPIRY_OTM = "expiry_otm"


#: 同日多条件成立时的确定性优先级，越靠前越优先。
#:
#: 止损排在止盈之前是有意的保守选择：日线数据看不到盘中先后，两者同日触及时
#: 假定先触及的是亏损一侧，不会高估策略表现。
EXIT_PRIORITY: tuple[ExitReason, ...] = (
    ExitReason.RISK_FORCED,
    ExitReason.STOP_LOSS,
    ExitReason.TAKE_PROFIT,
    ExitReason.DTE_FLOOR,
    ExitReason.TIME_EXIT,
    ExitReason.EXPIRY_ITM,
    ExitReason.EXPIRY_OTM,
)

_EXIT_RANK: Mapping[ExitReason, int] = MappingProxyType(
    {reason: i for i, reason in enumerate(EXIT_PRIORITY)}
)


def resolve_exit_reason(reasons: "list[ExitReason] | tuple[ExitReason, ...]") -> ExitReason:
    """多个了结原因同日成立时，按固定优先级取一个。"""
    if not reasons:
        raise ValueError("至少需要一个了结原因")
    return min(reasons, key=lambda r: _EXIT_RANK[r])


@dataclass(frozen=True, slots=True, order=True)
class ContractId:
    """可读且确定的合约标识，例如 ``QQQ_20240315_C_00400000``。

    可读是为了在交易明细里能肉眼核对；确定是为了跨运行可复现（见 design.md
    「确定性作为硬约束」）；不依赖外部代码表是为了离线可用。
    """

    symbol: str
    expiry: date
    right: Right
    strike: float

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.isupper():
            raise ValueError(f"标的代码须为大写非空：{self.symbol!r}")
        if self.strike <= 0:
            raise ValueError(f"行权价须为正：{self.strike}")
        # 行权价按千分位定宽编码，1/1000 以下的精度无法表达
        if abs(round(self.strike * 1000) - self.strike * 1000) > 1e-6:
            raise ValueError(f"行权价精度超出千分位：{self.strike}")

    def __str__(self) -> str:
        return (
            f"{self.symbol}_{self.expiry:%Y%m%d}_{self.right.value}"
            f"_{round(self.strike * 1000):08d}"
        )

    @classmethod
    def parse(cls, text: str) -> "ContractId":
        try:
            symbol, expiry, right, strike = text.split("_")
            return cls(
                symbol=symbol,
                expiry=date(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:8])),
                right=Right(right),
                strike=round(int(strike) / 1000, 3),
            )
        except (ValueError, KeyError) as exc:
            raise ValueError(f"无法解析合约标识：{text!r}") from exc


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """合约规格。交割方式来自这里，而非系统的统一假设。

    QQQ / SPY 期权是美式、实物交割：实值到期拿到 100 股股票，需要现金接货；
    指数期权（SPX）是欧式、现金交割。把它当配置而不是默认值，是因为按现金结算
    给实物交割的标的建模会系统性高估绩效。
    """

    symbol: str
    multiplier: int = 100
    exercise: ExerciseStyle = ExerciseStyle.AMERICAN
    settlement: SettlementStyle = SettlementStyle.PHYSICAL
    strike_step: float = 1.0
    vol_proxy: str = "^VXN"

    def __post_init__(self) -> None:
        if self.multiplier <= 0:
            raise ValueError("合约乘数须为正")
        if self.strike_step <= 0:
            raise ValueError("行权价步长须为正")


#: 期权链长表的字段集。按商业数据源的通用输出定义，而非按合成链能提供什么裁剪；
#: 某个实现提供不了的字段以缺失标记表达，MUST NOT 从接口中移除（design.md 决策 5）。
CHAIN_COLUMNS: tuple[str, ...] = (
    "asof",
    "symbol",
    "expiry",
    "strike",
    "right",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "iv",
    "delta",
    "gamma",
    "vega",
    "theta",
    "underlying_price",
    "source",
    "quality_flags",
    "greeks_model_derived",
)

#: 主键。
CHAIN_KEY: tuple[str, ...] = ("asof", "symbol", "expiry", "strike", "right")


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """一笔逻辑交易：从首次建仓到彻底了结的完整过程。

    统计单位是逻辑交易而非单个持仓——展期三次的头寸按持仓统计会变成四笔，
    胜率与持有期全部失真。
    """

    trade_id: int
    engine: str
    symbol: str
    entry_date: date
    exit_date: date
    contract: ContractId
    quantity: int
    entry_price: float
    exit_price: float
    multiplier: int
    gross_pnl: float
    fees: float
    fee_commission: float
    fee_regulatory: float
    fee_slippage: float
    net_pnl: float
    exit_reason: ExitReason
    suppressed_reasons: tuple[ExitReason, ...]
    holding_days: int
    source: QuoteSource
    entry_context: Mapping[str, Maybe[float]]
    exit_context: Mapping[str, Maybe[float]]
    quote_gaps: int = 0
    deferred_days: int = 0

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("数量须为正")
        if self.exit_date < self.entry_date:
            raise ValueError("了结日不得早于入场日")
        object.__setattr__(self, "entry_context", MappingProxyType(dict(self.entry_context)))
        object.__setattr__(self, "exit_context", MappingProxyType(dict(self.exit_context)))

    @property
    def is_win(self) -> bool:
        return self.net_pnl > 0


#: 交易明细导出时的列顺序；两条引擎必须一致。
TRADE_COLUMNS: tuple[str, ...] = (
    "trade_id",
    "engine",
    "symbol",
    "entry_date",
    "exit_date",
    "contract",
    "quantity",
    "entry_price",
    "exit_price",
    "multiplier",
    "gross_pnl",
    "fees",
    "fee_commission",
    "fee_regulatory",
    "fee_slippage",
    "net_pnl",
    "exit_reason",
    "suppressed_reasons",
    "holding_days",
    "source",
    "quote_gaps",
    "deferred_days",
)


def require(condition: bool, message: str) -> None:
    """启动期校验：不满足即抛 :class:`StartupError`。"""
    if not condition:
        raise StartupError(message)
