"""The selection trade-off surface: four moneyness x tenor tables.

The risk here is **a plausible number that corresponds to no real contract** — an
interpolated cell, the wrong mid, or the put's out-of-the-money direction inverted.
So these tests are about the cells coming from a row that really exists on the chain,
with the direction right.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.research.tradeoff import _spread_out, tradeoff_grid

SPOT = 400.0


def chain(asof=date(2024, 3, 1), dtes=(14, 21, 30, 45, 60, 90), right="C") -> pd.DataFrame:
    rows = []
    for dte in dtes:
        for m in (0.0, 0.02, 0.04, 0.06, 0.10):
            strike = SPOT * (1 + m) if right == "C" else SPOT * (1 - m)
            t = dte / 365
            mid = max(SPOT * 0.22 * np.sqrt(t) * np.exp(-((m / (0.22 * np.sqrt(t))) ** 2) / 2), 0.4)
            rows.append({
                "asof": asof, "symbol": "QQQ",
                "expiry": asof + timedelta(days=dte), "strike": float(strike),
                "right": right, "bid": mid * 0.98, "ask": mid * 1.02,
                "underlying_price": SPOT, "iv": 0.22,
                "delta": 0.5 * np.exp(-m * 8), "gamma": 0.02, "vega": 20.0,
                "theta": -mid / dte * 1.5, "dte": dte,
            })
    return pd.DataFrame(rows)


class TestGrid:
    def test_all_four_quantities(self):
        g = tradeoff_grid(chain(), spot=SPOT)
        assert set(g) == {"theta_pct_day", "delta_now", "delta_if_move", "breakeven_move"}

    def test_cells_come_from_real_contracts(self):
        """Nothing is interpolated. An interpolated number looks like a quote and
        corresponds to nothing you could buy."""
        # The chain holds only 14 and 90 days; asking for 30 should leave it empty.
        g = tradeoff_grid(chain(dtes=(14, 90)), spot=SPOT, tenors=(30,))
        assert g["theta_pct_day"].isna().all().all()

    def test_theta_worse_for_short_and_far_otm(self):
        """A structural fact: short-dated and out of the money burns the largest
        fraction per day."""
        t = tradeoff_grid(chain(), spot=SPOT)["theta_pct_day"]
        assert t.loc["10%", "14d"] > t.loc["0%", "14d"]
        assert t.loc["10%", "14d"] > t.loc["10%", "90d"]

    def test_breakeven_grows_with_moneyness(self):
        b = tradeoff_grid(chain(), spot=SPOT)["breakeven_move"]
        col = b["30d"].dropna()
        assert col.is_monotonic_increasing

    def test_breakeven_is_always_the_move_toward_the_thesis(self):
        """The convention: positive = how far in the direction of the thesis, for
        calls and puts alike.

        The same convention as the chain's moneyness. Without that, reading the table
        means converting direction in your head, which is where mistakes happen.
        """
        calls = tradeoff_grid(chain(right="C"), spot=SPOT, right="C")["breakeven_move"]
        puts = tradeoff_grid(chain(right="P"), spot=SPOT, right="P")["breakeven_move"]
        assert (calls.stack() > 0).all()
        assert (puts.stack() > 0).all()

    def test_delta_if_move_uses_a_model_and_is_labelled(self):
        """The chain holds no "what if it rises 3%", so it has to be repriced — the
        docstring states the bias is optimistic."""
        g = tradeoff_grid(chain(), spot=SPOT, move=0.03)
        assert g["delta_if_move"].notna().any().any()
        # At the money, delta should rise clearly after a 3% move.
        assert g["delta_if_move"].loc["0%", "30d"] > g["delta_now"].loc["0%", "30d"]

    def test_deep_otm_short_dated_exposure_collapses(self):
        """Short-dated and far out of the money: the exposure shrinks once the thesis
        plays out."""
        g = tradeoff_grid(chain(), spot=SPOT, move=0.03)
        short_deep = g["delta_if_move"].loc["10%", "14d"]
        long_deep = g["delta_if_move"].loc["10%", "90d"]
        assert short_deep < long_deep

    def test_empty_chain_returns_empty_not_zeros(self):
        g = tradeoff_grid(pd.DataFrame(), spot=SPOT)
        assert all(v.empty for v in g.values())

    def test_wrong_right_returns_empty(self):
        g = tradeoff_grid(chain(right="C"), spot=SPOT, right="P")
        assert all(v.empty for v in g.values())


class TestTenorPicking:
    def test_picks_from_what_exists(self):
        # With no more levels than are shown, keep them all — picking would drop the
        # ones nothing is closest to.
        assert _spread_out([9, 16, 30, 58], (14, 21, 30, 45, 60, 90)) == [9, 16, 30, 58]
        # Only pick when there are many.
        many = list(range(7, 120, 7))
        assert len(_spread_out(many, (14, 21, 30, 45, 60, 90))) == 6

    def test_no_duplicates(self):
        picked = _spread_out([30], (14, 21, 30, 45, 60, 90))
        assert picked == [30]

    def test_empty(self):
        assert _spread_out([], (14, 30)) == []
