"""Trade-level statistics.

**There is no equity curve at this level**, so there is no max drawdown, no
Sharpe, no capital utilisation — those need portfolio equity and only hold for
the event-driven engine. What is here is what the trade level can answer
honestly.

A buyer's return distribution is low win rate, positively skewed, fat tailed, and
looking only at the mean and the win rate misjudges it badly. So the distribution
is a first-class citizen: quantiles, tails, skew and kurtosis, and signals like
"the median and the mean have opposite signs", which says extremes are driving
the average.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backtest.core.types import ExitReason, QuoteSource

#: Quantiles of the distribution.
QUANTILES = (0.05, 0.25, 0.50, 0.75, 0.95)


def trade_stats(trades: pd.DataFrame) -> dict[str, Any]:
    """Overall trade statistics. With no trades this returns an empty summary
    rather than raising or reporting a misleading 0% win rate."""
    if trades.empty:
        return {"count": 0, "note": "the run produced no trades"}
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
    """Grouped by exit reason. The ways a trade can end usually perform very
    differently."""
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
    """The per-trade P&L distribution, on two scales.

    Absolute amounts and fractions of equity have different shapes; looking at
    only one misjudges the magnitude of the risk.
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
        # Median and mean of opposite sign.
        #
        # **Not a warning.** It was called ``driven_by_outliers`` and reported as "does
        # not generalise", which is an equity frame applied to an option book: a low win
        # rate with a long right tail is the *normal* payoff of buying options, and the
        # shape it describes is the one the strategy is supposed to have. The thing a
        # reader needs to know is narrower and is not a judgement -- that the mean is not
        # a summary of a typical trade here, so a per-trade expectation read off it will
        # be wrong in both directions.
        "mean_unlike_median": bool(np.sign(mean) != np.sign(median) and median != 0),
    }


def histogram(trades: pd.DataFrame, bins: int = 20) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["lower", "upper", "count"])
    counts, edges = np.histogram(trades["net_pnl"].to_numpy(dtype=float), bins=bins)
    return pd.DataFrame({"lower": edges[:-1], "upper": edges[1:], "count": counts})


def cost_breakdown(trades: pd.DataFrame) -> dict[str, Any]:
    """Costs broken out, before and after. In aggregate nothing looks wrong;
    broken out, it is clear what ate the money."""
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
        # Gross positive but net negative: the edge does not cover the costs.
        "eaten_by_costs": gross > 0 > net,
    }


def data_provenance(trades: pd.DataFrame) -> dict[str, Any]:
    """Provenance disclosure. Conclusions from synthetic data do not generalise
    and must be flagged prominently."""
    if trades.empty:
        return {"synthetic_share": 0.0, "all_synthetic": False}
    share = float((trades["source"] == QuoteSource.SYNTHETIC.value).mean())
    return {
        "synthetic_share": share,
        "all_synthetic": share == 1.0,
        "mixed": 0.0 < share < 1.0,
    }


def passive_expiry_check(trades: pd.DataFrame) -> dict[str, Any]:
    """A passive expiry, in the money especially, means the exit rules failed
    at least once."""
    if trades.empty:
        return {"itm": 0, "otm": 0}
    counts = trades["exit_reason"].value_counts()
    itm = int(counts.get(ExitReason.EXPIRY_ITM.value, 0))
    otm = int(counts.get(ExitReason.EXPIRY_OTM.value, 0))
    return {"itm": itm, "otm": otm, "rule_failed": itm > 0}


def environment() -> dict[str, str]:
    """Versions of the numerical dependencies. A version change moves the last
    floating-point digits and breaks reproducibility."""
    import numpy, pandas, scipy

    return {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "scipy": scipy.__version__,
    }
