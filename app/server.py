"""The local backtest service.

    python3 app/server.py

Then open http://localhost:8765.

**Why it has to be a local service**: a backtest runs Python (numpy/scipy) and reads
your data, and both of those are on your machine. A purely static page cannot do it, so
this is a service running locally with the browser as its interface, not a website.

The standard library is the only dependency. The core endpoints:

    GET  /api/health   is the service up (the front end decides from this whether it can
                       run backtests or only view exported results)
    GET  /api/meta     the available signals, symbols and defaults
    POST /api/run      run one backtest and return the trade log
"""

from __future__ import annotations

import json
import re
import sys
import traceback
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from store import VERDICTS, Store  # noqa: E402

STORE = Store()

PORT = 8765
STATIC = ROOT / "static"

#: The columns sent to the front end. All of them would be heavy (1,700 trades x 44
#: columns over ten years); these are enough to chart and group by.
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


def _tradeoff(q: dict[str, str]) -> dict[str, Any]:
    """One day's moneyness x tenor trade-off surface.

    Computed from **that day's real chain** rather than pasted from a formula — once real
    data is wired in it reflects the real skew and term structure automatically. So what
    it describes is always what you can actually buy.
    """
    from backtest.api import _as_date
    from backtest.config import BacktestConfig, ContractSpec, CostConfig, SyntheticChainConfig
    from backtest.data.chain_registry import build_chain_source
    from backtest.data.market import MarketData, VOL_PROXY_BY_SYMBOL
    from backtest.research.tradeoff import tradeoff_grid
    from backtest.selection.rules import enrich_chain

    from datetime import timedelta

    symbol = q.get("symbol", "QQQ")
    asof = _as_date(q.get("date") or "2024-12-31")
    # The configured range has to extend **past** the queried day: the synthetic chain
    # only generates expiries inside the range, and ending it on the queried day leaves
    # that day with no expiry ahead of it and an empty chain.
    start = asof - timedelta(days=400)
    end = asof + timedelta(days=200)
    cfg = BacktestConfig(
        symbol=symbol, start=start, end=end, notional_capital=100_000.0,
        contract=ContractSpec(symbol=symbol, vol_proxy=VOL_PROXY_BY_SYMBOL[symbol]),
        costs=CostConfig(), synthetic=SyntheticChainConfig(),
    )
    market = MarketData(cfg)
    sessions = [d for d in market.calendar.sessions if start <= d <= asof]
    if not sessions:
        raise ValueError(f"there are no trading days between {start} and {asof}")
    day = sessions[-1]          # the last session not after the queried day
    spot = market.spot(day)
    if not spot.is_present:
        raise ValueError(f"no underlying price on {day}: {spot.reason}")

    chain = build_chain_source(None, market, cfg)
    enriched = enrich_chain(chain.snapshot(day), cfg, market.calendar, day)
    grids = tradeoff_grid(enriched, spot=spot.unwrap(), right=q.get("right", "C"))

    def as_rows(frame):
        if frame.empty:
            return {"cols": [], "rows": []}
        return {
            "cols": list(frame.columns),
            "rows": [{"label": str(i), "values": [None if v != v else float(v)
                                                  for v in frame.loc[i]]}
                     for i in frame.index],
        }

    return {
        "day": str(day), "symbol": symbol, "spot": spot.unwrap(),
        "right": q.get("right", "C"),
        "synthetic": True,
        "grids": {k: as_rows(v) for k, v in grids.items()},
    }


