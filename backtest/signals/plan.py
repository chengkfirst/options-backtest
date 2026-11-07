"""The trade plan: expressing the seven components as **one decision**.

## Why this exists

The parameterised form breaks a strategy into unrelated constants: take profit
`1.0`, size "1 contract", selection `delta 0.30`. In a real trade those are
coupled — in a deep drawdown you buy longer dated, bet bigger, widen the stop and
raise the target, **all at once**. Those are facets of one judgement, not six
independent settings.

Split into constants they no longer know about each other, and there is no way to
say "this case gets these parameters, that case gets those".

``Plan`` writes one complete trade decision in one place:

    def plan(ctx):
        dd = ctx.signal("drawdown_60", default=0.0)
        if dd > -0.05:
            return None                          # not deep enough, nothing today

        deep = dd < -0.10
        c = ctx.chain[ctx.chain.is_call & (ctx.chain.spread < 0.08)]
        c = c[c.dte.between(45, 75)] if deep else c[c.dte.between(21, 45)]
        if c.empty:
            return None

        return Plan(
            contract    = c.iloc[(c.delta - (0.50 if deep else 0.30)).abs().argmin()],
            risk_pct    = 0.03 if deep else 0.015,   # bet bigger in a deep drawdown
            take_profit = 1.00 if deep else 0.60,    # and aim higher
            stop_loss   = 0.50,
            max_hold    = 40   if deep else 20,      # and allow it more time
            tag         = "deep" if deep else "shallow",
        )

``tag`` travels with the trade into the log. Afterwards the two cases can be
grouped on it directly — which is exactly what classified research needs, and the
classification reflects the judgement actually made at order time rather than one
imposed after the fact.

## Boundaries

Plans are supported only by the event-driven engine. The trade-table engine treats
each trade independently and computes exits vectorised, so it cannot express "this
trade has these exit rules". It refuses loudly rather than returning an
approximation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import pandas as pd

from backtest.core.errors import StartupError
from backtest.core.types import require


@dataclass(frozen=True, slots=True)
class Plan:
    """One complete trade decision. ``None`` means nothing today."""

    #: The contract chosen — **one row** picked out of ``ctx.chain``.
    contract: pd.Series

    # ── size (6). At most one; with none, the strategy's default method applies ──
    contracts: int | None = None          # a number of contracts outright
    risk_pct: float | None = None         # the fraction of equity this trade may lose
    notional: float | None = None         # a fixed amount committed to this trade

    # ── exits (3)(4). Anything omitted falls back to the strategy's ExitRule ──
    take_profit: float | None = None
    stop_loss: float | None = None
    max_hold: int | None = None           # longest holding period (trading days)
    dte_floor: int | None = None          # minimum days to expiry (trading days)

    # ── the record ──
    #: A grouping tag. Stored with the trade, for classified research afterwards.
    tag: str = ""
    #: One sentence for a person: why this, at the time. Also stored with the trade.
    note: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.contract, pd.Series):
            raise StartupError(
                f"Plan.contract wants one row of ctx.chain (a Series), got "
                f"{type(self.contract).__name__}. After filtering the chain, take a "
                f"row with .iloc[i]."
            )
        given = [x is not None for x in (self.contracts, self.risk_pct, self.notional)]
        require(sum(given) <= 1,
                "give at most one of contracts / risk_pct / notional")
        if self.contracts is not None:
            require(self.contracts >= 1, "contracts must be at least 1")
        if self.risk_pct is not None:
            require(0 < self.risk_pct <= 1,
                    "risk_pct must lie between 0 and 1 (0.02 = 2%)")
        if self.notional is not None:
            require(self.notional > 0, "notional must be positive")
        if self.take_profit is not None:
            require(self.take_profit > 0, "take_profit must be positive")
        if self.stop_loss is not None:
            require(0 < self.stop_loss <= 1,
                    "stop_loss must lie between 0 and 1 (0.5 = out after losing 50%)")
        if self.max_hold is not None:
            require(self.max_hold >= 1, "max_hold must be at least 1 trading day")


@dataclass(frozen=True, slots=True)
class Context:
    """Everything visible at the moment of deciding.

    **All of it stops at the decision day's close** — tomorrow is not visible. The chain
    is the execution day's (the contract is only truly settled when the order fills on
    T+1), while the signals and portfolio state are the decision day's; see
    ``signals.contracts.enrich_chain``.

    That split used to be held by construction alone: the engine built the right objects
    and nothing stopped a plan from reaching past them. ``ctx.data`` is a
    :class:`~backtest.core.dataview.DataView` cut at the decision day, so a read of
    tomorrow now raises instead of succeeding quietly.
    """

    day: date
    #: Every contract selectable that day, with the derived columns already added
    #: (iv/delta/.../dte/spread/moneyness/is_call) and anything unexecutable on T+1
    #: already removed. Pick freely on greeks and IV without hitting one that
    #: cannot be opened.
    chain: pd.DataFrame
    #: The underlying's price that day (traded, not adjusted).
    spot: float
    #: Portfolio state, for (6) and (7).
    equity: float
    cash: float
    positions: int
    #: The current drawdown (negative, e.g. -0.08). Brakes and reductions read this.
    drawdown: float
    #: This slot's write handle into the trade context, or ``None``.
    #:
    #: A :class:`~backtest.core.trade_context.SlotWriter` **bound to one slot**, so a
    #: component can record what it decided and on what, and cannot record it anywhere
    #: but under its own name. ``None`` where the caller is not a slot -- a plan is not
    #: one slot, it is the coupling of several, and giving it a namespace would invite a
    #: record that cannot be attributed.
    write: Any = None
    _view: Any = None
    #: The data view, cut at the decision day. ``ctx.data`` is how a plan reads anything
    #: the other fields do not already hold — and a read past the decision day **raises**
    #: rather than returning a number from the future. The fields above are the common
    #: reads kept to hand; this is everything else, with the cut enforced rather than
    #: assumed.
    data: Any = None

    def signal(self, name: str, default: float | None = None) -> float:
        """Read a signal for the day.

        When absent: ``default`` is used if given, otherwise this raises. **It never
        quietly returns 0** — a drawdown read as 0 turns "buy after a fall of 8%"
        into "buy every day".
        """
        if self._view is None:
            raise StartupError("this context carries no signal view")
        got = self._view[name]
        if got.is_missing:
            if default is None:
                raise StartupError(
                    f"the signal {name!r} is unavailable on {self.day} ({got.reason}). "
                    f"Either give a default, ctx.signal({name!r}, default=...), or "
                    f"test for it in the plan and return None to skip the day."
                )
            return default
        return float(got.unwrap())

    @property
    def view(self):
        """The raw signal view, which returns ``Maybe``. Use it to handle absence
        yourself."""
        return self._view

    @property
    def calls(self) -> pd.DataFrame:
        return self.chain[self.chain["is_call"]]

    @property
    def puts(self) -> pd.DataFrame:
        return self.chain[self.chain["is_put"]]


#: A plan function: takes the whole state at the time and returns one complete
#: trade decision, or ``None`` for no trade.
Planner = Callable[[Context], "Plan | None"]
