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
from collections import OrderedDict
from datetime import datetime
from typing import Any

from compilers import compile_entry, compile_plan, compile_review, compile_signals
from store import Store

STORE = Store()

#: The last few result objects, by the store's run id.
#:
#: The validation tools -- baselines, ablation, counterfactual exits -- all need the
#: **result object**, not the stored trade log: ablation re-runs the strategy with a slot
#: removed, baselines re-run with the entry dates shuffled, and the counterfactual reads
#: the shadow prices kept past each exit, which are not in the trade table at all.
#:
#: Kept in memory rather than rebuilt from the stored configuration. Rebuilding would
#: double every runtime, and -- worse -- would re-run from a serialised config and could
#: silently differ from the run being validated. A run that has aged out says so and
#: offers a rerun, which is honest; silently validating a *slightly different* run is not.
RECENT: "OrderedDict[int, Any]" = OrderedDict()

#: How many. Small because an event-driven result holds a daily snapshot, a path per
#: position per day and the shadow prices past each exit.
RECENT_KEEP = 3

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
            # Stored so the trial counter can deflate the best result of a search by how
            # much searching it took. Only the event-driven engine has an equity curve, so
            # the trade table stores NULL rather than a zero that would read as a real
            # Sharpe of zero.
            "sharpe_annualised": _sharpe_of(result),
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
    RECENT[record["id"]] = result
    while len(RECENT) > RECENT_KEEP:
        RECENT.popitem(last=False)
    # Declares to the interface which validation tools this run can actually answer, so a
    # button is never offered for something that would fail. The counterfactual needs the
    # shadow prices, which only the event-driven engine keeps.
    record["validations"] = validations_for(kwargs["engine"])
    # Keep what this run said about itself, so opening it again by id says the same. The
    # two endpoints that both return "a run" used to return different shapes, and that is
    # why nothing in the interface could use the stored one.
    STORE.record_notices(
        record["id"],
        boundary=record["boundary"], problems=record["problems"],
        diagnostics=record["diagnostics"], anomalies=record["anomalies"],
    )
    return record


def validations_for(engine: str) -> list[str]:
    """Which validation tools a run on this engine can answer.

    Declared from one place so a fresh run and a stored one offer the same buttons. The
    counterfactual needs the shadow prices only the event-driven engine keeps.
    """
    return sorted(k for k, v in VALIDATIONS.items()
                  if v["engines"] is None or engine in v["engines"])


#: The validation tools the interface can run on a stored result, and what each needs.
#:
#: Declared in one place rather than branched on in the handler: the interface renders its
#: buttons from this, so a tool added here appears there, and one that cannot run under an
#: engine is never offered.
VALIDATIONS: dict[str, dict[str, Any]] = {
    "baselines": {
        "label": "against a baseline",
        "why": "is this better than entering at random, or than just holding the "
               "underlying. One run of this decides whether an idea is worth researching.",
        "engines": None,
    },
    "ablation": {
        "label": "what each slot is worth",
        "why": "remove one component at a time and see what the result loses. A slot "
               "that costs nothing to remove is not earning its complexity.",
        "engines": None,
    },
    "counterfactual": {
        "label": "would a different exit have been better",
        "why": "replays each trade against other take-profit and stop-loss levels using "
               "the prices kept past the actual exit. A trade whose alternative never "
               "fired is censored, not assumed to have closed.",
        "engines": ("event_driven",),
    },
    "trials": {
        "label": "how much searching this took",
        "why": "counts the distinct configurations tried under this strategy name and "
               "deflates the best result by the size of the search.",
        "engines": None,
    },
}


