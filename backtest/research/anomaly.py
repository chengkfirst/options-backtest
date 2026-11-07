"""Anomaly scanning: picking out what is genuinely out of the ordinary in a result.

## The design choice: rules, not an LLM

Rules with fixed thresholds do the scanning; a model is never asked to "see what
looks interesting". Three reasons:

1. **A rule is testable** — each one can be given a counterexample that proves it
   really fires.
2. **A rule does not invent** — faced with a perfectly normal result, a model still
   tends to say something.
3. The real problems found by hand so far (a take-profit level that almost never
   fires, a conditional probability equal to the unconditional baseline, greek-ratio
   rankings that all land on the edge of the feasible region) are fixed patterns,
   and rules catch them.

A model is good at putting what was found into plain language, not at the finding.

## Nothing triggered means nothing said

Every rule has a threshold. An "insight" forced out of nothing is noise, and noise
is what makes real warnings get skipped.

## Where the thresholds live

Not here. Every cut is in :mod:`backtest.core.thresholds`, in the architecture document's
"method" group — chosen before looking at results, not after. That matters for this module
in particular: a rule whose threshold could be nudged after seeing a result it fired on
would be a rule that never fires inconveniently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from backtest.core.thresholds import get as _cut


@dataclass(frozen=True, slots=True)
class Finding:
    """One finding. ``severity``: ``high`` is worth stopping for, ``note`` is worth
    knowing."""

    kind: str
    severity: str
    headline: str
    detail: str


def scan(
    trades: pd.DataFrame,
    *,
    equity: "pd.DataFrame | None" = None,
    metrics: "dict[str, Any] | None" = None,
    costs: "dict[str, Any] | None" = None,
) -> list[Finding]:
    """Scan once and return what fired. Nothing triggered returns an empty list."""
    if trades is None or trades.empty:
        return []
    out: list[Finding] = []
    for rule in (_single_trade_dominates, _one_year_carries, _edge_decays,
                 _exit_never_fires, _costs_eat_edge, _positions_overlap,
                 _too_good_for_the_sample, _all_losers_same_reason):
        try:
            found = rule(trades, equity=equity, metrics=metrics, costs=costs)
        except Exception as exc:  # noqa: BLE001
            # One rule failing must not kill the scan -- and it must not vanish either.
            # A scan that came back with three findings when one rule could not run looks
            # exactly like a scan where that rule found nothing, and "found nothing" is
            # the answer a reader acts on. The failure is reported as a finding of its
            # own, in the same list, so it cannot be missed by someone reading the result
            # rather than the logs.
            out.append(Finding(
                kind="rule_failed",
                severity="high",
                headline=f"the {rule.__name__.lstrip('_')} check could not run",
                detail=(f"{type(exc).__name__}: {exc}. This is not a clean scan -- one "
                        f"rule produced no answer, which is not the same as finding "
                        f"nothing. Whatever it looks for has not been checked."),
            ))
            continue
        if found:
            out.append(found)
    return sorted(out, key=lambda f: 0 if f.severity == "high" else 1)


# ── the rules ───────────────────────────────────────────────

def _single_trade_dominates(t, **_) -> "Finding | None":
    """One trade carries most of the P&L — that is not a strategy, it is one piece
    of luck."""
    total = t["net_pnl"].sum()
    if total <= 0 or len(t) < 10:
        return None
    top = t["net_pnl"].max()
    share = top / total
    if share < _cut("single_trade_share"):
        return None
    row = t.loc[t["net_pnl"].idxmax()]
    return Finding(
        "single_trade", "high",
        f"one trade contributed {share:.0%} of the total P&L",
        f"the trade entered {row['entry_date']} made {top:,.0f}, against "
        f"{total:,.0f} across all {len(t)} trades. What the strategy looks like "
        f"without it is worth a separate look.",
    )


def _one_year_carries(t, **_) -> "Finding | None":
    """All the return came from one year — change the environment and it may be
    gone."""
    if len(t) < 30:
        return None
    by_year = t.groupby(pd.to_datetime(t["exit_date"]).dt.year)["net_pnl"].sum()
    if len(by_year) < 3:
        return None
    total = by_year.sum()
    # With total P&L near zero, "one year is 101% of it" means nothing — the
    # denominator is too small for a ratio to carry meaning. Requiring the total to
    # be at least three tenths of the sum of absolute yearly figures makes the
    # comparison stand up.
    scale = by_year.abs().sum()
    if total <= 0 or scale <= 0 or total < scale * 0.30:
        return None
    best_year = by_year.idxmax()
    share = by_year.max() / total
    if share < _cut("one_year_share"):
        return None
    others = by_year.drop(best_year)
    headline = (f"{best_year} contributed the entire P&L; the other years lost money"
                if share > 1.0 else f"{best_year} contributed {share:.0%} of the P&L")
    return Finding(
        "one_year", "high", headline,
        f"the other {len(others)} years total {others.sum():,.0f}, of which "
        f"{int((others < 0).sum())} lost money. This strategy may only hold in one "
        f"kind of environment.",
    )


def _edge_decays(t, **_) -> "Finding | None":
    """The win rate differs sharply between halves — the edge may be decaying, or
    the first half may be overfitted."""
    if len(t) < 60:
        return None
    half = len(t) // 2
    a = float((t["net_pnl"].iloc[:half] > 0).mean())
    b = float((t["net_pnl"].iloc[half:] > 0).mean())
    if abs(a - b) < _cut("win_rate_decay"):
        return None
    worse = b < a
    return Finding(
        "decay", "high" if worse else "note",
        f"win rate {a:.0%} in the first half, {b:.0%} in the second",
        "a gap of more than 20 percentage points. " + (
            "The second half is worse, which may be a decaying edge or a "
            "particularly favourable environment in the first half."
            if worse else "The second half is better; first confirm it is not just a "
                          "small sample."),
    )


def _exit_never_fires(t, **_) -> "Finding | None":
    """One exit condition almost never fires — that parameter is decoration."""
    if len(t) < 30 or "exit_reason" not in t:
        return None
    share = t["exit_reason"].value_counts(normalize=True)
    dist = {k: round(float(v), 2) for k, v in share.items()}
    for reason in ("take_profit", "stop_loss"):
        if reason not in share.index:
            return Finding(
                "dead_exit", "high",
                f"{reason} never fired once",
                f"this level does nothing at all; the settings beside it are deciding "
                f"in its place. Current distribution: {dist}",
            )
        if share[reason] < 0.05:
            n = int((t["exit_reason"] == reason).sum())
            return Finding(
                "dead_exit", "high",
                f"{reason} fired only {n} times ({share[reason]:.0%})",
                f"this level is set too far away to have any effect. Current "
                f"distribution: {dist}",
            )
    return None


def _costs_eat_edge(t, *, costs=None, **_) -> "Finding | None":
    """Costs are too large a share of the edge before costs.

    **This rule was blind to the option buyer's largest cost.** It read
    `fees_pct_of_gross`, and the spread is not a fee -- it is paid inside the fill, so it
    could never be in the numerator. On this project's own fixture the spread is several
    times the commission, which means the one rule that exists to say "the edge will not
    survive execution" was measuring the smaller half.

    Two readings now, because they fail differently:

    - **the edge before costs is positive and the costs take too much of it.** Measured
      against gross rebuilt at the mid, so the spread is in the numerator and not in the
      denominator. The old ratio had the spread in neither.
    - **the edge before costs is not positive at all.** Then there is no ratio to compare
      and the old rule returned nothing -- the quietest possible outcome for the worst
      possible case. Reported as its own finding.
    """
    if not costs:
        return None
    total = float(costs.get("total", 0.0) or 0.0)
    at_mid = costs.get("gross_pnl_at_mid")

    if at_mid is not None and float(at_mid) <= 0:
        return Finding(
            "costs", "high",
            "there is no edge for the costs to eat: gross P&L is not positive even at "
            "the mid",
            f"before any cost, filling every order at the mid, the strategy makes "
            f"{float(at_mid):,.0f}. Costs of {total:,.0f} are then beside the point -- "
            f"the entry and exit rules are not finding anything, and no cost assumption "
            f"will change that.",
        )

    pct = costs.get("cost_pct_of_gross_at_mid")
    if pct is None or not np.isfinite(pct) or pct < _cut("cost_share_warning"):
        return None
    spread = costs.get("spread")
    share = (f" The spread is {float(spread) / total:.0%} of it"
             if spread is not None and total else "")
    return Finding(
        "costs", "high",
        f"costs ate {pct:.0%} of the edge before costs",
        f"costs total {total:,.0f} against {float(costs['gross_pnl_at_mid']):,.0f} of "
        f"gross P&L at the mid, which is {pct:.0%}.{share}, and the spread is the cost "
        f"that is paid inside the fill rather than charged as a fee. Live costs are "
        f"usually above backtest costs, so at this ratio the edge is unlikely to survive "
        f"real execution.",
    )


def _positions_overlap(t, **_) -> "Finding | None":
    """The positions overlap heavily — the nominal trade count is inflated and they
    are not independent observations."""
    if len(t) < 20:
        return None
    spans = pd.DataFrame({
        "a": pd.to_datetime(t["entry_date"]),
        "b": pd.to_datetime(t["exit_date"]),
    }).sort_values("a")
    count, last_end = 0, None
    for _, r in spans.iterrows():
        if last_end is None or r["a"] >= last_end:
            count += 1
            last_end = r["b"]
    if count >= len(t) * 0.5:
        return None
    return Finding(
        "overlap", "note",
        f"only {count} of {len(t)} trades do not overlap",
        "positions held at the same time move together and are not independent "
        "observations. A win rate or a confidence interval computed on the nominal "
        "count is optimistic.",
    )


def _too_good_for_the_sample(t, *, metrics=None, **_) -> "Finding | None":
    """A high Sharpe on a small sample — usually luck, not an edge."""
    if not metrics:
        return None
    sharpe = metrics.get("sharpe")
    if sharpe is None or not np.isfinite(sharpe) or sharpe < 2.0 or len(t) > 100:
        return None
    return Finding(
        "too_good", "high",
        f"a Sharpe of {sharpe:.1f} on only {len(t)} trades",
        "at this sample size a Sharpe that high is more likely luck. Lengthen the "
        "range or change the underlying first, and see whether it survives.",
    )


def _all_losers_same_reason(t, **_) -> "Finding | None":
    """Nearly all the losses share one exit reason — that one rule is driving the
    result."""
    if len(t) < 30 or "exit_reason" not in t:
        return None
    losers = t[t["net_pnl"] <= 0]
    if len(losers) < 10:
        return None
    share = losers["exit_reason"].value_counts(normalize=True)
    top, pct = share.index[0], float(share.iloc[0])
    if pct < 0.85:
        return None
    return Finding(
        "loss_concentration", "note",
        f"{pct:.0%} of losing trades exited on {top}",
        f"{int(pct * len(losers))} of {len(losers)} losses share one reason. Changing "
        f"this level will move the result far more than changing any other.",
    )
