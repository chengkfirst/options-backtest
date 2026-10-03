"""Registering signals and reading them day by day.

Every signal is computed **once over the whole series** at the start of the run
and then sliced per day, rather than recomputed incrementally. This is a conscious
trade of safety for speed: recomputing daily is two orders of magnitude slower,
and the look-ahead risk that whole-series computation introduces happens to have
a reliable automatic check — the truncate-and-recompute property test. Buy the
speed with a test, not with the architecture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from backtest.data.underlying import UnderlyingBars
from backtest.core.errors import StartupError
from backtest.core.maybe import Maybe
from backtest.signals import indicators

@dataclass(frozen=True, slots=True)
class SignalMeta:
    """A signal describing itself: what it measures, over what window, in what
    unit, and what to watch out for when reading it.

    Having this metadata or not is not a matter of a prettier interface. Without
    it the interface can only list names alphabetically, and the user has to guess
    what convention ``rv_20`` is annualised on, or whether ``mom_20`` is a ratio
    or a difference. Guessing wrong raises nothing; it just produces a conclusion
    that does not hold together.
    """

    family: str        # grouping: price / trend / momentum / volatility
    measures: str      # what it measures
    unit: str          # the unit, or the range of values
    window: int | None # look-back window in trading days; None when there is none
    caveat: str = ""   # what is easy to get wrong when reading it


#: Each signal describing itself. **A new signal must be registered here too**,
#: or ``describe()`` raises outright — a signal nobody can state the meaning of is
#: worse than not having the signal.
SIGNAL_META: dict[str, SignalMeta] = {
    "close":       SignalMeta("price", "adjusted close", "USD", None,
                              "adjusted, not the price that traded that day; use "
                              "traded prices to line up with option contracts"),
    "sma_20":      SignalMeta("trend", "20-day simple moving average", "USD", 20),
    "sma_50":      SignalMeta("trend", "50-day simple moving average", "USD", 50),
    "sma_200":     SignalMeta("trend", "200-day simple moving average", "USD", 200,
                              "unavailable for the first 200 sessions of the range, "
                              "which is not the same as 0"),
    "ema_20":      SignalMeta("trend", "20-day exponential moving average", "USD", 20),
    "rsi_14":      SignalMeta("momentum", "relative strength, overbought/oversold",
                              "0–100", 14,
                              "70/30 are conventional thresholds, not a statistical "
                              "result"),
    "mom_20":      SignalMeta("momentum", "20-day cumulative return",
                              "ratio (0.05 = up 5%)", 20,
                              "a ratio, not a difference"),
    "atr_14":      SignalMeta("volatility", "14-day average true range", "USD", 14,
                              "an absolute amount; divide by price before comparing "
                              "across underlyings"),
    "rv_20":       SignalMeta("volatility", "20-day realised volatility",
                              "annualised ratio (0.20 = 20%)", 20,
                              "annualised on **trading days / 252**; subtracting it "
                              "from a calendar-day IV is not interpretable"),
    "bb_width_20": SignalMeta("volatility", "Bollinger band width, measuring "
                              "volatility contraction and expansion", "ratio", 20),
}


#: **IV level signals.** These take the volatility-proxy series (QQQ -> VXN,
#: SPY -> VIX) rather than adjusted prices, so they are a separate group with
#: their own registration.
#:
#: Why the group matters: an option is a position in volatility, not only in
#: direction. **The same directional judgement calls for completely different
#: action at high and at low IV** — buying a call at IV rank 90 is buying time at
#: a steep price. Selecting on delta and DTE alone is blind to this.
#:
#: The proxy is **real market data**, so these signals hold even when the option
#: chain is synthetic.
VOL_SIGNALS: dict[str, Callable[[pd.Series, UnderlyingBars], pd.Series]] = {
    "iv": lambda v, b: v,
    "iv_rank_252": lambda v, b: indicators.rank_in_window(v, 252),
    "iv_pct_252": lambda v, b: indicators.percentile_in_window(v, 252),
    "iv_rank_63": lambda v, b: indicators.rank_in_window(v, 63),
    # The volatility risk premium: implied minus realised. Positive means the
    # option is dearer than the underlying's actual movement — the seller's living
    # and the buyer's cost.
    "iv_rv_spread": lambda v, b: v - indicators.realized_vol(b.adjusted("close"), 20),
}


#: The signals registered by default. Each is ``bars -> Series`` and takes only
#: adjusted prices.
DEFAULT_SIGNALS: dict[str, Callable[[UnderlyingBars], pd.Series]] = {
    "close": lambda b: b.adjusted("close"),
    "sma_20": lambda b: indicators.sma(b.adjusted("close"), 20),
    "sma_50": lambda b: indicators.sma(b.adjusted("close"), 50),
    "sma_200": lambda b: indicators.sma(b.adjusted("close"), 200),
    "ema_20": lambda b: indicators.ema(b.adjusted("close"), 20),
    "rsi_14": lambda b: indicators.rsi(b.adjusted("close"), 14),
    "atr_14": lambda b: indicators.atr(
        b.adjusted("high"), b.adjusted("low"), b.adjusted("close"), 14
    ),
    "mom_20": lambda b: indicators.momentum(b.adjusted("close"), 20),
    "rv_20": lambda b: indicators.realized_vol(b.adjusted("close"), 20),
    "bb_width_20": lambda b: indicators.bollinger(b.adjusted("close"), 20)["width"],
}


def describe_signals(names: "tuple[str, ...] | None" = None) -> pd.DataFrame:
    """Every signal's self-description, grouped by family. The interface and the
    notebook read the same table.

    A custom signal with no registered metadata is labelled "custom (no
    description registered)" rather than left blank — blank looks like the system
    failed to find something, whereas the label says the user did not write one.
    """
    if names is not None and not names:
        raise StartupError(
            "no signal names were given to describe. An empty tuple used to be replaced "
            "by every default signal, so asking for none returned all of them -- pass "
            "the names, or omit the argument for the defaults"
        )
    names = names if names is not None else tuple(DEFAULT_SIGNALS)
    rows = []
    for name in names:
        m = SIGNAL_META.get(name)
        rows.append({
            "signal": name,
            "family": m.family if m else "custom",
            "measures": m.measures if m else "custom (no description registered)",
            "unit": m.unit if m else "—",
            "window": m.window if m else None,
            "caveat": m.caveat if m else "",
        })
    order = {"price": 0, "trend": 1, "momentum": 2, "volatility": 3, "custom": 9}
    frame = pd.DataFrame(rows)
    return (frame.assign(_o=frame["family"].map(lambda c: order.get(c, 9)))
                 .sort_values(["_o", "window", "signal"], na_position="first")
                 .drop(columns="_o").reset_index(drop=True))


@dataclass(frozen=True, slots=True)
class SignalView:
    """Every signal's value on one day. Absence is explicit and never reads as 0."""

    day: date
    _values: Mapping[str, float]

    def __getitem__(self, name: str) -> Maybe[float]:
        if name not in self._values:
            raise KeyError(f"unregistered signal: {name!r}")
        value = self._values[name]
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return Maybe.missing(f"{name} has no value on {self.day} "
                                 f"(warm-up period, or the data is absent)")
        return Maybe.some(float(value))

    def get(self, name: str) -> Maybe[float]:
        return self[name]

    def names(self) -> tuple[str, ...]:
        return tuple(self._values)


