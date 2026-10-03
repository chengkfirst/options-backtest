"""Splitting time so a fit is never scored on what it saw.

## Walk-forward

Fit on a window, score on the window after it, roll forward. Two modes:

- **rolling** — the fit window is a fixed length. It adapts to a changing market and
  estimates noisily, because it always has the same small sample.
- **expanding** — the fit window grows from the start. It estimates more stably and lags a
  changing market, because old data keeps its vote.

Neither is correct in general, and the choice is a research-method trade-off (section 8,
group 4), so it is set before the research rather than after seeing the results.

## Purged k-fold, and why options need it

Ordinary k-fold assumes a sample's information ends at its own timestamp. An option trade's
does not: a trade entered on Monday and held three weeks carries information about those
three weeks. Put Monday in the training fold and a day inside that holding period in the
test fold, and the test is being scored on something the fit already knew.

Purging removes the training samples whose holding period overlaps the test fold. An
**embargo** removes a few more immediately after it, because the market itself is
autocorrelated across the boundary even where no single trade spans it.

This is why the overlap matters so much here: with holding periods of weeks and a handful
of trades a month, an unpurged fold leaks almost everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, Sequence

import pandas as pd


@dataclass(frozen=True, slots=True)
class Fold:
    """One split: what may be fitted on, and what it is scored on."""

    index: int
    train: tuple[date, date]
    test: tuple[date, date]
    #: Samples dropped from training because their information reached into the test fold.
    purged: int = 0

    def __str__(self) -> str:
        a, b = self.train
        c, d = self.test
        tail = f", {self.purged} purged" if self.purged else ""
        return f"fold {self.index}: fit {a}–{b}, score {c}–{d}{tail}"


def walk_forward(
    sessions: Sequence[date],
    *,
    train: int,
    test: int,
    mode: Literal["rolling", "expanding"] = "rolling",
    step: int | None = None,
) -> list[Fold]:
    """Folds over ``sessions``, in trading days.

    ``step`` defaults to ``test``, which makes the test windows contiguous and
    non-overlapping — the only arrangement in which every day is scored once. A smaller
    step reuses test data across folds, so the folds stop being independent and an average
    over them is narrower than it looks.
    """
    if train < 1 or test < 1:
        raise ValueError(f"train and test must be at least 1 day, got {train} and {test}")
    step = step or test
    days = list(sessions)
    if len(days) < train + test:
        raise ValueError(
            f"{len(days)} sessions cannot hold a {train}-day fit and a {test}-day score. "
            f"Shorten the windows or lengthen the period — this is not a case to work "
            f"around with a partial fold, which would be scored on less data than the "
            f"others and quietly carry more weight per day"
        )

    folds: list[Fold] = []
    start = 0
    while start + train + test <= len(days):
        fit_from = 0 if mode == "expanding" else start
        fit_to = start + train - 1
        folds.append(Fold(
            index=len(folds),
            train=(days[fit_from], days[fit_to]),
            test=(days[fit_to + 1], days[min(fit_to + test, len(days) - 1)]),
        ))
        start += step
    return folds


def purged_kfold(
    entries: Sequence[date],
    exits: Sequence[date],
    *,
    k: int = 5,
    embargo_days: int = 0,
) -> list[Fold]:
    """``k`` folds over trades, purging the training samples that overlap each test fold.

    ``entries`` and ``exits`` are each trade's dates, so the holding period is known. A
    training trade is purged when **any** part of its holding period falls inside the test
    fold, or inside the embargo after it.

    The purge count is reported on every fold. A fold that purged most of its training data
    is telling you the trades overlap so heavily that there is almost no independent sample
    here — which is a finding, not a nuisance.
    """
    if len(entries) != len(exits):
        raise ValueError(f"{len(entries)} entries against {len(exits)} exits")
    if k < 2:
        raise ValueError(f"{k} folds cannot separate a fit from a score")
    n = len(entries)
    if n < k:
        raise ValueError(f"{n} trades cannot fill {k} folds")

    order = sorted(range(n), key=lambda i: entries[i])
    size, folds = n // k, []
    for f in range(k):
        lo = f * size
        hi = n if f == k - 1 else (f + 1) * size
        test_idx = order[lo:hi]
        test_from = min(entries[i] for i in test_idx)
        test_to = max(exits[i] for i in test_idx) + timedelta(days=embargo_days)

        purged = 0
        train_dates = []
        for i in order[:lo] + order[hi:]:
            # Overlap, not containment: a trade that merely reaches into the test fold
            # still carries information about it.
            if entries[i] <= test_to and exits[i] >= test_from:
                purged += 1
                continue
            train_dates.append(entries[i])
        if not train_dates:
            raise ValueError(
                f"fold {f} purged every training trade. The holding periods overlap the "
                f"whole period, so there is no independent sample to fit on — reduce k, "
                f"shorten the holding period, or accept that this data cannot support "
                f"k-fold"
            )
        folds.append(Fold(
            index=f,
            train=(min(train_dates), max(train_dates)),
            test=(test_from, max(exits[i] for i in test_idx)),
            purged=purged,
        ))
    return folds


def describe(folds: Sequence[Fold]) -> pd.DataFrame:
    """The folds as a table, for putting beside the per-fold results."""
    return pd.DataFrame([{
        "fold": f.index,
        "fit from": f.train[0], "fit to": f.train[1],
        "score from": f.test[0], "score to": f.test[1],
        "purged": f.purged,
    } for f in folds]).set_index("fold")
