"""Hedging and risk overlays.

**A hedge only means anything at the portfolio level** — one position hedging
another's risk presupposes that both exist at once. So this layer depends on the
event-driven engine; the trade-table engine treats each trade independently and
cannot express it.

Three things:

1. **Exposure limits**: block new entries, or require a reduction, when
   portfolio delta or vega is over the limit.
2. **A drawdown brake**: cut size at one drawdown level, stop opening at the next.
3. **Hedge costs accounted for separately**: the one most easily skipped. A hedge
   always costs money; the question is what the money bought. So it has to be
   possible to compute the result **with the hedges removed** as a control —
   looking only at the hedged curve never tells you whether it was worth it.

How the third is done: hedge positions are tagged, and the report can drop them
and recompute. That requires marking "this one is a hedge" at order time; it
cannot be recovered afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class ExposureLimits:
    """Portfolio exposure limits. Set as fractions of equity rather than absolute
    amounts, because equity moves."""

    max_delta_pct: float | None = None      # |delta notional| / equity
    max_vega_pct: float | None = None       # |vega| / equity
    max_positions: int | None = None
    #: What to do on a breach. ``"block"`` only blocks new entries; ``"reduce"``
    #: would also require cutting back (unimplemented).
    on_breach: str = "block"

    def check(self, greeks, equity: float) -> tuple[bool, str]:
        """Returns (may a new position be opened, why not)."""
        if equity <= 0:
            return False, "equity is not positive"
        if self.max_delta_pct is not None:
            ratio = abs(greeks.delta_dollars) / equity
            if ratio > self.max_delta_pct:
                return False, (f"delta exposure {ratio:.1%} is over the "
                               f"{self.max_delta_pct:.1%} limit")
        if self.max_vega_pct is not None:
            ratio = abs(greeks.vega) / equity
            if ratio > self.max_vega_pct:
                return False, (f"vega exposure {ratio:.1%} is over the "
                               f"{self.max_vega_pct:.1%} limit")
        if self.max_positions is not None and greeks.n_positions >= self.max_positions:
            return False, (f"{greeks.n_positions} positions is at the "
                           f"{self.max_positions} limit")
        return True, ""


@dataclass(frozen=True, slots=True)
class DrawdownBrake:
    """A drawdown brake. Steps down in stages and releases on recovery."""

    reduce_at: float = 0.10        # at this drawdown, halve the size
    halt_at: float = 0.20          # at this drawdown, stop opening
    resume_at: float = 0.05        # once the drawdown narrows to this, release

    def state(self, drawdown: float) -> str:
        """``normal`` / ``reduced`` / ``halted``. ``drawdown`` is negative."""
        d = abs(drawdown)
        if d >= self.halt_at:
            return "halted"
        if d >= self.reduce_at:
            return "reduced"
        return "normal"

    def scale(self, drawdown: float) -> float:
        return {"normal": 1.0, "reduced": 0.5, "halted": 0.0}[self.state(drawdown)]


def exposure_report(greeks_frame: pd.DataFrame, limits: ExposureLimits) -> pd.DataFrame:
    """Daily exposure against the limits: which days breached, and by how much."""
    if greeks_frame.empty:
        return pd.DataFrame()
    out = greeks_frame[["delta_pct_equity", "vega_pct_equity", "positions"]].copy()
    if limits.max_delta_pct is not None:
        out["delta over limit"] = out["delta_pct_equity"].abs() > limits.max_delta_pct
    if limits.max_vega_pct is not None:
        out["vega over limit"] = out["vega_pct_equity"].abs() > limits.max_vega_pct
    return out


def breach_summary(report: pd.DataFrame) -> dict[str, Any]:
    if report.empty:
        return {"days": 0}
    out: dict[str, Any] = {"days": len(report)}
    for col in report.columns:
        if col.endswith("over limit"):
            out[col] = {"days": int(report[col].sum()),
                        "share": float(report[col].mean())}
    return out


def without_hedges(trades: pd.DataFrame) -> pd.DataFrame:
    """Drop the hedge positions, giving the "what if we had not hedged" control.

    Requires an ``is_hedge`` column in the trade log — it has to be set **at order
    time**, because which trade was a hedge cannot be inferred from the results
    afterwards. Without the column this raises rather than guessing.
    """
    if "is_hedge" not in trades.columns:
        raise KeyError(
            "the trade log has no is_hedge column. A hedge has to be tagged at "
            "order time; which trade was a hedge cannot be recognised from the "
            "results afterwards."
        )
    return trades.loc[~trades["is_hedge"].astype(bool)]


def hedge_value(trades: pd.DataFrame) -> pd.DataFrame:
    """Was the hedge worth it: with hedges against hedges removed.

    Total return with hedges on its own means nothing — a hedge lowers the return
    by construction; the question is how much it improved the worst case. So the
    comparison is about the **tail**, not the mean.
    """
    if "is_hedge" not in trades.columns:
        raise KeyError("the trade log has no is_hedge column")
    hedges = trades.loc[trades["is_hedge"].astype(bool)]
    core = trades.loc[~trades["is_hedge"].astype(bool)]
    rows = []
    for name, part in (("with hedges", trades), ("hedges removed", core)):
        pnl = part["net_pnl"]
        rows.append({
            "basis": name, "trades": len(part),
            "total": float(pnl.sum()),
            "mean per trade": float(pnl.mean()) if len(pnl) else np.nan,
            "worst trade": float(pnl.min()) if len(pnl) else np.nan,
            "worst 5% mean": float(pnl[pnl <= pnl.quantile(0.05)].mean()) if len(pnl) > 20 else np.nan,
        })
    rows.append({
        "basis": "the hedges themselves", "trades": len(hedges),
        "total": float(hedges["net_pnl"].sum()) if len(hedges) else 0.0,
        "mean per trade": float(hedges["net_pnl"].mean()) if len(hedges) else np.nan,
        "worst trade": float(hedges["net_pnl"].min()) if len(hedges) else np.nan,
        "worst 5% mean": np.nan,
    })
    return pd.DataFrame(rows).set_index("basis")
