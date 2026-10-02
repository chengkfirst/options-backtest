"""Regime classification and stratified analysis.

**Rule-based only, never statistical.** With three or four complete cycles in ten
years, the "states" an HMM or a Bayesian method fits are hard to distinguish from
noise — and they have no names, so there is no way to explain to yourself why a
given day counts as high volatility. A rule-based regime is explainable,
reviewable, and has its parameters in plain sight.

The dimensions come from cross-market data: volatility level, volatility term
structure, trend, credit. Each is bucketed independently and their combination is
the regime. **When a dimension is unavailable the whole rule fails and says why**,
rather than making do with the rest — "high volatility" short one dimension is not
the same thing as the complete version.

Stratified analysis covers three views: the return distribution, execution
quality, and the drawdown context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Dimension:
    """One classification dimension."""

    name: str
    source: str                       # which column of the panel to read
    thresholds: tuple[float, ...]     # the bucket boundaries
    labels: tuple[str, ...]           # the name of each bucket
    rationale: str                    # why it is cut this way

    def __post_init__(self) -> None:
        if len(self.labels) != len(self.thresholds) + 1:
            raise ValueError(
                f"{self.name}: {len(self.thresholds)} boundaries need "
                f"{len(self.thresholds) + 1} labels, got {len(self.labels)}"
            )

    def classify(self, series: pd.Series) -> pd.Series:
        bins = [-np.inf, *self.thresholds, np.inf]
        return pd.cut(series, bins=bins, labels=list(self.labels), right=False)


#: The default dimensions. The thresholds are **parameters meant to be changed**,
#: not truths — they are written here precisely so they can be edited.
DEFAULT_DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        name="volatility level", source="vxn", thresholds=(0.18, 0.28),
        labels=("low vol", "normal", "high vol"),
        rationale="VXN's long-run median is about 20%. 18 and 28 are roughly the "
                  "terciles; they were not fitted.",
    ),
    Dimension(
        name="volatility term structure", source="term_structure", thresholds=(1.0,),
        labels=("inverted", "normal"),
        rationale="VIX3M/VIX below 1 means the front is dearer, which usually happens "
                  "in a panic. 1.0 is a structural boundary, not a tuned one.",
    ),
    Dimension(
        name="trend", source="trend_200", thresholds=(0.0,),
        labels=("below the 200-day", "above the 200-day"),
        rationale="The plainest possible trend split. Zero rather than some fitted "
                  "deviation, to avoid overfitting.",
    ),
)


@dataclass(frozen=True, slots=True)
class RegimeSet:
    """The regime label for each day."""

    frame: pd.DataFrame                 # one column per dimension, plus combined
    dimensions: tuple[Dimension, ...]
    unavailable: tuple[str, ...]        # dimensions not computed for want of data

    def on(self, day: date) -> dict[str, str]:
        ts = pd.Timestamp(day)
        if ts not in self.frame.index:
            return {d.name: "unknown" for d in self.dimensions}
        row = self.frame.loc[ts]
        return {k: ("unknown" if pd.isna(v) else str(v)) for k, v in row.items()}

    def describe(self) -> pd.DataFrame:
        rows = [{"dimension": d.name, "source": d.source,
                 "buckets": " / ".join(d.labels),
                 "boundaries": " , ".join(f"{t:g}" for t in d.thresholds),
                 "rationale": d.rationale,
                 "status": "not computed, data absent" if d.name in self.unavailable
                           else "available"}
                for d in self.dimensions]
        return pd.DataFrame(rows).set_index("dimension")

    def coverage(self) -> pd.Series:
        """How many days fall in each regime. A regime with very few days cannot
        support a conclusion that generalises."""
        if "combined" not in self.frame:
            return pd.Series(dtype=int)
        return self.frame["combined"].value_counts()


def classify(
    panel: pd.DataFrame, bars=None, dimensions: tuple[Dimension, ...] = DEFAULT_DIMENSIONS
) -> RegimeSet:
    """Label each day's regime by rule.

    ``bars`` supplies the underlying's bars, for the trend dimension (the adjusted
    close's deviation from its 200-day average).
    """
    work = panel.copy()
    if bars is not None:
        close = bars.adjusted("close")
        work = work.join(
            (close / close.rolling(200).mean() - 1.0).rename("trend_200"), how="outer"
        ).sort_index()
        work = work.ffill()

    out = pd.DataFrame(index=work.index)
    unavailable: list[str] = []
    for dim in dimensions:
        if dim.source not in work.columns or work[dim.source].notna().sum() == 0:
            unavailable.append(dim.name)
            continue
        out[dim.name] = dim.classify(work[dim.source])

    if len(out.columns):
        # Any dimension absent makes the whole thing "unknown" — making do with
        # the rest would quietly change what "high vol" means.
        combined = out.astype(str).agg(" · ".join, axis=1)
        out["combined"] = combined.where(out.notna().all(axis=1), "unknown")
    return RegimeSet(frame=out, dimensions=dimensions, unavailable=tuple(unavailable))


def stratify(
    trades: pd.DataFrame, regimes: RegimeSet, *, by: str = "combined", min_trades: int = 15
) -> pd.DataFrame:
    """Stratify trades by the regime on their entry day.

    Three views together: the **return distribution**, **execution quality**, and
    the **drawdown context**. A regime with too small a sample is still shown, but
    labelled — hiding it is worse than labelling it.
    """
    if trades.empty:
        return pd.DataFrame()
    labels = []
    for d in pd.to_datetime(trades["entry_date"]):
        state = regimes.on(d.date())
        labels.append(state.get(by) if by != "combined"
                      else state.get("combined", "unknown"))
    work = trades.assign(_regime=labels)

    rows = []
    for name, part in work.groupby("_regime", observed=True):
        pnl = part["net_pnl"]
        rows.append({
            "regime": name,
            "trades": len(part),
            "win rate": float((pnl > 0).mean()),
            "mean per trade": float(pnl.mean()),
            "median": float(pnl.median()),
            "total": float(pnl.sum()),
            "worst trade": float(pnl.min()),
            # Execution quality: quote gaps and deferred exits, both of which get
            # worse in extreme conditions.
            "quote gaps": float(part["quote_gaps"].mean()) if "quote_gaps" in part else np.nan,
            "deferred exits": float(part["deferred_days"].mean()) if "deferred_days" in part else np.nan,
            "sample": "adequate" if len(part) >= min_trades
                      else f"too small (<{min_trades})",
        })
    return pd.DataFrame(rows).set_index("regime").sort_values("trades", ascending=False)


def drawdown_context(equity: pd.DataFrame, regimes: RegimeSet, top: int = 5) -> pd.DataFrame:
    """What regime each of the deepest drawdowns happened in."""
    curve = equity["equity_mid"]
    dd = curve / curve.cummax() - 1
    worst = dd.nsmallest(top * 20)          # take a batch, then dedupe into episodes
    picked, seen = [], set()
    for day, value in worst.items():
        month = (day.year, day.month)
        if month in seen:
            continue
        seen.add(month)
        state = regimes.on(day.date() if hasattr(day, "date") else day)
        picked.append({"date": day, "drawdown": value, **state})
        if len(picked) >= top:
            break
    return pd.DataFrame(picked).set_index("date")
