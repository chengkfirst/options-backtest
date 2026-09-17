"""顶层门面。一个 :func:`run` 跑完整条链路。

    from backtest import run

    result = run(
        symbol="QQQ",
        start="2023-01-01", end="2023-12-31",
        entry="sma_20 > sma_50",
        select={"right": "call", "dte": 30, "delta": 0.30},
        exit={"hold_days": 10, "take_profit": 1.0, "stop_loss": 0.5, "dte_floor": 5},
    )
    result.summary()          # 打印一页结论
    result.trades             # 交易明细 DataFrame
    result.export("out/")     # 落盘
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd

from backtest.config import BacktestConfig, CostConfig, SyntheticChainConfig
from backtest.data.market import VOL_PROXY_BY_SYMBOL, MarketData
from backtest.data.synthetic.generator import SyntheticChainSource
from backtest.engine.expression import parse_condition
from backtest.engine.strategy import EntryRule, ExitRule, Strategy
from backtest.engine.sweep import SweepResult, sweep as _sweep
from backtest.engine.trade_table import TradeTableEngine, TradeTableResult
from backtest.errors import StartupError
from backtest.report import stats as R
from backtest.selection.rules import SelectionRule
from backtest.signals.registry import SignalSet
from backtest.types import ContractSpec, Right


def _as_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _selection_from(spec: Mapping[str, Any] | SelectionRule) -> SelectionRule:
    if isinstance(spec, SelectionRule):
        return spec
    spec = dict(spec)
    right = spec.pop("right", "call")
    dte = spec.pop("dte", 30)
    window = tuple(spec.pop("dte_window", (max(1, dte - 9), dte + 15)))
    return SelectionRule(
        right=Right.CALL if str(right).lower().startswith("c") else Right.PUT,
        target_dte=dte,
        dte_window=window,
        target_delta=spec.pop("delta", None if "moneyness" in spec else 0.30),
        moneyness=spec.pop("moneyness", None),
        min_volume=spec.pop("min_volume", None),
        min_open_interest=spec.pop("min_open_interest", None),
        max_relative_spread=spec.pop("max_spread", None),
    )


def _exit_from(spec: Mapping[str, Any] | ExitRule) -> ExitRule:
    if isinstance(spec, ExitRule):
        return spec
    spec = dict(spec)
    return ExitRule(
        hold_days=spec.pop("hold_days", 10),
        take_profit=spec.pop("take_profit", 1.0),
        stop_loss=spec.pop("stop_loss", 0.5),
        dte_floor=spec.pop("dte_floor", 5),
    )


def run(
    *,
    symbol: str = "QQQ",
    start: str | date = "2015-01-01",
    end: str | date = "2024-12-31",
    entry: str | Callable = "sma_20 > sma_50",
    select: Mapping[str, Any] | SelectionRule | None = None,
    exit: Mapping[str, Any] | ExitRule | None = None,  # noqa: A002 - 与策略术语一致
    quantity: int = 1,
    capital: float = 100_000.0,
    costs: CostConfig | None = None,
    synthetic: SyntheticChainConfig | None = None,
    name: str = "unnamed",
    refresh: bool = False,
) -> "Result":
    """跑一次成交表回测。"""
    proxy = VOL_PROXY_BY_SYMBOL.get(symbol)
    if proxy is None:
        raise StartupError(
            f"未知标的 {symbol}；已知标的与其波动率代理：{VOL_PROXY_BY_SYMBOL}"
        )
    config = BacktestConfig(
        symbol=symbol,
        start=_as_date(start),
        end=_as_date(end),
        notional_capital=capital,
        contract=ContractSpec(symbol=symbol, vol_proxy=proxy),
        costs=costs or CostConfig(),
        synthetic=synthetic or SyntheticChainConfig(),
    )

    market = MarketData(config, refresh=refresh)
    signals = SignalSet.from_bars(market.bars)
    chain = SyntheticChainSource(market, config)

    if isinstance(entry, str):
        condition, requires = parse_condition(entry, signals.names)
        entry_rule = EntryRule(entry, condition, requires)
    else:
        entry_rule = EntryRule(getattr(entry, "__name__", "custom"), entry, ())

    strategy = Strategy(
        entry=entry_rule,
        select=_selection_from(select or {}),
        exit=_exit_from(exit or {}),
        quantity=quantity,
        name=name,
    )
    outcome = TradeTableEngine(market, chain, signals, config).run(strategy)
    return Result(outcome, market, chain, signals)


@dataclass(frozen=True, slots=True)
class Result:
    """回测产出。``trades`` 是原始明细，其余是它的各种切面。"""

    raw: TradeTableResult
    _market: Any = None
    _chain: Any = None
    _signals: Any = None

    # ---- 数据 ----
    @property
    def trades(self) -> pd.DataFrame:
        return self.raw.trades

    @property
    def concurrency(self) -> pd.Series:
        return self.raw.concurrency

    @property
    def events(self) -> pd.DataFrame:
        return self.raw.events.to_frame()

    # ---- 切面 ----
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
        """配置 + 数据来源 + 依赖版本。结果可被重新执行的凭据。"""
        return {
            "strategy": {
                "name": self.raw.strategy.name,
                "entry": self.raw.strategy.entry.name,
                "quantity": self.raw.strategy.quantity,
                "select": dataclasses.asdict(self.raw.strategy.select)
                | {"right": self.raw.strategy.select.right.value},
                "exit": dataclasses.asdict(self.raw.strategy.exit),
            },
            "config": self.raw.config.describe(),
            "environment": R.environment(),
            "provenance": self.provenance(),
        }

    # ---- 呈现 ----
    def summary(self, printout: bool = True) -> str:
        lines: list[str] = []
        prov = self.provenance()
        if prov.get("all_synthetic"):
            lines += [
                "!! 本结果基于合成期权链，不代表真实可成交价格。",
                "!! 合成链没有波动率偏斜与真实 IV 动态；期权侧参数（选约/止盈/止损）不可据此调优。",
                "",
            ]
        elif prov.get("mixed"):
            lines += [f"!! 合成数据覆盖 {prov['synthetic_share']:.1%} 的交易，结论需按比例折扣。", ""]

        s = self.stats()
        if s["count"] == 0:
            lines.append("区间内未产生任何交易。")
            text = "\n".join(lines)
            if printout:
                print(text)
            return text

        lines += [
            f"策略 {self.raw.strategy.name} · {self.raw.config.symbol} · "
            f"{self.raw.config.start} ~ {self.raw.config.end}",
            f"交易 {s['count']} 笔 | 胜率 {s['win_rate']:.1%} | 平均持有 {s['avg_holding_days']:.1f} 个交易日",
            f"毛盈亏 {s['gross_pnl']:,.0f} | 费用 {s['fees']:,.0f} | 净盈亏 {s['net_pnl']:,.0f}",
            "",
            "— 单笔盈亏分布 —",
        ]
        d = self.distribution()
        q = d["quantiles_abs"]
        lines += [
            f"  5% {q['q5']:,.0f} | 25% {q['q25']:,.0f} | 中位 {q['q50']:,.0f} | "
            f"75% {q['q75']:,.0f} | 95% {q['q95']:,.0f}",
            f"  均值 {d['mean']:,.0f} | 最大盈 {d['max_win']:,.0f} | 最大亏 {d['max_loss']:,.0f}",
            f"  偏度 {d['skew']:.2f} | 峰度 {d['kurtosis']:.2f}",
        ]
        if d["driven_by_outliers"]:
            lines.append("  !! 中位数与均值符号相反：平均收益由少数极值驱动，不可外推")

        lines += ["", "— 按了结原因 —"]
        for reason, row in self.by_exit_reason().iterrows():
            lines.append(
                f"  {reason:<12} {int(row['count']):>4} 笔 | 胜率 {row['win_rate']:.0%} | "
                f"合计 {row['net_pnl']:>10,.0f}"
            )

        c = self.costs()
        lines += [
            "",
            "— 成本 —",
            f"  佣金 {c['commission']:,.0f} | 监管费 {c['regulatory']:,.0f} | 滑点 {c['slippage']:,.0f}"
            f" | 合计 {c['total']:,.0f}（占毛盈亏 {c['fees_pct_of_gross']:.1%}）",
        ]
        if c["eaten_by_costs"]:
            lines.append("  !! 毛盈亏为正而净盈亏为负：收益不足以覆盖交易成本")

        cap = self.raw.capital_check()
        lines += [
            "",
            "— 口径提醒 —",
            "  成交表引擎各笔独立计算：不含资金约束、无组合权益曲线，因此没有回撤与夏普。",
            f"  峰值同时持仓 {cap['peak_positions']} 笔，约需资金 {cap['peak_capital']:,.0f}"
            f"（名义资金 {cap['notional_capital']:,.0f}）",
        ]
        if not cap["feasible"]:
            lines.append("  !! 峰值资金需求超过名义资金，该结果在资金上不可实现，需用事件驱动引擎复核")

        pe = R.passive_expiry_check(self.trades)
        passive = pe.get("itm", 0) + pe.get("otm", 0)
        if passive:
            lines.append(
                f"  !! {passive} 笔走到被动到期（实值 {pe['itm']} / 虚值 {pe['otm']}）："
                f"了结规则未能在到期前生效，通常是临近到期价差爆开导致无法成交"
            )
        if pe.get("rule_failed"):
            lines.append("  !! 其中实值被动到期意味着本可了结的盈利头寸被拖到了结算")

        ev = self.raw.events.counts()
        if ev:
            lines += ["", "— 运行事件 —", "  " + " | ".join(f"{k}:{v}" for k, v in sorted(ev.items()))]

        text = "\n".join(lines)
        if printout:
            print(text)
        return text

    # ---- 扫描 ----
    def sweep(self, grid: Mapping[str, Any]) -> SweepResult:
        """在同一份数据与同一策略上扫描参数网格。

        排名本身不构成结论——相邻名次的差异是否超出噪音，要到研究工作台
        （ROADMAP.md 阶段 2）的证据分级才能回答。这里只记下试验次数备用。
        """
        return _sweep(
            self._market, self._chain, self._signals, self.raw.config, self.raw.strategy, grid
        )

    # ---- 导出 ----
    def export(self, directory: str | Path) -> dict[str, Path]:
        """交易明细、逐笔盈亏、运行事件、以及可复现所需的 manifest。"""
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
