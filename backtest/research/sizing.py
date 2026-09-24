"""仓位方法对照：同一套信号，不同的下注方式。

**信号和仓位是两件独立的事。** 一个有 edge 的信号配上错误的仓位方法照样能亏到爆仓；
一个平庸的信号配上稳健的仓位方法可以活很久。所以定 positioning 的正确做法是：
固定信号，只换仓位方法，看权益曲线怎么变。

对照的维度不只是收益——那是最不重要的一个。真正要看的是**回撤**、**单笔最大亏损占
权益的比例**、以及**资金占用率**。收益可以靠加杠杆堆出来，风险堆不出来。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from backtest.engine.strategy import Sizing, Strategy

#: 默认对照的几种方法。参数取的是常见值，不是优化出来的。
DEFAULT_METHODS: dict[str, Sizing] = {
    "固定 1 张": Sizing(method="fixed", contracts=1),
    "固定 3 张": Sizing(method="fixed", contracts=3),
    "每笔风险 1%": Sizing(method="risk_pct", risk_pct=0.01),
    "每笔风险 2%": Sizing(method="risk_pct", risk_pct=0.02),
    "每笔风险 5%": Sizing(method="risk_pct", risk_pct=0.05),
    "固定金额 5000": Sizing(method="fixed_notional", notional=5_000),
    "波动率目标 20%": Sizing(method="vol_target", risk_pct=0.02, target_vol=0.20),
}


def _metrics(result) -> dict[str, Any]:
    equity = result.equity["equity_mid"]
    returns = equity.pct_change().dropna()
    drawdown = equity / equity.cummax() - 1
    pnl = [r.net_pnl for r in result.records]
    start, finish = float(equity.iloc[0]), float(equity.iloc[-1])
    years = max(len(equity) / 252.0, 1e-9)
    return {
        "笔数": len(pnl),
        "总收益": finish / start - 1,
        "年化": (finish / start) ** (1 / years) - 1 if finish > 0 else np.nan,
        "年化波动": float(returns.std() * np.sqrt(252)) if len(returns) > 1 else np.nan,
        "最大回撤": float(drawdown.min()),
        "单笔最大亏占权益": float(min(pnl) / start) if pnl else np.nan,
        "平均资金占用": float((result.equity["deployed"] / equity).mean()),
        "峰值资金占用": float((result.equity["deployed"] / equity).max()),
        "峰值持仓": int(result.equity["positions"].max()),
    }


def compare(
    market, chain, signals, config, strategy: Strategy,
    methods: Mapping[str, Sizing] | None = None,
    *, max_concurrent: int | None = 5,
) -> pd.DataFrame:
    """同一策略跑多种仓位方法，并排比较。

    **只有仓位方法不同**，信号、选约、出场规则完全一致——否则比出来的差异说明不了
    是哪一项造成的。
    """
    from backtest.engine.event_driven import EventDrivenEngine

    methods = methods or DEFAULT_METHODS
    rows = []
    for name, sizing in methods.items():
        variant = replace(strategy, sizing=sizing)
        try:
            result = EventDrivenEngine(
                market, chain, signals, config, max_concurrent=max_concurrent
            ).run(variant)
        except Exception as exc:  # noqa: BLE001
            rows.append({"方法": name, "笔数": 0, "备注": f"跑不了：{type(exc).__name__}: {exc}"})
            continue
        rows.append({"方法": name, **_metrics(result), "备注": ""})
    frame = pd.DataFrame(rows).set_index("方法")
    return frame


def verdict(frame: pd.DataFrame) -> str:
    """把对照表读成一句话。

    刻意不推荐"收益最高"的那个——收益可以靠放大仓位堆出来，那不是方法好，是杠杆高。
    """
    usable = frame[frame["笔数"] > 0] if "笔数" in frame else frame
    if usable.empty:
        return "没有任何一种方法跑出结果。"
    best_return = usable["总收益"].idxmax()
    best_dd = usable["最大回撤"].idxmax()          # 回撤是负数，最大即最浅
    worst_single = usable["单笔最大亏占权益"].idxmin()
    lines = [
        f"收益最高：{best_return}（{usable.loc[best_return, '总收益']:.1%}，"
        f"但回撤 {usable.loc[best_return, '最大回撤']:.1%}）",
        f"回撤最浅：{best_dd}（{usable.loc[best_dd, '最大回撤']:.1%}）",
        f"单笔风险最大：{worst_single}"
        f"（一笔就亏掉权益的 {abs(usable.loc[worst_single, '单笔最大亏占权益']):.1%}）",
        "",
        "收益最高的那个通常只是仓位更大，不代表方法更好。看回撤和单笔风险。",
    ]
    return "\n".join(lines)
