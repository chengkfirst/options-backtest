"""One configuration from the browser, run and stored.

This is the orchestration between the interface and ``backtest.run``: translate the
form, compile whatever code it carries, run it, slim the trade log down to what the
charts need, and store the result.

Stored rather than kept in memory: what accumulates over long-term research is the
judgements, and a judgement attaches to a run record. Something that vanishes on restart
is a toy, not a research tool.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from compilers import compile_entry, compile_plan, compile_review, compile_signals
from store import Store

STORE = Store()

COLUMNS = [
    "trade_id", "entry_date", "exit_date", "contract", "quantity",
    "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl",
    "exit_reason", "holding_days",
    "entry_underlying_price", "entry_iv", "entry_delta", "entry_moneyness",
    "entry_dte_calendar", "exit_underlying_price", "exit_iv", "exit_delta",
    # The tag and note a plan attached. **The classification reflects the judgement
    # actually made at order time**, not one imposed afterwards from the outcome —
    # without it the interface can only group by numeric bands.
    "tag", "note",
]
#: Signal columns come through by prefix, so custom signals arrive automatically.
SIGNAL_PREFIX = "entry_sig_"


SYNTHETIC_WARNING = (
    "This run used the synthetic option chain: the underlying's prices are real and the "
    "option prices come from Black-Scholes. Conclusions about the underlying (when to "
    "enter) can be trusted; option-side conclusions (which delta, where to take profit) "
    "cannot."
)



def run_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """Turn one configuration from the front end into a run, and store it.

    Stored rather than kept in memory: what accumulates over long-term research is the
    judgements, and a judgement attaches to a run record. Something that vanishes on
    restart is a toy, not a research tool.
    """
    from backtest import run as run_backtest_fn

    started = datetime.now()
    kwargs: dict[str, Any] = {
        "symbol": payload.get("symbol", "QQQ"),
        "start": payload.get("start", "2020-01-01"),
        "end": payload.get("end", "2024-12-31"),
        "entry": payload.get("entry", "sma_20 > sma_50"),
        "quantity": int(payload.get("quantity", 1)),
        # Selection is assembled as a feasible region plus a ranking objective. Only
        # fields actually given are passed — blank means no limit, rather than inventing
        # a default band on the user's behalf.
        "select": {
            k: v for k, v in {
                "right": payload.get("right", "call"),
                "dte_lo": _opt_int(payload.get("dte_lo")),
                "dte_hi": _opt_int(payload.get("dte_hi")),
                "delta_lo": _opt_float(payload.get("delta_lo")),
                "delta_hi": _opt_float(payload.get("delta_hi")),
                "iv_lo": _opt_float(payload.get("iv_lo")),
                "iv_hi": _opt_float(payload.get("iv_hi")),
                "max_spread": _opt_float(payload.get("max_spread")),
                "min_open_interest": _opt_int(payload.get("min_open_interest")),
                "delta": _opt_float(payload.get("delta")),
            }.items() if v is not None
        },
        "exit": {
            "hold_days": _opt_int(payload.get("hold_days")),
            "take_profit": _opt_float(payload.get("take_profit")),
            "stop_loss": _opt_float(payload.get("stop_loss")),
            "dte_floor": _opt_int(payload.get("dte_floor")),
        },
        "name": payload.get("name") or "run",
    }
    if payload.get("signals_code", "").strip():
        kwargs["signals"] = compile_signals(payload["signals_code"])
    if isinstance(payload.get("sizing"), dict):
        kwargs["sizing"] = payload["sizing"]
    kwargs["engine"] = payload.get("engine", "trade_table")
    if kwargs["engine"] == "event_driven":
        mc = payload.get("max_concurrent")
        kwargs["max_concurrent"] = int(mc) if mc not in (None, "", "none") else None

    if payload.get("strategy_mode") == "plan":
        code = payload.get("plan_code", "").strip()
        if not code:
            raise ValueError("plan mode is selected but the plan code is empty")
        if kwargs["engine"] != "event_driven":
            raise ValueError(
                "a plan needs the event-driven engine. The trade-table engine treats "
                "trades independently and computes exits vectorised, so it cannot "
                "express 'this trade has these exit rules' — which is the point of a plan."
            )
        kwargs["plan"] = compile_plan(code)
    if payload.get("review_code", "").strip():
        kwargs["review"] = compile_review(payload["review_code"])
        if kwargs["engine"] != "event_driven":
            raise ValueError("a daily review needs the event-driven engine: the trade "
                             "table computes exits vectorised and has no 'each day' step.")
    if payload.get("strategy_mode") == "plan":
        pass
    elif payload.get("entry_mode") == "code":
        code = payload.get("entry_code", "").strip()
        if not code:
            raise ValueError("code mode is selected but the entry code is empty")
        kwargs["entry"] = compile_entry(code)

    result = run_backtest_fn(**kwargs)
    frame = result.trades
    seconds = (datetime.now() - started).total_seconds()

    cols = [c for c in COLUMNS if c in frame.columns]
    cols += [c for c in frame.columns if c.startswith(SIGNAL_PREFIX)]
    slim = frame[cols].copy() if len(frame) else frame
    for c in slim.columns:
        if slim[c].dtype.kind == "f":
            slim[c] = slim[c].round(5)
    for c in ("entry_date", "exit_date"):
        if c in slim.columns:
            slim[c] = slim[c].astype(str)

    config = _serialisable(kwargs, payload)
    spec = {
        "entry": config["entry"],
        "entry_code": config.get("entry_code", ""),
        "plan_code": config.get("plan_code", ""),
        "strategy_mode": payload.get("strategy_mode", "params"),
        "signals_code": config.get("signals_code", ""),
        "select": config["select"],
        "exit": config["exit"],
        "quantity": config["quantity"],
        "sizing": config.get("sizing"),
    }
    strategy = STORE.save_strategy(
        payload.get("strategy_name") or "untitled", spec, payload.get("strategy_note", "")
    )

    stats = result.stats()
    dist = result.distribution()
    prov = result.provenance()
    record = STORE.save_run(
        strategy=strategy,
        config=config,
        manifest=result.manifest(),
        events=result.raw.events.counts(),
        seconds=seconds,
        summary={
            "n": stats["count"],
            "net_pnl": float(stats.get("net_pnl", 0.0)),
            "win_rate": float(stats.get("win_rate", 0.0)),
            "median_pnl": float(dist.get("median", 0.0)) if stats["count"] else 0.0,
        },
        trades=slim,
        synthetic=bool(prov.get("all_synthetic")),
    )
    record["rows"] = json.loads(slim.to_json(orient="records")) if len(slim) else []
    # Three kinds of notice, each in a fixed place and never mixed:
    #   boundary   — the hard boundary on these numbers. It has to stand out, because it
    #                decides whether the conclusions can be used at all
    #   problems   — something actually went wrong in this run, with the detail available
    #   (component gaps are not here) — they are static and do not change per run, so
    #                they sit beside each layer in the left panel. Dumping the same seven
    #                lines into the results on every run gets ignored after twice, which
    #                is worse than not showing them.
    record["boundary"] = ""
    record["problems"] = []
    record["engine"] = kwargs["engine"]
    # What only the event-driven engine produces: the equity curve, portfolio metrics,
    # attribution, scenarios. What the trade table cannot produce is not invented; the
    # key is simply absent, and the front end hides the section rather than showing
    # a row of zeros.
    if kwargs["engine"] == "event_driven":
        try:
            equity = result.raw.equity
            record["equity"] = json.loads(
                equity.reset_index().to_json(orient="records", date_format="iso"))
            record["equity_metrics"] = result.equity_metrics()
            record["scenarios"] = json.loads(
                result.scenarios().reset_index().to_json(orient="records"))
        except Exception as exc:  # noqa: BLE001
            record["problems"].append(f"the equity-curve metrics could not be computed: "
                                      f"{type(exc).__name__}: {exc}")
    try:
        record["attribution"] = result.attribution().as_dict()
    except Exception as exc:  # noqa: BLE001
        record["problems"].append(f"P&L attribution failed: {type(exc).__name__}: {exc}")
    record["strategy_reused"] = strategy.get("reused", False)
    if prov.get("all_synthetic"):
        record["boundary"] = (
            "Synthetic option chain: the underlying's prices are real, the option prices "
            "come from Black-Scholes, and IV is constant with no skew. Conclusions about "
            "**when to enter** can be trusted; **which delta and where to take profit** "
            "cannot."
        )
    # This run's diagnostics. Unlike the component gaps these differ every run and can
    # be inspected — the front end can expand them to see which days and why.
    record["diagnostics"] = _run_diagnostics(result)
    # The anomaly scan: rule-based, and an empty list when nothing fires — the
    # interface shows or hides the section from that.
    try:
        from dataclasses import asdict

        record["anomalies"] = [asdict(f) for f in result.anomalies()]
    except Exception as exc:  # noqa: BLE001
        record["anomalies"] = []
        record["problems"].append(f"the anomaly scan failed: {type(exc).__name__}: {exc}")
    record["notes"] = [record["boundary"]] if record["boundary"] else []
    return record


def _run_diagnostics(result) -> list[dict[str, Any]]:
    """Summarise the run events into what happened, how often, and what can be expanded.

    A count alone is useless — "45 candidates dropped" does not say whether to care. The
    distribution of reasons is what distinguishes "the signal is too strict" from
    "nothing on the chain qualifies".
    """
    try:
        events = result.events
    except Exception:  # noqa: BLE001
        return []
    if events is None or not len(events):
        return []
    out = []
    for kind, group in events.groupby("kind", sort=False):
        reasons = (group["detail"].astype(str).str.slice(0, 80)
                   .value_counts().head(5).to_dict())
        out.append({
            "kind": str(kind),
            "count": int(len(group)),
            "first": str(group["date"].iloc[0]),
            "last": str(group["date"].iloc[-1]),
            "reasons": [{"why": k, "n": int(v)} for k, v in reasons.items()],
        })
    return sorted(out, key=lambda d: -d["count"])



def _finite(obj):
    """Replace NaN and ±Infinity with ``None``, recursively.

    It is also right semantically: when a Sharpe cannot be computed it is **not 0 and not
    some number**, it is absent. The front end shows "—" for null and "0.00" for 0, and
    the latter invents a figure.
    """
    import math

    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _serialisable(kwargs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Turn the configuration into something JSON can serialise.

    In code mode ``entry`` is a function object and ``signals`` a set of lambdas, and
    json.dumps blows up on both. They become readable descriptions here, which the front
    end can display and which make a run record say which rules it used.
    """
    out = dict(kwargs)
    if callable(out.get("entry")):
        code = payload.get("entry_code", "")
        first = next((ln.strip() for ln in code.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")), "custom code")
        out["entry"] = f"(custom code) {first}"
        out["entry_code"] = code
    if "signals" in out:
        out["signals"] = sorted(out["signals"])
        out["signals_code"] = payload.get("signals_code", "")
    return out


def _opt_int(v):
    return None if v in (None, "", "none") else int(v)


def _opt_float(v):
    return None if v in (None, "", "none") else float(v)


#: These types must carry a charset. Without one the browser guesses with its own
#: default encoding and non-ASCII text comes out garbled.
