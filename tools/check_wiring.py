"""The front/back contract: the interface declares what it needs, and the backend has
to supply it.

The two sides changing separately raises nothing; it just makes the feature fail
quietly:

- the backend returns ``id`` and the front end reads ``run_id`` — the run history's
  first column shows ``undefined``, and ``RUNS.find()`` "matches" because both sides
  are undefined, so it still looks like it works
- the backend computes ``tag`` and the column allowlist does not pass it through —
  every group comes out empty
- a sizing method is renamed and the front end keeps sending the old name — the
  backend never receives it and silently uses the default

**The interface declares ``CONTRACT`` explicitly in js/contract.js**, and this checks it
against a real response. Scanning the JS source to guess field names is unreliable (it
misses forms like ``RUNS[0].x``), hence the declaration. The interface checks the same
declaration at runtime and reports anything missing at once.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_FILE = ROOT / "app" / "static" / "js" / "contract.js"


def contract() -> dict[str, list[str]]:
    """Extract the CONTRACT declaration from js/contract.js."""
    js = CONTRACT_FILE.read_text(encoding="utf-8")
    m = re.search(r"export const CONTRACT = \{(.*?)\n\};", js, re.S)
    if not m:
        raise SystemExit(
            f"no CONTRACT declaration found in {CONTRACT_FILE.relative_to(ROOT)}"
        )
    body = re.sub(r"//.*", "", m.group(1))
    out: dict[str, list[str]] = {}
    for key, items in re.findall(r"(\w+):\s*\[(.*?)\]", body, re.S):
        out[key] = re.findall(r'"([^"]+)"', items)
    return out


#: What a declared path resolved to. ``EMPTY`` is not a failure -- see :func:`resolve`.
OK, MISSING, EMPTY = "ok", "missing", "empty"


def resolve(obj: object, path: str) -> tuple[str, str]:
    """Walk a dotted path, where ``*`` descends into every element of a list.

    Returns the outcome and **where it stopped**, because "config is missing" and
    "config.select.dte_lo is missing" call for different fixes and the second is the one
    that actually happens.

    ``EMPTY`` is reported for a list with no elements rather than ``MISSING``: a run where
    no anomaly fired or no scenario was covered cannot demonstrate that those fields
    exist, and failing on it would make the guard cry wolf on every quiet run. Those
    paths are listed as unchecked instead, so they are visibly not verified rather than
    silently counted as passing.
    """
    node = obj
    walked: list[str] = []
    for part in path.split("."):
        if part == "*":
            if not isinstance(node, list):
                return MISSING, ".".join(walked) or "(the root)"
            if not node:
                return EMPTY, ".".join(walked) or "(the root)"
            node = node[0]
            walked.append("*")
            continue
        if not isinstance(node, dict) or part not in node:
            return MISSING, ".".join(walked) or "(the root)"
        node = node[part]
        walked.append(part)
    return OK, path


def main() -> int:
    # This guard runs a real backtest, and a real backtest is saved. Send it somewhere
    # temporary: for a while every invocation wrote a run into the researcher's own
    # store under the default name, so their run history filled up with this guard's
    # take-profit and delta band next to their own work under the same name. Two
    # records of different things in one list reads as one strategy behaving randomly.
    os.environ["BENCH_STORE"] = tempfile.mkdtemp(prefix="check_wiring-")
    sys.path.insert(0, str(ROOT))
    from app.server import _meta_payload, run_backtest

    spec = contract()
    problems: list[str] = []
    unchecked: list[str] = []

    # **Every optional selection field is filled in.** Only fields actually given reach
    # the stored config ("blank means no limit"), so a sample that left them out would
    # make "absent because nobody asked" indistinguishable from "absent because it was
    # renamed" -- and the rename is the failure this guard exists for. The bands are
    # deliberately wide enough not to exclude anything on the synthetic chain, because a
    # sample run with no trades cannot check a single `rows.*` path.
    record = run_backtest({
        "symbol": "QQQ", "start": "2023-01-01", "end": "2023-06-30",
        "engine": "event_driven", "entry": "close < sma_20 * 0.98",
        "strategy_mode": "params", "entry_mode": "expr", "exit_mode": "fixed",
        "hold_days": 10, "dte_floor": 5, "quantity": 1,
        "take_profit": 0.8, "stop_loss": 0.4,
        "dte_lo": 21, "dte_hi": 45, "delta": 0.35,
        "delta_lo": 0.10, "delta_hi": 0.90, "iv_lo": 0.01, "iv_hi": 3.0,
        "max_spread": 0.50, "min_open_interest": 0,
        "sizing": {"method": "risk_pct", "risk_pct": 0.02},
    })
    if not record.get("rows"):
        problems.append(
            "the sample run produced no trades, so no `rows.*` path could be checked. "
            "Widen the sample's entry condition or range -- a guard that checks nothing "
            "passes for the wrong reason"
        )
    for path in spec.get("run", []):
        state, where = resolve(record, path)
        if state is MISSING:
            problems.append(f"the interface needs run.{path}, which the backend's run "
                            f"record does not contain (it stops at {where})")
        elif state is EMPTY:
            unchecked.append(f"run.{path}")
    # Declared as read-when-present. The interface handles their absence, so the runtime
    # check lets them go; here the sample payload filled every one of them in, which is
    # exactly what makes a rename fail rather than pass as "nobody asked for it".
    for path in spec.get("run_when_set", []):
        state, where = resolve(record, path)
        if state is MISSING:
            problems.append(
                f"the interface reads run.{path} when it is set, and this run set it and "
                f"the record does not contain it (it stops at {where}). Either it was "
                f"renamed, or the sample payload below no longer fills it in"
            )
        elif state is EMPTY:
            unchecked.append(f"run.{path}")
    for path in spec.get("run_optional", []):
        # A path under an absent root is the normal trade-table case; only the root itself
        # is required of an event-driven run.
        root = path.split(".")[0]
        if root not in record:
            problems.append(f"the interface needs run.{root} (the event-driven engine "
                            f"should supply it), and this run did not")
            continue
        state, where = resolve(record, path)
        if state is MISSING:
            problems.append(f"the backend sent run.{root} without run.{path} (it stops "
                            f"at {where}), so that section renders incomplete")
        elif state is EMPTY:
            unchecked.append(f"run.{path}")

    meta = _meta_payload()
    for path in spec.get("meta", []):
        state, where = resolve(meta, path)
        if state is MISSING:
            problems.append(f"the interface needs META.{path}, which /api/meta does "
                            f"not supply (it stops at {where})")
        elif state is EMPTY:
            unchecked.append(f"META.{path}")

    from backtest.portfolio.positioning import REQUIRES, Sizing

    # Each method is built **with** the parameter it needs, because the platform no longer
    # supplies one: "2% of equity per trade" is a strategy choice, and a default would size
    # every position in the book without appearing in the configuration a person reads
    # back. So what this checks is that every method name the interface offers is a method
    # the backend accepts.
    #
    # What it does **not** check: that the interface sends the matching parameter for the
    # method it selected. It does today -- `formPayload` in views/bench.js sends only the
    # parameters the chosen method uses -- but nothing holds it there. Checking it needs
    # the pairing declared in CONTRACT, the way the field lists are, so that this guard can
    # read it rather than parse the JS.
    sample = {"contracts": 1, "risk_pct": 0.02, "notional": 5_000.0, "target_vol": 0.20}
    for method in spec.get("sizing_methods", []):
        needs = REQUIRES.get(method)
        if needs is None:
            problems.append(f"the interface offers the sizing method {method!r} and the "
                            f"backend has no such method")
            continue
        try:
            Sizing(method=method, **{needs: sample[needs]})
        except Exception as exc:  # noqa: BLE001
            problems.append(f"the interface sends the sizing method {method!r} and the "
                            f"backend refuses it: {exc}")

    if problems:
        print("the front/back contract check failed:")
        for p in problems:
            print(f"  - {p}")
        print("\nThis class of change raises nothing; it just makes the feature fail "
              "quietly.")
        return 1
    print(f"the front/back contract check passed "
          f"({len(spec.get('run', []))} run fields · "
          f"{len(spec.get('run_optional', []))} event-driven fields · "
          f"{len(spec.get('meta', []))} meta fields · "
          f"{len(spec.get('sizing_methods', []))} sizing methods)")
    if unchecked:
        # Said out loud rather than folded into the pass. These paths were not verified,
        # and a reader who believes every declared field was checked is holding a
        # guarantee the guard did not give.
        print(f"  {len(unchecked)} path(s) could not be checked: the list they sit in "
              f"was empty in this run")
        for p in unchecked:
            print(f"    · {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
