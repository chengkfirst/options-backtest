"""Hedging and risk overlays.

**A hedge only means anything at the portfolio level** — one position hedging
another's risk presupposes that both exist at once. So this layer depends on the
event-driven engine; the trade-table engine treats each trade independently and
cannot express it.

Four things:

0. **A premium budget**: the document's "core budget measure" for a long-options book,
   and the only limit here whose breach is a certainty rather than a scenario -- an
   option held to expiry out of the money loses all of its premium.
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

from backtest.core.types import require
from backtest.portfolio.risk_actions import NOTHING, RiskAction, RiskDecision


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
class PremiumBudget:
    """How much premium the book may have outstanding at once.

    **The document calls premium at risk "a core budget measure" and nothing was
    budgeting it.** Greeks limits were -- delta and vega -- and those answer a different
    question. Delta says how the book moves on the next dollar of underlying; premium at
    risk says what it loses if every option it holds expires worthless. For a long-only
    options book the second is the one with a floor under it, and it is the only limit
    here whose breach is a *certainty* rather than a scenario: a position held to expiry
    out of the money loses its whole premium with probability one.

    Three limits, each a fraction of equity rather than an amount, for the same reason
    ``ExposureLimits`` gives: equity moves, and a dollar cap set at the start becomes a
    different constraint by the end.

    **Per expiry is the one that is not implied by the total.** Ten percent of equity
    spread over six expiries loses at most a sixth of it on any one Friday; the same ten
    percent on one expiry is all of it at once, and the total limit cannot tell the two
    apart. This is the long-options version of a concentration limit.

    ``min_cash_pct`` is the other side of the same budget: a book may be inside its
    premium limit and still have nothing left to pay a fee or meet an assignment with.
    """

    #: Total premium at risk, as a share of equity.
    max_premium_pct: float | None = None
    #: Premium at risk on any one expiry, as a share of equity.
    max_premium_per_expiry_pct: float | None = None
    #: Cash that must remain after an entry, as a share of equity.
    min_cash_pct: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_premium_pct", "max_premium_per_expiry_pct", "min_cash_pct"):
            value = getattr(self, name)
            if value is not None and not 0.0 < float(value) <= 1.0:
                raise ValueError(
                    f"{name} is a share of equity and must be in (0, 1]; got {value}. "
                    f"A premium budget above equity is not a budget, and one at or "
                    f"below zero forbids every trade"
                )

    @property
    def active(self) -> bool:
        return any(v is not None for v in
                   (self.max_premium_pct, self.max_premium_per_expiry_pct,
                    self.min_cash_pct))

    def check(self, positions, cash: float, equity: float,
              *, adding: float = 0.0, expiry=None) -> tuple[bool, str]:
        """Returns (may this premium be added, why not).

        ``adding`` is the premium the proposed entry would pay and ``expiry`` is its
        expiry, so the check is made **before** the position exists rather than reported
        after it. A limit tested only on what is already open is a limit the next trade
        always passes.
        """
        if equity <= 0:
            return False, "equity is not positive, so no budget can be computed from it"

        # Long positions only, matching `Snapshot.premium_at_risk`: a short's collateral
        # is capital tied up and comes back unless the option is exercised, so counting
        # it as premium at risk would overstate what the book can lose.
        held = [p for p in positions if p.quantity > 0]
        outstanding = sum(p.cost_basis for p in held)

        if self.max_premium_pct is not None:
            after = (outstanding + adding) / equity
            if after > self.max_premium_pct:
                return False, (
                    f"premium at risk would be {after:.1%} of equity, over the "
                    f"{self.max_premium_pct:.1%} budget "
                    f"(${outstanding:,.0f} open plus ${adding:,.0f})"
                )

        if self.max_premium_per_expiry_pct is not None and expiry is not None:
            on_expiry = sum(p.cost_basis for p in held if p.contract.expiry == expiry)
            after = (on_expiry + adding) / equity
            if after > self.max_premium_per_expiry_pct:
                return False, (
                    f"premium at risk on {expiry} would be {after:.1%} of equity, over "
                    f"the {self.max_premium_per_expiry_pct:.1%} per-expiry budget. "
                    f"**That whole amount resolves on one day**"
                )

        if self.min_cash_pct is not None:
            after = (cash - adding) / equity
            if after < self.min_cash_pct:
                return False, (
                    f"cash after the entry would be {after:.1%} of equity, under the "
                    f"{self.min_cash_pct:.1%} floor"
                )

        return True, ""


@dataclass(frozen=True, slots=True)
class DrawdownBrake:
    """A drawdown brake: a book-wide risk rule, expressed in the four risk actions.

    **Nothing here is defaulted.** Where to start cutting, where to stop opening, how hard
    to cut and when to release are risk decisions, and a platform-chosen set would govern
    the whole book without appearing in the configuration a person reads back. See
    ``tools/check_defaults.py``.

    ``keep`` is the one that was wrong rather than merely absent: the brake used to halve
    the size at ``reduce_at`` with 0.5 written into a lookup table, and it halved **new
    entries only** -- existing positions were untouched while the class read as a brake on
    the book. The document's ``scale_down`` is "reduce all positions by a ratio", so that
    is what it now does.
    """

    #: Drawdown (as a positive fraction) at which to scale the book down.
    reduce_at: float
    #: Drawdown at which to stop opening anything new.
    halt_at: float
    #: Once the drawdown narrows back to this, release.
    resume_at: float
    #: What fraction of each position to keep when scaling down. 0.5 halves the book.
    keep: float
    #: Drawdown at which to close the book entirely. ``None`` means never -- which is a
    #: real choice, not an absent one.
    liquidate_at: float | None = None

    def __post_init__(self) -> None:
        require(0 < self.resume_at <= self.reduce_at <= self.halt_at,
                f"the brake's stages must widen: resume_at {self.resume_at} <= reduce_at "
                f"{self.reduce_at} <= halt_at {self.halt_at}")
        require(0 <= self.keep < 1,
                f"keep must be between 0 and 1, and below 1 or nothing is cut; "
                f"got {self.keep}")
        if self.liquidate_at is not None:
            require(self.liquidate_at >= self.halt_at,
                    f"liquidate_at {self.liquidate_at} must be at least halt_at "
                    f"{self.halt_at}: closing the book while still opening is incoherent")

    def state(self, drawdown: float) -> str:
        """``normal`` / ``reduced`` / ``halted`` / ``liquidated``. ``drawdown`` is
        negative."""
        d = abs(drawdown)
        if self.liquidate_at is not None and d >= self.liquidate_at:
            return "liquidated"
        if d >= self.halt_at:
            return "halted"
        if d >= self.reduce_at:
            return "reduced"
        return "normal"

    def decide(self, drawdown: float) -> RiskDecision:
        """The day's verdict, in the document's vocabulary."""
        state = self.state(drawdown)
        d = abs(drawdown)
        if state == "liquidated":
            return RiskDecision(RiskAction.LIQUIDATE,
                                reason=f"drawdown {d:.1%} reached the liquidation level "
                                       f"of {self.liquidate_at:.1%}")
        if state == "halted":
            return RiskDecision(RiskAction.NO_NEW_POSITIONS,
                                reason=f"drawdown {d:.1%} is past the {self.halt_at:.1%} "
                                       f"halt level")
        if state == "reduced":
            return RiskDecision(RiskAction.SCALE_DOWN, keep=self.keep,
                                reason=f"drawdown {d:.1%} is past the "
                                       f"{self.reduce_at:.1%} reduce level")
        return NOTHING

    def scale(self, drawdown: float) -> float:
        """How much of a **new** position to open. Separate from ``decide`` because the
        two answer different questions: this one sizes an entry, that one acts on the
        book."""
        return {"normal": 1.0, "reduced": self.keep,
                "halted": 0.0, "liquidated": 0.0}[self.state(drawdown)]


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