def _meta_payload() -> dict[str, Any]:
    """Everything static the interface needs.

    A module-level function rather than a Handler method: it needs no request state, and
    the front/back contract check has to obtain it without starting the service.
    """
    from backtest.data.market import VOL_PROXY_BY_SYMBOL
    from backtest.components import COUPLINGS, as_form_spec
    from backtest.signals.registry import DEFAULT_SIGNALS, describe_signals

    return {
        "symbols": sorted(VOL_PROXY_BY_SYMBOL),
        "signals": sorted(DEFAULT_SIGNALS),
        # Beyond the name: the meaning, the unit, the window, the trap. With names
        # alone the interface can only list them alphabetically and the user has to
        # guess what convention rv_20 is annualised on.
        "signal_docs": describe_signals().fillna("").to_dict("records"),
        # The interface's form is generated from this definition. What a component is
        # and what it asks for is stated in exactly one place.
        "form": as_form_spec(),
        "couplings": [
            {"a": a, "b": b, "why": why} for a, b, why in COUPLINGS
        ],
        "gaps": _describe_gaps(),
        "defaults": {
            "symbol": "QQQ", "start": "2020-01-01", "end": "2024-12-31",
            "entry": "sma_20 > sma_50", "right": "call",
            "dte_lo": 21, "dte_hi": 45, "delta_lo": 0.25, "delta_hi": 0.45,
            "max_spread": 0.08, "delta": 0.35,
            "hold_days": 10, "take_profit": 1.0, "stop_loss": 0.5,
            "dte_floor": 5, "quantity": 1,
            "sizing_method": "risk_pct", "risk_pct": 0.02, "notional": 5000,
            "target_vol": 0.20, "risk_pct_vt": 0.02, "kelly_fraction": 0.25,
            "engine": "event_driven",
            "max_concurrent": 5,
            "strategy_mode": "params",
            # The default is the expression rather than code: a code box fills a screen
            # and pushes the components out of view. Someone who wants code can switch,
            # which beats everyone losing the overview on open.
            "entry_mode": "expr",
            "plan_code": (
                "def plan(ctx):\n"
                "    \"\"\"One function decides what to buy, how much, and how to exit.\n"
                "\n"
                "    In a real trade these are one judgement: the deeper the drawdown,\n"
                "    the longer dated, the bigger the bet, the wider the stop, the\n"
                "    higher the target. Four constants cannot express that.\n"
                "    Return None for no trade today.\n"
                "    \"\"\"\n"
                "    rsi = ctx.signal(\"rsi_14\", default=50.0)\n"
                "    if rsi > 45:\n"
                "        return None                      # (2) timing: not oversold enough\n"
                "\n"
                "    deep = rsi < 35\n"
                "\n"
                "    # (2b) what to buy: the deep case goes longer and nearer the money\n"
                "    c = ctx.calls[(ctx.calls.spread < 0.10) & ctx.calls.delta.notna()]\n"
                "    c = c[c.dte.between(45, 75)] if deep else c[c.dte.between(21, 45)]\n"
                "    if c.empty:\n"
                "        return None\n"
                "    target = 0.50 if deep else 0.30\n"
                "\n"
                "    return Plan(\n"
                "        contract    = c.iloc[(c.delta - target).abs().argmin()],\n"
                "        risk_pct    = 0.030 if deep else 0.015,   # (6) how much\n"
                "        stop_loss   = 0.60  if deep else 0.35,    # (4) thesis failed\n"
                "        take_profit = 1.00  if deep else 0.60,    # (3) paid\n"
                "        max_hold    = 40    if deep else 15,\n"
                "        tag         = \"deep\" if deep else \"shallow\",\n"
                "        note        = f\"RSI {rsi:.0f}\",\n"
                "    )\n"
            ),
            "entry_code": (
                "def entry(view):\n"
                "    \"\"\"Return True to enter that day. A missing signal never enters.\"\"\"\n"
                "    rsi = view[\"rsi_14\"]\n"
                "    rv  = view[\"rv_20\"]\n"
                "    if rsi.is_missing or rv.is_missing:\n"
                "        return False\n"
                "    # In a high-volatility environment, demand a deeper oversold\n"
                "    if rv.unwrap() > 0.30:\n"
                "        return rsi.unwrap() < 25\n"
                "    return rsi.unwrap() < 35\n"
            ),
            "review_code": "",
            "signals_code": (
                "# Custom signals: name -> function(bars) -> Series\n"
                "# Always use adjusted(), or ex-dividend days produce false gaps\n"
                "SIGNALS = {\n"
                "    \"ma20_slope\": lambda b: b.adjusted(\"close\")\n"
                "                       .rolling(20).mean().pct_change(5),\n"
                "}\n"
            ),
        },
        "code_help": USER_CODE_HELP,
        "plan_help": PLAN_HELP,
    }


def _describe_gaps() -> list[str]:
    """Which of the seven components do not yet stand up.

    **Read from backtest.components, never hand-written here.** A hand-written list goes
    stale once the feature lands, leaving a warning on the interface that stopped being
    true long ago — worse than showing nothing, because it looks authoritative.
    """
    from backtest.components import gaps

    return gaps() + ["selling: assignment of a cash-secured put is modelled, but orders "
                     "are still limited to the long side"]


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


USER_CODE_HELP = """What is available:

  view["sma_20"]        the signal's value, as a Maybe
    .is_missing         True during the warm-up or at a data gap
    .unwrap()           take the number out (raises when absent, so test first)
  view.day              the day (datetime.date)
  view.names()          every registered signal name

It must return True or False. Returning False when any signal is absent (no entry) is
the convention — an absence must never take part in a comparison as 0."""


