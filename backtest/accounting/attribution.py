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

    per_trade: pd.DataFrame          # one row per trade, the terms plus the residual
    totals: dict[str, float]         # each term's cumulative contribution
    unusable: int                    # trades that could not be attributed (greeks absent)
    high_residual: int               # trades whose residual is too large to trust
    #: How it was computed: ``"entry greeks, one step"`` or ``"daily"``. A reader cannot
    #: judge a residual without knowing which -- a 40% residual is expected of the first
    #: and is a defect in the second.
    method: str = "entry greeks, one step"
    #: The term names in this result, in order. The daily method has one more.
    terms: tuple[str, ...] = COMPONENTS

    def as_dict(self) -> dict[str, Any]:
        gross = sum(self.totals.values())
        return {
            "totals": self.totals,
            # **The share is of the gross magnitude, not of the signed total.**
            #
            # It was `v / gross` where gross is the signed sum, so a run with a negative
            # total printed every term with its sign reversed: a delta that earned
            # +13,836 against a total of −74,400 came out as −18.6%. The sign of a term
            # is in its dollar figure; a share is how much of the movement it accounts
            # for, which is a magnitude.
            "share": {k: (v / self.magnitude if self.magnitude else float("nan"))
                      for k, v in self.totals.items()},
            "magnitude": self.magnitude,
            "unusable": self.unusable,
            "high_residual": self.high_residual,
            "n": len(self.per_trade),
        }

    @property
    def magnitude(self) -> float:
        """The sum of the terms' absolute sizes -- the denominator a share belongs over.

        Using the signed total made the shares meaningless whenever the terms offset,
        which for an options book is the normal case: a long call earns on delta and
        gamma and pays on theta, and the net can be near zero with every term large.
        """
        return float(sum(abs(v) for v in self.totals.values()))

    def text(self) -> str:
        gross = sum(self.totals.values())
        magnitude = self.magnitude
        lines = ["P&L attribution", "─" * 52]
        for name in self.terms:
            v = self.totals.get(name, 0.0)
            share = f"{v / magnitude * 100:>6.1f}%" if magnitude else "     —"
            lines.append(f"  {name:<10} {v:>12,.0f}   {share} of the movement")
        lines += ["─" * 52, f"  net        {gross:>12,.0f}"]
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


#: The daily method's terms. ``spread`` is the entry and exit cost, which the one-step
#: method could only leave in the residual.
DAILY_COMPONENTS = ("delta", "gamma", "vega", "theta", "spread", "residual")


