"""The data view: every read cut at what was known.

## The cut has two halves, and only one of them lived here

**Which day you may ask about** is this object's half: a read for a day past ``as_of``
raises. That was always here.

**Which rows within that day were actually known** is the data layer's half, and it did
not exist -- there was no ``available_at`` column, and this docstring's first line claimed
the cut was made on one. The two dates coincide for a clean daily snapshot, which is why
nothing broke, and they come apart the moment a vendor delivers late, revises a figure or
backfills a gap. The column exists now, the chain sources withhold rows not yet known, and
a source that supplies no availability dates reports that they were **assumed** rather
than letting the assumption pass as data.

## Why an object rather than a convention

Look-ahead is the most fatal bias and the hardest to see by eye. This project has held it
off by **construction** — a signal view is built for the decision day and the chain for
the execution day — which works and leaves nothing enforcing it. A new call site that
reaches for ``market.bars`` directly gets tomorrow's close and nothing says so.

A view makes the rule the interface. Every read goes through an object that knows what
``as_of`` is, and a read past it **raises**. The document's phrasing: "out-of-range access
raises instead of returning silently".

## What "available at" means for each kind of data

Not the same thing, which is why the cut cannot be one date comparison:

- **a completed bar** is available after its session closes, so ``as_of`` includes it
- **the current snapshot** is available at ``as_of`` itself
- **a chain** is the set of contracts quoted at ``as_of``
- **a signal** is computed from bars up to ``as_of`` — the registry already guarantees
  this by the truncate-and-recompute property test, and the view carries the guarantee
  rather than restating it

## What this is not

It is not yet the document's full DataView: there is no ``cross_section``, no ``panel``
and no ``surface``, because this runs one underlying at a time and the synthetic chain has
no surface. Those raise :class:`NotImplementedError` with the reason, rather than being
absent — a missing method reads as "not needed here", and these are needed and not built.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from backtest.core.errors import StartupError
from backtest.core.maybe import Maybe


class LookAheadError(StartupError):
    """A read past ``as_of``.

    A subclass of :class:`StartupError` rather than a plain error: this is a bug in a
    strategy or a component, not a condition of the market, and it must stop the run
    rather than be recorded and carried on from.
    """


@dataclass(frozen=True, slots=True)
class DataView:
    """Everything a component may read, as of one moment.

    Hold one of these and tomorrow is unreachable. Build it with :meth:`at`.
    """

    as_of: date
    _market: Any
    _signals: Any
    _chain: Any = None

    # ---- construction ----
    @classmethod
    def at(cls, day: date, *, market, signals, chain=None) -> "DataView":
        return cls(as_of=day, _market=market, _signals=signals, _chain=chain)

    def rolled_to(self, day: date) -> "DataView":
        """A view at a later moment. Rolling backwards raises.

        Backwards would be a research tool re-reading history, which is legitimate but is
        not what this object is for; allowing it here would make ``as_of`` stop meaning
        "the frontier".
        """
        if day < self.as_of:
            raise LookAheadError(
                f"a view cannot roll backwards, from {self.as_of} to {day}. To look at "
                f"history, build a new view at that date"
            )
        return DataView(as_of=day, _market=self._market, _signals=self._signals,
                        _chain=self._chain)

    # ---- the cut ----
    def _guard(self, day: date, what: str) -> None:
        if day > self.as_of:
            raise LookAheadError(
                f"{what} for {day} was read by a view as of {self.as_of}. That is "
                f"look-ahead: the information did not exist yet"
            )

    # ---- queries ----
    def spot(self, day: date | None = None) -> Maybe[float]:
        """The underlying's **traded** close. Never the adjusted price: a strike was set
        against the price that traded."""
        day = day if day is not None else self.as_of
        self._guard(day, "the underlying close")
        return self._market.spot(day)

    def history(self, field: str = "close", n: int | None = None, *,
                adjusted: bool = True) -> pd.Series:
        """Bars up to and including ``as_of``, most recent last.

        ``adjusted`` defaults to True because this is what signals read; set it False only
        to line something up with an option strike.
        """
        bars = self._market.bars
        series = bars.adjusted(field) if adjusted else bars.traded(field)
        upto = series.loc[: pd.Timestamp(self.as_of)]
        return upto if n is None else upto.iloc[-n:]

    def signal(self, name: str) -> Maybe[float]:
        """One signal as of now. Absent reads as an explicit absence, never as 0."""
        return self._signals.on(self.as_of)[name]

    def signals(self) -> dict[str, Maybe[float]]:
        view = self._signals.on(self.as_of)
        return {name: view[name] for name in self._signals.names}

    def chain(self, day: date | None = None) -> pd.DataFrame:
        """The contracts quoted as of a day. Requires a chain source."""
        if self._chain is None:
            raise StartupError("this view was built without a chain source")
        day = day if day is not None else self.as_of
        self._guard(day, "the option chain")
        return self._chain.snapshot(day)

    def contract(self, contract, day: date | None = None) -> pd.DataFrame:
        """One contract's quotes on a day."""
        if self._chain is None:
            raise StartupError("this view was built without a chain source")
        day = day if day is not None else self.as_of
        self._guard(day, f"quotes for {contract}")
        return self._chain.series(contract, [day])

    def volatility(self, day: date | None = None) -> Maybe[float]:
        """The volatility proxy — real market data, so it holds on a synthetic chain."""
        day = day if day is not None else self.as_of
        self._guard(day, "the volatility proxy")
        return self._market.volatility(day)

    def risk_free_rate(self, day: date | None = None) -> float:
        day = day if day is not None else self.as_of
        self._guard(day, "the risk-free rate")
        return self._market.risk_free_rate(day)

    # ---- the document's queries that do not exist here ----
    def cross_section(self, field: str):
        raise NotImplementedError(
            "cross_section needs more than one underlying per run, which the engine does "
            "not do yet. See docs/requirements.md 1.19"
        )

    def panel(self, fields, window: int):
        raise NotImplementedError(
            "panel needs the feature graph in the data layer, which is not built. See "
            "docs/requirements.md 1.19"
        )

    def surface(self, underlying: str):
        raise NotImplementedError(
            "surface needs a SurfaceBuilder, which is moot on a synthetic chain whose IV "
            "is constant and is needed with real data. See docs/requirements.md 1.19"
        )
