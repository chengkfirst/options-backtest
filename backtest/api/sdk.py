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
from dataclasses import dataclass
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
    missing = [k for k in ("right",) if k not in spec]
    if "dte" not in spec and not ("dte_lo" in spec and "dte_hi" in spec):
        missing.append("dte (or dte_lo and dte_hi)")
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
        max_relative_spread=spec.pop("max_spread", None),
    )


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
        entry_rule = EntryRule(entry, condition, requires)
    elif entry is None:
        # A plan decides its own entries, so there is no condition to parse. The rule is a
        # labelled placeholder rather than a condition, and it never fires.
        entry_rule = EntryRule("decided by the plan", lambda view: False, ())
    else:
        entry_rule = EntryRule(getattr(entry, "__name__", "custom"), entry, ())

    strategy = Strategy(
        plan=plan,
        review=review,
        sizing=_sizing_from(sizing, quantity),
        entry=entry_rule,
        select=_selection_from(select or {}),
        exit=_exit_from(exit or {}),
        quantity=quantity,
        name=name,
    )
    if engine == "event_driven":
        from backtest.engine.loop import EventDrivenEngine

        outcome = EventDrivenEngine(
            market, chain_source, signal_set, config, max_concurrent=max_concurrent,
            shadow_days=shadow_days,
        ).run(strategy)
    else:
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
    return Result(outcome, market, chain_source, signal_set)


@dataclass(frozen=True, slots=True)
class Result:
    """What a run produces. ``trades`` is the raw log; everything else is a view
    onto it."""

    raw: TradeTableResult
    _market: Any = None
    _chain: Any = None
    _signals: Any = None

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

    def provenance(self) -> dict[str, Any]:
        return R.data_provenance(self.trades)

    def manifest(self) -> dict[str, Any]:
        """Configuration, provenance and dependency versions — what makes the result
        re-executable — and the ``run_id`` that identifies this exact combination.

        The ``run_id`` is the hash of the four things the architecture document says
        determine a result: the strategy config, the code commit, the data version and
        the run settings. It answers the question a counter cannot: **have I run this
        exact thing before.** A dirty working tree shows up as ``+dirty`` on the commit,
        because a result from uncommitted code cannot be reproduced from the commit alone.
        """
        from backtest.core.run_id import code_version, run_id

        strategy = {
            "name": self.raw.strategy.name,
            "entry": self.raw.strategy.entry.name,
            "quantity": self.raw.strategy.quantity,
            "select": (dataclasses.asdict(self.raw.strategy.select)
                       | {"right": self.raw.strategy.select.right.value}
                       if not callable(self.raw.strategy.select) else "<callable>"),
            "exit": dataclasses.asdict(self.raw.strategy.exit),
            "sizing": dataclasses.asdict(self.raw.strategy.sizing),
            "plan": self.raw.strategy.plan is not None,
            "review": self.raw.strategy.review is not None,
        }
        settings = self.raw.config.describe()
        # Which engine ran, and the limits it ran under. These change the trades and are
        # not part of the config, so they are hashed separately and explicitly.
        execution = {"engine": self.raw.ENGINE} | dict(self.raw.limits)
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
            f"net {s['net_pnl']:,.0f}",
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
        if d["driven_by_outliers"]:
            lines.append("  !! The median and the mean have opposite signs: a few "
                         "extremes drive the average, which does not generalise")

        lines += ["", "— by exit reason —"]
        for reason, row in self.by_exit_reason().iterrows():
            lines.append(
                f"  {reason:<12} {int(row['count']):>4} trades | win rate "
                f"{row['win_rate']:.0%} | total {row['net_pnl']:>10,.0f}"
            )

        c = self.costs()
        lines += [
            "",
            "— costs —",
            f"  commission {c['commission']:,.0f} | regulatory {c['regulatory']:,.0f} | "
            f"slippage {c['slippage']:,.0f} | total {c['total']:,.0f} "
            f"({c['fees_pct_of_gross']:.1%} of gross)",
        ]
        if c["eaten_by_costs"]:
            lines.append("  !! Gross is positive and net is negative: the edge does not "
                         "cover the trading costs")

        cap = self.raw.capital_check()
        lines += [
            "",
            "— what this engine does not cover —",
            "  The trade-table engine computes each trade independently: no capital "
            "constraint and no portfolio equity curve, so no drawdown and no Sharpe.",
            f"  Peak concurrent positions {cap['peak_positions']}, needing about "
            f"{cap['peak_capital']:,.0f} (notional {cap['notional_capital']:,.0f})",
        ]
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
        return {
            "total_return": float(curve.iloc[-1] / curve.iloc[0] - 1),
            "annualised": annual,
            "annualised_vol": vol,
            "sharpe": annual / vol if vol else float("nan"),
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
                       synthetic=self.synthetic)

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
