"""Parameter sweeps.

This is the trade-table engine's main value: hundreds of parameter sets in seconds,
where stepping day by day would take hours.

But a sweep is **the main source of overfitting**. An option strategy naturally has
many parameter dimensions (DTE, delta, take profit, stop loss, holding period,
signal parameters), and ten years of data may hold only one or two hundred
independent trades; under those conditions the "optimal parameters" a sweep finds
are almost certainly noise. So the **number of trials** is recorded here by force —
the multiple-testing correction needs it, and nobody gets to pretend they tried once.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from backtest.core.config import BacktestConfig
from backtest.data.options.chain import ChainSource
from backtest.data.market import MarketData
from backtest.signals.strategy import ExitRule
from backtest.engine.strategy import Strategy
from backtest.engine.vectorized import TradeTableEngine
from backtest.core.errors import StartupError
from backtest.research import metrics as R
from backtest.signals.contracts import SelectionRule
from backtest.signals.registry import SignalSet

#: The sweepable parameters and which rule object each belongs to.
SWEEPABLE = {
    "target_dte": "select",
    "target_delta": "select",
    "moneyness": "select",
    "hold_days": "exit",
    "take_profit": "exit",
    "stop_loss": "exit",
    "dte_floor": "exit",
}


@dataclass(frozen=True, slots=True)
class SweepResult:
    """One row per parameter set, plus the number of trials."""

    table: pd.DataFrame
    trials: int
    grid: Mapping[str, Sequence[Any]]

    def top(self, n: int = 10, by: str = "net_pnl") -> pd.DataFrame:
        """The top n sets, ordered by one column.

        **This ranking is not itself a conclusion**: whether the gap between adjacent
        ranks exceeds the noise is a separate question. This only lays the results out.
        """
        return self.table.sort_values(by, ascending=False).head(n)


def _apply(strategy: Strategy, params: Mapping[str, Any]) -> Strategy:
    select_changes = {k: v for k, v in params.items() if SWEEPABLE[k] == "select"}
    exit_changes = {k: v for k, v in params.items() if SWEEPABLE[k] == "exit"}
    new_select: SelectionRule = strategy.select
    if select_changes and callable(new_select):
        # Once selection is a function, sweeping target_dte means nothing — that
        # number lives in the function, not in the configuration. Silently skipping it
        # would leave the impression it was swept, so this refuses.
        raise StartupError(
            f"selection is a custom function, so {sorted(select_changes)} cannot be "
            f"swept — those parameters are inside your function, not in the "
            f"configuration. To sweep selection parameters, go back to a declarative "
            f"SelectionRule, or make the quantity a parameter of your function and "
            f"loop outside."
        )
    if select_changes:
        if "target_dte" in select_changes:
            dte = select_changes["target_dte"]
            lo, hi = new_select.dte_window
            if not lo <= dte <= hi:
                select_changes["dte_window"] = (max(1, dte - 9), dte + 15)
        new_select = replace(new_select, **select_changes)
    new_exit: ExitRule = replace(strategy.exit, **exit_changes) if exit_changes else strategy.exit
    return replace(strategy, select=new_select, exit=new_exit)


def sweep(
    market: MarketData,
    chain: ChainSource,
    signals: SignalSet,
    config: BacktestConfig,
    strategy: Strategy,
    grid: Mapping[str, Sequence[Any]],
) -> SweepResult:
    """Run every set in the grid; each is independent and deterministic."""
    unknown = sorted(set(grid) - set(SWEEPABLE))
    if unknown:
        raise StartupError(f"parameters {unknown} cannot be swept; sweepable: "
                           f"{sorted(SWEEPABLE)}")
    if not grid:
        raise StartupError("the parameter grid is empty")

    names = sorted(grid)
    rows: list[dict[str, Any]] = []
    for combo in itertools.product(*(grid[name] for name in names)):
        params = dict(zip(names, combo))
        variant = _apply(strategy, params)
        result = TradeTableEngine(market, chain, signals, config).run(variant)
        trades = result.trades
        summary = R.trade_stats(trades)
        rows.append(
            {
                **params,
                "count": summary["count"],
                "win_rate": summary.get("win_rate", float("nan")),
                "net_pnl": summary.get("net_pnl", 0.0),
                "avg_pnl": summary.get("net_pnl", 0.0) / summary["count"]
                if summary["count"]
                else float("nan"),
                "median_pnl": float(trades["net_pnl"].median()) if len(trades) else float("nan"),
                "peak_positions": result.capital_check()["peak_positions"],
            }
        )
    return SweepResult(pd.DataFrame(rows), trials=len(rows), grid=dict(grid))


@dataclass(frozen=True, slots=True)
class Plateau:
    """How much of the neighbourhood around the best result also works.

    This is the number to read instead of the peak. A result standing alone is the one
    that fitted this history; a result surrounded by results nearly as good is a shape the
    next period might also have — and a live parameter never lands exactly on the peak, so
    only the shape is usable.
    """

    best: Mapping[str, Any]
    best_value: float
    #: Neighbours within one step on every swept axis.
    neighbours: int
    #: Of those, how many stayed within ``tolerance`` of the best.
    holding: int
    tolerance: float
    #: The worst neighbour, which is what a parameter landing one step off would give.
    worst_neighbour: float

    @property
    def share(self) -> float:
        return self.holding / self.neighbours if self.neighbours else float("nan")

    def verdict(self) -> str:
        from backtest.core.thresholds import get

        plateau_at, slope_at = get("plateau_share"), get("slope_share")
        if not self.neighbours:
            return ("the best point has no neighbours in this grid, so nothing can be "
                    "said about whether it is a plateau or a spike. Sweep a finer grid")
        if self.share >= plateau_at:
            read = (f"a plateau at the {plateau_at:.0%} cut: the region works, not just "
                    f"the point")
        elif self.share >= slope_at:
            read = f"a slope at the {slope_at:.0%} cut — usable, but the edge of it is not"
        else:
            read = ("**a spike**: the neighbours do not hold, so this is the point that "
                    "fitted this history")
        return (f"best {self.best_value:,.0f} at {dict(self.best)}; "
                f"{self.holding} of {self.neighbours} neighbours within "
                f"{self.tolerance:.0%} ({self.share:.0%}), worst neighbour "
                f"{self.worst_neighbour:,.0f} — {read}")


def surface(table: pd.DataFrame, x: str, y: str, metric: str = "net_pnl") -> pd.DataFrame:
    """Two swept parameters as a grid, for seeing the shape rather than the ranking.

    A sorted table hides the thing that matters. Adjacent cells that all work are a
    region; one cell far above its neighbours is noise that happened to win.
    """
    for col in (x, y, metric):
        if col not in table.columns:
            raise StartupError(f"the sweep table has no {col!r}; it has "
                               f"{sorted(table.columns)}")
    return table.pivot_table(index=x, columns=y, values=metric, aggfunc="mean")


def plateau(
    table: pd.DataFrame,
    *,
    metric: str = "net_pnl",
    tolerance: float | None = None,
    axes: Sequence[str] | None = None,
) -> Plateau:
    """Measure the neighbourhood of the best result.

    ``axes`` defaults to every swept parameter in the table. A neighbour is a point one
    grid step away on each axis; "within ``tolerance``" is relative to the best value's
    magnitude, so it means the same thing whether the metric is in hundreds or millions.

    What this cannot do is tell a plateau from a spike on a grid of one point per axis.
    It says so rather than returning a number, because a confident answer from a grid too
    coarse to support one is worse than no answer.
    """
    from backtest.core.thresholds import get

    if tolerance is None:
        tolerance = get("plateau_tolerance")
    if metric not in table.columns:
        raise StartupError(f"the sweep table has no {metric!r}")
    swept = list(axes) if axes else [c for c in table.columns if c in SWEEPABLE]
    if not swept:
        raise StartupError(
            f"no swept parameter found in the table. Columns: {sorted(table.columns)}"
        )
    usable = table.dropna(subset=[metric])
    if usable.empty:
        raise StartupError(f"every row has an empty {metric!r}")

    best_row = usable.loc[usable[metric].idxmax()]
    best = {c: best_row[c] for c in swept}
    best_value = float(best_row[metric])

    # One grid step per axis, taken from the distinct values actually swept rather than
    # assumed — a sweep is often uneven (0.3, 0.5, 1.0) and assuming a constant step
    # would call distant points neighbours.
    steps = {}
    for c in swept:
        vals = sorted(set(usable[c].dropna()))
        pos = vals.index(best[c]) if best[c] in vals else None
        steps[c] = {vals[i] for i in (pos - 1, pos, pos + 1)
                    if pos is not None and 0 <= i < len(vals)}

    mask = pd.Series(True, index=usable.index)
    for c in swept:
        mask &= usable[c].isin(steps[c])
    near = usable.loc[mask]
    near = near.loc[~(near[swept] == pd.Series(best)).all(axis=1)]

    if near.empty:
        return Plateau(best=best, best_value=best_value, neighbours=0, holding=0,
                       tolerance=tolerance, worst_neighbour=float("nan"))

    scale = max(abs(best_value), 1.0)
    holding = int(((best_value - near[metric]).abs() / scale <= tolerance).sum())
    return Plateau(best=best, best_value=best_value, neighbours=len(near),
                   holding=holding, tolerance=tolerance,
                   worst_neighbour=float(near[metric].min()))
