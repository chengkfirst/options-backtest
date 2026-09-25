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
from backtest.data.chain_registry import build_chain_source
from backtest.engine.expression import parse_condition
from backtest.engine.strategy import EntryRule, ExitRule, Sizing, Strategy
from backtest.engine.sweep import SweepResult, sweep as _sweep
from backtest.engine.trade_table import TradeTableEngine, TradeTableResult
from backtest.errors import StartupError
from backtest.report import stats as R
from backtest.selection.rules import SelectionRule
from backtest.signals.registry import SignalSet
from backtest.types import ContractSpec, Right


def _as_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _sizing_from(spec, quantity: int) -> Sizing:
    """把 ``sizing=`` 收敛成 :class:`Sizing`。

    没给就用固定张数——**但这只是默认值，不是推荐做法**。固定张数会让每笔的风险
    占比随权益漂移：权益翻倍之后，同样 1 张只占一半的风险；权益腰斩之后又变成两倍。
    真正想控制风险的话用 ``risk_pct``。
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
    """把 ``select=`` 参数收敛成引擎认识的东西。

    三种写法：声明式字典、``SelectionRule``、或一个 ``f(chain, view)`` 函数。
    函数原样透传——它的校验在 ``select_contract`` 里做，那里才看得到当日的链。
    """
    if callable(spec) and not isinstance(spec, SelectionRule):
        return spec
    if isinstance(spec, SelectionRule):
        return spec
    spec = dict(spec)
    right = spec.pop("right", "call")
    # 界面给的是区间（dte_lo/hi、delta_lo/hi）；也接受老的点式写法（dte + delta）。
    lo, hi = spec.pop("dte_lo", None), spec.pop("dte_hi", None)
    if lo is not None and hi is not None:
        window = (int(lo), int(hi))
        dte = spec.pop("dte", (int(lo) + int(hi)) // 2)
    else:
        dte = spec.pop("dte", 30)
        window = tuple(spec.pop("dte_window", (max(1, dte - 9), dte + 15)))
    dlo, dhi = spec.pop("delta_lo", None), spec.pop("delta_hi", None)
    ilo, ihi = spec.pop("iv_lo", None), spec.pop("iv_hi", None)
    return SelectionRule(
        right=Right.CALL if str(right).lower().startswith("c") else Right.PUT,
        target_dte=dte,
        dte_window=window,
        target_delta=spec.pop("delta", None if "moneyness" in spec else 0.30),
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
    return ExitRule(
        hold_days=spec.pop("hold_days", 10),
        take_profit=spec.pop("take_profit", 1.0),
        stop_loss=spec.pop("stop_loss", 0.5),
        dte_floor=spec.pop("dte_floor", 5),
    )


def run(
    *,
    engine: str = "trade_table",
    max_concurrent: int | None = None,
    symbol: str = "QQQ",
    start: str | date = "2015-01-01",
    end: str | date = "2024-12-31",
    entry: str | Callable = "sma_20 > sma_50",
    plan: Callable | None = None,
    review: Callable | None = None,
    select: Mapping[str, Any] | SelectionRule | None = None,
    exit: Mapping[str, Any] | ExitRule | None = None,  # noqa: A002 - 与策略术语一致
    quantity: int = 1,
    sizing: "Mapping[str, Any] | Sizing | None" = None,
    capital: float = 100_000.0,
    costs: CostConfig | None = None,
    chain: "str | object | None" = None,
    synthetic: SyntheticChainConfig | None = None,
    name: str = "unnamed",
    refresh: bool = False,
    signals: Mapping[str, Callable] | None = None,
) -> "Result":
    """跑一次成交表回测。

    ``plan`` 是**计划式策略**：一个函数同时决定买什么、买多少、怎么了结。七个组件
    在真实交易里是联动的（回撤深就买得更久、下注更大、止损放宽），参数化写法表达
    不了这种联动。给了 ``plan`` 就绕过 ``entry``/``select``，只能配 ``engine="event_driven"``。
    见 ``backtest.engine.plan``。

    ``chain`` 指定期权数据源。默认 ``None`` 用合成链（**测试夹具，不是数据源**）。
    换成你自己的数据：``chain="file:/路径/到/你的数据"``，或直接传一个实现了
    ``ChainSource`` 协议的对象。详见 ``backtest.data.adapters``。

    ``signals`` 注册自定义信号：``{"名字": lambda bars: 一个 Series}``。
    注册后即可在 ``entry`` 表达式里按名字使用。``bars.adjusted("close")`` 取复权价
    （信号一律用复权价，否则除息日会出现虚假跳空）。

    ``engine`` 选引擎：

    ``"trade_table"``（默认）
        各笔独立计算，快两个数量级，适合扫参数。**没有权益曲线，因此没有回撤和夏普。**
    ``"event_driven"``
        逐日推进，有组合状态。慢，但给出权益曲线、回撤、资金约束与并发上限。
        ``max_concurrent`` 只对它有效。

    同一配置下两条引擎在无约束时产出相同的交易明细——这是互相校验的基础。
    """
    if engine not in ("trade_table", "event_driven"):
        raise StartupError(f"未知引擎 {engine!r}，可选 trade_table / event_driven")
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
    signal_set = SignalSet.from_bars(market.bars, extra=signals, vol_proxy=market.vol_proxy)
    chain_source = build_chain_source(chain, market, config)

    if isinstance(entry, str):
        condition, requires = parse_condition(entry, signal_set.names)
        entry_rule = EntryRule(entry, condition, requires)
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
        from backtest.engine.event_driven import EventDrivenEngine

        outcome = EventDrivenEngine(
            market, chain_source, signal_set, config, max_concurrent=max_concurrent
        ).run(strategy)
    else:
        if max_concurrent is not None:
            raise StartupError(
                "成交表引擎各笔独立计算、互不知晓，撑不起并发上限。"
                "要用 max_concurrent 请改 engine=\"event_driven\"。"
            )
        outcome = TradeTableEngine(market, chain_source, signal_set, config).run(strategy)
    return Result(outcome, market, chain_source, signal_set)


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

    # ---- 风险与研究 ----
    def attribution(self):
        """盈亏归因：delta / gamma / vega / theta / 残差。"""
        from backtest.risk.attribution import attribute

        return attribute(self.trades)

    def attribution_by(self, column: str, bins: int = 4):
        from backtest.risk.attribution import attribute_by

        return attribute_by(self.trades, column, bins)

    def greeks(self):
        """逐日组合层希腊字母。只有事件驱动引擎有。"""
        frame = getattr(self.raw, "greeks_frame", None)
        if frame is None:
            raise AttributeError(
                "成交表引擎没有组合状态，算不出逐日希腊字母。"
                "请用 engine=\"event_driven\" 重跑。"
            )
        return frame

    def stress(self, *, day=None, days_forward: int = 5):
        """某一日的压力网格。默认取持仓最多的那天。"""
        from backtest.risk.stress import stress_grid

        daily = getattr(self.raw, "greeks", ())
        if not daily:
            raise AttributeError("需要 engine=\"event_driven\" 才有逐日希腊字母")
        pick = (max(daily, key=lambda g: g.n_positions) if day is None
                else next((g for g in daily if g.day == day), None))
        if pick is None:
            raise KeyError(f"{day} 不在回测区间内")
        return stress_grid(pick, days_forward=days_forward)

    def scenarios(self, windows=None):
        """历史情景重放。不含任何展开近似，比压力测试更可信。"""
        from backtest.risk.stress import KNOWN_SCENARIOS, scenario_replay

        equity = getattr(self.raw, "equity", None)
        if equity is None:
            raise AttributeError("需要 engine=\"event_driven\" 才有权益曲线")
        return scenario_replay(equity, windows or KNOWN_SCENARIOS)

    def excursion(self):
        """MFE / MAE 研究：止盈止损该设在哪。会检测分布是否被现有规则截断。"""
        from backtest.research.excursion import study

        return study(self.trades, self.raw.strategy.exit)

    def equity_metrics(self) -> dict:
        """依赖权益曲线的指标。成交表引擎给不出，会明确报错。"""
        import numpy as np

        equity = getattr(self.raw, "equity", None)
        if equity is None:
            raise AttributeError(
                "成交表引擎没有权益曲线，因此没有回撤和夏普。"
                "请用 engine=\"event_driven\" 重跑。"
            )
        curve = equity["equity_mid"]
        liq = equity["equity_liq"]
        returns = curve.pct_change().dropna()
        years = max(len(curve) / 252.0, 1e-9)
        drawdown = curve / curve.cummax() - 1
        vol = float(returns.std() * np.sqrt(252)) if len(returns) > 1 else float("nan")
        annual = float((curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1)
        return {
            "总收益": float(curve.iloc[-1] / curve.iloc[0] - 1),
            "年化": annual,
            "年化波动": vol,
            "夏普": annual / vol if vol else float("nan"),
            "最大回撤": float(drawdown.min()),
            "最大回撤(清算价)": float((liq / liq.cummax() - 1).min()),
            "平均资金占用": float((equity["deployed"] / curve).mean()),
            "峰值持仓": int(equity["positions"].max()),
            "偏度": float(returns.skew()), "峰度": float(returns.kurtosis()),
        }

    # ---- 扫描 ----
    def investor_view(self) -> dict:
        """投资人视角：这是 alpha 还是加了杠杆的 beta，水下待多久，收益集中在哪。

        基准用**同期的标的收盘价**，所以 beta 回答的正是"收益里有多少只是
        标的本身在涨"。只有事件驱动引擎有权益曲线，成交表下会说明原因。
        """
        from backtest.research.investor import summary

        bars = self._market.bars
        bench = bars.adjusted("close")
        return summary(self.raw.equity, self.trades, benchmark=bench)

    def cost_sensitivity(self, multiples=(1.0, 2.0, 3.0)):
        """成本翻倍还活着吗。实盘成本高于回测是常态。"""
        from backtest.research.investor import cost_sensitivity

        return cost_sensitivity(self.trades, multiples)

    def anomalies(self) -> list:
        """扫一遍结果里真正反常的地方。什么都没触发就返回空列表。

        规则式，不是让模型去"找找看"——规则可测试、不会编。
        见 ``backtest.research.anomaly``。
        """
        from backtest.research.anomaly import scan

        try:
            metrics = self.equity_metrics()
        except Exception:  # noqa: BLE001
            metrics = None          # 成交表引擎没有权益曲线，相关规则自然跳过
        return scan(self.trades, metrics=metrics, costs=self.costs())

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
