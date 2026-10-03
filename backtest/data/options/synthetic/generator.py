"""The synthetic option chain.

**Its uses are strictly bounded**: verifying engine correctness, serving as a
test fixture, and standing in while underlying-side signal research is done. It
MUST NOT be used to tune option-side parameters (selection, take-profit, stop
loss) — it has no volatility skew, no realistic implied-volatility dynamics, and
no spread that varies with liquidity, so what gets tuned on it is the optimum
*under this synthetic model*, which bears no reliable relation to the real market.
A guard in :mod:`backtest.engine` enforces this.

Deliberately crude: every strike shares one volatility. A crude model that says
so is safer than a model that looks refined and is still wrong — the latter
invites belief in the backtest's conclusions.

**Generated on demand**: rather than precomputing ten years (tens of millions of
rows), each day is built when it is asked for. A real source is queried per day
and the synthetic chain is generated per day, so the access patterns match.
"""

from __future__ import annotations

import math
from datetime import date
from functools import lru_cache
from typing import Iterable

import numpy as np
import pandas as pd

from backtest.core.config import BacktestConfig
from backtest.data.options.chain import UNAVAILABLE, empty_chain, validate_chain
from backtest.data.market import MarketData
from backtest.data.options.pricing import bs_price, greeks, has_early_exercise_value
from backtest.core.types import CHAIN_COLUMNS, ContractId, QuoteSource, Right


