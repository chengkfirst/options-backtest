"""回归基线。

确定性测试只能保证"同一份代码跑两次一样"，挡不住**代码改动带来的非预期结果变化**。
基线比对补上这一层：对固定输入与配置，输出与基线的任何差异都必须被检出。

基线的更新是一次**显式操作**（``python -m tools.baseline update``），且更新前后的
差异会被打印出来——否则"顺手更新基线"会让这道防线形同虚设。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

BASELINE_DIR = Path(__file__).resolve().parent.parent / "tests" / "baselines"
BASELINE_FILE = BASELINE_DIR / "baseline_trades.csv"

#: 参与比对的列。上下文列随信号集合演化，不入基线以免噪音。
COMPARED = [
    "trade_id", "entry_date", "exit_date", "contract", "quantity",
    "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl",
    "exit_reason", "holding_days",
]


def build_trades() -> pd.DataFrame:
    """用离线夹具跑出基线策略的交易明细。输入固定，因此输出应当逐位稳定。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from backtest.api import Result
    from backtest.config import BacktestConfig
    from backtest.data.market import MarketData
    from backtest.data.synthetic.generator import SyntheticChainSource
    from backtest.data.underlying import build_bars
    from backtest.engine.expression import parse_condition
    from backtest.engine.strategy import EntryRule, ExitRule, Strategy
    from backtest.engine.trade_table import TradeTableEngine
    from backtest.selection.rules import SelectionRule
    from backtest.signals.registry import SignalSet
    from backtest.types import ContractSpec, Right
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
    """返回差异；无差异返回 ``None``。基线不存在时抛错，提示先 capture。"""
    if not BASELINE_FILE.exists():
        raise FileNotFoundError(f"基线不存在：{BASELINE_FILE}，先运行 `python -m tools.baseline update`")
    current = build_trades().reset_index(drop=True)
    stored = pd.read_csv(BASELINE_FILE, parse_dates=["entry_date", "exit_date"])
    stored["entry_date"] = stored["entry_date"].dt.date
    stored["exit_date"] = stored["exit_date"].dt.date
    if len(current) != len(stored):
        return pd.DataFrame({"issue": [f"笔数 {len(stored)} → {len(current)}"]})
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
        print(f"基线已建立：{len(trades)} 笔 → {BASELINE_FILE}")
    else:
        print(f"基线已更新：{len(previous)} 笔 → {len(trades)} 笔")
        print(f"净盈亏 {previous['net_pnl'].sum():.2f} → {trades['net_pnl'].sum():.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="回归基线")
    parser.add_argument("action", choices=["check", "update"])
    args = parser.parse_args()
    if args.action == "update":
        update()
        return 0
    diff = compare()
    if diff is None:
        print("基线比对通过")
        return 0
    print("基线比对失败，以下交易发生变化：")
    print(diff.to_string(index=False))
    print("\n若该改动确实应当改变结果，显式执行：python -m tools.baseline update")
    return 1


if __name__ == "__main__":
    sys.exit(main())
