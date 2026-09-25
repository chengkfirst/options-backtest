"""投资人要看的东西：这是 alpha 还是加了杠杆的 beta。

## 为什么单看收益率不够

买 QQQ call 的策略，收益里有多少只是 QQQ 本身在涨？这是投资人第一个问题，
也是这套指标存在的理由。没有对标的的 beta、没有同期买入持有的对照，
一个策略看起来的全部收益都可能只是杠杆化的市场暴露。

## 为什么不用夏普

期权买方是低胜率、正偏、厚尾。夏普用总波动做分母，**把上行波动也当成风险**，
系统性低估正偏策略。

- **Sortino** 只算下行偏离
- **Calmar** 是收益除以最大回撤——投资人真正关心的是"最坏的时候有多坏"

## 水下时间

最大回撤只说了"最深多少"，没说"待了多久"。−30% 三周回来和 −30% 两年回来，
是完全不同的产品。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: 一年多少个交易日。
YEAR = 252


def summary(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    benchmark: "pd.Series | None" = None,
) -> dict[str, Any]:
    """投资人视角的一组指标。

    ``benchmark`` 给了才有 beta 与对照——**没有它就不编**，缺的项标明原因。
    """
    curve = equity["equity_mid"].astype(float)
    out: dict[str, Any] = {}
    out.update(_risk_adjusted(curve))
    out.update(_underwater(curve))
    out.update(_concentration(trades))
    out.update(_vs_benchmark(curve, benchmark))
    return out


def _risk_adjusted(curve: pd.Series) -> dict[str, Any]:
    ret = curve.pct_change().dropna()
    if len(ret) < 2:
        return {"Sortino": np.nan, "Calmar": np.nan}
    years = max(len(curve) / YEAR, 1e-9)
    total = float(curve.iloc[-1] / curve.iloc[0] - 1.0)
    annual = (1.0 + total) ** (1 / years) - 1.0 if total > -1 else np.nan

    downside = ret[ret < 0]
    dd_vol = float(downside.std() * np.sqrt(YEAR)) if len(downside) > 1 else np.nan
    drawdown = curve / curve.cummax() - 1.0
    worst = float(drawdown.min())

    return {
        "Sortino": annual / dd_vol if dd_vol and np.isfinite(dd_vol) and dd_vol > 0 else np.nan,
        "Calmar": annual / abs(worst) if worst < 0 else np.nan,
        "下行波动": dd_vol,
    }


def _underwater(curve: pd.Series) -> dict[str, Any]:
    """在水下待了多久、最深那次多久才爬回来。"""
    peak = curve.cummax()
    under = curve < peak * (1 - 1e-12)
    share = float(under.mean())

    # 最深那次回撤的恢复用时；到区间末尾还没回来则记为「尚未收复」
    drawdown = curve / peak - 1.0
    trough = int(np.argmin(drawdown.to_numpy()))
    prior_peak = float(peak.iloc[trough])
    after = curve.iloc[trough:]
    back = np.flatnonzero((after >= prior_peak).to_numpy())
    recover = int(back[0]) if len(back) else None

    # 最长的一段水下时间
    longest, run = 0, 0
    for flag in under.to_numpy():
        run = run + 1 if flag else 0
        longest = max(longest, run)

    return {
        "水下时间占比": share,
        "最长水下(交易日)": longest,
        "最深回撤恢复用时(交易日)": recover if recover is not None else np.nan,
        "最深回撤是否已收复": recover is not None,
    }


def _concentration(trades: pd.DataFrame) -> dict[str, Any]:
    """收益集中在少数几笔上吗——是策略还是几次运气。"""
    if trades is None or trades.empty or "net_pnl" not in trades:
        return {}
    pnl = trades["net_pnl"].astype(float)
    total = float(pnl.sum())
    top5 = float(pnl.nlargest(min(5, len(pnl))).sum())
    out: dict[str, Any] = {
        "前5笔贡献": top5 / total if total > 0 else np.nan,
        "去掉最好5笔后": total - top5,
    }
    if "exit_date" in trades:
        by_year = pnl.groupby(pd.to_datetime(trades["exit_date"]).dt.year).sum()
        if len(by_year):
            out["盈利年份占比"] = float((by_year > 0).mean())
            out["最差一年"] = float(by_year.min())
    return out


def _vs_benchmark(curve: pd.Series, benchmark: "pd.Series | None") -> dict[str, Any]:
    """beta 与买入持有对照。

    **这是投资人第一个问题**：收益里有多少只是标的本身在涨。
    没有基准就如实说没有，不用别的东西凑。
    """
    if benchmark is None or benchmark.empty:
        return {"对标的beta": np.nan,
                "beta说明": "没给基准序列，无法判断这是 alpha 还是杠杆 beta"}

    bench = benchmark.astype(float).reindex(curve.index).ffill()
    a = curve.pct_change().dropna()
    b = bench.pct_change().dropna()
    idx = a.index.intersection(b.index)
    if len(idx) < 20:
        return {"对标的beta": np.nan, "beta说明": "与基准重叠的交易日不足 20 天"}
    a, b = a.loc[idx], b.loc[idx]
    var = float(b.var())
    beta = float(np.cov(a, b)[0, 1] / var) if var > 0 else np.nan

    years = max(len(idx) / YEAR, 1e-9)
    strat_total = float(curve.iloc[-1] / curve.iloc[0] - 1.0)
    bench_total = float(bench.dropna().iloc[-1] / bench.dropna().iloc[0] - 1.0)
    strat_ann = (1 + strat_total) ** (1 / years) - 1 if strat_total > -1 else np.nan
    bench_ann = (1 + bench_total) ** (1 / years) - 1 if bench_total > -1 else np.nan
    alpha = strat_ann - beta * bench_ann if np.isfinite(beta) else np.nan

    return {
        "对标的beta": beta,
        "年化alpha": alpha,
        "同期买入持有": bench_total,
        "策略总收益": strat_total,
        "beta说明": ("beta 接近 0：与标的基本无关" if abs(beta) < 0.2 else
                    f"每 1 单位标的波动带来约 {beta:.2f} 单位策略波动"),
    }


def cost_sensitivity(trades: pd.DataFrame, multiples=(1.0, 2.0, 3.0)) -> pd.DataFrame:
    """成本翻倍还活着吗。实盘成本高于回测是常态。"""
    if trades is None or trades.empty:
        return pd.DataFrame()
    gross = float(trades["gross_pnl"].sum())
    fees = float(trades["fees"].sum())
    rows = []
    for m in multiples:
        net = gross - fees * m
        rows.append({
            "成本倍数": f"{m:g}×",
            "总费用": fees * m,
            "净盈亏": net,
            "还能赚吗": net > 0,
        })
    return pd.DataFrame(rows).set_index("成本倍数")
