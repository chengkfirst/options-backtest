"""Cross-market data: Treasuries, the dollar, credit spreads, the volatility
term structure.

These are not extra lines on a chart. They are here for two things:

1. **Regime classification** — the VIX level, the volatility term structure and
   credit spreads together describe what environment this is, and a strategy
   usually performs very differently across environments.
2. **Cross-market confirmation** — a bullish judgement that Treasury yields, the
   dollar and credit spreads all agree with is a different thing from an isolated
   technical signal.

All of it from one source (Yahoo), because a second source is a second point of
failure. Each series' convention and limits are stated beside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtest.data.store import CacheKey, ParquetCache
from backtest.data.ingest import fetch_close_series
from backtest.core.run_log import EventKind, EventLog

#: The cross-market series. ``scale`` says how to get from the raw close to the
#: quantity actually used.
SERIES: dict[str, dict[str, str]] = {
    "vix": {
        "symbol": "^VIX", "scale": "0.01",
        "meaning": "30-day implied volatility of the S&P 500",
        "caveat": "this is the S&P's, not the Nasdaq's; for a QQQ strategy read VXN too",
    },
    "vix3m": {
        "symbol": "^VIX3M", "scale": "0.01",
        "meaning": "3-month implied volatility of the S&P 500",
        "caveat": "its ratio to VIX is the term structure; no data before 2011",
    },
    "vxn": {
        "symbol": "^VXN", "scale": "0.01",
        "meaning": "30-day implied volatility of the Nasdaq 100",
        "caveat": "this is the one a QQQ strategy should go by",
    },
    "ust10y": {
        "symbol": "^TNX", "scale": "0.01",
        "meaning": "10-year Treasury yield",
        "caveat": "Yahoo gives a percentage; already divided by 100",
    },
    "ust3m": {
        "symbol": "^IRX", "scale": "0.01",
        "meaning": "3-month Treasury yield",
        "caveat": "its difference from the 10-year is the curve; negative is widely "
                  "read as a recession signal",
    },
    "dollar": {
        "symbol": "DX-Y.NYB", "scale": "1",
        "meaning": "the dollar index",
        "caveat": "may have gaps in the earlier history",
    },
    "hyg": {
        "symbol": "HYG", "scale": "1",
        "meaning": "high-yield bond ETF",
        "caveat": "its ratio to LQD is a proxy for the credit spread, not the spread",
    },
    "lqd": {
        "symbol": "LQD", "scale": "1",
        "meaning": "investment-grade bond ETF",
        "caveat": "as above",
    },
}


@dataclass(frozen=True, slots=True)
class CrossMarket:
    """The cross-market panel, and which series were available."""

    frame: pd.DataFrame
    unavailable: dict[str, str]        # name -> why it could not be fetched

    def on(self, day: date) -> pd.Series | None:
        ts = pd.Timestamp(day)
        prior = self.frame.loc[:ts]
        return None if prior.empty else prior.iloc[-1]

    def describe(self) -> pd.DataFrame:
        """Each series' meaning, limits and availability. Read this table before
        changing any convention."""
        rows = []
        for name, meta in SERIES.items():
            rows.append({
                "series": name, "ticker": meta["symbol"], "means": meta["meaning"],
                "limits": meta["caveat"],
                "status": "absent: " + self.unavailable[name] if name in self.unavailable
                else f"{int(self.frame[name].notna().sum())} observations",
            })
        for derived, why in (
            ("term_structure", "VIX3M / VIX. Below 1 means the front is dearer, "
                               "which usually happens in a panic"),
            ("credit_proxy", "HYG / LQD. Falling means credit spreads are widening"),
            ("yield_curve", "10-year minus 3-month. Negative is inverted"),
        ):
            rows.append({"series": derived, "ticker": "(derived)", "means": why,
                         "limits": "a proxy, not the actual spread",
                         "status": f"{int(self.frame[derived].notna().sum())} observations"
                         if derived in self.frame else "cannot be computed"})
        return pd.DataFrame(rows).set_index("series")


def load_cross_market(
    start: date, end: date, *, cache: ParquetCache | None = None,
    events: EventLog | None = None, refresh: bool = False,
) -> CrossMarket:
    """Fetch the cross-market series.

    When one cannot be fetched this **records why and carries on** rather than
    aborting — cross-market data is supporting material, and missing one or two
    should not stop a backtest. But what is missing has to be stated, or regime
    classification quietly drops a dimension without the user knowing.
    """
    cache = cache if cache is not None else ParquetCache()
    events = events if events is not None else EventLog()
    # The warm-up has to match MarketData's (400 days). Fetch less and long-window
    # dimensions like the 200-day average cannot be computed at the start of the
    # range, leaving a long stretch of "unknown" regime — which is not missing
    # data, it is us not having fetched enough.
    fetch_start = start - timedelta(days=400)
    columns: dict[str, pd.Series] = {}
    unavailable: dict[str, str] = {}

    for name, meta in SERIES.items():
        key = CacheKey("cross", meta["symbol"], {"start": fetch_start, "end": end, "source": "yfinance"})
        try:
            frame = cache.get_or_load(
                key,
                lambda s=meta["symbol"]: fetch_close_series(s, fetch_start, end).to_frame("close"),
                refresh=refresh,
            )
            series = frame["close"].astype(float) * float(meta["scale"])
            series.index = pd.to_datetime(series.index)
            columns[name] = series
        except Exception as exc:  # noqa: BLE001
            unavailable[name] = f"{type(exc).__name__}: {exc}"
            events.record(EventKind.RANGE_TRUNCATED, start,
                          f"cross-market series {name} ({meta['symbol']}) "
                          f"unavailable: {exc}")

    panel = pd.DataFrame(columns).sort_index()
    if "vix3m" in panel and "vix" in panel:
        panel["term_structure"] = panel["vix3m"] / panel["vix"]
    if "hyg" in panel and "lqd" in panel:
        panel["credit_proxy"] = panel["hyg"] / panel["lqd"]
    if "ust10y" in panel and "ust3m" in panel:
        panel["yield_curve"] = panel["ust10y"] - panel["ust3m"]
    return CrossMarket(frame=panel, unavailable=unavailable)
