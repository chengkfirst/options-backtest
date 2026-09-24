"""组合层风险：希腊字母聚合、压力测试、情景重放、尾部度量。

**为什么用希腊字母而不是重新定价。** 压力测试可以"把标的打到 -20% 再把每张合约重算
一遍"，那样更精确。这里不这么做，理由有两个：一是定价被关在合成链内部，重算意味着
上层要知道价格怎么来的——真实数据接进来之后那个假设就不成立了；二是希腊字母展开是
业界做压力测试的通行方式，而且它用的就是链上给出的字段，换成真实数据源照样能用。

代价要说清楚：一阶／二阶展开在**大冲击**下失真，标的动 20% 时二阶项已经不够。所以
结果里带上展开阶数的提示，大冲击的格子会被标注。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from backtest.config import CALENDAR_DAYS_PER_YEAR

#: 超过这个幅度的标的冲击，二阶展开的误差已经不可忽略。
LARGE_SHOCK = 0.10

#: 默认的压力网格。标的按百分比、波动率按绝对点数。
DEFAULT_SPOT_SHOCKS = (-0.20, -0.10, -0.05, 0.0, 0.05, 0.10)
DEFAULT_VOL_SHOCKS = (-0.10, -0.05, 0.0, 0.05, 0.10)


@dataclass(frozen=True, slots=True)
class PortfolioGreeks:
    """某日的组合层希腊字母，已乘合约乘数与张数，单位是美元。"""

    day: date
    spot: float
    delta: float          # 标的变动 1 美元对应的组合价值变化
    gamma: float          # delta 对标的的变化率
    vega: float           # 波动率变动 1.00（100 点）对应的变化
    theta: float          # 每年的时间衰减
    n_positions: int
    equity: float
    missing: int          # 缺希腊字母、未计入的持仓数

    @property
    def delta_dollars(self) -> float:
        """delta 敞口折算成标的名义金额。"""
        return self.delta * self.spot

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day, "spot": self.spot,
            "delta": self.delta, "gamma": self.gamma, "vega": self.vega, "theta": self.theta,
            "delta_dollars": self.delta_dollars,
            "delta_pct_equity": self.delta_dollars / self.equity if self.equity else np.nan,
            "vega_pct_equity": self.vega / self.equity if self.equity else np.nan,
            "theta_per_day": self.theta / CALENDAR_DAYS_PER_YEAR,
            "positions": self.n_positions, "missing_greeks": self.missing,
        }


def aggregate_greeks(positions, chain, day: date, spot: float, equity: float) -> PortfolioGreeks:
    """把当日持仓的希腊字母加总。

    拿不到希腊字母的持仓**被计数而不是当作 0**——一个被当成 0 的 vega 会让组合看起来
    毫无波动率暴露，那正是最危险的误判。
    """
    totals = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
    missing = 0
    for pos in positions:
        frame = chain.series(pos.contract, [day])
        if frame.empty:
            missing += 1
            continue
        row = frame.iloc[0]
        if any(pd.isna(row.get(g)) for g in totals):
            missing += 1
            continue
        weight = pos.quantity * pos.multiplier
        for g in totals:
            totals[g] += float(row[g]) * weight
    return PortfolioGreeks(
        day=day, spot=spot, n_positions=len(tuple(positions)), equity=equity, missing=missing,
        **totals,
    )


def stress_grid(
    greeks: PortfolioGreeks,
    *,
    spot_shocks: Sequence[float] = DEFAULT_SPOT_SHOCKS,
    vol_shocks: Sequence[float] = DEFAULT_VOL_SHOCKS,
    days_forward: int = 0,
) -> pd.DataFrame:
    """标的 × 波动率的二维压力网格，格内是组合价值的变化（美元）。

    ``days_forward`` 同时把时间往前推，看"如果什么都不动、只过了 N 天"会怎样——
    对买方而言那是纯损失。
    """
    rows = []
    theta_effect = greeks.theta * (days_forward / CALENDAR_DAYS_PER_YEAR)
    for ds_pct in spot_shocks:
        ds = greeks.spot * ds_pct
        beyond = abs(ds_pct) > LARGE_SHOCK
        row = {"标的冲击": f"{ds_pct:+.0%}"}
        for dv in vol_shocks:
            if beyond:
                # 超出展开适用范围时**不给数字**。
                #
                # 对买方组合，gamma 为正，二阶项 ½γΔS² 会在大冲击下压倒 delta 项，
                # 算出"标的暴跌 20% 反而赚钱"这种方向都错的结果。标一句"可能失真"
                # 挡不住误读——一个错得理直气壮的数字比没有数字更危险。
                # 要看大冲击，用 scenario_replay：那里是真实历史，不含任何近似。
                row[f"IV{dv:+.0%}"] = np.nan
                continue
            row[f"IV{dv:+.0%}"] = (
                greeks.delta * ds + 0.5 * greeks.gamma * ds**2
                + greeks.vega * dv + theta_effect
            )
        row["说明"] = (
            f"超出二阶展开适用范围（±{LARGE_SHOCK:.0%}），不给数；请用历史情景重放"
            if beyond else ""
        )
        rows.append(row)
    return pd.DataFrame(rows).set_index("标的冲击")


def var_cvar(returns: pd.Series, level: float = 0.05) -> dict[str, float]:
    """历史 VaR 与 CVaR。

    用历史分位而非正态假设——期权组合的收益分布厚尾且偏斜，正态 VaR 会系统性低估
    尾部。样本量不足时给出提示，因为 5% 分位在 200 个观测上只由 10 个点决定。
    """
    clean = returns.dropna()
    n = len(clean)
    if n < 20:
        return {"n": n, "var": float("nan"), "cvar": float("nan"),
                "note": f"只有 {n} 个观测，尾部度量不可信"}
    var = float(np.quantile(clean, level))
    tail = clean[clean <= var]
    note = ""
    if n < 250:
        note = f"{n} 个观测，{level:.0%} 分位只由约 {max(1, int(n * level))} 个点决定，结论脆弱"
    return {"n": n, "var": var, "cvar": float(tail.mean()) if len(tail) else var,
            "tail_count": len(tail), "level": level, "note": note}


def scenario_replay(equity: pd.DataFrame, windows: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """在指定的历史窗口上重放权益曲线。

    压力测试问"如果发生 X 会怎样"，情景重放问"当年真的发生时，这个策略怎么样"。
    后者不依赖任何展开近似，因此更可信——代价是只有历史上发生过的才能看。
    """
    rows = []
    index = pd.to_datetime(equity.index)
    for name, (start, end) in windows.items():
        mask = (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
        part = equity.loc[mask]
        if part.empty:
            rows.append({"情景": name, "起": start, "止": end, "交易日": 0,
                         "区间收益": np.nan, "最大回撤": np.nan, "备注": "回测区间未覆盖"})
            continue
        curve = part["equity_mid"]
        rows.append({
            "情景": name, "起": start, "止": end, "交易日": len(part),
            "区间收益": curve.iloc[-1] / curve.iloc[0] - 1,
            "最大回撤": float((curve / curve.cummax() - 1).min()),
            "备注": "" if len(part) > 10 else "窗口过短",
        })
    return pd.DataFrame(rows).set_index("情景")


#: 常用的历史压力窗口。名字写清楚是什么事件，免得三个月后看不懂。
KNOWN_SCENARIOS: dict[str, tuple[str, str]] = {
    "2018Q4 急跌": ("2018-10-01", "2018-12-31"),
    "2020 疫情崩盘": ("2020-02-19", "2020-03-23"),
    "2020 反弹": ("2020-03-24", "2020-06-30"),
    "2022 加息熊市": ("2022-01-01", "2022-10-14"),
    "2023 反弹": ("2023-01-01", "2023-07-31"),
}


def daily_risk_report(greeks: PortfolioGreeks, equity_returns: pd.Series) -> str:
    """一页日风险报告。"""
    d = greeks.as_dict()
    v = var_cvar(equity_returns)
    lines = [
        f"日风险报告 · {greeks.day}",
        "─" * 52,
        f"  持仓 {greeks.n_positions} 个" + (f"（{greeks.missing} 个缺希腊字母，未计入）" if greeks.missing else ""),
        f"  delta 敞口   {d['delta_dollars']:>12,.0f}   占权益 {d['delta_pct_equity']:>7.2%}",
        f"  vega  敞口   {d['vega']:>12,.0f}   占权益 {d['vega_pct_equity']:>7.2%}",
        f"  gamma        {greeks.gamma:>12,.2f}",
        f"  theta/日     {d['theta_per_day']:>12,.0f}",
        "",
        f"  日收益 VaR(5%)  {v['var']:>8.2%}" if not np.isnan(v["var"]) else "  VaR 样本不足",
        f"  日收益 CVaR(5%) {v['cvar']:>8.2%}" if not np.isnan(v.get("cvar", np.nan)) else "",
    ]
    if v.get("note"):
        lines.append(f"  ! {v['note']}")
    return "\n".join(l for l in lines if l is not None)
