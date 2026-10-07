"""The static payload the interface asks for once, plus the trade-off surface.

Module-level functions rather than handler methods: none of this needs request state,
and the front/back contract check has to obtain the payload without starting a service.
"""

from __future__ import annotations

from typing import Any

from compilers import PLAN_HELP, REVIEW_HELP, USER_CODE_HELP

def _tradeoff(q: dict[str, str]) -> dict[str, Any]:
    """One day's moneyness x tenor trade-off surface.

    Computed from **that day's real chain** rather than pasted from a formula — once real
    data is wired in it reflects the real skew and term structure automatically. So what
    it describes is always what you can actually buy.
    """
    from backtest.api.sdk import _as_date
    from backtest.core.config import BacktestConfig, ContractSpec, CostConfig, SyntheticChainConfig
    from backtest.data.options.chain_registry import build_chain_source
    from backtest.data.market import MarketData, VOL_PROXY_BY_SYMBOL
    from backtest.research.tradeoff import tradeoff_grid
    from backtest.signals.contracts import enrich_chain

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



def _actions() -> dict[str, Any]:
    """The action vocabulary, split into what runs and what does not."""
    from backtest.core.actions import BUILT, Action

    return {
        "built": [a.value for a in Action if not BUILT.get(a)],
        "missing": {a.value: why for a, why in BUILT.items() if why},
    }


def _pace() -> dict[str, float]:
    """Seconds of wall clock per year of backtest, by engine, from the runs on record.

    **A spread, not a number.** The runtime is driven by how many positions the engine ends
    up valuing, not by the span: on this record, five-year runs have taken anywhere from 1.6
    to 416 seconds. A point estimate would simply be wrong in a different way from the
    hardcoded one it replaces, so the median and the ninth decile both travel, and the
    interface says both.

    An engine with fewer than three runs on record gets no entry, and the interface says it
    does not know rather than quoting a number nobody measured.
    """
    from datetime import date
    from statistics import median

    from store import Store

    by_engine: dict[str, list[float]] = {}
    try:
        rows = Store().runs(limit=400, include_archived=True)
    except Exception:  # noqa: BLE001
        return {}
    for row in rows:
        seconds = row.get("seconds")
        config = row.get("config") or {}
        if not seconds or seconds <= 0:
            continue
        try:
            span = (date.fromisoformat(str(row["end_date"])[:10])
                    - date.fromisoformat(str(row["start_date"])[:10])).days / 365.25
        except (ValueError, KeyError, TypeError):
            continue
        if span <= 0:
            continue
        by_engine.setdefault(config.get("engine") or "trade_table", []).append(seconds / span)
    def spread(rates: list[float]) -> dict[str, float]:
        rates = sorted(rates)
        at = lambda q: rates[min(len(rates) - 1, int(q * len(rates)))]  # noqa: E731
        return {"median": round(median(rates), 2), "high": round(at(0.9), 2),
                "runs": len(rates)}

    return {engine: spread(rates)
            for engine, rates in by_engine.items() if len(rates) >= 3}


