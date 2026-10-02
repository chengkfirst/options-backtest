"""Sizing methods compared: one set of signals, different ways of betting.

**Signal and size are two separate things.** A signal with an edge and the wrong
sizing method still blows up; a mediocre signal with robust sizing survives a long
time. So the right way to settle positioning is to fix the signal, change only the
sizing, and watch what the equity curve does.

Return is not the dimension that matters here — it is the least important one.
What matters is **drawdown**, the **largest single loss as a fraction of equity**,
and **capital utilisation**. Return can be piled up with leverage; risk cannot.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from backtest.portfolio.positioning import Sizing

from backtest.engine.strategy import Strategy

#: The methods compared by default. The parameters are common values, not
#: optimised ones.
DEFAULT_METHODS: dict[str, Sizing] = {
    "fixed 1 contract": Sizing(method="fixed", contracts=1),
    "fixed 3 contracts": Sizing(method="fixed", contracts=3),
    "1% risk per trade": Sizing(method="risk_pct", risk_pct=0.01),
    "2% risk per trade": Sizing(method="risk_pct", risk_pct=0.02),
    "5% risk per trade": Sizing(method="risk_pct", risk_pct=0.05),
    "fixed 5000 notional": Sizing(method="fixed_notional", notional=5_000),
    "scaled by volatility": Sizing(method="scale_by_vol", risk_pct=0.02, target_vol=0.20),
}


def _metrics(result) -> dict[str, Any]:
    equity = result.equity["equity_mid"]
    returns = equity.pct_change().dropna()
    drawdown = equity / equity.cummax() - 1
    pnl = [r.net_pnl for r in result.records]
    start, finish = float(equity.iloc[0]), float(equity.iloc[-1])
    years = max(len(equity) / 252.0, 1e-9)
    return {
        "trades": len(pnl),
        "total return": finish / start - 1,
        "annualised": (finish / start) ** (1 / years) - 1 if finish > 0 else np.nan,
        "annualised vol": float(returns.std() * np.sqrt(252)) if len(returns) > 1 else np.nan,
        "max drawdown": float(drawdown.min()),
        "worst trade / equity": float(min(pnl) / start) if pnl else np.nan,
        "mean capital used": float((result.equity["deployed"] / equity).mean()),
        "peak capital used": float((result.equity["deployed"] / equity).max()),
        "peak positions": int(result.equity["positions"].max()),
    }


def compare(
    market, chain, signals, config, strategy: Strategy,
    methods: Mapping[str, Sizing] | None = None,
    *, max_concurrent: int | None = 5,
) -> pd.DataFrame:
    """Run one strategy under several sizing methods, side by side.

    **Only the sizing differs** — signals, selection and exit rules are identical,
    or the differences could not be attributed to any one of them.
    """
    from backtest.engine.loop import EventDrivenEngine

    methods = methods or DEFAULT_METHODS
    rows = []
    for name, sizing in methods.items():
        variant = replace(strategy, sizing=sizing)
        try:
            result = EventDrivenEngine(
                market, chain, signals, config, max_concurrent=max_concurrent
            ).run(variant)
        except Exception as exc:  # noqa: BLE001
            rows.append({"method": name, "trades": 0,
                         "note": f"did not run: {type(exc).__name__}: {exc}"})
            continue
        rows.append({"method": name, **_metrics(result), "note": ""})
    frame = pd.DataFrame(rows).set_index("method")
    return frame


def verdict(frame: pd.DataFrame) -> str:
    """Read the comparison table as a sentence.

    It deliberately does not recommend the highest return — return can be piled up
    by sizing bigger, which is not a better method, only more leverage.
    """
    usable = frame[frame["trades"] > 0] if "trades" in frame else frame
    if usable.empty:
        return "none of the methods produced a result."
    best_return = usable["total return"].idxmax()
    best_dd = usable["max drawdown"].idxmax()     # drawdowns are negative: max is shallowest
    worst_single = usable["worst trade / equity"].idxmin()
    lines = [
        f"highest return: {best_return} ({usable.loc[best_return, 'total return']:.1%}, "
        f"but a {usable.loc[best_return, 'max drawdown']:.1%} drawdown)",
        f"shallowest drawdown: {best_dd} "
        f"({usable.loc[best_dd, 'max drawdown']:.1%})",
        f"largest single-trade risk: {worst_single} "
        f"(one trade loses {abs(usable.loc[worst_single, 'worst trade / equity']):.1%} "
        f"of equity)",
        "",
        "The highest return is usually just the bigger position, not the better "
        "method. Read the drawdown and the single-trade risk.",
    ]
    return "\n".join(lines)
