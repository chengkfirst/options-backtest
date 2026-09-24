"""期权链接口与长表结构。

字段集按**商业数据源的通用输出**定义，而不是按当前能拿到什么裁剪。某个实现提供不了
的字段（合成链没有成交量与未平仓）以缺失标记表达，MUST NOT 从接口中移除——否则
日后接入付费数据时会发现字段对不上，接口得重做，预留的扩展点就白留了。

访问方式有两个，对应回测的两种需要：

- :meth:`ChainSource.snapshot` —— 某日的全部合约，用于选约。
- :meth:`ChainSource.series` —— **某一张合约**在一段日期上的逐日报价，用于判定
  止盈止损的首次触发。这是成交表引擎的核心操作，一次切片取代逐日循环。
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Protocol

import pandas as pd

from backtest.errors import DataQualityError
from backtest.types import CHAIN_COLUMNS, ContractId, Right

#: 合成链无法提供的字段用它标记。下游据此跳过相关判断，而不是当成 0。
UNAVAILABLE = float("nan")


class ChainSource(Protocol):
    """期权链的统一访问接口。真实数据源与合成链实现同一个协议。"""

    def snapshot(self, day: date) -> pd.DataFrame:
        """某日全部可交易合约的长表，列为 :data:`CHAIN_COLUMNS`。"""
        ...

    def series(self, contract: ContractId, days: Iterable[date]) -> pd.DataFrame:
        """某张合约在给定日期上的逐日报价；无报价的日期不出现在结果中。"""
        ...


def empty_chain() -> pd.DataFrame:
    return pd.DataFrame(columns=list(CHAIN_COLUMNS))


def validate_chain(frame: pd.DataFrame, *, asof: date) -> pd.DataFrame:
    """质量校验。异常记录被剔除或标记，不得静默进入回测。

    返回剔除异常后的表；被剔除的原因写入 ``quality_flags``，便于事后追溯。
    """
    missing = set(CHAIN_COLUMNS) - set(frame.columns)
    if missing:
        raise DataQualityError(f"期权链缺少字段：{sorted(missing)}")
    if frame.empty:
        return frame

    flags = frame["quality_flags"].fillna("").astype(str)
    bad_price = (frame["bid"] < 0) | (frame["ask"] <= 0)
    inverted = frame["ask"] < frame["bid"]
    expired = pd.to_datetime(frame["expiry"]).dt.date < asof

    flags = flags.mask(bad_price, flags + "|negative_price")
    flags = flags.mask(inverted, flags + "|ask_below_bid")
    flags = flags.mask(expired, flags + "|already_expired")
    frame = frame.assign(quality_flags=flags.str.strip("|"))

    dropped = bad_price | inverted | expired
    if frame.duplicated(subset=["asof", "symbol", "expiry", "strike", "right"]).any():
        raise DataQualityError(f"{asof} 的期权链存在重复主键")
    return frame.loc[~dropped].reset_index(drop=True)


def relative_spread(bid: pd.Series | float, ask: pd.Series | float):
    """相对买卖价差 = (ask - bid) / mid。报价不可用时结果为 NaN。"""
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid


def delta_after_move(
    spot: float, strike: float, dte_days: float, iv: float, rate: float,
    right: Right, move: float, days_forward: int = 10,
) -> float:
    """标的动了 ``move``、又过了 ``days_forward`` 天之后，这张合约的 delta。

    **链上没有这个字段，只能重新定价**——"如果涨 3% 会怎样"不是任何数据源会提供的
    观测值。所以它放在数据层：模型推导出来的链上量本来就在这一层产生并打标，
    上层只管消费，不关心数字是观测还是推导。

    假设 IV 不变。现实里标的反弹时 IV 通常会掉，所以**这个数偏乐观**——
    偏的方向是确定的，用的人知道就行。
    """
    from backtest.pricing import greeks

    later = max(dte_days - days_forward, 1) / 365.0
    moved = spot * (1.0 + move) if right is Right.CALL else spot * (1.0 - move)
    g = greeks(moved, strike, later, rate, iv, 0.0, right)
    return abs(float(g["delta"]))
