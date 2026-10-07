"""Portfolio-level risk: aggregating greeks, stress tests, scenario replay, tail
measures.

**Why greeks rather than repricing.** A stress test could move the underlying to
-20% and reprice every contract, which is more accurate. It does not, for two
reasons: pricing is confined to the data layer, so repricing would mean the layers
above knowing how prices are made — an assumption that stops holding once real
data is wired in; and a greek expansion is how the industry stress-tests, using
exactly the columns the chain supplies, so it keeps working with a real vendor.

The cost has to be stated: a first/second-order expansion breaks down under a
**large shock**, and at a 20% move the second-order term is no longer enough. So
cells beyond that range report nothing at all rather than a number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from backtest.core.config import CALENDAR_DAYS_PER_YEAR

def _large_shock() -> float:
    """Beyond this underlying shock the second-order expansion's error is not negligible.

    A realism assumption in the document's register (group 1): sensitivity-tested, never
    optimised. Widening it does not make the expansion valid, it hides that it is not.
    """
    from backtest.core.thresholds import get

    return float(get("large_shock"))

#: The default stress grid. The underlying in percent, volatility in absolute
#: points.
DEFAULT_SPOT_SHOCKS = (-0.20, -0.10, -0.05, 0.0, 0.05, 0.10)
DEFAULT_VOL_SHOCKS = (-0.10, -0.05, 0.0, 0.05, 0.10)


@dataclass(frozen=True, slots=True)
class PortfolioGreeks:
    """The portfolio's greeks on one day, already multiplied by the contract
    multiplier and quantity, in dollars."""

    day: date
    spot: float
    delta: float          # change in portfolio value per $1 move in the underlying
    gamma: float          # rate of change of delta with the underlying
    vega: float           # change per 1.00 (100 point) move in volatility
    theta: float          # decay per year
    n_positions: int
    equity: float
    missing: int          # positions left out because their greeks were absent

    @property
    def delta_dollars(self) -> float:
        """Delta exposure expressed as notional in the underlying."""
        return self.delta * self.spot

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.day, "spot": self.spot,
            "delta": self.delta, "gamma": self.gamma, "vega": self.vega, "theta": self.theta,
            "delta_dollars": self.delta_dollars,
            "delta_pct_equity": self.delta_dollars / self.equity if self.equity else np.nan,
            "vega_pct_equity": self.vega / self.equity if self.equity else np.nan,
            "theta_per_day": self.theta / CALENDAR_DAYS_PER_YEAR,
            "positions": self.n_positions, "missing_greeks": self.missing,
        }


def aggregate_greeks(positions, chain, day: date, spot: float, equity: float) -> PortfolioGreeks:
    """Sum the greeks of the day's positions.

    A position whose greeks cannot be read is **counted, not treated as 0** — a
    vega read as 0 makes the portfolio look free of volatility exposure, which is
    the most dangerous misreading available.
    """
    totals = dict.fromkeys(("delta", "gamma", "vega", "theta"), 0.0)
    missing = 0
    for pos in positions:
        frame = chain.series(pos.contract, [day])
        if frame.empty:
            missing += 1
            continue
        row = frame.iloc[0]
        if any(pd.isna(row.get(g)) for g in totals):
            missing += 1
            continue
        weight = pos.quantity * pos.multiplier
        for g in totals:
            totals[g] += float(row[g]) * weight
    return PortfolioGreeks(
        day=day, spot=spot, n_positions=len(tuple(positions)), equity=equity, missing=missing,
        **totals,
    )


def greeks_by_expiry(positions, chain, day: date, spot: float,
                     equity: float) -> pd.DataFrame:
    """The book's greeks **grouped by expiry**, one row per expiry plus a total.

    The document's RiskModel row asks for "Greeks aggregation by underlying and expiry
    bucket", and the aggregate was one book-wide total. The expiry axis is the one that
    says something here: a book whose vega sits entirely on one expiry is a different
    book from one spreading it over six, and a total cannot tell them apart. The premium
    budget already groups by expiry; the greeks did not.

    Why expiry and not underlying: a run holds one underlying, so grouping by it returns
    the total with a different label. Expiry is the axis that has more than one value.

    A position whose greeks cannot be read is **counted in ``missing``, not treated as
    0** -- the same rule as :func:`aggregate_greeks`, and for the same reason: a vega
    read as 0 makes a bucket look free of volatility exposure.
    """
    rows: dict[date, dict[str, float]] = {}
    for pos in positions:
        bucket = rows.setdefault(pos.contract.expiry, dict.fromkeys(
            ("delta", "gamma", "vega", "theta", "premium_at_risk"), 0.0)
            | {"positions": 0, "missing": 0})
        bucket["positions"] += 1
        if pos.quantity > 0:
            bucket["premium_at_risk"] += pos.cost_basis

        frame = chain.series(pos.contract, [day])
        if frame.empty:
            bucket["missing"] += 1
            continue
        row = frame.iloc[0]
        if any(pd.isna(row.get(g)) for g in ("delta", "gamma", "vega", "theta")):
            bucket["missing"] += 1
            continue
        weight = pos.quantity * pos.multiplier
        for greek in ("delta", "gamma", "vega", "theta"):
            bucket[greek] += float(row[greek]) * weight

    if not rows:
        return pd.DataFrame(columns=["expiry", "days_to_expiry", "delta", "gamma",
                                     "vega", "theta", "premium_at_risk", "positions",
                                     "missing", "vega_share"])

    out = pd.DataFrame([
        {"expiry": expiry, "days_to_expiry": (expiry - day).days, **values}
        for expiry, values in sorted(rows.items())
    ])
    # The share of the book's **absolute** vega on each expiry. Absolute because two
    # expiries with opposite vega net to nothing and are not a book without vega risk --
    # they are a calendar spread, which has its own.
    total_vega = float(out["vega"].abs().sum())
    out["vega_share"] = (out["vega"].abs() / total_vega) if total_vega else float("nan")
    return out


def stress_grid(
    greeks: PortfolioGreeks,
    *,
    spot_shocks: Sequence[float] = DEFAULT_SPOT_SHOCKS,
    vol_shocks: Sequence[float] = DEFAULT_VOL_SHOCKS,
    days_forward: int = 0,
) -> pd.DataFrame:
    """A two-dimensional underlying x volatility stress grid; each cell is the
    change in portfolio value, in dollars.

    ``days_forward`` also advances time, to see what happens "if nothing moves and
    N days simply pass" — which for a buyer is pure loss.
    """
    rows = []
    theta_effect = greeks.theta * (days_forward / CALENDAR_DAYS_PER_YEAR)
    for ds_pct in spot_shocks:
        ds = greeks.spot * ds_pct
        limit = _large_shock()
        beyond = abs(ds_pct) > limit
        row = {"spot shock": f"{ds_pct:+.0%}"}
        for dv in vol_shocks:
            if beyond:
                # Outside the expansion's range, **no number is given.**
                #
                # For a buyer's portfolio gamma is positive, and under a large shock
                # the second-order term 0.5*gamma*dS^2 overwhelms the delta term,
                # producing results whose direction is wrong — "the underlying falls
                # 20% and we make money". A caveat saying "may be distorted" does
                # not stop the misreading: a confidently wrong number is more
                # dangerous than no number. For large shocks use scenario_replay,
                # which is real history and contains no approximation.
                row[f"IV{dv:+.0%}"] = np.nan
                continue
            row[f"IV{dv:+.0%}"] = (
                greeks.delta * ds + 0.5 * greeks.gamma * ds**2
                + greeks.vega * dv + theta_effect
            )
        row["note"] = (
            f"outside the second-order expansion's range (±{limit:.0%}); no "
            f"number given, use historical scenario replay"
            if beyond else ""
        )
        rows.append(row)
    return pd.DataFrame(rows).set_index("spot shock")


def var_cvar(returns: pd.Series, level: float = 0.05) -> dict[str, float]:
    """Historical VaR and CVaR.

    Historical quantiles rather than a normal assumption — an option portfolio's
    returns are fat-tailed and skewed, and normal VaR understates the tail
    systematically. A small sample is flagged, because on 200 observations the 5%
    quantile is decided by 10 points.
    """
    clean = returns.dropna()
    n = len(clean)
    if n < 20:
        return {"n": n, "var": float("nan"), "cvar": float("nan"),
                "note": f"only {n} observations; tail measures are not trustworthy"}
    var = float(np.quantile(clean, level))
    tail = clean[clean <= var]
    note = ""
    if n < 250:
        note = (f"{n} observations: the {level:.0%} quantile is decided by about "
                f"{max(1, int(n * level))} points, so the conclusion is fragile")
    return {"n": n, "var": var, "cvar": float(tail.mean()) if len(tail) else var,
            "tail_count": len(tail), "level": level, "note": note}


def scenario_replay(equity: pd.DataFrame, windows: dict[str, tuple[str, str]]) -> pd.DataFrame:
    """Replay the equity curve over named historical windows.

    A stress test asks "what if X happened"; a scenario replay asks "when it did
    happen, how did this strategy do". The latter depends on no expansion and is
    therefore more trustworthy — at the cost of only showing what actually
    happened.
    """
    rows = []
    index = pd.to_datetime(equity.index)
    for name, (start, end) in windows.items():
        mask = (index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
        part = equity.loc[mask]
        if part.empty:
            rows.append({"scenario": name, "from": start, "to": end, "sessions": 0,
                         "return": np.nan, "max drawdown": np.nan,
                         "note": "not covered by the backtest range"})
            continue
        curve = part["equity_mid"]
        rows.append({
            "scenario": name, "from": start, "to": end, "sessions": len(part),
            "return": curve.iloc[-1] / curve.iloc[0] - 1,
            "max drawdown": float((curve / curve.cummax() - 1).min()),
            "note": "" if len(part) > 10 else "the window is too short",
        })
    return pd.DataFrame(rows).set_index("scenario")


#: The usual historical stress windows. Each name says what the event was, so it
#: still reads three months later.
KNOWN_SCENARIOS: dict[str, tuple[str, str]] = {
    "2018 Q4 selloff": ("2018-10-01", "2018-12-31"),
    "2020 Covid crash": ("2020-02-19", "2020-03-23"),
    "2020 rebound": ("2020-03-24", "2020-06-30"),
    "2022 rate-hike bear": ("2022-01-01", "2022-10-14"),
    "2023 rebound": ("2023-01-01", "2023-07-31"),
}


def daily_risk_report(greeks: PortfolioGreeks, equity_returns: pd.Series) -> str:
    """A one-page daily risk report."""
    d = greeks.as_dict()
    v = var_cvar(equity_returns)
    lines = [
        f"Daily risk report · {greeks.day}",
        "─" * 52,
        f"  {greeks.n_positions} positions"
        + (f" ({greeks.missing} left out, greeks absent)" if greeks.missing else ""),
        f"  delta exposure {d['delta_dollars']:>12,.0f}   of equity {d['delta_pct_equity']:>7.2%}",
        f"  vega  exposure {d['vega']:>12,.0f}   of equity {d['vega_pct_equity']:>7.2%}",
        f"  gamma          {greeks.gamma:>12,.2f}",
        f"  theta/day      {d['theta_per_day']:>12,.0f}",
        "",
        f"  daily VaR(5%)  {v['var']:>8.2%}" if not np.isnan(v["var"]) else "  VaR: sample too small",
        f"  daily CVaR(5%) {v['cvar']:>8.2%}" if not np.isnan(v.get("cvar", np.nan)) else "",
    ]
    if v.get("note"):
        lines.append(f"  ! {v['note']}")
    return "\n".join(l for l in lines if l is not None)
