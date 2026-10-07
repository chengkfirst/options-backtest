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

from backtest.core.config import TRADING_DAYS_PER_YEAR

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
    """Every cost, including the one that is not a fee.

    ## The spread belongs here, and was not here

    **For an option buyer the spread is the largest cost by a wide margin** — measured on
    this project's own fixture it is several times the commission — and it is not a fee.
    It is paid inside the fill: a buy at the ask and a sell at the bid, which is correct
    modelling and already in `net_pnl`. But a section headed "costs" that lists
    commission, regulatory and slippage and totals them reads as *the* cost of trading,
    and with `slippage_per_contract` at its default of 0 the row said the cost of trading
    was negligible.

    So the spread is reported here beside the fees, and `total` is the sum of both. The
    fee-only figure stays available as `fees` for anyone who needs the two apart — the
    point is that the default reading is the true one.

    ## Why the denominator changed

    `total / abs(gross)` is meaningless when gross is negative: it is "fees as a share of
    the size of the loss", and it was printed as "% of gross". Worse for the rule that
    reads it, the spread could never be in the numerator **or** the denominator, since
    gross P&L is computed from fill prices that already have the spread in them.

    The primary denominator is now the **premium traded** — what was paid to open the
    positions. It is defined whatever the sign of the result, it is the figure an option
    buyer thinks in ("the spread cost me 3% of what I paid"), and the spread is not
    inside it.

    `cost_pct_of_gross_at_mid` keeps the gross comparison honest for a profitable run by
    rebuilding gross at the **mid** — so the spread is in the numerator and not in the
    denominator. It is absent, with a reason, when gross at mid is not positive: there is
    no edge for a cost to be a share of.
    """
    if trades.empty:
        return {"total": 0.0}
    gross = float(trades["gross_pnl"].sum())
    fees = {
        "commission": float(trades["fee_commission"].sum()),
        "regulatory": float(trades["fee_regulatory"].sum()),
        "slippage": float(trades["fee_slippage"].sum()),
    }
    fee_total = sum(fees.values())
    net = float(trades["net_pnl"].sum())

    spread = spread_paid(trades)
    spread_dollars = float(spread.get("spread_dollars", 0.0))
    # A trade table recorded before the quote was kept beside the fill cannot report the
    # spread. Said, rather than reported as zero -- a 0 here is the exact misreading this
    # function exists to remove.
    spread_known = spread.get("count", 0) > 0
    total = fee_total + (spread_dollars if spread_known else 0.0)

    premium = float((trades["entry_price"] * trades["quantity"].abs()
                     * trades["multiplier"]).sum())
    out: dict[str, Any] = {
        **fees,
        "fees": fee_total,
        "spread": spread_dollars if spread_known else None,
        "spread_note": ("" if spread_known else
                        "this run has no quote recorded beside its fills, so the spread "
                        "paid cannot be measured. It is not zero"),
        "total": total,
        "premium_traded": premium,
        "cost_pct_of_premium": (total / premium) if premium else float("inf"),
        "fees_pct_of_premium": (fee_total / premium) if premium else float("inf"),
        "gross_pnl": gross,
        "net_pnl": net,
        # Gross positive but net negative: the edge does not cover the costs.
        "eaten_by_costs": gross > 0 > net,
    }

    # Gross with the spread added back is gross as a frictionless fill would have made
    # it, which is the only gross a cost can be a share of without appearing on both
    # sides of the division.
    gross_at_mid = gross + (spread_dollars if spread_known else 0.0)
    if gross_at_mid > 0:
        out["gross_pnl_at_mid"] = gross_at_mid
        out["cost_pct_of_gross_at_mid"] = total / gross_at_mid
    else:
        out["gross_pnl_at_mid"] = gross_at_mid
        out["cost_pct_of_gross_at_mid"] = None
        out["gross_note"] = (
            "gross P&L before costs is not positive, so there is no edge for a cost to "
            "be a share of. Read cost_pct_of_premium instead -- a ratio to a loss is "
            "not a cost ratio"
        )
    return out


