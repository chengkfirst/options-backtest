"""What an investor asks: is this alpha, or leveraged beta.

## Why the return alone is not enough

For a strategy that buys QQQ calls, how much of the return was QQQ simply rising?
That is an investor's first question, and the reason these metrics exist. Without a
beta against the underlying and without a buy-and-hold comparison, everything a
strategy appears to have earned could be leveraged market exposure.

## Why not Sharpe

An option buyer has a low win rate, positive skew and fat tails. Sharpe divides by
total volatility, **treating upside volatility as risk**, and so understates a
positively skewed strategy systematically.

- **Sortino** counts only downside deviation
- **Calmar** is return over max drawdown — what an investor actually cares about is
  how bad the worst was

## Time underwater

Max drawdown says how deep, not how long. Minus 30% recovered in three weeks and
minus 30% recovered in two years are entirely different products.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: Trading days per year.
YEAR = 252


def summary(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    benchmark: "pd.Series | None" = None,
) -> dict[str, Any]:
    """The investor's set of metrics.

    Beta and the comparison exist only when ``benchmark`` is given — **without it
    nothing is invented**, and the missing entries state why.
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
        "downside_vol": dd_vol,
    }


def _underwater(curve: pd.Series) -> dict[str, Any]:
    """How long it spent underwater, and how long the deepest one took to recover."""
    peak = curve.cummax()
    under = curve < peak * (1 - 1e-12)
    share = float(under.mean())

    # Time to recover from the deepest drawdown; still underwater at the end of the
    # range is recorded as not yet recovered.
    drawdown = curve / peak - 1.0
    trough = int(np.argmin(drawdown.to_numpy()))
    prior_peak = float(peak.iloc[trough])
    after = curve.iloc[trough:]
    back = np.flatnonzero((after >= prior_peak).to_numpy())
    recover = int(back[0]) if len(back) else None

    # The longest single stretch underwater.
    longest, run = 0, 0
    for flag in under.to_numpy():
        run = run + 1 if flag else 0
        longest = max(longest, run)

    return {
        "share_underwater": share,
        "longest_underwater_days": longest,
        "deepest_recovery_days": recover if recover is not None else np.nan,
        "deepest_recovered": recover is not None,
    }


def _concentration(trades: pd.DataFrame) -> dict[str, Any]:
    """Is the return concentrated in a few trades — a strategy, or a few pieces of
    luck."""
    if trades is None or trades.empty or "net_pnl" not in trades:
        return {}
    pnl = trades["net_pnl"].astype(float)
    total = float(pnl.sum())
    top5 = float(pnl.nlargest(min(5, len(pnl))).sum())
    out: dict[str, Any] = {
        "top5_share": top5 / total if total > 0 else np.nan,
        "without_top5": total - top5,
    }
    if "exit_date" in trades:
        by_year = pnl.groupby(pd.to_datetime(trades["exit_date"]).dt.year).sum()
        if len(by_year):
            out["profitable_years_share"] = float((by_year > 0).mean())
            out["worst_year"] = float(by_year.min())
    return out


def _vs_benchmark(curve: pd.Series, benchmark: "pd.Series | None") -> dict[str, Any]:
    """Beta, and the buy-and-hold comparison.

    **This is an investor's first question**: how much of the return was the
    underlying rising. With no benchmark this says so plainly rather than
    substituting something else.
    """
    if benchmark is None or benchmark.empty:
        return {"beta_vs_underlying": np.nan,
                "beta_note": "no benchmark series was given, so there is no way to "
                             "tell alpha from leveraged beta"}

    bench = benchmark.astype(float).reindex(curve.index).ffill()
    a = curve.pct_change().dropna()
    b = bench.pct_change().dropna()
    idx = a.index.intersection(b.index)
    if len(idx) < 20:
        return {"beta_vs_underlying": np.nan,
                "beta_note": "fewer than 20 trading days overlap with the benchmark"}
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
        "beta_vs_underlying": beta,
        "annualised_alpha": alpha,
        "buy_and_hold": bench_total,
        "strategy_total": strat_total,
        "beta_note": ("beta near 0: essentially unrelated to the underlying"
                      if abs(beta) < 0.2 else
                      f"each unit of the underlying's move brings about {beta:.2f} "
                      f"units of the strategy's"),
    }


def cost_sensitivity(trades: pd.DataFrame, multiples=(1.0, 2.0, 3.0)) -> pd.DataFrame:
    """Does it survive costs doubling? Live costs above backtest costs is the norm."""
    if trades is None or trades.empty:
        return pd.DataFrame()
    gross = float(trades["gross_pnl"].sum())
    fees = float(trades["fees"].sum())
    rows = []
    for m in multiples:
        net = gross - fees * m
        rows.append({
            "cost multiple": f"{m:g}x",
            "total fees": fees * m,
            "net P&L": net,
            "still profitable": net > 0,
        })
    return pd.DataFrame(rows).set_index("cost multiple")
