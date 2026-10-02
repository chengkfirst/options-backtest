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

**The interface declares ``CONTRACT`` explicitly in bench.js**, and this checks it
against a real response. Scanning the JS source to guess field names is unreliable (it
misses forms like ``RUNS[0].x``), hence the declaration. The interface checks the same
declaration at runtime and reports anything missing at once.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "app" / "static" / "bench.js"


def contract() -> dict[str, list[str]]:
    """Extract the CONTRACT declaration from bench.js."""
    js = BENCH.read_text(encoding="utf-8")
    m = re.search(r"const CONTRACT = \{(.*?)\n\};", js, re.S)
    if not m:
        raise SystemExit("no CONTRACT declaration found in bench.js")
    body = re.sub(r"//.*", "", m.group(1))
    out: dict[str, list[str]] = {}
    for key, items in re.findall(r"(\w+):\s*\[(.*?)\]", body, re.S):
        out[key] = re.findall(r'"([^"]+)"', items)
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from app.server import _meta_payload, run_backtest

    spec = contract()
    problems: list[str] = []

    record = run_backtest({
        "symbol": "QQQ", "start": "2023-01-01", "end": "2023-06-30",
        "engine": "event_driven", "entry": "close < sma_20 * 0.98",
        "strategy_mode": "params", "entry_mode": "expr", "exit_mode": "fixed",
        "hold_days": 10, "dte_floor": 5, "quantity": 1,
        "take_profit": 0.8, "stop_loss": 0.4,
        "dte_lo": 21, "dte_hi": 45, "delta": 0.35,
        "sizing": {"method": "risk_pct", "risk_pct": 0.02},
    })
    for key in spec.get("run", []):
        if key not in record:
            problems.append(f"the interface needs run.{key}, which the backend's run "
                            f"record does not contain")
    for key in spec.get("run_optional", []):
        if key not in record:
            problems.append(f"the interface needs run.{key} (the event-driven engine "
                            f"should supply it), and this run did not")

    meta = _meta_payload()
    for key in spec.get("meta", []):
        if key not in meta:
            problems.append(f"the interface needs META.{key}, which /api/meta does "
                            f"not supply")

    from backtest.portfolio.positioning import Sizing

    for method in spec.get("sizing_methods", []):
        try:
            Sizing(method=method)
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
          f"{len(spec.get('meta', []))} meta fields · "
          f"{len(spec.get('sizing_methods', []))} sizing methods)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