PLAN_HELP = """The ctx a plan function receives:

  ctx.day                 the decision day
  ctx.spot                the underlying's price that day
  ctx.chain               every executable contract that day (a DataFrame, one per row)
  ctx.calls / ctx.puts    the same, already filtered by type
  ctx.equity              current equity        <- what (6) sizing reads
  ctx.cash                available cash
  ctx.positions           how many positions are open
  ctx.drawdown            the current drawdown (negative, e.g. -0.08)
  ctx.signal(name, default=...)  read a signal; absent with no default raises, and it
                                 never quietly returns 0
  ctx.view[name]          the raw Maybe, for handling absence yourself

The columns of chain / calls / puts:
  iv delta gamma vega theta   the greeks and implied volatility
  bid ask mid spread          the quote; spread is the relative bid/ask spread
  dte                         days to expiry in calendar days (the market convention)
  sessions_left               days to expiry in trading days (the executability basis)
  moneyness                   positive always means out of the money, calls and puts alike
  strike expiry right         the contract's identity
  is_call / is_put            booleans, so "C"/"P" need not be remembered

The chain already has anything unexecutable on T+1 removed, so picking freely on the
greeks cannot land on something that will not open.

The fields of Plan:
  contract      one row picked from chain (required)
  contracts / risk_pct / notional    size; at most one, else the global sizing method
  take_profit / stop_loss            omitted falls back to the global value
  max_hold / dte_floor               the same
  tag           a grouping tag, stored with the trade for comparison afterwards
  note          one sentence for a person, also stored with the trade
"""