def _meta_payload() -> dict[str, Any]:
    """Everything static the interface needs.

    A module-level function rather than a Handler method: it needs no request state, and
    the front/back contract check has to obtain it without starting the service.
    """
    from backtest.data.market import VOL_PROXY_BY_SYMBOL
    from backtest.core.slots import COUPLINGS, as_form_spec
    from backtest.signals.registry import DEFAULT_SIGNALS, describe_signals
    from runner import VALIDATIONS

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
        # The shared action vocabulary, and **which of it the engine can actually execute**.
        # `backtest.core.actions.BUILT` is the one definition of that in code and carries the
        # reason for each gap; the composer crosses it with the architecture's per-slot
        # vocabulary so a card can say what a slot may emit and what will actually happen.
        # Read rather than restated, because a note written on this side goes stale the day
        # the engine grows one of them.
        "actions": _actions(),
        # How long a backtest actually takes, per engine, measured from the record rather
        # than guessed. The form used to say "roughly four seconds a year", which on this
        # machine's own history is half of what it takes — an estimate that is wrong by
        # a factor of two is worse than none, because the reader stops believing the
        # progress line and starts wondering whether the page has hung.
        "pace": _pace(),
        # The registered forms of each slot. The composer builds its variant pickers and
        # their parameter forms from this, so a form added in the backend is offered in
        # the interface with no edit on this side.
        "components": _components(),
        # Every cut the interface shows a verdict against, from the one registry that
        # holds them. **No threshold may be a literal in the JS**: a cut written into the
        # interface turns a judgement ("the profit is concentrated") into something that
        # reads as a measurement, and nobody can then answer "what was decided for me".
        # Each carries what it decides and why, so a flag can name the cut behind it.
        "thresholds": {
            name: {"value": t.value, "kind": t.kind, "decides": t.decides,
                   "because": t.because}
            for name, t in _thresholds().items()
        },
        # The validation tools, declared once in the runner and rendered from here. A
        # tool added there appears in the interface without a second edit, which is the
        # only arrangement under which the two sides cannot drift.
        "validations": [
            {"kind": k, "label": v["label"], "why": v["why"],
             "engines": list(v["engines"]) if v["engines"] else None}
            for k, v in sorted(VALIDATIONS.items())
        ],
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
            "review_code": (
                "def review(ctx, pos):\n"
                '    """Asked of every open position, every day.\n'
                "\n"
                "    Return None to keep holding. Uncomment a line to make\n"
                "    it a rule. kind= names the slot whose reasoning closed\n"
                "    the trade, so a staged exit still files under (3).\n"
                '    """\n'
                "    # Take profit in stages, not all at one level:\n"
                "    # if pos.ratio > 0.5:\n"
                "    #     return Reduce(0.5, \"half off at +50%\")\n"
                "\n"
                "    # A stop that moves: out after giving back a third\n"
                "    # peak = pos.mfe_so_far\n"
                "    # if peak > 0.4 and pos.ratio < peak * 0.67:\n"
                "    #     return Close(\"gave a third back\", kind=\"take_profit\")\n"
                "\n"
                "    # The thesis failed. Usually fires before a fixed stop\n"
                "    # if ctx.signal(\"close\") < ctx.signal(\"sma_50\"):\n"
                "    #     return Close(\"below the 50-day\", kind=\"stop_loss\")\n"
                "\n"
                "    return None\n"
            ),
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
        "review_help": REVIEW_HELP,
    }


def _components() -> dict[str, Any]:
    """Every registered form of every slot, with the parameters each one takes.

    **Read from the registry, never written out here.** This is what lets the composer
    offer a form it has never seen: a variant registered in the backend appears in the
    interface with its own schema, its own ranges and its own one-line description, and a
    slot the researcher must choose for cannot be silently filled in by the platform.

    The description matters as much as the schema. "stop_loss/underlying@1" tells a reader
    nothing; what they need is that it fires on a move in the thing the thesis was about
    and leaves the premium to volatility, which is the sentence the variant carries.
    """
    from backtest.core.component import SLOTS, variants

    #: How a form for this slot reaches the engine. Declared here, in the layer that
    #: already translates a filled-in form into ``run()`` arguments, rather than guessed in
    #: the browser — the interface should carry whatever the registry holds without
    #: knowing anything about what a slot *means*.
    #:
    #: ``risk`` is the book-wide rule: it acts on the whole book rather than on one trade,
    #: so the engine takes it by its own argument. Everything else is a component of a
    #: trade and goes through ``components``.
    sends_to = {slot: ("risk" if slot == "headwind" else "components") for slot in SLOTS}

    by_slot: dict[str, list[dict[str, Any]]] = {slot: [] for slot in SLOTS}
    for v in variants():
        by_slot.setdefault(v.slot, []).append({
            "slot": v.slot,
            "name": v.name,
            "version": v.version,
            "ref": v.ref,
            "describes": v.describes,
            "stateful": v.stateful,
            "sends_to": sends_to.get(v.slot, "components"),
            "params": [{
                "name": p.name, "kind": p.kind, "means": p.means,
                "lo": p.lo, "hi": p.hi, "required": p.required,
            } for p in v.params],
        })
    # Slots with no registered form are reported as empty rather than left out: "this slot
    # has one shape and it is not swappable yet" is a fact the composer should be able to
    # state, and a missing key would make it indistinguishable from a typo.
    return by_slot


def _thresholds():
    """The threshold registry, including any override in force.

    Imported here rather than at module level: the app layer loads before a backtest is
    ever run, and nothing in the interface should pull in the engine to render a form.
    """
    from backtest.core.thresholds import THRESHOLDS

    return THRESHOLDS


def _describe_gaps() -> list[str]:
    """Which of the seven components do not yet stand up.

    **Read from backtest.core.slots, never hand-written here.** A hand-written list goes
    stale once the feature lands, leaving a warning on the interface that stopped being
    true long ago — worse than showing nothing, because it looks authoritative.
    """
    from backtest.core.slots import gaps
    from backtest.signals.lifecycle import gaps as lifecycle_gaps

    return (gaps() + lifecycle_gaps()
            + ["selling: assignment of a cash-secured put is modelled, but orders are "
               "still limited to the long side"])