def spread_paid(trades: pd.DataFrame) -> dict[str, Any]:
    """The spread paid, as a share of the premium it was paid on.

    **The document's most load-bearing cost metric for a long-options book**, and the one
    the trade table could not answer until the quote was recorded beside the fill: a
    contract bought 0.05 above a 4.90 mid and sold 0.18 below a 17.82 one paid 1.0% and
    1.0% of those premiums, round trip, and nothing in a finished run said so.

    Measured as the distance from the **mid**, which is the counterfactual that matters:
    the mid is the price a frictionless fill would have had, so ``fill - mid`` on a buy
    and ``mid - fill`` on a sell is exactly what the spread assumption cost. That makes
    this a measurement of λ's effect per trade rather than a restatement of λ.

    Expressed as a share rather than in dollars because that is the form that compares:
    $4 of spread is trivial on a $200 premium and ruinous on a $20 one, and a book of
    cheap options is where a long-options strategy is most tempted to go.

    ``fee_slippage`` is **not** part of this. That column is the explicit per-contract
    slippage fee, a separate assumption; adding the two would count the spread twice for
    anyone who set both.
    """
    if trades.empty:
        return {"count": 0, "note": "the run produced no trades"}
    for column in ("entry_mid", "exit_mid"):
        if column not in trades.columns:
            return {"count": 0,
                    "note": f"this trade table has no {column}, so the fill cannot be "
                            f"compared with the quote it came from. Runs recorded before "
                            f"the quote was kept beside the fill cannot answer this"}

    quantity = trades["quantity"].abs().astype(float)
    multiplier = trades["multiplier"].astype(float)
    premium = trades["entry_price"].astype(float) * quantity * multiplier

    # Signed so that **adverse is positive** on both legs: a buy fills above the mid, a
    # sell below it. Long trades only -- `quantity` is positive for a long, and a short
    # entry would reverse both signs. Stated rather than handled, because no short option
    # position can be opened here.
    entry_cost = (trades["entry_price"].astype(float)
                  - trades["entry_mid"].astype(float)) * quantity * multiplier
    exit_cost = (trades["exit_mid"].astype(float)
                 - trades["exit_price"].astype(float)) * quantity * multiplier

    usable = (premium > 0) & entry_cost.notna() & exit_cost.notna()
    if not usable.any():
        return {"count": 0,
                "excluded": int(len(trades)),
                "note": "no trade has both a positive entry premium and a quote on both "
                        "legs, so there is nothing to express a spread as a share of"}

    round_trip = (entry_cost + exit_cost)[usable]
    share = round_trip / premium[usable]
    return {
        "count": int(usable.sum()),
        "excluded": int((~usable).sum()),
        # The plain mean answers "what did a typical trade pay"; the premium-weighted
        # ratio answers "what did the book pay". They differ whenever size varies, and
        # both are wanted, so neither stands in for the other.
        "spread_pct_of_premium_mean": float(share.mean()),
        "spread_pct_of_premium_weighted": float(round_trip.sum() / premium[usable].sum()),
        "spread_pct_of_premium_max": float(share.max()),
        "spread_dollars": float(round_trip.sum()),
        "entry_pct_of_premium": float(
            (entry_cost[usable] / premium[usable]).mean()),
        "exit_pct_of_premium": float((exit_cost[usable] / premium[usable]).mean()),
        # Gross P&L before the spread was paid. The comparison the metric exists for:
        # a strategy whose edge is smaller than this is losing to its own fills.
        "spread_pct_of_gross_pnl": (
            float(round_trip.sum() / abs(trades["gross_pnl"].astype(float).sum()))
            if trades["gross_pnl"].astype(float).sum() else float("inf")),
    }


