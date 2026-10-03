"""The experiment tracker: counting what was tried.

## Why counting is the hard part

The Deflated Sharpe needs the number of trials, and that number is the one everybody
undercounts. The honest count is not "how many configs did I run today"; it is **every
parameter set, every variant and every grouping explored while chasing one idea**. Three
sweeps of twelve, plus five stratifications looking for a dimension that separates, is 41
trials — and reported as one it turns a noise result into a discovery.

So the count has to come from a record rather than from memory. Every run has a ``run_id``
that identifies the exact combination (``backtest.core.run_id``), so a repeat is
recognisable and distinct trials can be counted without double-counting a rerun.

## What an "idea" is

A tag the researcher chooses: ``"rsi oversold entry"``, ``"wider stop on QQQ"``. The trial
count is per idea, because that is the search the Deflated Sharpe has to pay for. Choosing
the tag is a judgement and cannot be automated — the one thing this module refuses to do is
guess which runs belong to the same search, because guessing wrong in the generous
direction is how the count gets understated.

## What this module is not

It holds no state. Persistence is ``app/store.py``, which already stores runs immutably and
versions strategies by content hash; this is the arithmetic over whatever records it is
handed. Keeping them apart is what lets Layer 6 stay above Layer 7 rather than depending on
the application.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Trials:
    """What was tried while chasing one idea."""

    idea: str
    #: Distinct configurations, by run_id. A rerun of the same thing is not a new trial.
    distinct: int
    #: Every run, reruns included. The gap from `distinct` is how much was repeated.
    runs: int
    #: Groupings explored, which count as trials and are the ones usually forgotten.
    groupings: int = 0
    #: The best per-period Sharpe seen, when the records carry one.
    best_sharpe: float | None = None
    #: Every Sharpe seen, for the variance the Deflated Sharpe prefers over an assumption.
    sharpes: tuple[float, ...] = ()

    @property
    def total(self) -> int:
        """What the Deflated Sharpe should be told."""
        return self.distinct + self.groupings

    def verdict(self) -> str:
        line = (f"{self.idea}: {self.total} trials "
                f"({self.distinct} distinct configurations")
        if self.groupings:
            line += f" + {self.groupings} groupings"
        line += ")"
        if self.runs > self.distinct:
            line += f"; {self.runs - self.distinct} reruns not counted again"
        if self.best_sharpe is not None:
            line += f"; best Sharpe {self.best_sharpe:.3f}"
        return line


def count_trials(
    records: Iterable[Mapping[str, Any]],
    *,
    idea: str,
    groupings: int = 0,
) -> Trials:
    """Count the trials in a set of run records.

    Each record needs a ``run_id``; ``sharpe`` is used when present. Records without a
    ``run_id`` **raise**, rather than being counted as distinct: a missing id would make
    every rerun look like a new trial and inflate the count, which sounds conservative and
    is not — it makes the deflated result look worse than the search deserves, and a
    researcher who notices will stop trusting the number.
    """
    ids, sharpes, runs = set(), [], 0
    for r in records:
        runs += 1
        rid = r.get("run_id")
        if not rid:
            raise ValueError(
                "a run record has no run_id, so a rerun cannot be told from a new trial. "
                "Every run gets one from result.manifest(); store it with the record"
            )
        ids.add(rid)
        s = r.get("sharpe")
        if s is not None and np.isfinite(s):
            sharpes.append(float(s))
    return Trials(
        idea=idea, distinct=len(ids), runs=runs, groupings=groupings,
        best_sharpe=max(sharpes) if sharpes else None,
        sharpes=tuple(sharpes),
    )


def deflate(trials: Trials, *, periods: int, skew: float = 0.0, kurtosis: float = 3.0):
    """The best result of this search, deflated by the size of the search.

    Uses the spread of the trial Sharpes when there are at least two, which is the honest
    input — an assumed variance is a guess about how noisy the search was.
    """
    from backtest.research.validation.deflated import deflated_sharpe

    if trials.best_sharpe is None:
        raise ValueError(
            f"no run under {trials.idea!r} recorded a Sharpe, so there is nothing to "
            f"deflate"
        )
    return deflated_sharpe(
        trials.best_sharpe, trials=trials.total, periods=periods,
        skew=skew, kurtosis=kurtosis,
        sharpes=np.asarray(trials.sharpes) if len(trials.sharpes) > 1 else None,
    )


def describe(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Runs grouped by idea, with distinct configurations and reruns.

    The ``reruns`` column is worth reading: a high count usually means a config is being
    rerun because its result was not stored, which is the failure the ``run_id`` exists to
    prevent.
    """
    if not records:
        return pd.DataFrame(columns=["idea", "runs", "distinct", "reruns"])
    rows = []
    by_idea: dict[str, list] = {}
    for r in records:
        by_idea.setdefault(r.get("idea") or "untagged", []).append(r)
    for idea, group in sorted(by_idea.items()):
        seen = Counter(r.get("run_id") for r in group)
        rows.append({"idea": idea, "runs": len(group), "distinct": len(seen),
                     "reruns": len(group) - len(seen)})
    return pd.DataFrame(rows).set_index("idea")
