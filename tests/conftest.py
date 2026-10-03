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


def make_raw_bars(start: date = START, end: date = END, seed: int = SEED) -> pd.DataFrame:
    """Deterministic daily bars. Four ex-dividend events, so the two price sets must
    separate."""
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

    # One ex-dividend per quarter, so the adjustment factor steps down cumulatively.
    factor = np.ones(n)
    for i in range(0, n, 63):
        factor[: i + 1] *= 1 - 0.0015
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
    dividends = pd.Series(
        [0.55, 0.57, 0.60, 0.62],
        index=pd.to_datetime(["2022-09-19", "2022-12-19", "2023-03-20", "2023-06-20"]),
    )
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
