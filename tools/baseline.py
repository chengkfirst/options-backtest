"""The regression baseline.

A determinism test only guarantees that the same code twice gives the same answer; it
cannot catch **an unintended change in results caused by a change in the code**. The
baseline comparison covers that: on fixed inputs and a fixed configuration, any
difference from the baseline has to be caught.

Updating the baseline is an **explicit act** (``python -m tools.baseline update``), and
the difference is printed when it happens — otherwise "just update the baseline while
I am here" makes this defence worthless.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

BASELINE_DIR = Path(__file__).resolve().parent.parent / "tests" / "baselines"
BASELINE_FILE = BASELINE_DIR / "baseline_trades.csv"

#: The columns compared. The context columns evolve with the signal set and stay out
#: of the baseline to avoid noise.
COMPARED = [
    "trade_id", "entry_date", "exit_date", "contract", "quantity",
    "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl",
    "exit_reason", "holding_days",
]


def build_trades() -> pd.DataFrame:
    """Run the baseline strategy's trade log on the offline fixture. The input is
    fixed, so the output should be bit-stable."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backtest.api.sdk import Result
    from backtest.core.config import BacktestConfig
    from backtest.data.market import MarketData
    from backtest.data.options.synthetic.generator import SyntheticChainSource
    from backtest.data.underlying import build_bars
    from backtest.signals.entry.expression import parse_condition
    from backtest.signals.strategy import EntryRule, ExitRule
    from backtest.engine.strategy import Strategy
    from backtest.engine.vectorized import TradeTableEngine
    from backtest.signals.contracts import SelectionRule
    from backtest.signals.registry import SignalSet
    from backtest.core.types import ContractSpec, Right
    from tests.conftest import END, make_raw_bars

    from datetime import date

    config = BacktestConfig(
        symbol="QQQ", start=date(2022, 6, 1), end=END,
        contract=ContractSpec(symbol="QQQ", vol_proxy="^VXN"),
    )
    bars = build_bars("QQQ", make_raw_bars())
    idx = bars.frame.index
    market = MarketData(
        config, bars=bars,
        rates=pd.Series(0.045, index=idx), vol_proxy=pd.Series(0.22, index=idx),
        dividends=pd.Series(
            [0.55, 0.57, 0.60, 0.62],
            index=pd.to_datetime(["2022-09-19", "2022-12-19", "2023-03-20", "2023-06-20"]),
        ),
    )
    signals = SignalSet.from_bars(bars)
    condition, requires = parse_condition("sma_20 > sma_50", signals.names)
    strategy = Strategy(
        name="baseline",
        entry=EntryRule("sma_20 > sma_50", condition, requires),
        select=SelectionRule(right=Right.CALL, target_dte=30, dte_window=(21, 45), target_delta=0.30),
        exit=ExitRule(hold_days=10, take_profit=1.0, stop_loss=0.5, dte_floor=5),
    )
    result = Result(
        TradeTableEngine(market, SyntheticChainSource(market, config), signals, config).run(strategy)
    )
    return result.trades[COMPARED]


def compare() -> pd.DataFrame | None:
    """Return the difference, or ``None`` when there is none. With no baseline this
    raises and says to capture one first."""
    if not BASELINE_FILE.exists():
        raise FileNotFoundError(f"no baseline at {BASELINE_FILE}; run "
                                f"`python -m tools.baseline update` first")
    current = build_trades().reset_index(drop=True)
    stored = pd.read_csv(BASELINE_FILE, parse_dates=["entry_date", "exit_date"])
    stored["entry_date"] = stored["entry_date"].dt.date
    stored["exit_date"] = stored["exit_date"].dt.date
    if len(current) != len(stored):
        return pd.DataFrame({"issue": [f"trade count {len(stored)} -> {len(current)}"]})
    numeric = current.select_dtypes("number").columns
    diff_mask = ~(
        current[numeric].round(8).eq(stored[numeric].round(8)).all(axis=1)
        & current.drop(columns=numeric).eq(stored.drop(columns=numeric)).all(axis=1)
    )
    return current.loc[diff_mask] if diff_mask.any() else None


def update() -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    previous = pd.read_csv(BASELINE_FILE) if BASELINE_FILE.exists() else None
    trades = build_trades()
    trades.to_csv(BASELINE_FILE, index=False, date_format="%Y-%m-%d")
    if previous is None:
        print(f"baseline created: {len(trades)} trades -> {BASELINE_FILE}")
    else:
        print(f"baseline updated: {len(previous)} trades -> {len(trades)}")
        print(f"net P&L {previous['net_pnl'].sum():.2f} -> {trades['net_pnl'].sum():.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="the regression baseline")
    parser.add_argument("action", choices=["check", "update"])
    args = parser.parse_args()
    if args.action == "update":
        update()
        return 0
    diff = compare()
    if diff is None:
        print("the baseline comparison passed")
        return 0
    print("the baseline comparison failed; these trades changed:")
    print(diff.to_string(index=False))
    print("\nIf the change really should move the results, run explicitly: "
          "python -m tools.baseline update")
    return 1


if __name__ == "__main__":
    sys.exit(main())