class SignalSet:
    """Computed once, read per day."""

    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self._by_day = {ts.date(): row for ts, row in frame.iterrows()}

    @classmethod
    def from_bars(
        cls,
        bars: UnderlyingBars,
        extra: Mapping[str, Callable[[UnderlyingBars], pd.Series]] | None = None,
        vol_proxy: pd.Series | None = None,
    ) -> "SignalSet":
        """Compute every signal.

        The IV-level group only exists when ``vol_proxy`` is supplied. When it is
        unavailable the group is **not registered**, rather than filled with 0 or
        NaN — an iv_rank fixed at 0 makes "only buy at low IV" true every single
        day, and raises nothing.
        """
        definitions = dict(DEFAULT_SIGNALS)
        definitions.update(extra or {})
        columns = {name: fn(bars).astype(float) for name, fn in definitions.items()}

        if vol_proxy is not None and not vol_proxy.empty:
            aligned = vol_proxy.reindex(bars.frame.index).ffill()
            for name, fn in VOL_SIGNALS.items():
                columns[name] = fn(aligned, bars).astype(float)

        frame = pd.DataFrame(columns, index=bars.frame.index)
        return cls(frame)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._frame.columns)

    @property
    def frame(self) -> pd.DataFrame:
        return self._frame

    def require(self, names: "list[str] | tuple[str, ...]") -> None:
        """Validate the subscribed signal names at startup.

        A misspelled name has to surface before the first trading day, not fail
        part-way through the run.
        """
        unknown = [n for n in names if n not in self._frame.columns]
        if unknown:
            raise StartupError(
                f"unregistered signals: {unknown}; available: "
                f"{list(self._frame.columns)}"
            )

    def on(self, day: date) -> SignalView:
        row = self._by_day.get(day)
        if row is None:
            return SignalView(day, {name: float("nan") for name in self._frame.columns})
        return SignalView(day, row.to_dict())
