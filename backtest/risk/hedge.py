"""对冲与风险叠加。

**对冲只有在组合层才有意义**——一个头寸对冲另一个头寸的风险，前提是它们并存。
所以这一层依赖事件驱动引擎，成交表引擎各笔独立，表达不了。

三件事：

1. **敞口限额**：组合层 delta / vega 超限时拦住新开仓，或要求减仓。
2. **回撤熔断**：权益回撤到某档就降规模、再一档就停止开仓。
3. **对冲成本独立核算**：这是最容易被忽略的一条。对冲一定是花钱的，问题是
   "花的这些钱换来了什么"。所以必须能算出**剔除对冲之后**的结果来对照——
   只看加了对冲的曲线，永远不知道它值不值。

第 3 条的实现方式：对冲头寸单独打标，报告时可以把它们剔掉重算。这要求下单时就
标明"这笔是对冲"，事后补不回来。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class ExposureLimits:
    """组合层敞口上限。按占权益的比例设，而不是绝对金额——权益会变。"""

    max_delta_pct: float | None = None      # |delta 名义| / 权益
    max_vega_pct: float | None = None       # |vega| / 权益
    max_positions: int | None = None
    #: 超限时怎么办。``"block"`` 只拦新开仓；``"reduce"`` 还要求减仓（未实现）。
    on_breach: str = "block"

    def check(self, greeks, equity: float) -> tuple[bool, str]:
        """返回（是否允许新开仓，原因）。"""
        if equity <= 0:
            return False, "权益非正"
        if self.max_delta_pct is not None:
            ratio = abs(greeks.delta_dollars) / equity
            if ratio > self.max_delta_pct:
                return False, f"delta 敞口 {ratio:.1%} 超过上限 {self.max_delta_pct:.1%}"
        if self.max_vega_pct is not None:
            ratio = abs(greeks.vega) / equity
            if ratio > self.max_vega_pct:
                return False, f"vega 敞口 {ratio:.1%} 超过上限 {self.max_vega_pct:.1%}"
        if self.max_positions is not None and greeks.n_positions >= self.max_positions:
            return False, f"持仓数 {greeks.n_positions} 已达上限 {self.max_positions}"
        return True, ""


@dataclass(frozen=True, slots=True)
class DrawdownBrake:
    """回撤熔断。分档降级，恢复后解除。"""

    reduce_at: float = 0.10        # 回撤到此，规模减半
    halt_at: float = 0.20          # 回撤到此，停止开新仓
    resume_at: float = 0.05        # 回撤收窄到此，解除

    def state(self, drawdown: float) -> str:
        """``normal`` / ``reduced`` / ``halted``。``drawdown`` 是负数。"""
        d = abs(drawdown)
        if d >= self.halt_at:
            return "halted"
        if d >= self.reduce_at:
            return "reduced"
        return "normal"

    def scale(self, drawdown: float) -> float:
        return {"normal": 1.0, "reduced": 0.5, "halted": 0.0}[self.state(drawdown)]


def exposure_report(greeks_frame: pd.DataFrame, limits: ExposureLimits) -> pd.DataFrame:
    """逐日敞口与限额的对照。哪些天超了、超了多少。"""
    if greeks_frame.empty:
        return pd.DataFrame()
    out = greeks_frame[["delta_pct_equity", "vega_pct_equity", "positions"]].copy()
    if limits.max_delta_pct is not None:
        out["delta 超限"] = out["delta_pct_equity"].abs() > limits.max_delta_pct
    if limits.max_vega_pct is not None:
        out["vega 超限"] = out["vega_pct_equity"].abs() > limits.max_vega_pct
    return out


def breach_summary(report: pd.DataFrame) -> dict[str, Any]:
    if report.empty:
        return {"days": 0}
    out: dict[str, Any] = {"days": len(report)}
    for col in report.columns:
        if col.endswith("超限"):
            out[col] = {"天数": int(report[col].sum()), "占比": float(report[col].mean())}
    return out


def without_hedges(trades: pd.DataFrame) -> pd.DataFrame:
    """剔除对冲头寸，得到"不对冲会怎样"的对照。

    需要交易明细里有 ``is_hedge`` 列——它必须在**下单时**打上，事后无法从结果里推断
    哪一笔是对冲。没有这一列时直接报错，不猜。
    """
    if "is_hedge" not in trades.columns:
        raise KeyError(
            "交易明细里没有 is_hedge 列。对冲头寸必须在下单时打标，"
            "事后没法从结果里认出哪笔是对冲。"
        )
    return trades.loc[~trades["is_hedge"].astype(bool)]


def hedge_value(trades: pd.DataFrame) -> pd.DataFrame:
    """对冲值不值：加了对冲 vs 剔除对冲。

    只看"加了对冲的总收益"没有意义——对冲当然会拉低收益，问题是它把最坏的情况改善了
    多少。所以对照的重点是**尾部**，不是均值。
    """
    if "is_hedge" not in trades.columns:
        raise KeyError("交易明细里没有 is_hedge 列")
    hedges = trades.loc[trades["is_hedge"].astype(bool)]
    core = trades.loc[~trades["is_hedge"].astype(bool)]
    rows = []
    for name, part in (("含对冲", trades), ("剔除对冲", core)):
        pnl = part["net_pnl"]
        rows.append({
            "口径": name, "笔数": len(part),
            "合计": float(pnl.sum()),
            "平均每笔": float(pnl.mean()) if len(pnl) else np.nan,
            "最差单笔": float(pnl.min()) if len(pnl) else np.nan,
            "最差 5% 平均": float(pnl[pnl <= pnl.quantile(0.05)].mean()) if len(pnl) > 20 else np.nan,
        })
    rows.append({
        "口径": "对冲本身", "笔数": len(hedges),
        "合计": float(hedges["net_pnl"].sum()) if len(hedges) else 0.0,
        "平均每笔": float(hedges["net_pnl"].mean()) if len(hedges) else np.nan,
        "最差单笔": float(hedges["net_pnl"].min()) if len(hedges) else np.nan,
        "最差 5% 平均": np.nan,
    })
    return pd.DataFrame(rows).set_index("口径")