def attribute_daily(trades: pd.DataFrame, paths: pd.DataFrame) -> Attribution:
    """Attribute P&L **day by day**, using each day's own greeks.

    ## Why the one-step version is not enough

    :func:`attribute` expands once, from entry to exit, using the **entry** greeks. Over
    a three-week hold on an option that is the wrong tool and the residual says so --
    measured at 40% of gross on this project's own fixture, with a third of the trades
    individually untrustworthy. The system already says as much about its own stress
    grid: past a 10% move a second-order expansion gets the *direction* wrong, so the
    cells are left empty. Attribution was applying that same expansion to the whole
    holding period's move.

    Stepping daily keeps every expansion inside the range where it holds. The greeks for
    each step are the ones at the **start** of that step, which is what a Taylor term
    means.

    ## The spread is its own term

    The daily marks are **mids**; the fills are not. So the mid-to-mid path and the
    trade's gross P&L differ by exactly the spread paid on the way in and out, and in the
    one-step method that difference had nowhere to go but the residual -- where it was
    indistinguishable from the expansion failing. It is now a line of its own, signed
    negative because it is a cost.

    ## Closing the last step

    The daily path records up to the session **before** the exit, so its last mark is not
    the exit mark -- on the fixture's first trade that omits a +5.05 move, 40% of the
    trade. The final step is therefore taken from the last path row to the trade's own
    exit mid, with the last row's greeks. Reconstructed here rather than by changing what
    the engine records, because the path's span is what the excursion and counterfactual
    tools are built on.

    What is left in the residual after this is vanna, volga, the third order, and the
    discretisation of one day.
    """
    required = ["trade_id", "entry_price", "entry_mid", "exit_price", "exit_mid",
                "gross_pnl", "multiplier", "quantity"]
    missing_cols = [c for c in required if c not in trades.columns]
    if missing_cols:
        raise KeyError(
            f"the trade log is missing columns the daily attribution needs: "
            f"{missing_cols}. A table recorded before the quote was kept beside the fill "
            f"cannot separate the spread from the expansion's residual"
        )
    path_needs = ["trade_id", "date", "mark", "underlying", "iv",
                  "delta", "gamma", "vega", "theta"]
    if paths is None or len(paths) == 0:
        raise KeyError(
            "the daily attribution needs the per-trade daily path "
            "(result.raw.paths_frame), which only the event-driven engine produces"
        )
    missing_path = [c for c in path_needs if c not in paths.columns]
    if missing_path:
        raise KeyError(
            f"the daily path is missing {missing_path}. A path recorded before all four "
            f"greeks were kept on it can only support the one-step attribution"
        )
    if trades.empty:
        return Attribution(pd.DataFrame(columns=list(DAILY_COMPONENTS)),
                           dict.fromkeys(DAILY_COMPONENTS, 0.0), 0, 0,
                           method="daily", terms=DAILY_COMPONENTS)

    by_trade = {int(k): v.sort_values("date") for k, v in paths.groupby("trade_id")}
    rows = []
    unusable = 0
    for _, trade in trades.iterrows():
        path = by_trade.get(int(trade["trade_id"]))
        scale = float(trade["quantity"]) * float(trade["multiplier"])
        if path is None or len(path) < 2 or not np.isfinite(scale) or scale == 0:
            unusable += 1
            continue

        # Each step uses the greeks at its start, and the step's own moves.
        start = path.iloc[:-1]
        end = path.iloc[1:]
        d_spot = end["underlying"].to_numpy() - start["underlying"].to_numpy()
        d_vol = end["iv"].to_numpy() - start["iv"].to_numpy()
        days = np.diff(pd.to_datetime(path["date"]).to_numpy()).astype(
            "timedelta64[D]").astype(float)

        # ── the last step, which is the awkward one ───────────────────────────────
        #
        # The exit day's fill is processed before the day's excursions are recorded, so
        # the position is already gone and that session has no path row -- the path ends
        # one session short. On the fixture's first trade the missing step is a +5.05
        # move, 40% of the whole trade, so it cannot be dropped.
        #
        # Closed with the **start greeks**, like every other step, and that is not the
        # obvious choice. The trapezoid rule -- averaging the greeks at the two ends --
        # was tried first, on the reasoning that one expansion over a large gap is what
        # stepping daily exists to avoid. **It was measurably worse**: the residual grew
        # from 23,140 to 28,380 and the untrustworthy trade count from 50 to 65.
        #
        # The reason is arithmetic rather than empirical. An average delta across the
        # step already contains half the gamma effect over it -- that is what the
        # trapezoid rule for the integral of delta means -- so pairing it with an
        # explicit ½γΔS² term counts gamma one and a half times. In a second-order
        # expansion delta must be the delta at the **start**, and the gamma term supplies
        # the rest. The measurement and the algebra agree, which is the only reason to
        # trust either.
        last = path.iloc[-1]
        tail_days = float((pd.Timestamp(trade["exit_date"])
                           - pd.Timestamp(last["date"])).days)
        tail_spot = float(trade["exit_underlying_price"]) - float(last["underlying"])
        tail_vol = float(trade["exit_iv"]) - float(last["iv"])
        terms = {
            "delta": float(np.nansum(start["delta"].to_numpy() * d_spot) * scale
                           + float(last["delta"]) * tail_spot * scale),
            "gamma": float(0.5 * np.nansum(start["gamma"].to_numpy() * d_spot ** 2) * scale
                           + 0.5 * float(last["gamma"]) * tail_spot ** 2 * scale),
            "vega": float(np.nansum(start["vega"].to_numpy() * d_vol) * scale
                          + float(last["vega"]) * tail_vol * scale),
            "theta": float(np.nansum(start["theta"].to_numpy() * days
                                     / CALENDAR_DAYS_PER_YEAR) * scale
                           + float(last["theta"]) * tail_days
                           / CALENDAR_DAYS_PER_YEAR * scale),
        }
        if not all(np.isfinite(v) for v in terms.values()):
            unusable += 1
            continue

        # Negative because it is a cost: paid above the mid on the way in, below it on
        # the way out.
        terms["spread"] = -(
            (float(trade["entry_price"]) - float(trade["entry_mid"]))
            + (float(trade["exit_mid"]) - float(trade["exit_price"]))) * abs(scale)
        explained = sum(terms.values())
        actual = float(trade["gross_pnl"])
        row = dict(terms)
        row.update({"explained": explained, "actual": actual,
                    "residual": actual - explained,
                    "trade_id": int(trade["trade_id"]), "steps": len(path)})
        for key in ("exit_reason", "entry_date"):
            if key in trades.columns:
                row[key] = trade[key]
        rows.append(row)

    if not rows:
        return Attribution(pd.DataFrame(columns=list(DAILY_COMPONENTS)),
                           dict.fromkeys(DAILY_COMPONENTS, 0.0), unusable, 0,
                           method="daily", terms=DAILY_COMPONENTS)

    out = pd.DataFrame(rows)
    denom = out["actual"].abs().replace(0.0, np.nan)
    out["residual_share"] = (out["residual"].abs() / denom).fillna(np.inf)
    high = int((out["residual_share"] > _residual_warn()).sum())
    totals = {name: float(out[name].sum()) for name in DAILY_COMPONENTS}
    return Attribution(out, totals, unusable, high,
                       method="daily", terms=DAILY_COMPONENTS)


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
