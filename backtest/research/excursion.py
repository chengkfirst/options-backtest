"""MFE / MAE: where the take-profit and the stop belong.

**Maximum favourable excursion (MFE)** is the furthest a trade ever ran in profit
while held; **maximum adverse excursion (MAE)** is the deepest it ever ran under.
Their distributions answer two questions directly:

- if four in five trades never ran past +60%, a take-profit at +100% is waiting
  for something that rarely arrives
- if half the eventual winners were once down more than -40%, a stop at -30% cuts
  all of them off

This is far more direct than sweeping parameters: **a sweep tries thresholds and
keeps whichever scored best, while MFE/MAE looks at where the price actually
went.** The former overfits to the particular historical path; the latter is a
fact about the distribution.

The data can only be collected while the position is held and cannot be
reconstructed afterwards — which is why the event-driven engine records it daily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

from backtest.research.boundary import Boundary, Scope, header

#: The default levels examined, as fractions of the entry price.
PROFIT_LEVELS = (0.25, 0.50, 0.75, 1.00, 1.50, 2.00)
LOSS_LEVELS = (0.20, 0.30, 0.40, 0.50, 0.60, 0.80)


@dataclass(frozen=True, slots=True)
class ExcursionStudy:
    frame: pd.DataFrame          # one row per trade: mfe / mae / net P&L / won
    n: int
    censoring: tuple[str, ...] = ()   # warnings that the current rules censor it

    def profit_target_table(self, levels: Sequence[float] = PROFIT_LEVELS) -> pd.DataFrame:
        """What fraction of trades **ever reached** each take-profit level.

        A reach rate is not the return from exiting there — the price may keep
        rising after it is touched. This table answers "is this target realistic",
        not "what does this target earn". The latter needs a backtest.
        """
        rows = []
        for lv in levels:
            hit = self.frame["mfe"] >= lv
            rows.append({
                "level": f"+{lv:.0%}",
                "reached": int(hit.sum()),
                "reach rate": float(hit.mean()),
                "mean final P&L, reached": float(self.frame.loc[hit, "net_pnl"].mean()) if hit.any() else np.nan,
                "mean final P&L, not reached": float(self.frame.loc[~hit, "net_pnl"].mean()) if (~hit).any() else np.nan,
            })
        return pd.DataFrame(rows).set_index("level")

    def stop_loss_table(self, levels: Sequence[float] = LOSS_LEVELS) -> pd.DataFrame:
        """How many **eventual winners** each stop level would cut off.

        This is a stop's real cost: it cuts losses, and it also cuts the winners
        that fell deep before coming back.
        """
        winners = self.frame["net_pnl"] > 0
        rows = []
        for lv in levels:
            touched = self.frame["mae"] <= -lv
            killed = touched & winners
            rows.append({
                "level": f"-{lv:.0%}",
                "reached": int(touched.sum()),
                "reach rate": float(touched.mean()),
                "of which were winners": int(killed.sum()),
                "profit cut off": float(self.frame.loc[killed, "net_pnl"].sum()),
                "loss avoided": float(self.frame.loc[touched & ~winners, "net_pnl"].sum()),
            })
        return pd.DataFrame(rows).set_index("level")

    def summary(self) -> dict[str, Any]:
        f = self.frame
        winners, losers = f[f["net_pnl"] > 0], f[f["net_pnl"] <= 0]
        return {
            "n": self.n,
            "mfe_median": float(f["mfe"].median()),
            "mfe_q75": float(f["mfe"].quantile(0.75)),
            "mfe_q90": float(f["mfe"].quantile(0.90)),
            "mae_median": float(f["mae"].median()),
            "mae_q25": float(f["mae"].quantile(0.25)),
            "winners_mae_median": float(winners["mae"].median()) if len(winners) else np.nan,
            "losers_mfe_median": float(losers["mfe"].median()) if len(losers) else np.nan,
        }

    #: MFE and MAE are premium excursions, so they are option-side throughout.
    scope = Scope.OPTION

    def text(self, *, synthetic: bool) -> str:
        """The distributions, with the boundary stated before them.

        ``synthetic`` is required. These distributions are the usual way a take-profit or a
        stop gets set, which makes an unlabelled one the most costly thing in this module:
        a level read off a constant-IV chain is a level fitted to Black-Scholes.
        """
        s = self.summary()
        warn = ["", "!! The distribution is censored; the conclusions below cannot "
                    "be used to set levels directly:"] + \
               [f"   {c}" for c in self.censoring] if self.censoring else []
        return "\n".join([
            f"MFE / MAE study · {s['n']} trades",
            "─" * 56,
            *header(Boundary(self.scope, synthetic), what="a premium excursion"),
            f"  furthest in profit (MFE)  median {s['mfe_median']:+.0%}"
            f"  75th {s['mfe_q75']:+.0%}  90th {s['mfe_q90']:+.0%}",
            f"  deepest under (MAE)       median {s['mae_median']:+.0%}"
            f"  25th {s['mae_q25']:+.0%}",
            "",
            f"  eventual winners ran down to a median of {s['winners_mae_median']:+.0%}"
            f"   <- a stop tighter than this cuts off half the winners",
            f"  eventual losers ran up to a median of {s['losers_mfe_median']:+.0%}"
            f"   <- a take-profit looser than this gives that back",
            *warn,
        ])


def study(trades: pd.DataFrame, exit_rule=None) -> ExcursionStudy:
    """Build an MFE/MAE study from a trade log.

    Needs the ``mfe`` and ``mae`` columns, which only the event-driven engine
    collects. The trade-table engine treats each trade independently and does not
    step day by day, so it cannot collect them; this raises rather than returning
    an empty result.

    **Pass ``exit_rule`` to detect censoring.** This is the trap in this module: if
    the run had its stop at -50%, no trade could ever run to -60%, and the MAE
    distribution is cut off flat at -50%. Using that distribution to set the stop
    is circular — all you can see is that the stop is reasonable where it already is.

    To study where a stop belongs, first run with the stop **very wide or off**, so
    the price walks its own path. The same goes for a take-profit.
    """
    for col in ("mfe", "mae"):
        if col not in trades.columns:
            raise KeyError(
                f"the trade log has no {col} column. MFE/MAE can only be collected "
                f"while a position is held, so rerun with engine=\"event_driven\" — "
                f"the trade table cannot collect it."
            )
    if len(trades) and trades["mfe"].isna().all():
        # The columns exist but are entirely NaN, which is what the trade-table
        # engine produces. Saying "no usable trades" would mislead, suggesting the
        # strategy never filled; the real reason is that this engine cannot
        # collect daily excursions.
        raise KeyError(
            f"all {len(trades)} trades have empty mfe/mae — they came from the "
            f"trade-table engine, which does not step day by day and cannot observe "
            f"excursions while a position is held. Rerun with "
            f"engine=\"event_driven\"."
        )
    frame = trades.dropna(subset=["mfe", "mae", "net_pnl"]).copy()
    if frame.empty:
        raise ValueError("no trades usable for an MFE/MAE study")

    censoring: list[str] = []
    if exit_rule is not None:
        if getattr(exit_rule, "stop_loss", None) is not None:
            censoring.append(
                f"a stop loss at -{exit_rule.stop_loss:.0%} was in force: anything "
                f"reaching it was cut, so the MAE distribution is censored there. To "
                f"set a stop, first rerun with the stop very wide or off."
            )
        if getattr(exit_rule, "take_profit", None) is not None:
            censoring.append(
                f"a take profit at +{exit_rule.take_profit:.0%} was in force: "
                f"anything reaching it was closed, so the MFE distribution is "
                f"censored there. To set a take profit, first rerun with it very "
                f"high or off."
            )
        if getattr(exit_rule, "hold_days", None) is not None:
            censoring.append(
                f"a holding cap of {exit_rule.hold_days} trading days: longer paths "
                f"were never observed."
            )
    return ExcursionStudy(frame=frame, n=len(frame), censoring=tuple(censoring))
