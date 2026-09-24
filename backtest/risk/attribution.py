"""盈亏归因：钱到底从哪来。

一个看似赚钱的买方策略，如果收益全部来自 vega（进场后隐含波动率涨了），而 delta
是亏的，那它根本不是你以为的那个策略——换个波动率环境就废。只看总盈亏看不出这件事。

方法是对期权价格做二阶泰勒展开：

    ΔP ≈ delta·ΔS + ½·gamma·ΔS² + vega·Δσ + theta·Δt

四项之和与实际盈亏的差就是**残差**。残差大说明近似失效——通常是标的动得太大
（二阶展开不够）或持有期太长（希腊字母在期间变化太多）。**残差不是垃圾桶**：
它本身是一个诊断量，大到一定程度就说明这次归因不可信。

口径：
- ``vega`` 是波动率变动 1.00（100 个点）对应的价格变化，所以 Δσ 用小数
- ``theta`` 是每年的衰减，所以 Δt 用年（日历日 / 365，与定价口径一致）
- 全部乘以合约乘数与张数，单位是美元
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backtest.config import CALENDAR_DAYS_PER_YEAR

#: 残差占毛盈亏的比例超过这个值，就认为这次归因不可信。
RESIDUAL_WARN = 0.35

COMPONENTS = ("delta", "gamma", "vega", "theta", "residual")


@dataclass(frozen=True, slots=True)
class Attribution:
    """一份归因结果。"""

    per_trade: pd.DataFrame          # 每笔一行，含四个分量与残差
    totals: dict[str, float]         # 各分量的累计贡献
    unusable: int                    # 因缺希腊字母而无法归因的笔数
    high_residual: int               # 残差过大、归因不可信的笔数

    def as_dict(self) -> dict[str, Any]:
        gross = sum(self.totals.values())
        return {
            "totals": self.totals,
            "share": {k: (v / gross if gross else float("nan")) for k, v in self.totals.items()},
            "unusable": self.unusable,
            "high_residual": self.high_residual,
            "n": len(self.per_trade),
        }

    def text(self) -> str:
        gross = sum(self.totals.values())
        lines = ["盈亏归因", "─" * 46]
        for name in COMPONENTS:
            v = self.totals[name]
            share = f"{v / gross * 100:>6.1f}%" if gross else "     —"
            lines.append(f"  {name:<10} {v:>12,.0f}   {share}")
        lines += ["─" * 46, f"  合计       {gross:>12,.0f}"]
        if self.unusable:
            lines.append(f"  ! {self.unusable} 笔缺希腊字母，未纳入归因")
        if self.high_residual:
            lines.append(
                f"  ! {self.high_residual} 笔残差超过毛盈亏的 {RESIDUAL_WARN:.0%}，"
                f"这些笔的归因不可信"
            )
        return "\n".join(lines)


def attribute(trades: pd.DataFrame) -> Attribution:
    """对交易明细做盈亏归因。

    缺任何一个希腊字母的交易**被排除并计数**，不是填 0 混进去——一个被当成 0 的
    vega 会让归因结论完全错误，而且不会报错。
    """
    required = ["entry_delta", "entry_gamma", "entry_vega", "entry_theta",
                "entry_underlying_price", "exit_underlying_price",
                "entry_iv", "exit_iv", "entry_date", "exit_date",
                "gross_pnl", "multiplier", "quantity"]
    missing_cols = [c for c in required if c not in trades.columns]
    if missing_cols:
        raise KeyError(f"交易明细缺少归因所需的列：{missing_cols}")
    if trades.empty:
        return Attribution(pd.DataFrame(columns=list(COMPONENTS)), dict.fromkeys(COMPONENTS, 0.0), 0, 0)

    usable = trades.dropna(subset=required)
    unusable = len(trades) - len(usable)
    if usable.empty:
        return Attribution(pd.DataFrame(columns=list(COMPONENTS)), dict.fromkeys(COMPONENTS, 0.0),
                           unusable, 0)

    scale = usable["multiplier"] * usable["quantity"]
    d_spot = usable["exit_underlying_price"] - usable["entry_underlying_price"]
    d_vol = usable["exit_iv"] - usable["entry_iv"]
    d_years = (
        (pd.to_datetime(usable["exit_date"]) - pd.to_datetime(usable["entry_date"])).dt.days
        / CALENDAR_DAYS_PER_YEAR
    )

    out = pd.DataFrame(index=usable.index)
    out["delta"] = usable["entry_delta"] * d_spot * scale
    out["gamma"] = 0.5 * usable["entry_gamma"] * d_spot**2 * scale
    out["vega"] = usable["entry_vega"] * d_vol * scale
    out["theta"] = usable["entry_theta"] * d_years * scale
    out["explained"] = out[["delta", "gamma", "vega", "theta"]].sum(axis=1)
    out["actual"] = usable["gross_pnl"]
    out["residual"] = out["actual"] - out["explained"]

    denom = out["actual"].abs().replace(0.0, np.nan)
    out["residual_share"] = (out["residual"].abs() / denom).fillna(np.inf)
    high = int((out["residual_share"] > RESIDUAL_WARN).sum())

    for key in ("trade_id", "exit_reason", "entry_date"):
        if key in usable.columns:
            out[key] = usable[key]

    totals = {name: float(out[name].sum()) for name in COMPONENTS}
    return Attribution(out, totals, unusable, high)


def attribute_by(trades: pd.DataFrame, column: str, bins: int = 4) -> pd.DataFrame:
    """按某一列分组做归因，回答"某类情形下的钱是从哪来的"。

    数值列自动等频分档，类别列直接分组。
    """
    if column not in trades.columns:
        raise KeyError(f"交易明细里没有 {column!r}")
    frame = trades.dropna(subset=[column])
    if frame.empty:
        return pd.DataFrame(columns=list(COMPONENTS))
    if pd.api.types.is_numeric_dtype(frame[column]):
        groups = pd.qcut(frame[column], bins, duplicates="drop")
    else:
        groups = frame[column]

    rows = []
    for key, part in frame.groupby(groups, observed=True):
        result = attribute(part)
        rows.append({"组": str(key), "笔数": len(part), **result.totals,
                     "残差可疑笔数": result.high_residual})
    return pd.DataFrame(rows).set_index("组")