def compile_plan(code: str):
    """Compile the user's Python into a plan function.

    It executes in this process on this machine, as ``compile_entry`` does. A compile or
    execution error is raised back to the front end in full.
    """
    namespace: dict[str, Any] = {}
    from backtest.engine.plan import Plan

    namespace["Plan"] = Plan
    try:
        exec(compile(code, "<your trade plan>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(
            f"syntax error in the plan code, line {exc.lineno}: {exc.msg}\n\n{PLAN_HELP}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the plan code raised: {type(exc).__name__}: {exc}") from exc

    fn = namespace.get("plan")
    if fn is None or not callable(fn):
        raise ValueError(
            "the plan code must define a function named plan.\n\n" + PLAN_HELP
        )
    return fn


def compile_review(code: str):
    """Compile the user's daily review function. ``Close`` / ``Reduce`` / ``Add`` are put
    straight into its namespace."""
    from backtest.engine.review import Add, Close, Reduce

    namespace: dict[str, Any] = {"Close": Close, "Reduce": Reduce, "Add": Add}
    try:
        exec(compile(code, "<your daily review>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(f"syntax error in the review code, line {exc.lineno}: "
                         f"{exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the review code raised: {type(exc).__name__}: {exc}") from exc
    fn = namespace.get("review")
    if fn is None or not callable(fn):
        raise ValueError(
            "the review code must define a function named review:\n\n"
            "def review(ctx, pos):\n"
            "    if pos.mfe_so_far > 0.4 and pos.ratio < pos.mfe_so_far * 0.67:\n"
            "        return Close('gave back a third', kind='take_profit')\n"
            "    return None"
        )
    return fn


def compile_entry(code: str):
    """Compile the user's Python into an entry function.

    This code runs **on your machine, in your process**, exactly as it would in Jupyter.
    The service listens only on 127.0.0.1.

    A compile or execution error is raised back to the front end in full — seeing "it
    failed" in the browser without knowing where is worse than a crash.
    """
    namespace: dict[str, Any] = {}
    try:
        exec(compile(code, "<your entry logic>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(
            f"syntax error in the entry code, line {exc.lineno}: {exc.msg}\n\n"
            f"{USER_CODE_HELP}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the entry code raised: {type(exc).__name__}: {exc}") from exc

    fn = namespace.get("entry")
    if fn is None or not callable(fn):
        raise ValueError(
            "the entry code must define a function named entry, for example:\n\n"
            "def entry(view):\n"
            "    rsi = view['rsi_14']\n"
            "    if rsi.is_missing:\n"
            "        return False\n"
            "    return rsi.unwrap() < 30\n\n" + USER_CODE_HELP
        )

    def guarded(view):
        try:
            out = fn(view)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"the entry function raised on {getattr(view, 'day', '?')}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(out, bool):
            raise RuntimeError(
                f"the entry function must return True or False; on "
                f"{getattr(view, 'day', '?')} it returned {type(out).__name__}: {out!r}"
            )
        return out

    guarded.__name__ = "user_entry_logic"
    return guarded


def compile_signals(code: str) -> dict[str, Any]:
    """Compile the user's Python into custom signals.

    The convention: define a dict named SIGNALS, ``{"name": lambda bars: a Series}``.
    ``bars.adjusted("close")`` gives adjusted prices — signals always use adjusted
    prices, or ex-dividend days produce false gaps.
    """
    namespace: dict[str, Any] = {}
    try:
        exec(compile(code, "<your signals>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(f"syntax error in the signal code, line {exc.lineno}: "
                         f"{exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"the signal code raised: {type(exc).__name__}: {exc}") from exc

    signals = namespace.get("SIGNALS")
    if signals is None:
        raise ValueError(
            "the signal code must define a dict named SIGNALS, for example:\n\n"
            "SIGNALS = {\n"
            "    'ma20_slope': lambda b: b.adjusted('close').rolling(20).mean().pct_change(5),\n"
            "}"
        )
    if not isinstance(signals, dict) or not all(callable(v) for v in signals.values()):
        raise ValueError("SIGNALS must be a dict of {name: function}")
    return signals


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
TEXT_TYPES = {"text/html", "text/css", "text/javascript", "application/javascript",
              "application/json", "text/plain"}


class Handler(SimpleHTTPRequestHandler):
    #: The standard library defaults to HTTP/1.0, which opens a new TCP connection per
    #: request. One page load is five or six static files plus several /api calls, and
    #: that churn is unnecessary. Declaring 1.1 lets the browser reuse the connection —
    #: on condition that **every response carries a correct Content-Length**, or the
    #: browser waits for ever, the page spins, and neither side raises. Every exit
    #: currently satisfies it: ``_json`` sets it explicitly, static files go through
    #: SimpleHTTPRequestHandler, and errors go through send_error. Any future branch
    #: that writes to wfile directly has to set Content-Length too.
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def _serve_index(self):
        """Stamp every js/css reference with the file's modification time when serving
        the index.

        Browsers cache js/css heuristically, so new HTML meets old JS and the error
        reported is "an element is null" or "a function is undefined" — symptoms that
        point at the code when the cause is a file that was never reloaded.

        Cache-Control only affects future loads and cannot rescue what is already cached;
        renaming files has to be maintained by hand. A ``?v=`` from the mtime changes the
        URL whenever the file changes, so the browser must refetch.
        """
        index = STATIC / "index.html"
        if not index.exists():
            return self.send_error(404, "index.html is not in %s" % STATIC)
        html = index.read_text(encoding="utf-8")

        def stamp(match):
            attr, name = match.group(1), match.group(2)
            target = STATIC / name
            if not target.exists():
                # A reference to a file that does not exist. Left silent, the page is
                # missing a script and nothing says so.
                sys.stderr.write(f"  index.html references a file that does not exist: "
                                 f"{name}\n")
                return match.group(0)
            return f'{attr}="{name}?v={int(target.stat().st_mtime)}"'

        html = re.sub(r'(src|href)="([\w.\-/]+\.(?:js|css))"', stamp, html)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def end_headers(self):
        """Every static file is revalidated against the server.

        Without this, Chrome caches js/css heuristically and a reload after an edit still
        runs the old code, raising nothing. A local tool does not care about the
        bandwidth; correctness comes first.
        """
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def guess_type(self, path):
        """Add charset=utf-8 to the text types.

        The standard library gives ``text/html`` from the extension with no encoding. One
        wrong guess by the browser garbles the page — and it is a silent failure: the page
        opens, nothing raises, and it simply cannot be read.
        """
        base = super().guess_type(path)
        main = base.split(";")[0].strip()
        return f"{main}; charset=utf-8" if main in TEXT_TYPES else base

    def log_message(self, fmt, *args):  # only print failures; successes would flood
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---- API ----
    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path in ("/", "/index.html"):
            return self._serve_index()
        if url.path == "/api/health":
            return self._json({"ok": True, **STORE.overview()})
        if url.path == "/api/tradeoff":
            try:
                return self._json(_tradeoff(q))
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"{type(exc).__name__}: {exc}",
                                   "detail": traceback.format_exc()}, 400)
        if url.path == "/api/meta":
            return self._json(self._meta())
        if url.path == "/api/runs":
            return self._json({"runs": STORE.runs(
                strategy_name=q.get("strategy"), symbol=q.get("symbol"),
                verdict=q.get("verdict"), limit=int(q.get("limit", 200)))})
        if url.path == "/api/run":
            try:
                run_id = int(q.get("id", ""))
            except ValueError:
                return self._json({"error": "the id parameter is missing or invalid"}, 400)
            record = STORE.run(run_id)
            if record is None:
                return self._json({"error": f"there is no run {run_id}"}, 404)
            try:
                trades = STORE.trades(run_id)
            except (FileNotFoundError, KeyError) as exc:
                return self._json({"error": str(exc)}, 410)
            record["rows"] = json.loads(trades.to_json(orient="records")) if len(trades) else []
            record["notes_list"] = STORE.notes(run_id)
            record["notes"] = list(_describe_gaps())
            if record["synthetic"]:
                record["notes"].insert(0, SYNTHETIC_WARNING)
            return self._json(record)
        if url.path == "/api/strategies":
            name = q.get("name")
            return self._json({"strategies":
                STORE.strategy_versions(name) if name else STORE.strategies()})
        if url.path == "/api/notes":
            return self._json({"notes": STORE.notes(), "verdicts": VERDICTS})
        if url.path.startswith("/api/"):
            return self._json({"error": f"unknown endpoint {url.path}"}, 404)
        return super().do_GET()

    def do_HEAD(self):
        """HEAD on an API path goes through the same routing.

        Otherwise HEAD /api/meta is treated as a static file and returns 404: the path
        exists and is reported as missing.
        """
        if self.path.startswith("/api/"):
            self._head_only = True
            try:
                return self.do_GET()
            finally:
                self._head_only = False
        return super().do_HEAD()

    def do_POST(self):
        if self.path not in ("/api/run", "/api/note", "/api/run/delete"):
            return self._json({"error": f"unknown endpoint {self.path}"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": f"the request body is not valid JSON: {exc}"}, 400)

        if self.path == "/api/note":
            try:
                note = STORE.add_note(
                    self._run_id(payload),
                    payload.get("body", ""),
                    payload.get("verdict", "open"),
                )
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"note": note})

        if self.path == "/api/run/delete":
            try:
                STORE.delete_run(self._run_id(payload))
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"ok": True})

        try:
            record = run_backtest(payload)
        except Exception as exc:  # noqa: BLE001
            # The whole error chain goes back to the front end. A silent failure is the
            # least acceptable kind of error in this project — seeing "it failed" in the
            # browser without knowing where is worse than a crash.
            detail = traceback.format_exc(limit=6)
            sys.stderr.write(detail)
            return self._json(
                {"error": f"{type(exc).__name__}: {exc}", "detail": detail}, 400
            )

        return self._json(record)

    # ---- helpers ----
    def _meta(self) -> dict[str, Any]:
        return _meta_payload()

    def _run_id(self, payload) -> int:
        """Take the run id out of the request body.

        Failing to find it raises; it never falls back to 0. Writing
        ``payload.get("run_id", 0)`` would quietly turn "the front end sent no id" into
        "operate on run 0" — a note attached to the wrong run, the wrong record deleted,
        and nothing raised anywhere.
        """
        raw = payload.get("id", payload.get("run_id"))
        if raw is None:
            raise ValueError("the request body has no run id (the field is `id`)")
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"the run id is not an integer: {raw!r}") from None

    def _json(self, obj, status: int = 200):
        """Serialise and reply.

        Serialisation can itself fail (a function object finding its way into the
        configuration, say). The connection must never simply drop then — the front end
        would see "the request failed" and nothing about where. So a plain error comes
        back instead, stating the cause.
        """
        try:
            # allow_nan=False is the point. By default this emits NaN / Infinity
            # literals, which Python reads back and **JSON does not allow**, so the
            # browser's JSON.parse fails outright. What that looks like: 200 from the
            # server, a clean log, and a front end that says only "the response was not
            # JSON", with nowhere to start. The classic trigger is a NaN Sharpe on zero
            # trades, which destroys the whole response. So non-finite values become null
            # first ("cannot be computed" should be null anyway), with allow_nan=False
            # behind it — if anything slips through, raising loudly beats emitting
            # invalid JSON.
            body = json.dumps(_finite(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            status = 500
            body = json.dumps(
                {"error": f"the server failed to serialise the response: {exc}",
                 "detail": "This is a server bug; please report it with the steps you "
                           "took."},
                ensure_ascii=False,
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)


def main() -> int:
    if not STATIC.exists():
        sys.stderr.write(f"the static directory is missing: {STATIC}\n")
        return 1
    try:
        import backtest  # noqa: F401
    except ImportError as exc:
        sys.stderr.write(
            f"importing backtest failed: {exc}\n"
            f"Start it with a Python that has the dependencies, for example:\n"
            f"  /opt/anaconda3/bin/python3 {Path(__file__).relative_to(Path.cwd())}\n"
        )
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"the bench is running  ->  http://localhost:{PORT}")
    print("Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
