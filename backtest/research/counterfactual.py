"""Counterfactual exits: what a different exit would have done.

## The question these answer

The MFE/MAE distributions say where the price went. They do not say what a different rule
would have *earned*, because an exit changes the path: a wider stop keeps a trade alive
into days the actual run never saw.

So the comparison needs two things the engine must have collected while running:

- the **path while held** (``paths``), for an alternative exit that fires *earlier* than
  the real one
- the **shadow after the exit** (``shadow``), for one that fires *later*

Neither can be reconstructed afterwards, which is why the engine collects both by default.

## The two honest limits, stated before the numbers

**One: this is in-sample by construction.** The alternative exit is being chosen after
seeing the path it will be scored on. A level that wins here wins because it fitted these
trades. The architecture document is explicit: anything a counterfactual suggests must be
confirmed out of sample before adoption. :attr:`Replay.in_sample` carries that, and
:func:`report` prints it beside every result rather than once at the top.

**Two: the window is finite.** An alternative exit that never fires within the path plus
the shadow has an **unknown** outcome, not a zero one. Those trades are counted as
``censored`` and excluded from the totals. A tool that assumed "it would have closed at the
end of the window" would quietly bias every wider stop towards whatever the window
happened to contain — and the longer the stop, the worse the bias, which is precisely the
direction you are trying to measure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Replay:
    """One alternative exit, replayed over every trade it could be replayed on."""

    name: str
    #: Net P&L of the real exits, over the trades this replay could score.
    actual: float
    #: Net P&L the alternative would have produced on those same trades.
    alternative: float
    scored: int
    #: Trades whose alternative exit never fired inside the observed window. Their
    #: outcome is unknown and they are in neither total.
    censored: int
    #: Trades with no usable path at all.
    skipped: int = 0
    #: Always True, and printed. A counterfactual chooses its rule after seeing the path.
    in_sample: bool = True

    @property
    def difference(self) -> float:
        """Positive means the alternative would have earned more."""
        return self.alternative - self.actual

    def verdict(self) -> str:
        if not self.scored:
            return f"{self.name}: nothing could be scored ({self.censored} censored)"
        from backtest.core.thresholds import get

        line = (f"{self.name}: {self.difference:+,.0f} against the real exits "
                f"over {self.scored} trades")
        if self.censored:
            share = self.censored / (self.scored + self.censored)
            line += (f"; **{self.censored} censored** ({share:.0%}) — their outcome is "
                     f"unknown, not zero")
            if share > get("censored_share_warning"):
                line += ", so this number rests on the minority that did close"
        return line


def _price_path(trade_id: int, paths: pd.DataFrame, shadow: pd.DataFrame) -> pd.DataFrame:
    """The whole observed path for one trade: while held, then after the exit.

    Sorted by date, with ``held`` saying which side of the exit each row is on — an
    alternative exit that fires while held is observed directly, and one that fires after
    is observed only because the shadow was collected.
    """
    held = paths.loc[paths["trade_id"] == trade_id, ["date", "ratio"]].copy()
    held["held"] = True
    after = shadow.loc[shadow["trade_id"] == trade_id, ["date", "ratio"]].copy()
    after["held"] = False
    both = pd.concat([held, after], ignore_index=True)
    return both.sort_values("date").reset_index(drop=True)


def replay_exit(
    trades: pd.DataFrame,
    paths: pd.DataFrame,
    shadow: pd.DataFrame,
    *,
    take_profit: float | None = None,
    stop_loss: float | None = None,
    name: str | None = None,
) -> Replay:
    """Replay every trade under one alternative take-profit and stop-loss pair.

    ``take_profit`` and ``stop_loss`` are fractions of the entry price, as in ``ExitRule``.
    ``None`` means that side is off. The first of the two to be touched on the observed
    path wins, and when both are touched on the same day the **stop** does — the same
    conservative convention the engine uses, because a daily bar cannot say which came
    first and assuming the better one flatters the result.

    A trade whose alternative never fires is censored, never assumed to close.
    """
    if take_profit is None and stop_loss is None:
        raise ValueError("replay_exit needs a take_profit or a stop_loss to replay")
    label = name or " and ".join(
        part for part in (
            f"take profit at +{take_profit:.0%}" if take_profit is not None else "",
            f"stop at -{stop_loss:.0%}" if stop_loss is not None else "",
        ) if part)

    actual = alternative = 0.0
    scored = censored = skipped = 0
    for _, t in trades.iterrows():
        tid = int(t["trade_id"])
        path = _price_path(tid, paths, shadow)
        if path.empty or not np.isfinite(t.get("net_pnl", np.nan)):
            skipped += 1
            continue

        # The entry cost, so a ratio can be turned back into money. quantity x
        # multiplier x entry price is what was paid.
        at_risk = float(t["entry_price"]) * float(t["quantity"]) * float(t["multiplier"])

        hit = None
        for _, row in path.iterrows():
            r = float(row["ratio"])
            if stop_loss is not None and r <= -stop_loss:
                hit = -stop_loss
                break
            if take_profit is not None and r >= take_profit:
                hit = take_profit
                break
        if hit is None:
            censored += 1
            continue

        scored += 1
        actual += float(t["net_pnl"])
        # The alternative's gross, less the fees the real trade paid: a different exit
        # level does not change the commission, and inventing a fee model here would add
        # a second difference to a comparison meant to isolate one.
        alternative += hit * at_risk - float(t.get("fees", 0.0))

    return Replay(name=label, actual=actual, alternative=alternative, scored=scored,
                  censored=censored, skipped=skipped)


def sweep_levels(
    trades: pd.DataFrame,
    paths: pd.DataFrame,
    shadow: pd.DataFrame,
    *,
    take_profits: Sequence[float],
    stop_losses: Sequence[float],
) -> pd.DataFrame:
    """Every take-profit against every stop, as a grid of differences from the real exits.

    **The levels are required, not defaulted.** Which exit levels are worth exploring is a
    strategy choice — group 3 in the architecture document's register — and the document
    permits optimising those under its protocol, not the platform picking them. A default
    here would decide the search on your behalf and then present the winner as a finding.

    **Read the plateau, not the peak.** A single cell that stands out is the cell that
    fitted these trades; a region of adjacent cells that all improve is a shape the next
    period might also have. The censored share is reported per cell for the same reason —
    a cell that only scored a third of the trades is not comparable with one that scored
    all of them.
    """
    if not len(take_profits) or not len(stop_losses):
        raise ValueError(
            "sweep_levels needs the levels to explore. They are a strategy choice "
            "(the document's group 3), so there is no default: a default would decide the "
            "search and then report its winner as a result"
        )
    rows = []
    for tp in take_profits:
        for sl in stop_losses:
            r = replay_exit(trades, paths, shadow, take_profit=tp, stop_loss=sl)
            total = r.scored + r.censored
            rows.append({
                "take_profit": tp, "stop_loss": sl,
                "difference": r.difference if r.scored else np.nan,
                "scored": r.scored,
                "censored_share": r.censored / total if total else np.nan,
            })
    return pd.DataFrame(rows)


def report(replays: Sequence[Replay]) -> str:
    """The replays, best first, each carrying its own caveats."""
    usable = [r for r in replays if r.scored]
    if not usable:
        return ("no alternative exit could be scored. Either the engine ran without "
                "shadow tracking, or every alternative fell outside the observed window.")
    lines = ["Counterfactual exits", "─" * 68,
             "  In-sample by construction: each level was chosen after seeing the path it",
             "  is scored on. Confirm anything promising out of sample before adopting it.",
             ""]
    for r in sorted(usable, key=lambda x: -x.difference):
        lines.append(f"  {r.verdict()}")
    skipped = sum(r.skipped for r in replays)
    if skipped:
        lines += ["", f"  {skipped} trade-replays had no usable path "
                      f"(the trade-table engine collects none)."]
    return "\n".join(lines)
