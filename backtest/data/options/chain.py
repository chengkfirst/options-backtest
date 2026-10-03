"""The option-chain interface and the long-form layout.

The column set is defined after **what commercial vendors generally supply**, not
trimmed to what is available today. A field one implementation cannot fill (the
synthetic chain has no volume and no open interest) is marked missing; it MUST
NOT be dropped from the interface — otherwise wiring up paid data later turns up
mismatched columns, the interface has to be redone, and the extension point was
reserved for nothing.

There are two ways in, matching the two things a backtest needs:

- :meth:`ChainSource.snapshot` — every contract on one day, for selection.
- :meth:`ChainSource.series` — **one contract's** daily quotes over a span, for
  finding when a take-profit or stop was first touched. This is the trade-table
  engine's core operation: one slice instead of a day-by-day loop.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Protocol

import pandas as pd

from backtest.core.errors import DataQualityError
from backtest.core.types import CHAIN_COLUMNS, ContractId, Right

#: Marks a field the synthetic chain cannot supply. Downstream code skips the
#: affected test rather than reading it as 0.
UNAVAILABLE = float("nan")


class ChainSource(Protocol):
    """One way in to any option chain. Real sources and the synthetic chain
    implement the same protocol."""

    #: Whether the availability dates were assumed rather than supplied.
    #:
    #: Part of the protocol, not an optional extra. The document's first data rule is that
    #: nothing enters a backtest without an availability date; a source that supplies none
    #: has that date **assumed** for it, and a reader has to be able to tell which kind of
    #: run they are looking at. A source that omitted this would report neither, which
    #: reads as "no assumption was made".
    availability_assumed: bool

    def snapshot(self, day: date, *, known_by: date | None = None) -> pd.DataFrame:
        """Every tradable contract on one day, in long form, with the columns
        :data:`CHAIN_COLUMNS`."""
        ...

    def series(self, contract: ContractId, days: Iterable[date]) -> pd.DataFrame:
        """One contract's quotes on the given days; days without a quote do not
        appear in the result."""
        ...


def empty_chain() -> pd.DataFrame:
    return pd.DataFrame(columns=list(CHAIN_COLUMNS))


def validate_chain(frame: pd.DataFrame, *, asof: date) -> pd.DataFrame:
    """Quality checks. Bad rows are dropped or flagged; none may enter the
    backtest silently.

    Returns the frame with the bad rows removed; the reason each was dropped goes
    into ``quality_flags`` so it can be traced afterwards.
    """
    missing = set(CHAIN_COLUMNS) - set(frame.columns)
    if missing:
        raise DataQualityError(f"the chain is missing columns: {sorted(missing)}")
    if frame.empty:
        return frame

    flags = frame["quality_flags"].fillna("").astype(str)
    bad_price = (frame["bid"] < 0) | (frame["ask"] <= 0)
    inverted = frame["ask"] < frame["bid"]
    expired = pd.to_datetime(frame["expiry"]).dt.date < asof

    flags = flags.mask(bad_price, flags + "|negative_price")
    flags = flags.mask(inverted, flags + "|ask_below_bid")
    flags = flags.mask(expired, flags + "|already_expired")
    frame = frame.assign(quality_flags=flags.str.strip("|"))

    dropped = bad_price | inverted | expired
    if frame.duplicated(subset=["asof", "symbol", "expiry", "strike", "right"]).any():
        raise DataQualityError(f"the chain for {asof} has duplicate primary keys")
    return frame.loc[~dropped].reset_index(drop=True)


def relative_spread(bid: pd.Series | float, ask: pd.Series | float):
    """Relative spread = (ask - bid) / mid. NaN when the quote is unusable."""
    mid = (bid + ask) / 2.0
    return (ask - bid) / mid


def delta_after_move(
    spot: float, strike: float, dte_days: float, iv: float, rate: float,
    right: Right, move: float, days_forward: int = 10,
) -> float:
    """This contract's delta after the underlying moves by ``move`` and
    ``days_forward`` days pass.

    **The chain has no such column; it has to be repriced** — "what if it rises
    3%" is not an observation any vendor supplies. Hence its place in the data
    layer: model-derived chain quantities are produced and flagged here, and the
    layers above consume them without caring whether a number was observed or
    derived.

    Assumes IV is unchanged. In reality IV usually falls as the underlying
    rebounds, so **this number is optimistic** — the direction of the bias is
    known, which is enough for the caller.
    """
    from backtest.data.options.pricing import greeks

    later = max(dte_days - days_forward, 1) / 365.0
    moved = spot * (1.0 + move) if right is Right.CALL else spot * (1.0 - move)
    g = greeks(moved, strike, later, rate, iv, 0.0, right)
    return abs(float(g["delta"]))
