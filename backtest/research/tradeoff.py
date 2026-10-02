"""The contract-selection trade-off surface: one day's chain, spread out over
moneyness x tenor.

## Why this exists

The intuition about choosing a contract is about **structure** — "that one is too
far out of the money, it will not come back even if the move happens", "theta at
that tenor is unbearable". Behind those judgements is the shape of a few quantities
over the moneyness x tenor surface. Without seeing that shape, there is only memory
to go on.

## Four quantities, one sentence each

===================  ======================================================
``theta_pct_day``     with the underlying still, what percentage of the
                      premium burns each day
``delta_now``         the directional exposure right now
``delta_if_move``     what the exposure becomes **once the thesis plays
                      out** <- the one most easily overlooked
``breakeven_move``    how far the underlying has to move to break even.
                      **Positive always means "in the direction of the
                      thesis"**, for calls and puts alike — the same
                      convention as the chain's ``moneyness``, so no
                      direction has to be converted while reading
===================  ======================================================

``delta_if_move`` is the important one here. A short-dated, far out-of-the-money
contract's exposure actually shrinks once the underlying really moves — the thesis
was right and the position is gone. The delta at entry cannot show that.

## Where the numbers come from

**Computed from the day's real chain, not pasted from a formula into a static
table.** So once real data is wired in it reflects the real skew and term structure;
on the synthetic chain it reflects Black-Scholes' properties. In both cases the table
honestly describes what you can actually buy right now, rather than what some model
says things should look like.

``delta_if_move`` is the one entry that needs a model — the chain holds no "what if
the underlying rises 3%", so it has to be repriced. Its provenance is flagged
separately.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

#: The moneyness levels shown by default (positive always means out of the money,
#: for calls and puts alike).
MONEYNESS = (0.00, 0.02, 0.04, 0.06, 0.10)
#: The tenors shown by default, in calendar days.
TENORS = (14, 21, 30, 45, 60, 90)


def tradeoff_grid(
    chain: pd.DataFrame,
    *,
    spot: float,
    right: str = "C",
    moneyness: Sequence[float] = MONEYNESS,
    tenors: Sequence[int] | None = None,
    move: float = 0.03,
    rate: float = 0.04,
) -> dict[str, pd.DataFrame]:
    """Spread the day's chain into four moneyness x tenor tables.

    Each cell takes the real contract **closest** to that moneyness and tenor; where
    nothing is close enough the cell is left empty, and **nothing is interpolated** —
    an interpolated number looks exactly like a real quote while corresponding to
    nothing you could buy.
    """
    if chain.empty:
        return {k: pd.DataFrame() for k in
                ("theta_pct_day", "delta_now", "delta_if_move", "breakeven_move")}

    pool = chain.loc[chain["right"].astype(str) == right].copy()
    if pool.empty:
        return {k: pd.DataFrame() for k in
                ("theta_pct_day", "delta_now", "delta_if_move", "breakeven_move")}

    pool["mid"] = (pool["bid"].astype(float) + pool["ask"].astype(float)) / 2.0
    is_call = right == "C"
    pool["_mny"] = (pool["strike"] / spot - 1.0) if is_call else (1.0 - pool["strike"] / spot)
    if "dte" not in pool.columns:
        asof = pd.Timestamp(pool["asof"].iloc[0]).date()
        pool["dte"] = [(pd.Timestamp(e).date() - asof).days for e in pool["expiry"]]

    # The tenors come from **what the chain actually holds**, not a hard-coded list.
    # Hard-coded, a sparse set of expiries leaves whole columns empty and looks like a
    # broken system; and a real chain's expiry distribution varies by underlying
    # anyway.
    if tenors is None:
        available = sorted(int(d) for d in pool["dte"].unique() if d > 0)
        tenors = _spread_out(available, TENORS)

    out = {k: pd.DataFrame(index=[f"{m:.0%}" for m in moneyness],
                           columns=[f"{t}d" for t in tenors], dtype=float)
           for k in ("theta_pct_day", "delta_now", "delta_if_move", "breakeven_move")}

    for mi, m in enumerate(moneyness):
        for ti, t in enumerate(tenors):
            # Tolerances: moneyness within 1.2 percentage points, tenor within 25%.
            # Outside them the cell stays empty; nothing is forced.
            near = pool.loc[(pool["_mny"] - m).abs().le(0.012)
                            & (pool["dte"] - t).abs().le(max(3, t * 0.25))]
            if near.empty:
                continue
            row = near.iloc[((near["_mny"] - m).abs() / 0.012
                             + (near["dte"] - t).abs() / max(3, t * 0.25)).argmin()]
            mid, dte = float(row["mid"]), float(row["dte"])
            if mid <= 0.01 or dte <= 0:
                continue
            r_, c_ = f"{m:.0%}", f"{t}d"

            theta = float(row.get("theta", np.nan))
            if np.isfinite(theta):
                out["theta_pct_day"].at[r_, c_] = abs(theta) / mid / 365.0
            delta = float(row.get("delta", np.nan))
            if np.isfinite(delta):
                out["delta_now"].at[r_, c_] = abs(delta)

            strike = float(row["strike"])
            be = (strike + mid) / spot - 1.0 if is_call else 1.0 - (strike - mid) / spot
            out["breakeven_move"].at[r_, c_] = be

            iv = float(row.get("iv", np.nan))
            if np.isfinite(iv) and iv > 0:
                out["delta_if_move"].at[r_, c_] = _delta_after(
                    spot, strike, dte, iv, rate, is_call, move)
    return out


def _delta_after(spot, strike, dte, iv, rate, is_call, move) -> float:
    """Delta after the thesis plays out (the underlying moves by ``move``) and ten
    days pass.

    The repricing lives in the data layer (``data.chain.delta_after_move``) rather
    than calling pricing directly here — the layers above consume the chain's columns
    and do not care whether a number was observed or derived, which is what lets the
    data source be swapped with no change above.
    """
    from backtest.data.options.chain import delta_after_move
    from backtest.core.types import Right

    return delta_after_move(spot, strike, dte, iv, rate,
                            Right.CALL if is_call else Right.PUT, move)


def _spread_out(available: list[int], wanted: Sequence[int], n: int = 6) -> list[int]:
    """From the tenors the chain actually holds, pick those closest to the
    conventional levels.

    The conventional levels (14/21/30/45/60/90) exist so tables from different days
    can be compared; but if a given day's chain holds nothing near 90 days, forcing an
    empty column just makes it look like a broken system.
    """
    if not available:
        return []
    # When the chain holds only a few expiries, keep them all. Picking by the
    # conventional levels would drop the ones nothing is closest to (9/16/30/58 days
    # would lose the 9), losing information for nothing on a sparse chain.
    if len(available) <= n:
        return available
    picked: list[int] = []
    for w in wanted:
        best = min([d for d in available if d not in picked] or available,
                   key=lambda d: abs(d - w))
        if best not in picked:
            picked.append(best)
    return sorted(picked)[:n]
