"""What uniquely identifies a run.

The architecture document names four things: the strategy config, the code commit, the
data snapshot and the run settings. Their hash is the ``run_id``.

## Why a hash rather than a counter

A counter answers "which run was this". A hash answers the question that matters for
research: **have I run this exact thing before.** Two configs that differ only in the
order a dict was built hash the same; two that differ in one parameter do not. So a sweep
can tell a genuinely new trial from a repeat, and the trial count — which the Deflated
Sharpe needs — stops being a guess.

## What goes in, and what deliberately does not

In: everything that changes the numbers. Out: anything that changes between two runs of
the same thing — a timestamp, a run name, a note. Putting a timestamp in would make every
run unique and the hash worthless, which is the easy mistake here.

A missing commit or data version is recorded as ``"unknown"`` rather than omitted. Omitting
it would make a run on uncommitted code hash the same as one on committed code, which is
exactly the pair you most need to tell apart.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

#: Keys ignored when hashing: they differ between two runs of the same thing.
VOLATILE = frozenset({"name", "strategy_name", "strategy_note", "note", "started",
                      "seconds", "run_id", "refresh"})


def code_version(root: Path | None = None) -> str:
    """The commit, with a ``+dirty`` suffix when the tree has uncommitted changes.

    The suffix matters more than the hash. A result produced from a dirty tree cannot be
    reproduced from the commit alone, and saying so is the difference between a
    reproducible run and one that merely looks it.
    """
    root = root or Path(__file__).resolve().parent.parent.parent
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5)
        if rev.returncode != 0:
            return "unknown"
        commit = rev.stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                capture_output=True, text=True, timeout=5)
        return commit + ("+dirty" if status.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _stable(obj: Any) -> Any:
    """Reduce to something whose JSON form does not depend on insertion order."""
    if isinstance(obj, Mapping):
        return {k: _stable(v) for k, v in sorted(obj.items()) if k not in VOLATILE}
    if isinstance(obj, (list, tuple)):
        return [_stable(v) for v in obj]
    if callable(obj):
        # A code-mode entry or a custom signal. The function object's identity is not
        # stable across processes, so the source the caller recorded is what counts;
        # here, name it so two different callables do not hash alike by accident.
        return f"<callable {getattr(obj, '__name__', 'anonymous')}>"
    if isinstance(obj, float) and obj != obj:
        return "nan"
    return obj


def run_id(
    *,
    strategy: Mapping[str, Any],
    settings: Mapping[str, Any],
    execution: Mapping[str, Any],
    data_version: str = "unknown",
    commit: str | None = None,
) -> str:
    """The 16-character hash of what determines a result.

    ``strategy`` is the components and their parameters; ``settings`` is the period, the
    capital and the cost model; ``execution`` is which engine ran and the limits it ran
    under; ``data_version`` identifies the data snapshot.

    ``execution`` is a **required** argument rather than part of ``settings`` because it
    was the thing that went missing. Two runs of the same strategy on the same data, one
    on each engine, hashed identically -- and so did a run with a concurrency limit and
    one without, which is not the same set of trades at all. The direction of that error
    matters: the id exists so the trial counter can tell a rerun from a new trial, and
    collapsing distinct configurations into one id makes the search look **smaller** than
    it was, which makes the deflated result look better than it earned.
    """
    if not execution:
        raise ValueError(
            "run_id needs the execution settings: which engine ran, and the limits it "
            "ran under. Omitting them makes two different computations hash alike"
        )
    payload = {
        "strategy": _stable(strategy),
        "settings": _stable(settings),
        "execution": _stable(execution),
        "data": data_version,
        "code": commit if commit is not None else code_version(),
    }
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