def iv_versus_realized(trades: pd.DataFrame, paths: pd.DataFrame) -> dict[str, Any]:
    """IV at entry against the volatility the underlying actually realised while held.

    **The volatility risk premium, measured per trade.** A buyer pays the implied and
    receives the realised, and the long-options literature's central finding is that the
    first exceeds the second on average -- so a strategy that buys options has to beat
    that gap, not merely be right about direction. The document lists it as an Options
    metric and the pre-launch checklist asks for it by name.

    ``paths`` is the daily per-trade frame (``result.raw.paths_frame``), which carries the
    underlying's price on every session each trade was held. The trade table holds only
    the endpoints, and two prices are a return rather than a volatility.

    Two honest limits, both reported rather than smoothed over:

    - **A short holding period makes a noisy estimate.** The standard error of a realised
      vol from *n* returns is about 1/sqrt(2n) of itself -- roughly 22% of the figure over
      ten sessions -- so one trade's gap says very little and the distribution across
      trades is the thing to read. ``median_sessions`` is reported for exactly this.
    - A trade with fewer than three returns in its window is **excluded and counted**,
      never filled in. A realised vol of 0 would read as the most profitable possible
      trade for a buyer, which is the opposite of what an unmeasurable window means.

    **Which IV this is.** On a synthetic chain the entry IV comes from the volatility
    proxy index (VIX for SPY, VXN for QQQ) rather than from a traded option quote, so the
    implied side is a real market series but not this contract's own market. Read
    ``data_provenance`` beside this before taking the gap as a statement about the option
    that was traded.
    """
    min_returns = 3
    if trades.empty:
        return {"count": 0, "note": "the run produced no trades"}
    if paths is None or len(paths) == 0:
        return {"count": 0,
                "note": "no daily path was given, so there is no realised volatility to "
                        "compare the implied against. The trade-table engine does not "
                        "produce one -- use engine='event_driven'"}
    if "entry_iv" not in trades.columns:
        return {"count": 0,
                "note": "this trade table has no entry_iv, so there is no implied "
                        "volatility to compare"}

    by_trade = {int(tid): part for tid, part in paths.groupby("trade_id")}
    rows = []
    excluded = 0
    for _, trade in trades.iterrows():
        iv = float(trade.get("entry_iv", np.nan))
        path = by_trade.get(int(trade["trade_id"]))
        if not np.isfinite(iv) or iv <= 0 or path is None:
            excluded += 1
            continue
        prices = pd.Series(path.sort_values("date")["underlying"].astype(float).to_numpy())
        returns = np.log(prices).diff().dropna()
        returns = returns[np.isfinite(returns)]
        if len(returns) < min_returns:
            excluded += 1
            continue
        realised = float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))
        rows.append({"trade_id": int(trade["trade_id"]), "entry_iv": iv,
                     "realized_vol": realised, "gap": iv - realised,
                     "n_returns": int(len(returns))})

    if not rows:
        return {"count": 0, "excluded": excluded,
                "note": f"no trade had {min_returns} or more usable returns in its "
                        f"holding window with a solved entry IV, so the comparison "
                        f"cannot be made"}

    frame = pd.DataFrame(rows)
    gap = frame["gap"]
    return {
        "count": int(len(frame)),
        "excluded": excluded,
        "mean_entry_iv": float(frame["entry_iv"].mean()),
        "mean_realized_vol": float(frame["realized_vol"].mean()),
        "mean_gap": float(gap.mean()),
        "median_gap": float(gap.median()),
        # The share of trades where the buyer received more volatility than was paid for.
        # A rate rather than a total, because it is the one figure here that a handful of
        # large trades cannot move.
        "share_realized_above_implied": float((gap < 0).mean()),
        "median_sessions": float(frame["n_returns"].median()),
        "per_trade": frame,
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


def turnover(trades: pd.DataFrame, equity: pd.DataFrame) -> dict[str, Any]:
    """Turnover: how much was traded relative to the capital employed.

    One of the document's trade metrics and one of its capacity measures. For a strategy
    holding days to weeks it is the figure that says how hard the costs are working
    against the return -- and `cost_breakdown` answers that directly, so this is a second
    view of something already answered rather than a missing fact. Both are reported
    because they answer it differently: the cost share says what was paid, and turnover
    says how much activity produced it.

    **Notional, not premium.** A long option's premium is a fraction of the exposure it
    carries, so turnover on premium would make a highly leveraged book look quiet. The
    document's capacity question is about the size being pushed through the market, which
    is the contracts times the multiplier times the underlying.

    Annualised on the sessions actually run rather than on a calendar year: a six-month
    backtest annualised as though it were a year would double the figure.
    """
    import numpy as np

    if trades is None or trades.empty or equity is None or equity.empty:
        return {"turnover": float("nan"),
                "note": "no trades or no equity curve, so there is nothing to divide"}
    needed = {"quantity", "entry_price", "multiplier"}
    missing = sorted(needed - set(trades.columns))
    if missing:
        raise KeyError(f"turnover needs {', '.join(missing)}")

    # Each trade is opened and closed, so the notional crosses the market twice.
    notional = (trades["quantity"].abs().astype(float)
                * trades["multiplier"].astype(float)
                * trades["entry_price"].astype(float)).sum() * 2.0
    mean_equity = float(equity["equity_mid"].astype(float).mean())
    sessions = len(equity)
    if mean_equity <= 0 or not sessions:
        return {"turnover": float("nan"),
                "note": "the mean equity is not positive, so a ratio to it is not one"}
    gross = notional / mean_equity
    return {
        "turnover": gross,
        "turnover_annualised": gross * (TRADING_DAYS_PER_YEAR / sessions),
        "notional_traded": notional,
        "mean_equity": mean_equity,
        "sessions": sessions,
        "note": "premium traded, counted on both sides, over the mean equity. "
                "Annualised on the sessions run rather than on a calendar year",
    }