def validate(run_id: int, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run one validation tool against a stored run, and return its report as text.

    Text rather than a structure: every one of these reports leads with the boundary on
    its own conclusions, and the boundary is a sentence, not a field. Splitting it into
    numbers for the interface to re-assemble is exactly how the boundary gets dropped.
    """
    if kind not in VALIDATIONS:
        raise ValueError(
            f"there is no validation called {kind!r}; one of "
            f"{', '.join(sorted(VALIDATIONS))}"
        )
    if kind == "trials":
        return _trials(run_id, params)
    result = RECENT.get(run_id)
    if result is None:
        raise ValueError(
            f"run {run_id} is no longer in memory (the last {RECENT_KEEP} are kept). "
            f"These tools re-run the strategy, so they need the run itself rather than "
            f"its stored trade log -- run it again and validate that one"
        )
    engines = VALIDATIONS[kind]["engines"]
    if engines is not None and result.raw.ENGINE not in engines:
        raise ValueError(
            f"{kind} needs the {' or '.join(engines)} engine; this run used "
            f"{result.raw.ENGINE}"
        )
    if kind == "baselines":
        draws = int(params.get("draws") or 0)
        if draws and draws < 10:
            raise ValueError("random entry needs at least 10 draws, or one lucky draw "
                             "becomes the baseline")
        return {"text": _text(result.baselines(draws=draws), result.synthetic, kind)}
    if kind == "ablation":
        return {"text": _text(result.ablate(accept_coupled=bool(params.get("accept_coupled"))),
                              result.synthetic, kind)}
    tps = [float(x) for x in (params.get("take_profits") or [])]
    sls = [float(x) for x in (params.get("stop_losses") or [])]
    if not tps or not sls:
        raise ValueError(
            "the counterfactual needs the levels to compare against. The platform does "
            "not pick them: which levels are worth asking about is the research question, "
            "and a default here would be the platform answering it"
        )
    from backtest.research.counterfactual import grid_report

    grid = result.counterfactual(take_profits=tps, stop_losses=sls)
    return {"text": grid_report(grid, synthetic=result.synthetic)}


def _text(obj, synthetic: bool, kind: str) -> str:
    """One report as text, with its boundary first."""
    from backtest.research import ablation, baselines, counterfactual

    module = {"baselines": baselines, "ablation": ablation,
              "counterfactual": counterfactual}[kind]
    return module.report(obj, synthetic=synthetic)


def _trials(run_id: int, params: dict[str, Any]) -> dict[str, Any]:
    """The trial count under this run's strategy name, and the deflated best."""
    from backtest.research import tracker

    row = STORE.run(run_id)
    if row is None:
        raise ValueError(f"there is no run {run_id}")
    idea = row["strategy_name"]
    records = STORE.run_records(idea)
    unplaced = [r for r in records if not r["run_id"]]
    lines = [f"Search size under {idea!r}", "-" * 56, ""]
    if unplaced:
        lines += [
            f"  !! {len(unplaced)} of {len(records)} runs were saved before the "
            f"reproducibility fingerprint was stored, so they cannot be told apart from "
            f"a rerun. They are left out below, which makes the count a **lower bound**.",
            "",
        ]
        records = [r for r in records if r["run_id"]]
    if not records:
        return {"text": "\n".join(lines + ["  no run under this name carries a "
                                           "fingerprint, so there is nothing to count"])}
    trials = tracker.count_trials(records, idea=idea,
                                  groupings=int(params.get("groupings") or 0))
    lines += [f"  distinct configurations : {trials.distinct}",
              f"  runs                    : {trials.runs}",
              f"  reruns                  : {trials.runs - trials.distinct}"]
    if trials.best_sharpe is None:
        lines += ["", "  no run recorded a Sharpe, so there is nothing to deflate. Only "
                      "the event-driven engine builds an equity curve."]
        return {"text": "\n".join(lines)}
    # Years, because the stored Sharpe is annualised. Passing days against an annualised
    # Sharpe is the mistake the deflated-Sharpe guard refuses, and it overstates the
    # result badly -- so the unit is in the name on both sides.
    years = float(params.get("years") or 0)
    if years <= 0:
        lines += ["", "  the deflated Sharpe needs how many years the best run covered. "
                      "It is not assumed: the stored Sharpe is annualised, so the period "
                      "count has to be in years, and guessing it would change the answer."]
        return {"text": "\n".join(lines)}
    try:
        d = tracker.deflate(trials, periods=years)
    except ValueError as exc:
        # The deflated Sharpe refuses inputs it cannot support -- too few periods, or a
        # Sharpe and a period count on different bases. **The refusal is the answer**, not
        # an error to hide: a six-month backtest genuinely cannot carry one, and showing a
        # number anyway is the failure this whole tool exists to prevent.
        lines += ["", f"  the deflated Sharpe cannot be computed here: {exc}"]
        return {"text": "\n".join(lines)}
    lines += ["", f"  best Sharpe              : {d.sharpe:.3f}",
              f"  the best of {d.trials} worthless : {d.benchmark:.3f}",
              f"  deflated probability     : {d.probability:.0%}", "",
              "  " + d.verdict()]
    return {"text": "\n".join(lines)}


def _sharpe_of(result) -> float | None:
    """The run's Sharpe, or ``None`` when this engine cannot produce one.

    ``equity_metrics`` needs a daily equity curve, which the trade table does not build.
    Returning ``None`` rather than catching everything keeps a real failure visible.
    """
    if not hasattr(result.raw, "equity") or result.raw.equity is None:
        return None
    value = result.equity_metrics().get("sharpe")
    return float(value) if value is not None else None


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
