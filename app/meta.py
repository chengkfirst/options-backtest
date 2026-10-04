"""The static payload the interface asks for once, plus the trade-off surface.

Module-level functions rather than handler methods: none of this needs request state,
and the front/back contract check has to obtain the payload without starting a service.
"""

from __future__ import annotations

from typing import Any

from compilers import PLAN_HELP, USER_CODE_HELP

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


