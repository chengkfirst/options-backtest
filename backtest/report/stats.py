"""交易层统计。

**本阶段没有权益曲线**，因此没有最大回撤、夏普、资金占用——那些依赖组合权益，
要等事件驱动引擎（路线图阶段 4）才成立。这里只做交易层能诚实回答的事。

买方的收益分布低胜率、正偏、厚尾，只看均值与胜率会严重误判。所以分布是一等公民：
分位数、尾部、偏度峰度，以及"中位数与均值符号相反"这种由极值驱动的信号。
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backtest.types import ExitReason, QuoteSource

#: 分布分位点。
QUANTILES = (0.05, 0.25, 0.50, 0.75, 0.95)


def trade_stats(trades: pd.DataFrame) -> dict[str, Any]:
    """总体交易统计。零交易时返回空统计，而不是报错或给出误导性的零胜率。"""
    if trades.empty:
        return {"count": 0, "note": "回测区间内未产生任何交易"}
    pnl = trades["net_pnl"]
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    return {
        "count": int(len(pnl)),
        "win_rate": float((pnl > 0).mean()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf"),
        "avg_holding_days": float(trades["holding_days"].mean()),
        "gross_pnl": float(trades["gross_pnl"].sum()),
        "fees": float(trades["fees"].sum()),
        "net_pnl": float(pnl.sum()),
    }


def by_exit_reason(trades: pd.DataFrame) -> pd.DataFrame:
    """按了结原因分组。四类终止方式的表现通常差异巨大。"""
    if trades.empty:
        return pd.DataFrame(columns=["count", "win_rate", "net_pnl", "avg_pnl"])
    grouped = trades.groupby("exit_reason")["net_pnl"]
    out = pd.DataFrame(
        {
            "count": grouped.size(),
            "win_rate": trades.groupby("exit_reason")["net_pnl"].apply(lambda s: (s > 0).mean()),
            "net_pnl": grouped.sum(),
            "avg_pnl": grouped.mean(),
        }
    )
    return out.sort_values("count", ascending=False)


def pnl_distribution(trades: pd.DataFrame, notional_capital: float) -> dict[str, Any]:
    """单笔盈亏分布，两种口径并存。

    绝对金额与占权益比例的形状不同，只看其一会误判风险量级。
    """
    if trades.empty:
        return {"count": 0}
    pnl = trades["net_pnl"].to_numpy(dtype=float)
    pct = pnl / notional_capital
    mean, median = float(pnl.mean()), float(np.median(pnl))
    return {
        "count": int(pnl.size),
        "quantiles_abs": {f"q{int(q*100)}": float(np.quantile(pnl, q)) for q in QUANTILES},
        "quantiles_pct": {f"q{int(q*100)}": float(np.quantile(pct, q)) for q in QUANTILES},
        "mean": mean,
        "median": median,
        "max_win": float(pnl.max()),
        "max_loss": float(pnl.min()),
        "skew": float(pd.Series(pnl).skew()),
        "kurtosis": float(pd.Series(pnl).kurtosis()),
        # 中位数与均值符号相反 = 平均收益由少数极值驱动
        "driven_by_outliers": bool(np.sign(mean) != np.sign(median) and median != 0),
    }


def histogram(trades: pd.DataFrame, bins: int = 20) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["lower", "upper", "count"])
    counts, edges = np.histogram(trades["net_pnl"].to_numpy(dtype=float), bins=bins)
    return pd.DataFrame({"lower": edges[:-1], "upper": edges[1:], "count": counts})


def cost_breakdown(trades: pd.DataFrame) -> dict[str, Any]:
    """成本分项与扣费前后对照。合起来看不出问题，分开看才知道钱被谁吃掉。"""
    if trades.empty:
        return {"total": 0.0}
    gross = float(trades["gross_pnl"].sum())
    fees = {
        "commission": float(trades["fee_commission"].sum()),
        "regulatory": float(trades["fee_regulatory"].sum()),
        "slippage": float(trades["fee_slippage"].sum()),
    }
    total = sum(fees.values())
    net = float(trades["net_pnl"].sum())
    return {
        **fees,
        "total": total,
        "gross_pnl": gross,
        "net_pnl": net,
        "fees_pct_of_gross": (total / abs(gross)) if gross else float("inf"),
        # 毛盈亏为正而净盈亏为负：收益不足以覆盖交易成本
        "eaten_by_costs": gross > 0 > net,
    }


def data_provenance(trades: pd.DataFrame) -> dict[str, Any]:
    """数据来源披露。合成数据的结论不可外推，必须显著标注。"""
    if trades.empty:
        return {"synthetic_share": 0.0, "all_synthetic": False}
    share = float((trades["source"] == QuoteSource.SYNTHETIC.value).mean())
    return {
        "synthetic_share": share,
        "all_synthetic": share == 1.0,
        "mixed": 0.0 < share < 1.0,
    }


def passive_expiry_check(trades: pd.DataFrame) -> dict[str, Any]:
    """被动到期尤其是实值被动到期，意味着了结规则失效过一次。"""
    if trades.empty:
        return {"itm": 0, "otm": 0}
    counts = trades["exit_reason"].value_counts()
    itm = int(counts.get(ExitReason.EXPIRY_ITM.value, 0))
    otm = int(counts.get(ExitReason.EXPIRY_OTM.value, 0))
    return {"itm": itm, "otm": otm, "rule_failed": itm > 0}


def environment() -> dict[str, str]:
    """关键数值依赖的版本。版本变化会改变浮点末位，破坏可复现性。"""
    import numpy, pandas, scipy

    return {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "scipy": scipy.__version__,
    }
