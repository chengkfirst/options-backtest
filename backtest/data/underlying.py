"""Underlying daily bars: **two sets of prices**.

- **Adjusted** (``adjusted``): corrected for splits and dividends, for signals
  and returns. Only these keep ex-dividend and split days free of false gaps.
- **Traded** (``traded``): the prices that actually traded, for lining up with
  option strikes, for moneyness, and as the spot input to a pricing model.

The two must be kept apart. An option's strike is set against the **price that
traded at the time**, while the adjusted price is rewritten by every dividend
that follows — judging a historical contract's moneyness off adjusted prices
produces an error that grows monotonically with cumulative dividends, raises
nothing, and simply shifts every historical selection. QQQ's dividends compound
past 5% over ten years, which is not a negligible amount.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from backtest.errors import DataQualityError
from backtest.maybe import Maybe

RAW_FIELDS = ("open", "high", "low", "close")
ADJ_PREFIX = "adj_"


@dataclass(frozen=True, slots=True)
class UnderlyingBars:
    """One underlying's daily bars over a range, both price sets together."""

    symbol: str
    frame: pd.DataFrame

    # ---- access ----
    def traded(self, field: str = "close") -> pd.Series:
        """Traded prices. For everything option-related."""
        self._check_field(field)
        return self.frame[field]

    def adjusted(self, field: str = "close") -> pd.Series:
        """Adjusted prices. For signals and returns."""
        self._check_field(field)
        return self.frame[ADJ_PREFIX + field]

    @property
    def volume(self) -> pd.Series:
        return self.frame["volume"]

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(d.date() for d in self.frame.index)

    def traded_close_on(self, day: date) -> Maybe[float]:
        """The traded close on one day. A non-trading day returns an explicit
        absence, not ``None``."""
        ts = pd.Timestamp(day)
        if ts not in self.frame.index:
            return Maybe.missing(f"{day} is not a trading day for {self.symbol}")
        return Maybe.some(float(self.frame.at[ts, "close"]))

    def slice(self, start: date, end: date) -> "UnderlyingBars":
        mask = (self.frame.index >= pd.Timestamp(start)) & (
            self.frame.index <= pd.Timestamp(end)
        )
        return UnderlyingBars(self.symbol, self.frame.loc[mask])

    @staticmethod
    def _check_field(field: str) -> None:
        if field not in RAW_FIELDS and field != "volume":
            raise KeyError(f"unknown field {field!r}; available: "
                           f"{RAW_FIELDS + ('volume',)}")

    def __len__(self) -> int:
        return len(self.frame)


def build_bars(symbol: str, raw: pd.DataFrame) -> UnderlyingBars:
    """Build both price sets from Yahoo's raw columns.

    The adjustment factor is ``Adj Close / Close``, applied to open/high/low as
    well — adjusting only the close leaves the intraday range and the close in
    different price systems, which distorts indicators like ATR.
    """
    frame = pd.DataFrame(index=pd.to_datetime(raw.index))
    frame.index.name = "date"
    for src, dst in zip(("Open", "High", "Low", "Close"), RAW_FIELDS):
        frame[dst] = raw[src].astype(float)
    frame["volume"] = raw["Volume"].astype("int64")

    factor = (raw["Adj Close"].astype(float) / raw["Close"].astype(float)).rename("factor")
    for f in RAW_FIELDS:
        frame[ADJ_PREFIX + f] = frame[f] * factor

    validate_bars(symbol, frame)
    return UnderlyingBars(symbol, frame)


def validate_bars(symbol: str, frame: pd.DataFrame) -> None:
    """Quality checks. Bad data must be rejected, never enter the backtest."""
    if frame.empty:
        raise DataQualityError(f"{symbol} has no bars")
    if frame.index.has_duplicates:
        dupes = frame.index[frame.index.duplicated()].unique()
        raise DataQualityError(f"{symbol} has duplicate dates: {list(dupes[:5])}")
    if not frame.index.is_monotonic_increasing:
        raise DataQualityError(f"{symbol} bars are not in ascending date order")

    price_cols = list(RAW_FIELDS) + [ADJ_PREFIX + f for f in RAW_FIELDS]
    non_positive = (frame[price_cols] <= 0).any(axis=1)
    if non_positive.any():
        raise DataQualityError(
            f"{symbol} has non-positive prices, first at "
            f"{frame.index[non_positive][0].date()}"
        )
    inverted = frame["high"] < frame["low"]
    if inverted.any():
        raise DataQualityError(
            f"{symbol} has a high below its low, first at "
            f"{frame.index[inverted][0].date()}"
        )
    if frame[price_cols].isna().any().any():
        raise DataQualityError(f"{symbol} has missing prices")
