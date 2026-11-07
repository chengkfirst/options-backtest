"""Every engine parameter is reachable from `run()`. The direction that was missing.

`tests/test_components.py` checks that every field the interface offers reaches `run()` —
a form field that goes nowhere is filled in and has no effect. **Nothing checked the other
way**, and two parameters were in that state: `ExposureLimits` had unit tests and a live
call site at the engine's entry check, and `enforce_capital` decides whether the cash
constraint applies at all. Neither could be set from the public API, so the document's
Constraint row read as built on the strength of code nobody could switch on.

A dead parameter is quieter than a dead form field. The form field at least appears in the
interface; this one appears in the engine, in a unit test, and in a conformance table, and
is unreachable.

## What is exempt, and why each one

The exemptions are listed with reasons rather than as a bare allow-list, which is the
mistake `test_components` had to correct in its own check: a list that only *excuses* a
name is a list that hides the case the check exists for.
"""

from __future__ import annotations

import inspect

from tests.kinds import kind, labels

pytestmark = labels("architecture", "cross")

#: Parameters that are not settings. Each says why it cannot be a `run()` argument.
PLUMBING: dict[str, str] = {
    "self": "not a parameter",
    "market": "built by run() from symbol, start and end",
    "chain": "built by run() from the chain= spec, through the registry",
    "signals": "built by run() from the bars, plus the signals= argument",
    "config": "assembled by run() from capital, costs, execution_lag_days and the rest",
    "events": "the run's own log, created by run() and returned on the result",
    "bus": "the run's own event bus, created by run() and returned on the result",
    "strategy": "assembled by run() from entry, exit, select, sizing and components",
    "until": "exposed as until=, by that name",
    "resume_from": "exposed as resume_from=, by that name",
}


def _engine_parameters(cls) -> list[str]:
    return [p for p in inspect.signature(cls.__init__).parameters
            if p not in PLUMBING]


class TestEveryEngineSettingCanBeSet:
    @kind("guard")
    def test_each_event_driven_parameter_is_reachable_from_run(self):
        from backtest.api.sdk import run
        from backtest.engine.loop import EventDrivenEngine

        source = inspect.getsource(run)
        unreachable = [name for name in _engine_parameters(EventDrivenEngine)
                       if f"{name}=" not in source]
        assert not unreachable, (
            f"these engine parameters cannot be set from run(), so they are dead from "
            f"the public API and from the interface: {unreachable}. Either wire each one "
            f"through run() -- into the fingerprint too, since a setting that changes "
            f"the result and not the id makes a search look smaller than it was -- or "
            f"delete it. A parameter with a unit test and no way in still reads as built"
        )

    @kind("guard")
    def test_each_parameter_that_blocks_entries_is_in_the_run_fingerprint(self):
        """The companion failure. Wiring a limit into `run()` and not into the id makes
        two different runs share a fingerprint, which has happened seven times in this
        project — so it is checked rather than remembered.
        """
        from backtest.api.sdk import run

        config = dict(
            symbol="QQQ", start="2023-01-01", end="2023-06-30",
            entry="close < sma_20 * 0.98",
            select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                    "delta_lo": 0.25, "delta_hi": 0.45},
            exit={"hold_days": 20, "dte_floor": 7},
            engine="event_driven", sizing={"method": "fixed"}, quantity=1,
        )
        #: setting -> a value that differs from the default. Every one of these blocks or
        #: changes entries, so every one must move the id.
        movers = {
            "exposure": {"max_delta_pct": 0.01},
            "premium_budget": {"max_premium_pct": 0.01},
            "scenario_limit": {"max_loss": 1.0, "scenarios": [[-0.05, -0.05]]},
            "max_concurrent": 1,
            "enforce_capital": False,
        }
        plain = run(**config).manifest()["run_id"]
        shared = [name for name, value in movers.items()
                  if run(**(config | {name: value})).manifest()["run_id"] == plain]
        assert not shared, (
            f"these settings changed the run and not its fingerprint: {shared}"
        )

    def test_the_exemptions_all_name_a_real_parameter(self):
        """An exemption for a parameter that no longer exists is an exemption that will
        silently cover a future one with the same name."""
        from backtest.engine.loop import EventDrivenEngine
        from backtest.engine.vectorized import TradeTableEngine

        real = set()
        for cls in (EventDrivenEngine, TradeTableEngine):
            real |= set(inspect.signature(cls.__init__).parameters)
            real |= set(inspect.signature(cls.run).parameters)
        stale = sorted(set(PLUMBING) - real)
        assert not stale, (
            f"these exemptions name nothing: {stale}. Remove them, or they will excuse a "
            f"future parameter that happens to share the name"
        )

    def test_every_exemption_says_why(self):
        thin = [name for name, why in PLUMBING.items() if len(why) < 15]
        assert not thin, f"these exemptions give no reason: {thin}"


class TestTheExposureLimitActuallyBinds:
    """It had unit tests and a call site and could not be switched on. These are the
    tests that make "wired in" mean something."""

    CONFIG = dict(
        symbol="QQQ", start="2023-01-01", end="2023-12-31",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    @kind("limit")
    def test_a_tight_limit_blocks_trades_and_a_loose_one_does_not(self):
        from backtest.api.sdk import run

        base = len(run(**self.CONFIG).trades)
        assert base > 3

        loose = len(run(**(self.CONFIG | {"exposure": {"max_delta_pct": 100.0}})).trades)
        assert loose == base, (
            "a delta limit of 100x equity changed the result, so it is not the limit "
            "being read"
        )
        tight = len(run(**(self.CONFIG | {"exposure": {"max_delta_pct": 0.001}})).trades)
        assert tight < base

    @kind("limit")
    def test_a_blocked_entry_says_the_limit_blocked_it(self):
        from backtest.api.sdk import run
        from backtest.core.run_log import EventKind

        result = run(**(self.CONFIG | {"exposure": {"max_delta_pct": 0.001}}))
        blocked = [e for e in result.raw.events.of_kind(EventKind.CANDIDATE_DROPPED)
                   if "exposure limit" in e.detail]
        assert blocked, "entries were blocked and nothing said why"
        assert "delta exposure" in blocked[0].detail

    def test_an_unknown_setting_is_refused(self):
        from backtest.api.sdk import run
        from backtest.core.errors import StartupError

        with pytest_raises(StartupError, "max_delta"):
            run(**(self.CONFIG | {"exposure": {"max_delta": 0.5}}))

    def test_an_empty_limit_is_refused_rather_than_ignored(self):
        from backtest.api.sdk import run
        from backtest.core.errors import StartupError

        with pytest_raises(StartupError, "no limit"):
            run(**(self.CONFIG | {"exposure": {}}))

    def test_the_trade_table_engine_refuses_it(self):
        from backtest.api.sdk import run
        from backtest.core.errors import StartupError

        with pytest_raises(StartupError, "no book"):
            run(**(self.CONFIG | {"engine": "trade_table",
                                   "exposure": {"max_delta_pct": 0.5}}))


def pytest_raises(kind_, needle):
    """`pytest.raises` with the message check inline, since every case here wants both."""
    import pytest

    return pytest.raises(kind_, match=needle)
