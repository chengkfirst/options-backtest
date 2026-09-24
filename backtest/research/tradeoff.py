"""选合约的取舍面：同一天的链，按虚值 × 期限摊开看。

## 为什么要这个

选合约的直觉是关于**结构**的——"这张太虚了，动了也起不来"、"这么短的 theta 扛
不住"。这些判断背后是几个量在虚值 × 期限这个面上的形状。看不见那个形状，就只能
凭记忆猜。

## 四个量，各回答一句话

===================  ======================================================
``theta_pct_day``     标的不动，我每天烧掉权利金的百分之几
``delta_now``         现在的方向暴露
``delta_if_move``     **论点兑现之后**暴露变成多少 ← 最容易被忽略的一个
``breakeven_move``    标的要动多少我才回本。**正数一律表示"朝论点方向动"**，
                      看涨看跌同号——与链上 ``moneyness`` 的口径一致，
                      免得读表时还要在脑子里换算方向
===================  ======================================================

``delta_if_move`` 是这里最重要的。短期深虚值的合约，标的真动了暴露反而缩——
论点对了，仓位没了。只看入场时的 delta 看不出这件事。

## 数字从哪来

**从当日真实链上算，不是从公式贴一张静态表。** 这样真实数据接进来之后它自动反映
真实的偏斜与期限结构；合成链上它反映的就是 Black-Scholes 的性质。两种情况下这张
表都诚实地描述"你当下真的能买到什么"，而不是描述某个模型应该是什么样。

``delta_if_move`` 是唯一需要模型的一项——链上没有"如果标的涨 3% 会怎样"这种数据，
只能重新定价。它会被单独标记来源。
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

#: 默认看的虚值档位（正数一律表示虚值，看涨看跌同号）。
MONEYNESS = (0.00, 0.02, 0.04, 0.06, 0.10)
#: 默认看的期限档位（日历日）。
TENORS = (14, 21, 30, 45, 60, 90)


def tradeoff_grid(
    chain: pd.DataFrame,
    *,
    spot: float,
    right: str = "C",
    moneyness: Sequence[float] = MONEYNESS,
    tenors: Sequence[int] | None = None,
    move: float = 0.03,
    rate: float = 0.04,
) -> dict[str, pd.DataFrame]:
    """把当日链摊成「虚值 × 期限」的四张表。

    每个格子取**最接近**该虚值/期限的那张真实合约；找不到足够接近的就留空，
    **不插值**——插出来的数字看起来和真实报价一样，但它不对应任何你能买到的东西。
    """
    if chain.empty:
        return {k: pd.DataFrame() for k in
                ("theta_pct_day", "delta_now", "delta_if_move", "breakeven_move")}

    pool = chain.loc[chain["right"].astype(str) == right].copy()
    if pool.empty:
        return {k: pd.DataFrame() for k in
                ("theta_pct_day", "delta_now", "delta_if_move", "breakeven_move")}

    pool["mid"] = (pool["bid"].astype(float) + pool["ask"].astype(float)) / 2.0
    is_call = right == "C"
    pool["_mny"] = (pool["strike"] / spot - 1.0) if is_call else (1.0 - pool["strike"] / spot)
    if "dte" not in pool.columns:
        asof = pd.Timestamp(pool["asof"].iloc[0]).date()
        pool["dte"] = [(pd.Timestamp(e).date() - asof).days for e in pool["expiry"]]

    # 期限用**链上真有的**，不用写死的档位。写死的话，到期日稀疏时整列是空的，
    # 看起来像系统坏了；而真实链的到期日分布本来就因标的而异。
    if tenors is None:
        available = sorted(int(d) for d in pool["dte"].unique() if d > 0)
        tenors = _spread_out(available, TENORS)

    out = {k: pd.DataFrame(index=[f"{m:.0%}" for m in moneyness],
                           columns=[f"{t}天" for t in tenors], dtype=float)
           for k in ("theta_pct_day", "delta_now", "delta_if_move", "breakeven_move")}

    for mi, m in enumerate(moneyness):
        for ti, t in enumerate(tenors):
            # 容差：虚值 ±1.2 个百分点，期限 ±25%。超出就留空，不凑。
            near = pool.loc[(pool["_mny"] - m).abs().le(0.012)
                            & (pool["dte"] - t).abs().le(max(3, t * 0.25))]
            if near.empty:
                continue
            row = near.iloc[((near["_mny"] - m).abs() / 0.012
                             + (near["dte"] - t).abs() / max(3, t * 0.25)).argmin()]
            mid, dte = float(row["mid"]), float(row["dte"])
            if mid <= 0.01 or dte <= 0:
                continue
            r_, c_ = f"{m:.0%}", f"{t}天"

            theta = float(row.get("theta", np.nan))
            if np.isfinite(theta):
                out["theta_pct_day"].at[r_, c_] = abs(theta) / mid / 365.0
            delta = float(row.get("delta", np.nan))
            if np.isfinite(delta):
                out["delta_now"].at[r_, c_] = abs(delta)

            strike = float(row["strike"])
            be = (strike + mid) / spot - 1.0 if is_call else 1.0 - (strike - mid) / spot
            out["breakeven_move"].at[r_, c_] = be

            iv = float(row.get("iv", np.nan))
            if np.isfinite(iv) and iv > 0:
                out["delta_if_move"].at[r_, c_] = _delta_after(
                    spot, strike, dte, iv, rate, is_call, move)
    return out


def _delta_after(spot, strike, dte, iv, rate, is_call, move) -> float:
    """论点兑现（标的动 ``move``）且过了十天之后的 delta。

    重定价放在数据层（``data.chain.delta_after_move``），不在这里直接调定价——
    上层只消费链上的字段，不关心数字是观测还是推导，这样换数据源时上层零改动。
    """
    from backtest.data.chain import delta_after_move
    from backtest.types import Right

    return delta_after_move(spot, strike, dte, iv, rate,
                            Right.CALL if is_call else Right.PUT, move)


def _spread_out(available: list[int], wanted: Sequence[int], n: int = 6) -> list[int]:
    """从链上真有的期限里，挑出最接近惯用档位的那几个。

    惯用档位（14/21/30/45/60/90）是为了让不同日子的表可以横向比较；但如果某天
    链上根本没有接近 90 天的到期日，硬列一栏空的只会让人以为系统坏了。
    """
    if not available:
        return []
    # 链上本来就没几个到期日时全留下。按惯用档挑会把"没人最接近它"的那些丢掉
    # （9/16/30/58 天会丢掉 9 天），链稀疏时等于白白少给信息。
    if len(available) <= n:
        return available
    picked: list[int] = []
    for w in wanted:
        best = min([d for d in available if d not in picked] or available,
                   key=lambda d: abs(d - w))
        if best not in picked:
            picked.append(best)
    return sorted(picked)[:n]
