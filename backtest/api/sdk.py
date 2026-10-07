"""The top-level facade. One :func:`run` goes through the whole chain.

    from backtest import run

    result = run(
        symbol="QQQ",
        start="2023-01-01", end="2023-12-31",
        entry="sma_20 > sma_50",
        select={"right": "call", "dte": 30, "delta": 0.30},
        exit={"hold_days": 10, "take_profit": 1.0, "stop_loss": 0.5, "dte_floor": 5},
    )
    result.summary()          # print a one-page conclusion
    result.trades             # the trade log, as a DataFrame
    result.export("out/")     # write it out
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from backtest.core.config import BacktestConfig, CostConfig, SyntheticChainConfig
from backtest.data.market import VOL_PROXY_BY_SYMBOL, MarketData
from backtest.data.options.chain_registry import build_chain_source
from backtest.signals.entry.expression import parse_condition
from backtest.signals.strategy import EntryRule, ExitRule
from backtest.portfolio.positioning import Sizing
from backtest.engine.strategy import Strategy
from backtest.research.sweep import SweepResult, sweep as _sweep
from backtest.engine.vectorized import TradeTableEngine, TradeTableResult
from backtest.core.errors import InvariantError, StartupError
from backtest.research.holdout import HoldOut, check as holdout_check
from backtest.research import metrics as R
from backtest.signals.contracts import SelectionRule
from backtest.signals.registry import SignalSet
from backtest.core.types import ContractSpec, Right


def _as_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _sizing_from(spec, quantity: int) -> Sizing:
    """Reduce ``sizing=`` to a :class:`Sizing`.

    With nothing given this is a fixed number of contracts — **a default, not a
    recommendation**. A fixed count lets each trade's fraction of risk drift with
    equity: after equity doubles, the same 1 contract is half the risk; after it
    halves, twice. To actually control risk, use ``risk_pct``.
    """
    if spec is None:
        return Sizing(method="fixed", contracts=quantity)
    if isinstance(spec, Sizing):
        return spec
    opts = dict(spec)
    method = str(opts.pop("method", "fixed"))
    opts.setdefault("contracts", quantity)
    return Sizing(method=method, **{k: v for k, v in opts.items() if v is not None})


def _selection_from(spec):
    """Reduce the ``select=`` argument to something the engine understands.

    Three forms: a declarative dict, a ``SelectionRule``, or an ``f(chain, view)``
    function. A function passes through untouched — its validation happens in
    ``select_contract``, which is where the day's chain is visible.
    """
    if callable(spec) and not isinstance(spec, SelectionRule):
        return spec
    if isinstance(spec, SelectionRule):
        return spec
    spec = dict(spec)
    # Required, not defaulted. See SelectionRule: delta and tenor are slot 2b, and the
    # platform choosing them makes the most consequential parameters in the strategy
    # invisible in the configuration a person reads back.
    # One tenor window, written three ways, all meaning the same thing: a band as
    # dte_lo/dte_hi (what the interface sends), a band as dte_window (what a config file
    # holds), or a point plus an explicit window. Accepting only some of them would mean a
    # file this system wrote could not be read back by it.
    spec = dict(spec)
    if "dte_window" in spec and "dte_lo" not in spec:
        lo_, hi_ = spec["dte_window"]
        spec["dte_lo"], spec["dte_hi"] = int(lo_), int(hi_)
        spec.pop("dte_window")
    if "delta_band" in spec and "delta_lo" not in spec:
        lo_, hi_ = spec.pop("delta_band")
        spec["delta_lo"], spec["delta_hi"] = float(lo_), float(hi_)
    if "iv_band" in spec and "iv_lo" not in spec:
        lo_, hi_ = spec.pop("iv_band")
        spec["iv_lo"], spec["iv_hi"] = float(lo_), float(hi_)

    missing = [k for k in ("right",) if k not in spec]
    if "dte" not in spec and not ("dte_lo" in spec and "dte_hi" in spec):
        missing.append("dte (or dte_lo and dte_hi, or dte_window)")
    if "delta" not in spec and "moneyness" not in spec and not (
            "delta_lo" in spec and "delta_hi" in spec):
        missing.append("delta (or delta_lo and delta_hi, or moneyness)")
    if missing:
        raise StartupError(
            "select needs " + ", ".join(missing) + ". These are strategy choices and the "
            "platform does not pick them: a default here would set the delta and the tenor "
            "for you and then not appear in the configuration you read back. For example "
            'select={"right": "call", "dte_lo": 21, "dte_hi": 45, "delta_lo": 0.25, '
            '"delta_hi": 0.45}'
        )
    right = spec.pop("right")
    # The interface sends bands (dte_lo/hi, delta_lo/hi); point form (dte + delta)
    # is also accepted.
    lo, hi = spec.pop("dte_lo", None), spec.pop("dte_hi", None)
    if lo is not None and hi is not None:
        window = (int(lo), int(hi))
        dte = spec.pop("dte", (int(lo) + int(hi)) // 2)
    else:
        dte = spec.pop("dte")
        if "dte_window" not in spec:
            raise StartupError(
                f"select got dte={dte} and no window. The window is a **hard** "
                f"constraint -- nothing outside it is considered -- so inventing one "
                f"around your point would widen the rule you wrote. Give "
                f'dte_window=(lo, hi), or the band form dte_lo / dte_hi.'
            )
        window = tuple(spec.pop("dte_window"))
    dlo, dhi = spec.pop("delta_lo", None), spec.pop("delta_hi", None)
    ilo, ihi = spec.pop("iv_lo", None), spec.pop("iv_hi", None)
    return SelectionRule(
        right=Right.CALL if str(right).lower().startswith("c") else Right.PUT,
        target_dte=dte,
        dte_window=window,
        # The point objective used to order within the band. When a band was given and
        # no point, this is the band's midpoint -- arithmetic on the user's own choice,
        # not a second choice made for them. Same treatment as `dte` above.
        target_delta=spec.pop(
            "delta",
            (float(dlo) + float(dhi)) / 2 if dlo is not None and dhi is not None
            and "moneyness" not in spec else None),
        moneyness=spec.pop("moneyness", None),
        min_volume=spec.pop("min_volume", None),
        min_open_interest=spec.pop("min_open_interest", None),
        delta_band=(float(dlo), float(dhi)) if dlo is not None and dhi is not None else None,
        iv_band=(float(ilo), float(ihi)) if ilo is not None and ihi is not None else None,
        max_relative_spread=spec.pop("max_spread", spec.pop("max_relative_spread", None)),
    )


def _brake_from(spec):
    """Build the book-wide risk rule as a component, or ``None``.

    **Which form** is the researcher's, not the platform's. The document lists this slot's
    responses as "to be defined" and names two examples; both are registered, and a
    configuration that does not say which one it wants is refused rather than given the
    one I happened to write first.
    """
    from backtest.core.component import build, variants

    if not spec:
        return None
    opts = dict(spec)
    if "variant" not in opts:
        offered = ", ".join(v.name for v in variants("headwind"))
        raise StartupError(
            f"risk needs a variant: which form of book-wide rule to run. Registered: "
            f"{offered}. They respond to different things -- the distance from the "
            f"high-water mark, or one session's loss -- and which matters is a question "
            f'about the strategy. For example risk={{"variant": "drawdown", "params": '
            f'{{"reduce_at": 0.10, "halt_at": 0.20, "keep": 0.5}}}}'
        )
    return build({"slot": "headwind", **opts})


def _components_from(spec) -> dict[str, Any]:
    """Turn ``{"stop_loss": {"variant": "underlying", "params": {...}}}`` into components.

    A slot given here decides that slot instead of the matching field on ``exit``. Which
    form of a slot to use is the question the first of this platform's two uses exists to
    answer, and the platform offers the forms without choosing between them.
    """
    from backtest.core.component import build

    if not spec:
        return {}
    out = {}
    for slot, one in dict(spec).items():
        if not isinstance(one, Mapping):
            raise StartupError(
                f"components[{slot!r}] should be "
                f'{{"variant": "...", "params": {{...}}}}, got {one!r}'
            )
        out[slot] = build({"slot": slot, **dict(one)})
    return out


def _exit_from(spec: Mapping[str, Any] | ExitRule) -> ExitRule:
    if isinstance(spec, ExitRule):
        return spec
    spec = dict(spec)
    # No defaults here either. The same values were written in three places -- this
    # function, the dataclass and the interface's form -- so fixing one of them would have
    # left the other two filling in a level nobody chose.
    return ExitRule(
        hold_days=spec.pop("hold_days", None),
        take_profit=spec.pop("take_profit", None),
        stop_loss=spec.pop("stop_loss", None),
        dte_floor=spec.pop("dte_floor", None),
    )


def run_batch(configs, *, runner=None):
    """Run a batch of configurations. See ``backtest.engine.runner.run_batch``.

    The default runner lives here rather than there because that module is Layer 5 and
    this is Layer 7: a default of :func:`run` inside it was a reverse import, which
    ``tools/check_deps.py`` refused.
    """
    from backtest.engine.runner import run_batch as _run_batch

    return _run_batch(configs, runner=runner if runner is not None else run)


def _scenario_limit_from(given):
    """The scenario limit from a mapping, with the unknown key refused.

    Refused rather than ignored, as everywhere else here: a misspelled ``max_los``
    accepted silently is a researcher who believes a limit is in force and is reading
    the unlimited result.
    """
    from backtest.portfolio.scenario_rule import ScenarioLimit

    if given is None:
        return None
    if isinstance(given, ScenarioLimit):
        return given
    known = {"max_loss", "scenarios"}
    unknown = sorted(set(given) - known)
    if unknown:
        raise StartupError(
            f"these are not scenario-limit settings: {unknown}. It takes "
            f"{', '.join(sorted(known))}"
        )
    missing = sorted(known - set(given))
    if missing:
        raise StartupError(
            f"a scenario limit needs {', '.join(missing)}. Which shocks a strategy "
            f"should survive is a research choice, so there is no default -- for example "
            f"scenario_limit={{'max_loss': 5000, 'scenarios': [[-0.05, 0.5]]}}"
        )
    return ScenarioLimit(max_loss=float(given["max_loss"]),
                         scenarios=tuple(tuple(float(x) for x in pair)
                                         for pair in given["scenarios"]))


def _premium_budget_from(given):
    """The premium budget from a mapping, with the unknown key refused.

    Same reason as the scenario limit beside it: a misspelled ``max_premium`` accepted
    silently is a researcher who believes the book is budgeted and is reading the
    unbudgeted result. **There is no default**, and that is deliberate -- how much of
    equity a strategy may have at risk in premium is the single most consequential number
    in a long-options book, and a platform that picks it has made the strategy's main
    decision on the researcher's behalf.
    """
    from backtest.portfolio.constraints import PremiumBudget

    if given is None:
        return None
    if isinstance(given, PremiumBudget):
        return given
    known = {"max_premium_pct", "max_premium_per_expiry_pct", "min_cash_pct"}
    unknown = sorted(set(given) - known)
    if unknown:
        raise StartupError(
            f"these are not premium-budget settings: {unknown}. It takes "
            f"{', '.join(sorted(known))}, each a share of equity"
        )
    if not given:
        raise StartupError(
            "a premium budget with no limits in it would be no budget. Set at least one "
            "of max_premium_pct, max_premium_per_expiry_pct, min_cash_pct -- for "
            "example premium_budget={'max_premium_pct': 0.10}"
        )
    try:
        return PremiumBudget(**{k: float(v) for k, v in given.items()})
    except ValueError as exc:
        raise StartupError(str(exc)) from exc


def _exposure_from(given):
    """Portfolio exposure limits from a mapping, with the unknown key refused.

    **This existed, the engine called it every candidate, and nothing could set it.**
    `ExposureLimits` had unit tests and a live call site at `loop.py`'s entry check, and
    no parameter reached it from `run()` or the interface — so the document's Constraint
    row read as built on the strength of code that could not be switched on. A reverse
    wiring check (`tests/test_reachable.py`) now fails on any engine parameter in that
    state.

    Fractions of equity rather than amounts, for the reason `ExposureLimits` gives:
    equity moves, and a dollar cap set at the start is a different constraint by the end.
    """
    from backtest.portfolio.constraints import ExposureLimits

    if given is None:
        return None
    if isinstance(given, ExposureLimits):
        return given
    known = {"max_delta_pct", "max_vega_pct", "max_positions", "on_breach"}
    unknown = sorted(set(given) - known)
    if unknown:
        raise StartupError(
            f"these are not exposure settings: {unknown}. It takes "
            f"{', '.join(sorted(known))}"
        )
    if not given:
        raise StartupError(
            "an exposure limit with nothing in it would be no limit. Set at least one of "
            "max_delta_pct, max_vega_pct, max_positions -- for example "
            "exposure={'max_delta_pct': 0.5}"
        )
    out = dict(given)
    for key in ("max_delta_pct", "max_vega_pct"):
        if key in out:
            value = float(out[key])
            if value <= 0:
                raise StartupError(
                    f"{key} is a share of equity and must be positive; got {value}. "
                    f"A limit at or below zero forbids every entry"
                )
            out[key] = value
    if "max_positions" in out:
        out["max_positions"] = int(out["max_positions"])
        if out["max_positions"] < 1:
            raise StartupError(
                f"max_positions must be at least 1; got {out['max_positions']}. "
                f"Use max_concurrent=0 if the intent is to open nothing"
            )
    return ExposureLimits(**out)


def _lifecycle_from(given) -> "Lifecycle":
    """The lifecycle settings from a mapping, with the unknown key refused.

    Refused rather than ignored for the same reason the config does it: a misspelled
    ``reentry_polcy`` accepted silently is a researcher who believes a policy is in force
    and is reading the permissive result.
    """
    from backtest.signals.lifecycle import Lifecycle

    if given is None:
        return Lifecycle()
    if isinstance(given, Lifecycle):
        return given
    known = {f for f in Lifecycle.__dataclass_fields__}
    unknown = sorted(set(given) - known)
    if unknown:
        raise StartupError(
            f"these are not lifecycle settings: {unknown}. One of "
            f"{', '.join(sorted(known))}"
        )
    return Lifecycle(**dict(given))


def check_data(
    *,
    symbol: str = "QQQ",
    start: str | date = "2015-01-01",
    end: str | date = "2024-12-31",
    chain: "str | object | None" = None,
    costs: CostConfig | None = None,
    synthetic: SyntheticChainConfig | None = None,
    refresh: bool = False,
):
    """Load the data a run would load, check it, and report -- **without running
    anything**.

    The entry point for the question "is my data usable here", which until now had no
    answer short of starting a backtest and reading its failure. The checks are the data
    layer's own (``backtest.data.pipeline``), so this reports exactly what a run would
    hit: the source and whether it is synthetic, the dual-price split, the calendar, the
    quality flags, rates, dividends, the volatility proxy, the expiries, and the chain's
    columns and availability dates.

    Takes the same ``symbol``, ``start``, ``end`` and ``chain`` as :func:`run`, so a
    configuration that checks out here is the one that will run.

    Returns a ``PipelineReport``: ``.text()`` for a person, ``.as_dict()`` for a
    program, ``.failed`` and ``.warned`` for a script that has to decide. **It raises
    nothing for bad data** -- that is the point of a report -- but it does raise if the
    data cannot be loaded at all, because then there is nothing to report on.

    >>> report = check_data(symbol="QQQ", start="2023-01-01", end="2023-12-31",
    ...                     chain={"kind": "file", "path": "your/data.parquet"})
    >>> print(report.text())                       # doctest: +SKIP
    """
    from backtest.data.pipeline import inspect_pipeline

    proxy = VOL_PROXY_BY_SYMBOL.get(symbol)
    config = BacktestConfig(
        symbol=symbol,
        start=_as_date(start),
        end=_as_date(end),
        notional_capital=100_000.0,
        contract=ContractSpec(symbol=symbol, vol_proxy=proxy),
        costs=costs or CostConfig(),
        synthetic=synthetic or SyntheticChainConfig(),
    )
    market = MarketData(config, refresh=refresh)
    return inspect_pipeline(market, build_chain_source(chain, market, config))


def run(
    *,
    engine: str = "trade_table",
    max_concurrent: int | None = None,
    symbol: str = "QQQ",
    start: str | date = "2015-01-01",
    end: str | date = "2024-12-31",
    # Required. The entry condition is slot 2 and the single largest strategy choice in
    # the run; a platform-supplied one would mean a result attributed to a rule nobody
    # wrote. `symbol`, `start` and `end` keep their defaults: the document's register puts
    # the universe and the period among the engineering trade-offs, not the strategy.
    entry: str | Callable | None = None,
    plan: Callable | None = None,
    review: Callable | None = None,
    select: Mapping[str, Any] | SelectionRule | None = None,
    exit: Mapping[str, Any] | ExitRule | None = None,  # noqa: A002 - the strategy's own term
    quantity: int = 1,
    sizing: "Mapping[str, Any] | Sizing | None" = None,
    capital: float = 100_000.0,
    costs: CostConfig | None = None,
    chain: "str | object | None" = None,
    synthetic: SyntheticChainConfig | None = None,
    name: str = "unnamed",
    refresh: bool = False,
    signals: Mapping[str, Callable] | None = None,
    shadow_days: int | None = None,
    execution_lag_days: int | None = None,
    components: "Mapping[str, Mapping[str, Any]] | None" = None,
    risk: "Mapping[str, Any] | None" = None,
    hold_out: "tuple[str | date, str | date] | None" = None,
    unlock: str = "",
    #: Slot 1 · setup / regime: ``f(ctx) -> bool``, whether this is an environment to
    #: trade in at all. Returning True **arms** the candidate; it opens nothing by
    #: itself, and entry then looks for timing inside the armed window.
    #:
    #: ``None`` is always armed, which is what the engine did before the Armed state
    #: existed. Separating the two is what makes either measurable: while they were one
    #: test, no ablation could say what a regime filter was worth, because removing it
    #: also removed the timing.
    setup: Callable | None = None,
    #: The lifecycle settings, as a mapping: ``reentry_policy``,
    #: ``cooldown_sessions``, ``max_concurrent_trades``. Every default reproduces the
    #: engine's old behaviour, so naming them changes no result.
    lifecycle: "Mapping[str, Any] | None" = None,
    #: Stop after this session and put a checkpoint on the result, so the run can be
    #: continued. The event-driven engine only: the trade table evaluates each trade
    #: independently and has no state between sessions to snapshot.
    until: "str | date | None" = None,
    #: Continue a run from a checkpoint. See ``backtest.engine.runner``.
    resume_from: "Any | None" = None,
    #: A scenario loss limit that **blocks** an entry whose addition would breach it:
    #: ``{"max_loss": 5000, "scenarios": [[-0.05, 0.5], [-0.10, 1.0]]}``. Both keys are
    #: required -- which shocks a strategy should survive is a research choice.
    scenario_limit: "Mapping[str, Any] | None" = None,
    premium_budget: "Mapping[str, Any] | None" = None,
    exposure: "Mapping[str, Any] | None" = None,
    # **A realism assumption, and it was unreachable too.** The engine refuses an entry
    # the cash cannot pay for; turning that off is what makes the two engines comparable
    # (the trade-table engine has no book and so no cash constraint), and it was settable
    # only by constructing the engine by hand. It is already in the run fingerprint.
    enforce_capital: bool = True,
) -> "Result":
    """Run one backtest.

    ``plan`` is a **plan-based strategy**: one function decides what to buy, how much,
    and how to get out. In a real trade the seven components are coupled (a deep
    drawdown means buying longer dated, betting bigger, widening the stop), and the
    parameterised form cannot express that. Giving ``plan`` bypasses ``entry`` and
    ``select``, and requires ``engine="event_driven"``. See ``backtest.signals.plan``.

    ``chain`` chooses the option data source. ``None`` uses the synthetic chain (**a
    test fixture, not a data source**). To use your own data:
    ``chain="file:/path/to/your/data"``, or pass an object implementing the
    ``ChainSource`` protocol. See ``backtest.data.options.adapters``.

    ``signals`` registers custom signals: ``{"name": lambda bars: a Series}``. Once
    registered they can be used by name in the ``entry`` expression.
    ``bars.adjusted("close")`` gives adjusted prices (signals always use adjusted
    prices, or ex-dividend days produce false gaps).

    ``shadow_days`` is how long to keep quoting a contract after its trade closes, which
    is what counterfactual exit research reads. It defaults from the threshold registry and
    0 turns it off. Event-driven only: the trade table does not step day by day.

    ``execution_lag_days`` is how many trading days pass between the decision and the fill.
    It defaults to 1 and exists here so the document's execution-lag sensitivity can
    actually be run — a strategy whose edge disappears at two days was never going to
    survive a real desk.

    ``engine`` chooses the engine:

    ``"trade_table"`` (the default)
        Each trade computed independently; two orders of magnitude faster, and good
        for sweeps. **No equity curve, so no drawdown and no Sharpe.**
    ``"event_driven"``
        Steps day by day with portfolio state. Slower, but gives the equity curve,
        drawdown, capital constraints and concurrency limits. ``max_concurrent``
        applies only to it.

    On the same configuration and with no constraints the two engines produce the same
    trade log — which is the basis for checking each against the other.
    """
    if engine not in ("trade_table", "event_driven"):
        raise StartupError(f"unknown engine {engine!r}; one of trade_table / event_driven")
    proxy = VOL_PROXY_BY_SYMBOL.get(symbol)
    if proxy is None:
        raise StartupError(
            f"unknown symbol {symbol}; known symbols and their volatility proxies: "
            f"{VOL_PROXY_BY_SYMBOL}"
        )
    lag = 1 if execution_lag_days is None else int(execution_lag_days)
    config = BacktestConfig(
        symbol=symbol,
        start=_as_date(start),
        end=_as_date(end),
        execution_lag_days=lag,
        # The floor has to clear the lag, or an exit decided on T cannot be filled before
        # the contract expires and the position is dragged into settlement. The config
        # enforces it; raising the default here keeps a longer lag usable rather than
        # turning it into a validation error the caller has to solve.
        min_days_to_expiry=max(5, lag + 1),
        notional_capital=capital,
        contract=ContractSpec(symbol=symbol, vol_proxy=proxy),
        costs=costs or CostConfig(),
        synthetic=synthetic or SyntheticChainConfig(),
    )

    # The document's research protocol opens with the hold-out, and nothing enforced it:
    # a run could be scored on data already looked at, repeatedly, with neither the result
    # nor the record of it saying so. Checked before any work is done, so a refusal costs
    # nothing.
    reserved = (HoldOut(_as_date(hold_out[0]), _as_date(hold_out[1]))
                if hold_out else None)
    hold_out_note = holdout_check(reserved, start=config.start, end=config.end,
                                  unlock=unlock)

    market = MarketData(config, refresh=refresh)
    signal_set = SignalSet.from_bars(market.bars, extra=signals, vol_proxy=market.vol_proxy)
    chain_source = build_chain_source(chain, market, config)

    if entry is None and plan is None:
        raise StartupError(
            "run() needs an entry condition, for example entry=\"close < sma_20 * 0.98\". "
            "It is the largest strategy choice in the run and the platform does not supply "
            "one -- a default would attribute the result to a rule nobody wrote. A plan "
            "strategy decides entry itself and needs no separate condition."
        )
    if isinstance(entry, str):
        condition, requires = parse_condition(entry, signal_set.names)
        entry_rule = EntryRule(entry, condition, requires, is_expression=True)
    elif entry is None:
        # A plan decides its own entries, so there is no condition to parse. The rule is a
        # labelled placeholder rather than a condition, and it never fires.
        entry_rule = EntryRule("decided by the plan", lambda view: False, ())
    else:
        entry_rule = EntryRule(getattr(entry, "__name__", "custom"), entry, ())

    built = _components_from(components)
    strategy = Strategy(
        plan=plan,
        review=review,
        sizing=_sizing_from(sizing, quantity),
        entry=entry_rule,
        select=_selection_from(select or {}),
        exit=_exit_from(exit or {}),
        quantity=quantity,
        name=name,
        components=built,
        setup=setup,
        lifecycle=_lifecycle_from(lifecycle),
    )
    if engine == "event_driven":
        from backtest.engine.loop import EventDrivenEngine

        outcome = EventDrivenEngine(
            market, chain_source, signal_set, config, max_concurrent=max_concurrent,
            shadow_days=shadow_days, brake=_brake_from(risk),
            scenario_limit=_scenario_limit_from(scenario_limit),
            budget=_premium_budget_from(premium_budget),
            limits=_exposure_from(exposure),
            enforce_capital=enforce_capital,
        ).run(strategy, until=_as_date(until) if until else None,
              resume_from=resume_from)
    else:
        if exposure is not None:
            raise StartupError(
                "the trade-table engine cannot enforce an exposure limit. The limit is "
                "on the **book's** delta and vega, and the trade table evaluates each "
                "trade independently with no book to aggregate. Use "
                "engine='event_driven'"
            )
        if not enforce_capital:
            raise StartupError(
                "the trade-table engine has no cash constraint to turn off: it "
                "evaluates each trade independently without a running balance, which is "
                "what enforce_capital=False makes the event-driven engine do. Passing it "
                "here would read as a setting that had an effect"
            )
        if premium_budget is not None:
            raise StartupError(
                "the trade-table engine cannot enforce a premium budget. The budget is "
                "a limit on what the **book** has at risk at once, and the trade table "
                "evaluates each trade independently with no book -- there is nothing for "
                "a total to be a total of. Use engine='event_driven'"
            )
        if scenario_limit is not None:
            raise StartupError(
                "the trade-table engine cannot enforce a scenario limit. The limit is "
                "checked against the book as it would be with the trade in it, and the "
                "trade table evaluates each trade independently with no book. Use "
                "engine='event_driven'"
            )
        if until is not None or resume_from is not None:
            raise StartupError(
                "the trade-table engine cannot be stopped at a session or resumed. It "
                "evaluates each trade independently over the whole range rather than "
                "stepping day by day, so there is no state between sessions to "
                "snapshot. Use engine='event_driven'"
            )
        if risk:
            raise StartupError(
                "a book-wide risk rule needs the event-driven engine: scaling the book "
                "down or closing it acts on positions that are open, and the trade table "
                "computes each trade independently with no book to act on. Rerun with "
                'engine="event_driven".'
            )
        if shadow_days:
            raise StartupError(
                "shadow tracking needs the event-driven engine: the trade table does not "
                "step day by day, so there is no day on which to quote a closed trade's "
                "contract. Use engine=\"event_driven\"."
            )
        if max_concurrent is not None:
            raise StartupError(
                "the trade-table engine computes trades independently and they know "
                "nothing of each other, so it cannot support a concurrency limit. For "
                "max_concurrent use engine=\"event_driven\"."
            )
        outcome = TradeTableEngine(market, chain_source, signal_set, config).run(strategy)
    return Result(outcome, market, chain_source, signal_set, hold_out=hold_out_note)


@dataclass(frozen=True, slots=True)
class Result:
    """What a run produces. ``trades`` is the raw log; everything else is a view
    onto it."""

    raw: TradeTableResult
    _market: Any = None
    _chain: Any = None
    _signals: Any = None
    #: Whether a hold-out was declared, and whether this run reached into it.
    #:
    #: Carried on the result rather than left to the caller to remember, because the
    #: question a reader has about a number is whether it is out of sample -- and "no
    #: hold-out was declared" and "declared and respected" are different answers that
    #: would otherwise look identical.
    hold_out: Mapping[str, Any] = field(default_factory=dict)

    # ---- the data ----
    @property
    def trades(self) -> pd.DataFrame:
        return self.raw.trades

    @property
    def concurrency(self) -> pd.Series:
        return self.raw.concurrency

    @property
    def events(self) -> pd.DataFrame:
        return self.raw.events.to_frame()

    # ---- views ----
    def stats(self) -> dict[str, Any]:
        return R.trade_stats(self.trades)

    def by_exit_reason(self) -> pd.DataFrame:
        return R.by_exit_reason(self.trades)

    def distribution(self) -> dict[str, Any]:
        return R.pnl_distribution(self.trades, self.raw.config.notional_capital)

    def histogram(self, bins: int = 20) -> pd.DataFrame:
        return R.histogram(self.trades, bins)

    def costs(self) -> dict[str, Any]:
        return R.cost_breakdown(self.trades)

    def spread(self) -> dict[str, Any]:
        """The spread paid, as a share of the premium. See
        :func:`backtest.research.metrics.spread_paid`."""
        return R.spread_paid(self.trades)

    def vol_premium(self) -> dict[str, Any]:
        """IV at entry against the volatility realised while held. See
        :func:`backtest.research.metrics.iv_versus_realized`.

        Needs the daily path, so the trade-table engine returns the reason rather than a
        figure -- it evaluates each trade over the whole range without stepping through
        it, and there is no path to take a volatility of.
        """
        paths = getattr(self.raw, "paths_frame", None)
        return R.iv_versus_realized(self.trades,
                                    paths() if callable(paths) else paths)

    def provenance(self) -> dict[str, Any]:
        out = R.data_provenance(self.trades)
        if self.hold_out:
            out["hold_out"] = dict(self.hold_out)
        # Whether the look-ahead cut rests on the data or on an assumption.
        #
        # The document's first data rule is that nothing enters a backtest without an
        # availability date. A source that supplies none is **assumed** to have been
        # available on the day it describes, which is right for a clean daily snapshot and
        # wrong for a vendor that delivers late, revises, or backfills. The assumption is
        # defensible; being unable to tell whether it was made is not.
        source = getattr(self, "_chain", None)
        assumed = getattr(source, "availability_assumed", None)
        if assumed is not None:
            out["availability_assumed"] = bool(assumed)
            out["availability_note"] = (
                "the data carried no availability dates, so every row is assumed to have "
                "been known on the day it describes. True for a clean daily snapshot; "
                "wrong for a feed that arrives late or revises"
                if assumed else
                "availability dates came from the data, and rows not yet known were "
                "withheld"
            )
            withheld = getattr(source, "_withheld", 0)
            if withheld:
                out["rows_withheld_as_unknown"] = int(withheld)
        return out

    def why(self, trade_id: int | None = None) -> str:
        """Why a fill happened: the chain back to the component that decided it.

        Requirement 0.6 -- "a fill traced back through order -> target -> signal ->
        component, with the data values used". Without an argument every fill in the run;
        with a ``trade_id``, that trade's.

        **It prints the gaps.** A chain that is short because a link was never set looks
        exactly like a complete one, so each trace says which of the four hops it found
        and whether it reached a slot. A run where every fill is explainable and one
        where none is produce the same trade table.
        """
        from backtest.core.events import EventType

        bus = getattr(self.raw, "bus", None)
        if bus is None:
            return ("this run has no event stream, so no fill can be traced. The "
                    "trade-table engine does not publish one -- it has no orders and no "
                    "targets to publish, which is the same gap as requirement 0.1")

        fills = [e for e in bus.of_type(EventType.FILL)
                 if trade_id is None or e.trade_id == trade_id]
        if not fills:
            which = "" if trade_id is None else f" for trade {trade_id}"
            return f"no fills{which} in this run"

        traces = [bus.trace(f.id) for f in fills]
        broken = [t for t in traces if not t.complete]
        lines = [f"Why · {len(fills)} fill(s), "
                 f"{len(traces) - len(broken)} traceable to a component", "─" * 68]
        for trace in traces:
            lines += [trace.describe(), ""]
        if broken:
            lines.append(f"** {len(broken)} fill(s) cannot be traced to a decision. A "
                         f"fill with no chain is one nobody can explain, and it is not "
                         f"visible in the trade table **")
        return "\n".join(lines)

    def turnover(self) -> dict:
        """How much was traded relative to the capital employed.

        Beside ``costs()`` rather than instead of it: the cost share says what was paid,
        and turnover says how much activity produced it.
        """
        from backtest.research.metrics import turnover

        # `self.raw.equity`, and absent on the trade-table engine -- which is a refusal
        # with a reason rather than an AttributeError, as `equity_metrics` does it. The
        # doc-example guard caught this: I wrote `self.equity`, which does not exist,
        # and documented the call without running it.
        equity = getattr(self.raw, "equity", None)
        if equity is None:
            raise AttributeError(
                "the trade-table engine has no equity curve, so turnover has nothing to "
                "divide by. Rerun with engine=\"event_driven\"."
            )
        return turnover(self.trades, equity)

    def manifest(self) -> dict[str, Any]:
        """Configuration, provenance and dependency versions — what makes the result
        re-executable — and the ``run_id`` that identifies this exact combination.

        The ``run_id`` is the hash of the four things the architecture document says
        determine a result: the strategy config, the code commit, the data version and
        the run settings. It answers the question a counter cannot: **have I run this
        exact thing before.** A dirty working tree shows up as ``+dirty`` on the commit,
        because a result from uncommitted code cannot be reproduced from the commit alone.
        """
        from backtest.core.run_id import code_version, guards_were_green, run_id

        strategy = {
            "name": self.raw.strategy.name,
            "entry": self.raw.strategy.entry.name,
            "entry_is_expression": self.raw.strategy.entry.is_expression,
            "quantity": self.raw.strategy.quantity,
            "select": (dataclasses.asdict(self.raw.strategy.select)
                       | {"right": self.raw.strategy.select.right.value}
                       if not callable(self.raw.strategy.select) else "<callable>"),
            "exit": dataclasses.asdict(self.raw.strategy.exit),
            "sizing": dataclasses.asdict(self.raw.strategy.sizing),
            "plan": self.raw.strategy.plan is not None,
            "review": self.raw.strategy.review is not None,
            # Each swapped-in component as slot/name@version plus its parameters. The
            # reference carries the version, so a result from version 1 of a stop cannot
            # be mistaken for one from version 2 -- which is the whole reason a component
            # is versioned.
            "components": {
                slot: {"ref": c.ref, "params": dict(c.params)}
                for slot, c in sorted(self.raw.strategy.components.items())
            },
            # Slot 1, and the lifecycle settings. Both change which trades happen: a
            # setup slot that does not arm removes all of them, and a reentry policy
            # removes most of them -- 136 trades becomes 1 under `once`.
            #
            # **Fourth time this class of gap has appeared**: something that moves the
            # result reaching the engine without reaching the fingerprint. It was found
            # here by a round-trip check reporting two runs identical when one had a
            # five-session cooldown and the other had none.
            "setup": self.raw.strategy.setup is not None,
            "lifecycle": dataclasses.asdict(self.raw.strategy.lifecycle),
        }
        settings = self.raw.config.describe()
        # Which engine ran, and the limits it ran under. These change the trades and are
        # not part of the config, so they are hashed separately and explicitly.
        execution = {"engine": self.raw.ENGINE} | dict(self.raw.limits)
        # The book-wide risk rule, which changes which trades happen and how large they
        # are. It was missing, so two runs under different rules -- and under no rule at
        # all -- shared one id. Third time this class of gap has appeared: a thing that
        # moves the result reaching the engine without reaching the fingerprint.
        rule = getattr(self.raw, "risk_rule", None)
        execution["risk_rule"] = (
            {"ref": rule.ref, "params": dict(rule.params)} if rule is not None else None
        )
        if len(execution) == 1:
            raise InvariantError(
                f"the {self.raw.ENGINE} engine produced a result with no recorded limits, "
                f"so the run fingerprint cannot tell it from a run under different ones"
            )
        provenance = self.provenance()
        # The data version: with a real source this is its snapshot id. On the synthetic
        # chain it is the generator's parameters, because those are what decide the
        # prices — calling it "synthetic" alone would hash two different chains alike.
        data_version = ("synthetic:" + str(sorted(settings.get("synthetic", {}).items()))
                        if provenance.get("all_synthetic") else "vendor")
        # **The cleaning rules, which are part of what data the run saw.**
        #
        # The document's second data rule is that cleaning is replayable on the same raw
        # data and the same rule version. The rules never reached the fingerprint, so a
        # run that dropped every crossed quote and one that kept them shared an id --
        # and a sensitivity test over cleaning, which the register asks for by name,
        # would have reported one run repeated. Sixth time this class of gap has turned
        # up: something that moves the result reaching the engine and not the id.
        cleaning = getattr(getattr(self, "_chain", None), "cleaning", None)
        if cleaning is not None and cleaning.version():
            data_version += " clean:" + str(cleaning.version())
        commit = code_version()
        return {
            "run_id": run_id(strategy=strategy, settings=settings, execution=execution,
                             data_version=data_version, commit=commit),
            "code": commit,
            "data_version": data_version,
            "strategy": strategy,
            "config": settings,
            "execution": execution,
            "environment": R.environment(),
            # Whether the guards passed on this exact code. A result from a commit whose
            # suite was failing is a different kind of result, and nothing recorded which.
            "guards": guards_were_green(),
            "provenance": provenance,
        }

    # ---- presentation ----
    def summary(self, printout: bool = True) -> str:
        lines: list[str] = []
        prov = self.provenance()
        if prov.get("all_synthetic"):
            lines += [
                "!! This result rests on a synthetic option chain and does not "
                "represent prices that could be traded.",
                "!! The synthetic chain has no volatility skew and no realistic IV "
                "dynamics; option-side parameters (selection, take profit, stop loss) "
                "must not be tuned on it.",
                "",
            ]
        elif prov.get("mixed"):
            lines += [f"!! Synthetic data covers {prov['synthetic_share']:.1%} of the "
                      f"trades; discount the conclusions accordingly.", ""]

        s = self.stats()
        if s["count"] == 0:
            lines.append("No trades were produced in this range.")
            text = "\n".join(lines)
            if printout:
                print(text)
            return text

        lines += [
            f"strategy {self.raw.strategy.name} · {self.raw.config.symbol} · "
            f"{self.raw.config.start} ~ {self.raw.config.end}",
            f"{s['count']} trades | win rate {s['win_rate']:.1%} | mean holding "
            f"{s['avg_holding_days']:.1f} trading days",
            f"gross {s['gross_pnl']:,.0f} | fees {s['fees']:,.0f} | "
            f"net {s['net_pnl']:,.0f}   (the spread is inside gross — see costs below)",
        ]
        # **Three returns where there is a curve to compute them from.** `total_return`
        # is the account's, and the account earns interest on its cash: the ledger keeps
        # that as its own term of the identity and this line is where it used to be
        # folded back in and read as the strategy's.
        try:
            m = self.equity_metrics()
        except Exception:                                   # noqa: BLE001
            m = None                                        # no equity curve
        if m is not None:
            lines += [
                f"strategy {m['strategy_total_return']:+.1%} "
                f"({m['strategy_annualised']:+.2%} a year) | "
                f"account {m['account_total_return']:+.1%} "
                f"({m['annualised']:+.2%}) | "
                f"interest {m['interest_earned']:,.0f} earned on cash",
                f"sharpe {m['sharpe']:.2f} | sortino {m['sortino']:.2f}   "
                f"(on the excess return, over a {m['risk_free_rate']:.2%} risk-free rate)",
            ]
        lines += [
            "",
            "— per-trade P&L distribution —",
        ]
        d = self.distribution()
        q = d["quantiles_abs"]
        lines += [
            f"  5% {q['q5']:,.0f} | 25% {q['q25']:,.0f} | median {q['q50']:,.0f} | "
            f"75% {q['q75']:,.0f} | 95% {q['q95']:,.0f}",
            f"  mean {d['mean']:,.0f} | best {d['max_win']:,.0f} | "
            f"worst {d['max_loss']:,.0f}",
            f"  skew {d['skew']:.2f} | kurtosis {d['kurtosis']:.2f}",
        ]
        if d["mean_unlike_median"]:
            lines.append("  the median and the mean have opposite signs, which is the "
                         "ordinary shape of a long-options book")
            lines.append("  (a low win rate with a long right tail). The mean is not a "
                         "typical trade here; read both.")

        lines += ["", "— by exit reason —"]
        for reason, row in self.by_exit_reason().iterrows():
            lines.append(
                f"  {reason:<12} {int(row['count']):>4} trades | win rate "
                f"{row['win_rate']:.0%} | total {row['net_pnl']:>10,.0f}"
            )

        c = self.costs()
        # **The spread on its own line, and first.** This section used to list the three
        # fees and total them, which for an option buyer is the smaller half of the cost
        # and reads as the whole of it -- the more so because `slippage_per_contract`
        # defaults to 0, so the line said the cost of trading was negligible.
        lines += ["", "— costs —"]
        if c.get("spread") is not None:
            lines.append(
                f"  spread (paid inside the fill) {c['spread']:,.0f} | fees "
                f"{c['fees']:,.0f} | total {c['total']:,.0f} "
                f"({c['cost_pct_of_premium']:.1%} of the {c['premium_traded']:,.0f} "
                f"premium traded)")
        else:
            lines.append(
                f"  fees {c['fees']:,.0f} ({c['fees_pct_of_premium']:.1%} of the "
                f"{c['premium_traded']:,.0f} premium traded). {c['spread_note']}")
        lines.append(
            f"  commission {c['commission']:,.0f} | regulatory {c['regulatory']:,.0f} | "
            f"slippage {c['slippage']:,.0f}")
        # Against the edge before costs, which is gross rebuilt at the mid: the spread is
        # inside every fill price, so a ratio to plain gross has it on both sides of the
        # division. Absent rather than misleading when there is no edge to be a share of.
        if c.get("cost_pct_of_gross_at_mid") is not None:
            lines.append(
                f"  that is {c['cost_pct_of_gross_at_mid']:.0%} of the "
                f"{c['gross_pnl_at_mid']:,.0f} this made before costs, filling at the "
                f"mid")
        elif c.get("gross_note"):
            lines.append(f"  {c['gross_note']}")
        if c["eaten_by_costs"]:
            lines.append("  !! Gross is positive and net is negative: the edge does not "
                         "cover the trading costs")

        cap = self.raw.capital_check()
        # **It said "the trade-table engine" on every run, including event-driven ones.**
        # The capital figures below are worth printing either way -- peak concurrent
        # positions and the capital they needed -- but the sentence above them described
        # a limitation the event-driven engine does not have, on its own output.
        if self.raw.ENGINE == "trade_table":
            lines += [
                "",
                "— what this engine does not cover —",
                "  The trade-table engine computes each trade independently: no capital "
                "constraint and no portfolio equity curve, so no drawdown and no Sharpe.",
            ]
        else:
            lines += ["", "— capital —"]
        lines.append(
            f"  Peak concurrent positions {cap['peak_positions']}, needing about "
            f"{cap['peak_capital']:,.0f} (notional {cap['notional_capital']:,.0f})")
        if not cap["feasible"]:
            lines.append("  !! Peak capital exceeds the notional, so this result is not "
                         "achievable with the capital given; recheck it with the "
                         "event-driven engine")

        pe = R.passive_expiry_check(self.trades)
        passive = pe.get("itm", 0) + pe.get("otm", 0)
        if passive:
            lines.append(
                f"  !! {passive} trades reached a passive expiry "
                f"({pe['itm']} in the money / {pe['otm']} out): the exit rules did not "
                f"take effect before expiry, usually because the spread blew out close "
                f"to expiry and nothing could be filled"
            )
        if pe.get("rule_failed"):
            lines.append("  !! An in-the-money passive expiry among them means a "
                         "profitable position that could have been closed was dragged "
                         "into settlement")

        ev = self.raw.events.counts()
        if ev:
            lines += ["", "— run events —",
                      "  " + " | ".join(f"{k}:{v}" for k, v in sorted(ev.items()))]

        text = "\n".join(lines)
        if printout:
            print(text)
        return text

    # ---- risk and research ----
    def attribution(self):
        """P&L attribution: delta / gamma / vega / theta / residual."""
        from backtest.accounting.attribution import attribute

        return attribute(self.trades)

    def attribution_daily(self):
        """P&L attribution stepped **day by day**, with the spread as its own term.

        The one-step `attribution()` expands from entry to exit on the entry greeks; over
        a multi-week option hold its residual runs to tens of percent. This uses each
        day's own greeks and reports the spread separately instead of leaving it in the
        residual. Needs the daily path, so it is event-driven only.
        """
        from backtest.accounting.attribution import attribute_daily

        paths = getattr(self.raw, "paths_frame", None)
        if paths is None:
            raise AttributeError(
                "the trade-table engine produces no daily path, so attribution can only "
                "be the one-step kind. Rerun with engine=\"event_driven\"."
            )
        return attribute_daily(self.trades, paths)

    def attribution_by(self, column: str, bins: int = 4):
        from backtest.accounting.attribution import attribute_by

        return attribute_by(self.trades, column, bins)

    def greeks(self):
        """Daily portfolio greeks. Only the event-driven engine has them."""
        frame = getattr(self.raw, "greeks_frame", None)
        if frame is None:
            raise AttributeError(
                "the trade-table engine has no portfolio state, so it cannot compute "
                "daily greeks. Rerun with engine=\"event_driven\"."
            )
        return frame

    def stress(self, *, day=None, days_forward: int = 5):
        """One day's stress grid. By default the day with the most positions."""
        from backtest.portfolio.scenarios import stress_grid

        daily = getattr(self.raw, "greeks", ())
        if not daily:
            raise AttributeError("daily greeks need engine=\"event_driven\"")
        pick = (max(daily, key=lambda g: g.n_positions) if day is None
                else next((g for g in daily if g.day == day), None))
        if pick is None:
            raise KeyError(f"{day} is not in the backtest range")
        return stress_grid(pick, days_forward=days_forward)

    def scenarios(self, windows=None):
        """Historical scenario replay. It contains no expansion and is therefore more
        trustworthy than a stress test."""
        from backtest.portfolio.scenarios import KNOWN_SCENARIOS, scenario_replay

        equity = getattr(self.raw, "equity", None)
        if equity is None:
            raise AttributeError("an equity curve needs engine=\"event_driven\"")
        return scenario_replay(equity, windows or KNOWN_SCENARIOS)

    def excursion(self):
        """MFE / MAE: where the take-profit and the stop belong. It detects whether
        the distribution is censored by the current rules."""
        from backtest.research.excursion import study

        return study(self.trades, self.raw.strategy.exit)

    def counterfactual(self, *, take_profits, stop_losses):
        """Replay every closed trade under alternative exit levels.

        The levels are **required**: which ones are worth exploring is a strategy choice,
        which the architecture document permits optimising under its protocol and does not
        permit the platform picking. Needs the event-driven engine with shadow tracking on,
        because an alternative that fires after the real exit is only observable if the
        contract was still being quoted.
        """
        from backtest.research.counterfactual import sweep_levels

        paths = getattr(self.raw, "paths_frame", None)
        shadow = getattr(self.raw, "shadow_frame", None)
        if paths is None or shadow is None:
            raise AttributeError(
                "counterfactual exits need the event-driven engine: the trade table "
                "collects neither the path while held nor the shadow after the exit. "
                "Rerun with engine=\"event_driven\"."
            )
        if shadow.empty:
            raise AttributeError(
                "nothing was shadowed, so an alternative exit that fires after the real one "
                "cannot be observed and every such trade would come back censored. Rerun "
                "with shadow_days set above 0."
            )
        return sweep_levels(self.trades, paths, shadow,
                            take_profits=take_profits, stop_losses=stop_losses)

    def _risk_free_rate(self) -> float:
        """The period's average risk-free rate, from the series the interest was accrued
        at. Falls back to the config constant, and says which in `equity_metrics`."""
        market = getattr(self, "_market", None)
        rates = getattr(market, "_rates", None)
        if rates is not None and len(rates):
            # The series is datetime-indexed and the config holds dates, which do not
            # compare. Normalised rather than assumed either way.
            index = pd.to_datetime(rates.index)
            cut = rates.loc[(index >= pd.Timestamp(self.raw.config.start))
                            & (index <= pd.Timestamp(self.raw.config.end))]
            if len(cut):
                return float(cut.mean())
        return float(self.raw.config.fallback_risk_free_rate)

    def equity_metrics(self) -> dict:
        """The metrics that need an equity curve. The trade-table engine cannot
        produce them and says so."""
        import numpy as np

        equity = getattr(self.raw, "equity", None)
        if equity is None:
            raise AttributeError(
                "the trade-table engine has no equity curve, so there is no drawdown "
                "and no Sharpe. Rerun with engine=\"event_driven\"."
            )
        curve = equity["equity_mid"]
        liq = equity["equity_liq"]
        returns = curve.pct_change().dropna()
        years = max(len(curve) / 252.0, 1e-9)
        drawdown = curve / curve.cummax() - 1
        vol = float(returns.std() * np.sqrt(252)) if len(returns) > 1 else float("nan")
        annual = float((curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1)

        # ── three returns, named apart ─────────────────────────────────────────
        #
        # **The ledger keeps interest as its own term of the accounting identity and this
        # function folded it back in.** `equity_mid` is cash plus holdings, and the cash
        # carries every dollar of interest earned, so `total_return` was the account's
        # return being read as the strategy's. On this project's own fixture that is a
        # difference of thousands on a strategy whose net P&L is a few hundred.
        #
        # So three figures with three names, rather than one figure that is quietly the
        # wrong one for most questions:
        #
        #   strategy  what the trading produced, interest removed
        #   account   what the account did, interest included -- what a statement shows
        #   excess    the account's return less the risk-free rate, which is the one a
        #             risk-adjusted ratio is defined on
        strategy_curve = equity.get("strategy_equity")
        if strategy_curve is None:                 # a curve from before the column existed
            strategy_curve = curve
        strategy_returns = strategy_curve.pct_change().dropna()
        strategy_annual = float(
            (strategy_curve.iloc[-1] / strategy_curve.iloc[0]) ** (1 / years) - 1)
        strategy_vol = (float(strategy_returns.std() * np.sqrt(252))
                        if len(strategy_returns) > 1 else float("nan"))

        # **The rate the cash actually earned, not the config's fallback.** Interest is
        # accrued session by session off the market's own rate series; deducting the
        # fallback constant instead would subtract a different number from the one that
        # was added, which is the mismatch this whole separation exists to remove. The
        # fallback is used only when there is no series -- the same order the ledger uses.
        rate = self._risk_free_rate()
        excess_annual = annual - rate

        # **Sharpe on the excess return, which is its definition.** It was
        # `annualised / vol` on a curve that *included* the interest -- the risk-free
        # rate added once and not subtracted once, both times in the flattering
        # direction. At 4.5% against a 20-40% vol that is 0.1 to 0.2 of Sharpe.
        sharpe = excess_annual / vol if vol and np.isfinite(vol) else float("nan")
        downside = strategy_returns[strategy_returns < 0]
        downside_vol = (float(downside.std() * np.sqrt(252))
                        if len(downside) > 1 else float("nan"))
        sortino = (excess_annual / downside_vol
                   if downside_vol and np.isfinite(downside_vol) else float("nan"))

        return {
            # The strategy on its own, which is what a component decision is judged on.
            "strategy_total_return": float(
                strategy_curve.iloc[-1] / strategy_curve.iloc[0] - 1),
            "strategy_annualised": strategy_annual,
            "strategy_annualised_vol": strategy_vol,
            "interest_earned": float(equity["interest"].iloc[-1])
            if "interest" in equity.columns else float("nan"),
            # The account, interest included. `total_return` keeps its name and its
            # meaning -- it was always the account's figure -- and is now labelled.
            "total_return": float(curve.iloc[-1] / curve.iloc[0] - 1),
            "account_total_return": float(curve.iloc[-1] / curve.iloc[0] - 1),
            "annualised": annual,
            "annualised_vol": vol,
            # Excess, and the rate it is excess of.
            "risk_free_rate": rate,
            "excess_annualised": excess_annual,
            "sharpe": sharpe,
            "sortino": sortino,
            "ratio_basis": ("sharpe and sortino are on the excess return "
                            f"(account {annual:.2%} less the risk-free {rate:.2%}); "
                            f"the strategy alone returned {strategy_annual:.2%}"),
            "max_drawdown": float(drawdown.min()),
            "max_drawdown_liq": float((liq / liq.cummax() - 1).min()),
            "mean_capital_used": float((equity["deployed"] / curve).mean()),
            "peak_positions": int(equity["positions"].max()),
            "skew": float(returns.skew()), "kurtosis": float(returns.kurtosis()),
        }

    def investor_view(self) -> dict:
        """The investor's view: is this alpha or leveraged beta, how long underwater,
        where the returns concentrate.

        The benchmark is **the underlying's close over the same period**, so beta
        answers exactly "how much of this return was the underlying rising". Only the
        event-driven engine has an equity curve; under the trade table this says why.
        """
        from backtest.research.investor import summary

        bars = self._market.bars
        bench = bars.adjusted("close")
        return summary(self.raw.equity, self.trades, benchmark=bench,
                       synthetic=self.synthetic,
                       risk_free=self._risk_free_rate())

    @property
    def synthetic(self) -> bool:
        """Whether the option prices came from the synthetic chain.

        Read from the ``source`` column of the trades, not from a flag anyone set, which is
        what makes the boundary on every conclusion automatic rather than remembered.
        """
        return bool(self.provenance().get("all_synthetic"))

    def report(self) -> str:
        """Every research tool's output, each with its boundary stated.

        One call rather than several, because the boundary is the part that gets skipped
        when each tool is invoked separately — and it is the part that decides whether any
        of the numbers may be acted on.
        """
        from backtest.research import ablation, baselines

        blocks = [baselines.report(self.baselines(), synthetic=self.synthetic)]
        try:
            blocks.append(ablation.report(self.ablate(), synthetic=self.synthetic))
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"Ablation could not run: {type(exc).__name__}: {exc}")
        try:
            blocks.append(self.excursion().text(synthetic=self.synthetic))
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"MFE / MAE could not run: {type(exc).__name__}: {exc}")
        return "\n\n".join(blocks)

    def baselines(self, *, draws: int = 0) -> list:
        """Separate skill from structure: would the underlying, or random timing, have
        done as well?

        Two comparisons, both from the architecture document:

        - **a delta-equivalent position in the underlying** — if shares with the same
          entry exposure beat the option, the option added nothing, and every hour spent
          on selection and exit levels was spent on a worse instrument
        - **random entries with the same structure** — the same contract rules, the same
          sizing and the same exits, on dates drawn at random. If the signal cannot beat
          that, what looks like timing is the drift plus the structure

        ``draws`` is how many random draws to run; 0 skips the random baseline, because
        each draw is a full backtest and the cost should be asked for, not imposed. One
        draw is never enough and is refused — the comparison is a percentile in a
        distribution, and a single run of it reads as a win or a loss that it cannot
        support.

        Run this **before** refining anything. Tuning a take-profit level on a strategy
        that cannot beat random entries is work on a number that does not matter.
        """
        from backtest.research.baselines import delta_equivalent, random_entry

        out = [delta_equivalent(self.trades, self._market.bars,
                                multiplier=self.raw.config.contract.multiplier)]
        if not draws:
            return out

        trades = self.trades
        if trades.empty:
            return out
        sessions = list(self._market.sessions())
        entries = len(trades)
        strategy = self.raw.strategy
        config, market, chain, signals = (self.raw.config, self._market, self._chain,
                                          self._signals)
        engine_name = getattr(self.raw, "ledger", None) is not None

        def one_draw(dates) -> float:
            """One run with the entry replaced by "enter on these dates".

            Everything else is the same object the strategy used, so the only difference
            is the timing — which is the whole point of the comparison.
            """
            from dataclasses import replace

            wanted = set(dates)
            rule = EntryRule("random", lambda view: view.day in wanted, ())
            variant = replace(strategy, entry=rule)
            if engine_name:
                from backtest.engine.loop import EventDrivenEngine

                result = EventDrivenEngine(market, chain, signals, config).run(variant)
            else:
                result = TradeTableEngine(market, chain, signals, config).run(variant)
            return float(sum(r.net_pnl for r in result.records))

        out.append(random_entry(
            one_draw, n_entries=entries, sessions=sessions, draws=draws,
            # The strategy's own entry dates, so the draw matches their distribution over
            # time. Without this the baseline is spread across every period while the
            # strategy may have traded in one, and the comparison becomes one between
            # market environments rather than between timing and no timing.
            like=[d.date() if hasattr(d, "date") else d
                  for d in pd.to_datetime(trades["entry_date"])],
            strategy_pnl=float(trades["net_pnl"].sum()),
        ))
        return out

    def ablate(self, slots=None, *, accept_coupled: bool = False) -> list:
        """What each slot is worth: run again with one switched off, and compare.

        ``slots`` defaults to every ablatable slot. A slot the architecture defines but
        that cannot be ablated yet raises with the reason rather than reporting a
        contribution of zero, which would be a wrong answer rather than no answer.

        The stop-loss ablation is refused under ``risk_pct`` sizing unless
        ``accept_coupled=True``, because removing the stop also changes the position size
        — ``size = risk budget / stop distance``. Two variables moved, so the difference
        cannot be attributed to the stop. Ablate with fixed sizing for a clean reading.
        """
        from backtest.research.ablation import NEUTRAL, ablate as _ablate

        wanted = list(slots) if slots is not None else sorted(NEUTRAL)
        market, chain, signals, config = (self._market, self._chain, self._signals,
                                          self.raw.config)
        event_driven = getattr(self.raw, "ledger", None) is not None

        def run_one(variant) -> tuple[float, int]:
            if event_driven:
                from backtest.engine.loop import EventDrivenEngine

                result = EventDrivenEngine(market, chain, signals, config).run(variant)
            else:
                result = TradeTableEngine(market, chain, signals, config).run(variant)
            return float(sum(r.net_pnl for r in result.records)), len(result.records)

        return [_ablate(self.raw.strategy, slot, run_one, accept_coupled=accept_coupled)
                for slot in wanted]

    def save_config(self, path) -> "Any":
        """Write the config file that reproduces this run.

        A settled strategy is what the second use revolves around, and until this it had
        no form: it lived in the arguments of whatever call produced it. Re-run the file
        with ``run_from(path)``.

        Raises for a run whose deciding part is a Python function — a plan, a review, a
        custom entry or selector — rather than writing a file that drops it silently.
        """
        from backtest.api.config_file import save

        return save(self, path)

    def config(self) -> dict:
        """The same thing as plain data, without writing a file."""
        from backtest.api.config_file import to_mapping

        return to_mapping(self)

    def delay_test(self, extra=(1, 2)) -> "Any":
        """Does the result survive acting a day later.

        The document names this beside the data view's cut as a defence against look-ahead,
        and it catches what a cut cannot: a signal that is legitimately readable and whose
        edge lives entirely in acting on it immediately. Every read is in range and the
        result is still not reachable.

        Read it as a shape, not a verdict — a real short-horizon effect decays fast and so
        does an artefact.
        """
        from backtest.research.delay import decay

        config = self.raw.config
        market, chain, signals = self._market, self._chain, self._signals
        event_driven = getattr(self.raw, "ledger", None) is not None
        strategy = self.raw.strategy

        def at_lag(lag: int) -> tuple[float, int]:
            from backtest.engine.loop import EventDrivenEngine

            # The floor has to clear the lag, or an exit decided on T cannot be filled
            # before the contract expires — the same rule run() applies.
            moved = dataclasses.replace(
                config, execution_lag_days=lag,
                min_days_to_expiry=max(config.min_days_to_expiry, lag + 1))
            engine = (EventDrivenEngine(market, chain, signals, moved) if event_driven
                      else TradeTableEngine(market, chain, signals, moved))
            out = engine.run(strategy)
            return float(sum(r.net_pnl for r in out.records)), len(out.records)

        return decay(at_lag, base_lag=config.execution_lag_days, extra=tuple(extra))

    def realism_test(self, fractions=None) -> "Any":
        """How much of this result is the fill assumption, and does it survive the
        honest one.

        The document's first research rule: a realism assumption is tested for
        sensitivity and never optimised. ``adverse_fraction`` is the assumption results
        move most on -- about 9% of gross between the mid and the ask on this data -- and
        the least observable, since the fill a strategy would have got is the one fact a
        backtest cannot have.

        1.0 must be among the fractions and the result names no best one. A sweep that
        omits 1.0 reports how the result improves as the assumption is relaxed, which is
        a search rather than a test.
        """
        from backtest.research.realism import DOCUMENT_FRACTIONS, sensitivity

        config = self.raw.config
        market, chain, signals = self._market, self._chain, self._signals
        event_driven = getattr(self.raw, "ledger", None) is not None
        strategy = self.raw.strategy

        def at_fraction(fraction: float) -> tuple[float, int]:
            from backtest.engine.loop import EventDrivenEngine

            moved = dataclasses.replace(
                config,
                costs=dataclasses.replace(config.costs, adverse_fraction=fraction))
            engine = (EventDrivenEngine(market, chain, signals, moved) if event_driven
                      else TradeTableEngine(market, chain, signals, moved))
            out = engine.run(strategy)
            return float(sum(r.net_pnl for r in out.records)), len(out.records)

        return sensitivity(at_fraction,
                           fractions=tuple(fractions or DOCUMENT_FRACTIONS))

    def compare_forms(self, slot: str, forms) -> "Any":
        """Run several **forms** of one slot side by side.

        Ablation asks whether a slot is worth having. This asks which form of it to keep —
        a stop on the premium against a stop on the underlying — which is what settling a
        component means, and which a parameter sweep over one form cannot answer.

        ``forms`` is required: which forms, at which parameters, is the research question.
        Needs the event-driven engine; the trade table computes exits vectorised over the
        whole holding window and cannot apply a per-day decision function.

        >>> r.compare_forms("stop_loss", [
        ...     {"variant": "premium",    "params": {"fraction": 0.4}},
        ...     {"variant": "underlying", "params": {"fraction": 0.03}},
        ... ])
        """
        from backtest.research.forms import compare

        if getattr(self.raw, "ledger", None) is None:
            raise AttributeError(
                "comparing forms needs the event-driven engine: the trade table computes "
                "exits vectorised and cannot apply a per-day decision function. Rerun "
                "with engine=\"event_driven\"."
            )
        market, chain, signals, config = (self._market, self._chain, self._signals,
                                          self.raw.config)

        def run_one(candidate) -> tuple[float, int]:
            from backtest.engine.loop import EventDrivenEngine

            out = EventDrivenEngine(market, chain, signals, config).run(candidate)
            return float(sum(r.net_pnl for r in out.records)), len(out.records)

        return compare(self.raw.strategy, slot, forms, run_one)

    def cost_sensitivity(self, multiples=(1.0, 2.0, 3.0)):
        """Does it survive costs doubling? Live costs above backtest costs is the
        norm."""
        from backtest.research.investor import cost_sensitivity

        return cost_sensitivity(self.trades, multiples)

    def anomalies(self) -> list:
        """Scan the result for what is genuinely out of the ordinary. With nothing
        triggered this returns an empty list.

        Rule-based, rather than asking a model to go looking — rules are testable and
        do not invent. See ``backtest.research.anomaly``.
        """
        from backtest.research.anomaly import scan

        try:
            metrics = self.equity_metrics()
        except Exception:  # noqa: BLE001
            metrics = None          # no equity curve: those rules simply do not run
        return scan(self.trades, metrics=metrics, costs=self.costs())

    def sweep(self, grid: Mapping[str, Any]) -> SweepResult:
        """Sweep a parameter grid on the same data and the same strategy.

        The ranking is not itself a conclusion — whether the gap between adjacent ranks
        exceeds the noise is a separate question. This records the number of trials for
        that purpose.
        """
        return _sweep(
            self._market, self._chain, self._signals, self.raw.config, self.raw.strategy, grid
        )

    # ---- export ----
    def export(self, directory: str | Path) -> dict[str, Path]:
        """The trade log, per-trade P&L, run events, and the manifest needed to
        reproduce it."""
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        paths = {
            "trades": out / "trades.csv",
            "pnl": out / "pnl_series.csv",
            "events": out / "events.csv",
            "manifest": out / "manifest.json",
        }
        self.trades.to_csv(paths["trades"], index=False, date_format="%Y-%m-%d")
        self.trades[["trade_id", "exit_date", "net_pnl"]].to_csv(
            paths["pnl"], index=False, date_format="%Y-%m-%d"
        )
        self.events.to_csv(paths["events"], index=False, date_format="%Y-%m-%d")
        paths["manifest"].write_text(
            json.dumps(self.manifest(), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return paths