class SyntheticChainSource:
    """A chain generated from the underlying's close, the volatility proxy and
    Black-Scholes."""

    #: Availability is not assumed here: each row is generated on the day it describes,
    #: so the two dates coincide by construction rather than by guess. Declared so
    #: provenance can report it without special-casing which source it is looking at.
    availability_assumed = False

    def __init__(self, market: MarketData, config: BacktestConfig | None = None) -> None:
        self.market = market
        self.config = config if config is not None else market.config
        self._cache: dict[date, pd.DataFrame] = {}

    # ---- the protocol ----
    def snapshot(self, day: date, *, known_by: date | None = None) -> pd.DataFrame:
        """``known_by`` is accepted and has no effect: every row here is generated on the
        day it describes, so nothing can arrive late. Accepted rather than refused so the
        two sources stay interchangeable."""
        if day not in self._cache:
            self._cache[day] = self._build(day)
        return self._cache[day]

    def series(self, contract: ContractId, days: Iterable[date]) -> pd.DataFrame:
        """One contract's daily quotes. Days without an underlying close or a
        volatility simply do not appear."""
        rows = []
        for day in days:
            if day > contract.expiry:
                break
            row = self._quote(day, contract)
            if row is not None:
                rows.append(row)
        if not rows:
            return empty_chain()
        return pd.DataFrame(rows, columns=list(CHAIN_COLUMNS))

    # ---- generation ----
    def _early_exercise_flags(self, strikes, theo, s: float, day: date, expiry: date,
                              right: Right, rate: float, years: float):
        """Where the European price is wrong because the option is American.

        Two cases, and this file used to be able to reach only one of them. It returned
        early when no dividend fell in the window, which is right for the call -- a call
        is exercised early to capture a dividend, so no dividend means no case -- and
        wrong for the put, which is exercised early to collect the strike and earn
        interest on it. With that return in place, and with ``rate`` and ``years`` never
        passed, the put branch of ``has_early_exercise_value`` could not be reached from
        the only caller it had.

        Flagged in ``quality_flags`` rather than left for the layers above to work
        out: they consume the chain's columns and do not care how the numbers
        were arrived at.
        """
        if self.config.contract.exercise.value != "american":
            return [""] * len(strikes)
        cash = self.market.dividends_between(day, expiry)
        if right is Right.CALL and cash <= 0:
            return [""] * len(strikes)
        if right is Right.PUT and rate <= 0:
            return [""] * len(strikes)
        intrinsic = (
            np.maximum(s - strikes, 0.0) if right is Right.CALL else np.maximum(strikes - s, 0.0)
        )
        return [
            "early_exercise_value"
            if has_early_exercise_value(
                right=right, option_price=float(p), intrinsic=float(i), strike=float(k),
                dividend_in_window=cash, rate=rate, years_to_expiry=years,
            )
            else ""
            for p, i, k in zip(theo, intrinsic, strikes)
        ]

    def _inputs(self, day: date, expiry: date):
        spot = self.market.spot(day)
        vol = self.market.volatility(day)
        if spot.is_missing or vol.is_missing:
            return None
        s = spot.unwrap()
        return (
            s,
            vol.unwrap(),
            self.market.risk_free_rate(day),
            self.market.dividend_yield(day, expiry, s),
            self.config.time.year_fraction(day, expiry),
        )

    def _strike_grid(self, spot: float) -> np.ndarray:
        step = self.config.contract.strike_step
        width = self.config.synthetic.strike_range
        lo = math.floor(spot * (1 - width) / step) * step
        hi = math.ceil(spot * (1 + width) / step) * step
        return np.round(np.arange(lo, hi + step / 2, step), 3)

    def _quotes(self, strikes, s, k_vol, t, r, q, right: Right):
        theo = bs_price(s, strikes, t, r, k_vol, q, right)
        theo = np.maximum(np.asarray(theo, dtype=float), 0.0)
        g = greeks(s, strikes, t, r, k_vol, q, right)
        half = np.maximum(
            theo * self.config.synthetic.spread_pct, self.config.synthetic.spread_floor
        ) / 2.0
        bid = np.maximum(theo - half, 0.0)
        ask = theo + half
        return theo, bid, ask, g

    def _build(self, day: date) -> pd.DataFrame:
        cfg = self.config
        expiries = [
            e
            for e in self.market.expiries
            if day < e <= day + pd.Timedelta(days=cfg.synthetic.max_dte).to_pytimedelta()
        ]
        if not expiries:
            return empty_chain()

        spot = self.market.spot(day)
        if spot.is_missing:
            return empty_chain()
        strikes = self._strike_grid(spot.unwrap())

        blocks = []
        for expiry in expiries:
            prepared = self._inputs(day, expiry)
            if prepared is None:
                continue
            s, vol, r, q, t = prepared
            for right in (Right.CALL, Right.PUT):
                theo, bid, ask, g = self._quotes(strikes, s, vol, t, r, q, right)
                blocks.append(
                    pd.DataFrame(
                        {
                            "asof": day,
                            # Generated on the day it describes, so the two coincide by
                            # construction. Carried anyway: the two sources must have the
                            # same shape, or code that reads one breaks on the other.
                            "available_at": day,
                            "symbol": cfg.symbol,
                            "expiry": expiry,
                            "strike": strikes,
                            "right": right.value,
                            "bid": np.round(bid, 4),
                            "ask": np.round(ask, 4),
                            "last": np.round(theo, 4),
                            "volume": UNAVAILABLE,
                            "open_interest": UNAVAILABLE,
                            "iv": vol,
                            "delta": g["delta"],
                            "gamma": g["gamma"],
                            "vega": g["vega"],
                            "theta": g["theta"],
                            "underlying_price": s,
                            "source": QuoteSource.SYNTHETIC.value,
                            "quality_flags": self._early_exercise_flags(
                                strikes, theo, s, day, expiry, right, r, t
                            ),
                            # These greeks come from the same model that made the
                            # quotes: self-consistent model output, not market
                            # observation. Selecting by delta on the synthetic
                            # chain is therefore circular.
                            "greeks_model_derived": True,
                        }
                    )
                )
        if not blocks:
            return empty_chain()
        frame = pd.concat(blocks, ignore_index=True)
        return validate_chain(frame, asof=day)

    def _quote(self, day: date, contract: ContractId) -> dict | None:
        prepared = self._inputs(day, contract.expiry)
        if prepared is None:
            return None
        s, vol, r, q, t = prepared
        theo, bid, ask, g = self._quotes(
            np.array([contract.strike]), s, vol, t, r, q, contract.right
        )
        return {
            "asof": day,
            "available_at": day,
            "symbol": contract.symbol,
            "expiry": contract.expiry,
            "strike": contract.strike,
            "right": contract.right.value,
            "bid": round(float(bid[0]), 4),
            "ask": round(float(ask[0]), 4),
            "last": round(float(theo[0]), 4),
            "volume": UNAVAILABLE,
            "open_interest": UNAVAILABLE,
            "iv": vol,
            "delta": float(g["delta"][0]),
            "gamma": float(g["gamma"][0]),
            "vega": float(g["vega"][0]),
            "theta": float(g["theta"][0]),
            "underlying_price": s,
            "source": QuoteSource.SYNTHETIC.value,
            "quality_flags": self._early_exercise_flags(
                np.array([contract.strike]), theo, s, day, contract.expiry,
                contract.right, r, t,
            )[0],
            "greeks_model_derived": True,
        }
