"""Test fixtures: a deterministic offline market.

A test has to pass reliably on a machine with no network, or it is not a regression
defence but one more source of non-determinism. The underlying's prices come from a
seeded geometric Brownian motion, so every run's input is bit-identical.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.core.config import BacktestConfig
from backtest.core.clock import TradingCalendar
from backtest.data.market import MarketData
from backtest.data.options.synthetic.generator import SyntheticChainSource
from backtest.data.underlying import build_bars
from backtest.signals.registry import SignalSet
from backtest.core.types import ContractSpec

SEED = 20260917
START = date(2022, 1, 1)
END = date(2023, 12, 31)


#: The fixture's dividends: amount per ex-date, one source of truth.
#:
#: Both the adjustment factor and the `dividends` series are built from this. They used to
#: be written out separately -- a factor stepping every 63 sessions, about thirteen times,
#: beside a four-entry dividend series on unrelated dates, under a docstring that said
#: "four ex-dividend events". Nothing failed, because no test asked the two to agree; a
#: fixture that describes itself wrongly is how a later test comes to assert the wrong
#: thing with confidence.
DIVIDENDS: dict[str, float] = {
    "2022-09-19": 0.55,
    "2022-12-19": 0.57,
    "2023-03-20": 0.60,
    "2023-06-20": 0.62,
}


def make_raw_bars(start: date = START, end: date = END, seed: int = SEED) -> pd.DataFrame:
    """Deterministic daily bars, with the four ex-dividend events in :data:`DIVIDENDS`,
    so the traded and adjusted price sets must separate."""
    sessions = TradingCalendar(start - timedelta(days=430), end).sessions
    rng = np.random.default_rng(seed)
    n = len(sessions)
    steps = rng.normal(loc=0.0004, scale=0.012, size=n)
    close = 300.0 * np.exp(np.cumsum(steps))
    spread = np.abs(rng.normal(0, 0.004, size=n)) * close
    frame = pd.DataFrame(
        {
            "Open": close * (1 + rng.normal(0, 0.002, size=n)),
            "High": close + spread,
            "Low": close - spread,
            "Close": close,
            "Volume": rng.integers(3e7, 8e7, size=n),
        },
        index=pd.to_datetime([pd.Timestamp(d) for d in sessions]),
    )
    frame["Low"] = np.minimum(frame["Low"], frame[["Open", "Close"]].min(axis=1))
    frame["High"] = np.maximum(frame["High"], frame[["Open", "Close"]].max(axis=1))

    # The adjustment factor, built from the dividends themselves rather than from a step
    # every 63 sessions. Two details that were wrong and are easy to get wrong again:
    #
    #   - it applies to the bars **strictly before** the ex-date. On the ex-date the price
    #     has already dropped by the dividend, so adjusting that bar too double-counts it.
    #     The old slice was `[: i + 1]`, which included it.
    #   - each step is the dividend as a fraction of that day's close, not a flat 0.15%.
    factor = np.ones(n)
    index = frame.index
    for ex_date, amount in DIVIDENDS.items():
        stamp = pd.Timestamp(ex_date)
        before = index < stamp
        if not before.any() or not (index >= stamp).any():
            continue
        close_on_ex = float(frame["Close"].to_numpy()[index.get_indexer([stamp],
                                                                       method="bfill")[0]])
        factor[before] *= 1 - amount / close_on_ex
    frame["Adj Close"] = frame["Close"] * factor
    return frame


@pytest.fixture(scope="session")
def config() -> BacktestConfig:
    return BacktestConfig(
        symbol="QQQ",
        start=date(2022, 6, 1),
        end=END,
        contract=ContractSpec(symbol="QQQ", vol_proxy="^VXN"),
    )


@pytest.fixture(scope="session")
def market(config) -> MarketData:
    bars = build_bars("QQQ", make_raw_bars())
    idx = bars.frame.index
    rates = pd.Series(0.045, index=idx)
    vol = pd.Series(0.22, index=idx)
    dividends = pd.Series(list(DIVIDENDS.values()),
                          index=pd.to_datetime(list(DIVIDENDS)))
    return MarketData(config, bars=bars, rates=rates, vol_proxy=vol, dividends=dividends)


@pytest.fixture(scope="session")
def chain(market) -> SyntheticChainSource:
    return SyntheticChainSource(market)


@pytest.fixture(scope="session")
def signals(market) -> SignalSet:
    return SignalSet.from_bars(market.bars)


# ── the strategy choices a test makes ──────────────────────────────────────
#
# The platform refuses to default a strategy choice: which contract, where to stop, how
# large. A test still has to pick values, and repeating the same four arguments at thirty
# call sites buries whatever each test is actually varying.
#
# So the values live here, once, named and visible. **This is the distinction the rule
# turns on**: a default a reader can see and change is a starting point, while one that
# fills itself in when a field is omitted, in production, and never appears in the
# configuration afterwards is a decision taken on the researcher's behalf. These are the
# first kind. `tools/check_defaults.py` watches the production dataclasses, not this file.

def selection(**over):
    """A :class:`SelectionRule` for tests: a 30-day call around 0.30 delta.

    Pass only what the test varies.
    """
    from backtest.core.types import Right
    from backtest.signals.contracts import SelectionRule

    base = {"right": Right.CALL, "target_dte": 30, "dte_window": (21, 45),
            "target_delta": 0.30}
    if "moneyness" in over and "target_delta" not in over:
        base["target_delta"] = None
    return SelectionRule(**(base | over))


def exits(**over):
    """An :class:`ExitRule` for tests: 20 days, +100% / -50%, out at 5 days to expiry."""
    from backtest.signals.strategy import ExitRule

    base = {"hold_days": 20, "take_profit": 1.0, "stop_loss": 0.5, "dte_floor": 5}
    return ExitRule(**(base | over))


# ── offline ────────────────────────────────────────────────────────────────
#
# About ten test files call `backtest.run(...)`, which builds its own MarketData and
# reaches yfinance through the parquet cache. On this machine the cache is warm, so they
# pass; `.cache/` is gitignored, so **a fresh clone or CI downloads market data to run the
# test suite**, and the regression baseline is pinned to figures a vendor can revise.
#
# Neither is acceptable in a suite whose job is to say whether the code changed. Every
# test gets the deterministic bars from `make_raw_bars`, the same ones the explicit
# `market` fixture uses, and the network is never reached.
#
# **Session-scoped, and that is not a detail.** The first version was function-scoped, and
# pytest creates higher-scoped fixtures first -- so a module-scoped fixture that called
# `run()` was built on real network data while everything around it ran offline. The two
# then disagreed, and the disagreement surfaced as a config file that "did not reproduce
# its run": it reproduced it exactly, against different data.
#
# Set BACKTEST_TEST_ONLINE=1 to turn it off, for a test that is genuinely about fetching.

@pytest.fixture(scope="session", autouse=True)
def _offline():
    """Point MarketData at the fixture's bars instead of the network.

    Patched at `MarketData.__init__` rather than at the fetch functions: patching the
    fetchers would still let a stale cache answer, and a cache that answers differently on
    two machines is the same problem wearing a different hat.
    """
    import os

    if os.environ.get("BACKTEST_TEST_ONLINE"):
        yield
        return

    from backtest.data import market as market_module

    original = market_module.MarketData.__init__
    raw = make_raw_bars()

    def offline_init(self, config, *args, **kwargs):
        kwargs.setdefault("bars", build_bars(config.symbol, raw))
        index = kwargs["bars"].frame.index
        kwargs.setdefault("rates", pd.Series(0.045, index=index))
        kwargs.setdefault("vol_proxy", pd.Series(0.22, index=index))
        kwargs.setdefault("dividends", pd.Series(list(DIVIDENDS.values()),
                                                 index=pd.to_datetime(list(DIVIDENDS))))
        return original(self, config, *args, **kwargs)

    market_module.MarketData.__init__ = offline_init
    try:
        yield
    finally:
        market_module.MarketData.__init__ = original
