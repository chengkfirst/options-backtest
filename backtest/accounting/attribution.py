"""P&L attribution: where the money actually came from.

If a buyer's strategy that looks profitable made all of it on vega (implied
volatility rose after entry) while delta lost, it is not the strategy you think it
is — change the volatility environment and it stops working. Total P&L alone
cannot show this.

The method is a second-order Taylor expansion of the option price:

    dP ≈ delta*dS + 0.5*gamma*dS^2 + vega*dSigma + theta*dt

The difference between the sum of those four and the actual P&L is the
**residual**. A large residual means the approximation failed — usually because
the underlying moved too far (second order is not enough) or the holding period
was long (the greeks changed too much over it). **The residual is not a waste
bin**: it is itself a diagnostic, and past a threshold it says this attribution
cannot be trusted.

Conventions:
- ``vega`` is the price change per 1.00 (100 point) move in volatility, so
  dSigma is a decimal
- ``theta`` is decay per year, so dt is in years (calendar/365, as in pricing)
- everything is multiplied by the contract multiplier and quantity, in dollars
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backtest.core.config import CALENDAR_DAYS_PER_YEAR

def _residual_warn() -> float:
    """Above this residual share of gross P&L the attribution is not trusted.

    A realism assumption (the document's group 1): it marks where the Taylor expansion
    stopped describing the P&L it claims to, so raising it would hide a failure rather
    than fix one.
    """
    from backtest.core.thresholds import get

    return float(get("attribution_residual_warning"))

COMPONENTS = ("delta", "gamma", "vega", "theta", "residual")


@dataclass(frozen=True, slots=True)
class Attribution:
    """One attribution result."""

    per_trade: pd.DataFrame          # one row per trade, four terms plus the residual
    totals: dict[str, float]         # each term's cumulative contribution
    unusable: int                    # trades that could not be attributed (greeks absent)
    high_residual: int               # trades whose residual is too large to trust

    def as_dict(self) -> dict[str, Any]:
        gross = sum(self.totals.values())
        return {
            "totals": self.totals,
            "share": {k: (v / gross if gross else float("nan")) for k, v in self.totals.items()},
            "unusable": self.unusable,
            "high_residual": self.high_residual,
            "n": len(self.per_trade),
        }

    def text(self) -> str:
        gross = sum(self.totals.values())
        lines = ["P&L attribution", "─" * 46]
        for name in COMPONENTS:
            v = self.totals[name]
            share = f"{v / gross * 100:>6.1f}%" if gross else "     —"
            lines.append(f"  {name:<10} {v:>12,.0f}   {share}")
        lines += ["─" * 46, f"  total      {gross:>12,.0f}"]
        if self.unusable:
            lines.append(f"  ! {self.unusable} trades left out, greeks absent")
        if self.high_residual:
            lines.append(
                f"  ! {self.high_residual} trades have a residual above "
                f"{_residual_warn():.0%} of gross P&L; their attribution is not "
                f"trustworthy"
            )
        return "\n".join(lines)


def attribute(trades: pd.DataFrame) -> Attribution:
    """Attribute the P&L of a trade log.

    A trade missing any greek is **excluded and counted**, not filled with 0 — a
    vega read as 0 makes the attribution entirely wrong and raises nothing.
    """
    required = ["entry_delta", "entry_gamma", "entry_vega", "entry_theta",
                "entry_underlying_price", "exit_underlying_price",
                "entry_iv", "exit_iv", "entry_date", "exit_date",
                "gross_pnl", "multiplier", "quantity"]
    missing_cols = [c for c in required if c not in trades.columns]
    if missing_cols:
        raise KeyError(f"the trade log is missing columns attribution needs: "
                       f"{missing_cols}")
    if trades.empty:
        return Attribution(pd.DataFrame(columns=list(COMPONENTS)), dict.fromkeys(COMPONENTS, 0.0), 0, 0)

    usable = trades.dropna(subset=required)
    unusable = len(trades) - len(usable)
    if usable.empty:
        return Attribution(pd.DataFrame(columns=list(COMPONENTS)), dict.fromkeys(COMPONENTS, 0.0),
                           unusable, 0)

    scale = usable["multiplier"] * usable["quantity"]
    d_spot = usable["exit_underlying_price"] - usable["entry_underlying_price"]
    d_vol = usable["exit_iv"] - usable["entry_iv"]
    d_years = (
        (pd.to_datetime(usable["exit_date"]) - pd.to_datetime(usable["entry_date"])).dt.days
        / CALENDAR_DAYS_PER_YEAR
    )

    out = pd.DataFrame(index=usable.index)
    out["delta"] = usable["entry_delta"] * d_spot * scale
    out["gamma"] = 0.5 * usable["entry_gamma"] * d_spot**2 * scale
    out["vega"] = usable["entry_vega"] * d_vol * scale
    out["theta"] = usable["entry_theta"] * d_years * scale
    out["explained"] = out[["delta", "gamma", "vega", "theta"]].sum(axis=1)
    out["actual"] = usable["gross_pnl"]
    out["residual"] = out["actual"] - out["explained"]

    denom = out["actual"].abs().replace(0.0, np.nan)
    out["residual_share"] = (out["residual"].abs() / denom).fillna(np.inf)
    high = int((out["residual_share"] > _residual_warn()).sum())

    for key in ("trade_id", "exit_reason", "entry_date"):
        if key in usable.columns:
            out[key] = usable[key]

    totals = {name: float(out[name].sum()) for name in COMPONENTS}
    return Attribution(out, totals, unusable, high)


def attribute_by(trades: pd.DataFrame, column: str, bins: int = 4) -> pd.DataFrame:
    """Attribute within groups of one column, answering "in this kind of situation,
    where did the money come from".

    Numeric columns are bucketed by equal frequency; categorical ones are grouped
    directly.
    """
    if column not in trades.columns:
        raise KeyError(f"the trade log has no {column!r}")
    frame = trades.dropna(subset=[column])
    if frame.empty:
        return pd.DataFrame(columns=list(COMPONENTS))
    if pd.api.types.is_numeric_dtype(frame[column]):
        groups = pd.qcut(frame[column], bins, duplicates="drop")
    else:
        groups = frame[column]

    rows = []
    for key, part in frame.groupby(groups, observed=True):
        result = attribute(part)
        rows.append({"group": str(key), "trades": len(part), **result.totals,
                     "suspect residuals": result.high_residual})
    return pd.DataFrame(rows).set_index("group")
