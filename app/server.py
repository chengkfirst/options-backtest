"""本地回测服务。

    python3 app/server.py

浏览器打开 http://localhost:8765 即可。

**为什么必须是本地服务**：回测要跑 Python（numpy/scipy）、要读你的数据，这两样都在你
这台机器上。纯静态网页做不到，所以是"本地跑服务、浏览器当界面"，而不是一个真的网站。

依赖只有标准库。接口只有三个：

    GET  /api/health   服务在不在（前端据此决定是"能跑回测"还是"只能看导出的结果"）
    GET  /api/meta     可用信号、标的、默认参数
    POST /api/run      跑一次回测，返回交易明细

设计上刻意不做的事：不落库、不做用户、不做后台任务队列。运行结果放内存，关掉就没了
——研究过程中真正要留存的是导出的 CSV 和你自己记下的结论，不是一堆半成品运行记录。
"""

from __future__ import annotations

import json
import re
import sys
import traceback
import warnings
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from store import VERDICTS, Store  # noqa: E402

STORE = Store()

PORT = 8765
STATIC = ROOT / "static"

#: 送回前端的列。全部送太重（十年 1700 笔 × 44 列），这些够画图与分组。
COLUMNS = [
    "trade_id", "entry_date", "exit_date", "contract", "quantity",
    "entry_price", "exit_price", "gross_pnl", "fees", "net_pnl",
    "exit_reason", "holding_days",
    "entry_underlying_price", "entry_iv", "entry_delta", "entry_moneyness",
    "entry_dte_calendar", "exit_underlying_price", "exit_iv", "exit_delta",
    # 计划式打的分组标签与备注。**分类口径来自下单当时的真实判断**，
    # 不是事后按结果硬分的——漏掉它，界面就只能按数值区间硬分组。
    "tag", "note",
]
#: 信号列按前缀带上，用户自定义的信号也会自动进来。
SIGNAL_PREFIX = "entry_sig_"


SYNTHETIC_WARNING = (
    "这次用的是合成期权链：标的价格真实，期权价格由 Black-Scholes 生成。"
    "基于标的的结论（何时进场）可信；期权侧的结论（选什么 delta、止盈设多少）不可信。"
)


def _tradeoff(q: dict[str, str]) -> dict[str, Any]:
    """某一天的「虚值 × 期限」取舍面。

    从**当日真实链**上算，不是贴一张公式表——真实数据接进来之后它会自动反映真实的
    偏斜与期限结构。所以它描述的始终是"你当下真的能买到什么"。
    """
    from backtest.api import _as_date
    from backtest.config import BacktestConfig, ContractSpec, CostConfig, SyntheticChainConfig
    from backtest.data.chain_registry import build_chain_source
    from backtest.data.market import MarketData, VOL_PROXY_BY_SYMBOL
    from backtest.research.tradeoff import tradeoff_grid
    from backtest.selection.rules import enrich_chain

    from datetime import timedelta

    symbol = q.get("symbol", "QQQ")
    asof = _as_date(q.get("date") or "2024-12-31")
    # 配置区间必须**跨过**查询日往后延：合成链只在区间内生成到期日，
    # 把结束日设成查询日的话，那天前面一个到期日都没有，链是空的。
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
        raise ValueError(f"{start} 到 {asof} 之间没有交易日")
    day = sessions[-1]          # 不晚于查询日的最后一个交易日
    spot = market.spot(day)
    if not spot.is_present:
        raise ValueError(f"{day} 没有标的价格：{spot.reason}")

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
    """界面要的全部静态信息。

    模块级函数而非 Handler 方法：它不需要请求状态，而且前后端契约检查
    要在不起服务的情况下拿到它。
    """
    from backtest.data.market import VOL_PROXY_BY_SYMBOL
    from backtest.components import COUPLINGS, as_form_spec
    from backtest.signals.registry import DEFAULT_SIGNALS, describe_signals

    return {
        "symbols": sorted(VOL_PROXY_BY_SYMBOL),
        "signals": sorted(DEFAULT_SIGNALS),
        # 名字之外还要含义、单位、窗口、坑。只给名字的话，界面只能把它们
        # 按字母表倒成一排，使用者得自己猜 rv_20 是按什么口径年化的。
        "signal_docs": describe_signals().fillna("").to_dict("records"),
        # 界面的表单从这份定义生成。组件是什么、要填什么，只有这一处说法。
        "form": as_form_spec(),
        "couplings": [
            {"a": a, "b": b, "why": why} for a, b, why in COUPLINGS
        ],
        "gaps": _describe_gaps(),
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
            # 默认落在表达式而不是代码：代码框占掉一屏，会把后面的组件挤出视野。
            # 要写代码的人自己切一下，比每个人一打开就看不到全貌好。
            "entry_mode": "expr",
            "plan_code": (
                "def plan(ctx):\n"
                "    \"\"\"一个函数同时决定买什么、买多少、怎么了结。\n"
                "\n"
                "    这四件事在真实交易里是一个判断：回撤越深，买得越久、\n"
                "    下注越大、止损越宽、目标越高。拆成四个常数就表达不了。\n"
                "    返回 None 表示今天不做。\n"
                "    \"\"\"\n"
                "    rsi = ctx.signal(\"rsi_14\", default=50.0)\n"
                "    if rsi > 45:\n"
                "        return None                      # ② 时点：不够超卖\n"
                "\n"
                "    deep = rsi < 35\n"
                "\n"
                "    # ②b 选什么：深档买更久、更实值\n"
                "    c = ctx.calls[(ctx.calls.spread < 0.10) & ctx.calls.delta.notna()]\n"
                "    c = c[c.dte.between(45, 75)] if deep else c[c.dte.between(21, 45)]\n"
                "    if c.empty:\n"
                "        return None\n"
                "    target = 0.50 if deep else 0.30\n"
                "\n"
                "    return Plan(\n"
                "        contract    = c.iloc[(c.delta - target).abs().argmin()],\n"
                "        risk_pct    = 0.030 if deep else 0.015,   # ⑥ 多大\n"
                "        stop_loss   = 0.60  if deep else 0.35,    # ④ 论据失效点\n"
                "        take_profit = 1.00  if deep else 0.60,    # ③ 兑现点\n"
                "        max_hold    = 40    if deep else 15,\n"
                "        tag         = \"深超卖\" if deep else \"浅超卖\",\n"
                "        note        = f\"RSI {rsi:.0f}\",\n"
                "    )\n"
            ),
            "entry_code": (
                "def entry(view):\n"
                "    \"\"\"返回 True 就在这天入场。缺失信号一律不入场。\"\"\"\n"
                "    rsi = view[\"rsi_14\"]\n"
                "    rv  = view[\"rv_20\"]\n"
                "    if rsi.is_missing or rv.is_missing:\n"
                "        return False\n"
                "    # 高波动环境下要求更极端的超卖\n"
                "    if rv.unwrap() > 0.30:\n"
                "        return rsi.unwrap() < 25\n"
                "    return rsi.unwrap() < 35\n"
            ),
            "review_code": "",
            "signals_code": (
                "# 自定义信号：名字 -> 函数(bars) -> Series\n"
                "# 一律用 adjusted（复权价），否则除息日会出现虚假跳空\n"
                "SIGNALS = {\n"
                "    \"ma20_slope\": lambda b: b.adjusted(\"close\")\n"
                "                       .rolling(20).mean().pct_change(5),\n"
                "}\n"
            ),
        },
        "code_help": USER_CODE_HELP,
        "plan_help": PLAN_HELP,
    }


def _describe_gaps() -> list[str]:
    """七个组件里当前撑不起来的那些。

    **从 backtest.components 读，不在这里手写。** 手写的清单会在功能补上之后忘了改，
    于是界面长期挂着一条早就不成立的警告——那比不显示更糟，因为它看起来是权威的。
    """
    from backtest.components import gaps

    return gaps() + ["卖方：现金担保 put 的指派已建模，但下单方向仍限多头"]


def run_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    """把前端的一份配置跑成一次回测，并落库。

    落库而不是留在内存：长期研究积累的是判断，而判断依附在运行记录上。服务重启就
    丢光的东西是玩具，不是研究工具。
    """
    from backtest import run as run_backtest_fn

    started = datetime.now()
    kwargs: dict[str, Any] = {
        "symbol": payload.get("symbol", "QQQ"),
        "start": payload.get("start", "2020-01-01"),
        "end": payload.get("end", "2024-12-31"),
        "entry": payload.get("entry", "sma_20 > sma_50"),
        "quantity": int(payload.get("quantity", 1)),
        # 选约按「可行域 + 排序目标」组装。只传真给了的字段——留空表示不限，
        # 而不是替使用者编一个默认区间。
        "select": {
            k: v for k, v in {
                "right": payload.get("right", "call"),
                "dte_lo": _opt_int(payload.get("dte_lo")),
                "dte_hi": _opt_int(payload.get("dte_hi")),
                "delta_lo": _opt_float(payload.get("delta_lo")),
                "delta_hi": _opt_float(payload.get("delta_hi")),
                "iv_lo": _opt_float(payload.get("iv_lo")),
                "iv_hi": _opt_float(payload.get("iv_hi")),
                "max_spread": _opt_float(payload.get("max_spread")),
                "min_open_interest": _opt_int(payload.get("min_open_interest")),
                "delta": _opt_float(payload.get("delta")),
            }.items() if v is not None
        },
        "exit": {
            "hold_days": _opt_int(payload.get("hold_days")),
            "take_profit": _opt_float(payload.get("take_profit")),
            "stop_loss": _opt_float(payload.get("stop_loss")),
            "dte_floor": _opt_int(payload.get("dte_floor")),
        },
        "name": payload.get("name") or "run",
    }
    if payload.get("signals_code", "").strip():
        kwargs["signals"] = compile_signals(payload["signals_code"])
    if isinstance(payload.get("sizing"), dict):
        kwargs["sizing"] = payload["sizing"]
    kwargs["engine"] = payload.get("engine", "trade_table")
    if kwargs["engine"] == "event_driven":
        mc = payload.get("max_concurrent")
        kwargs["max_concurrent"] = int(mc) if mc not in (None, "", "none") else None

    if payload.get("strategy_mode") == "plan":
        code = payload.get("plan_code", "").strip()
        if not code:
            raise ValueError("选了交易计划模式，但计划代码是空的")
        if kwargs["engine"] != "event_driven":
            raise ValueError(
                "交易计划只能用事件驱动引擎。成交表引擎各笔独立、向量化算出场，"
                "表达不了'这一笔用这套出场规则'——而那正是计划式的要点。"
            )
        kwargs["plan"] = compile_plan(code)
    if payload.get("review_code", "").strip():
        kwargs["review"] = compile_review(payload["review_code"])
        if kwargs["engine"] != "event_driven":
            raise ValueError("逐日复查只能用事件驱动引擎：成交表向量化算出场，没有「逐日」这个环节。")
    if payload.get("strategy_mode") == "plan":
        pass
    elif payload.get("entry_mode") == "code":
        code = payload.get("entry_code", "").strip()
        if not code:
            raise ValueError("选了代码模式，但入场代码是空的")
        kwargs["entry"] = compile_entry(code)

    result = run_backtest_fn(**kwargs)
    frame = result.trades
    seconds = (datetime.now() - started).total_seconds()

    cols = [c for c in COLUMNS if c in frame.columns]
    cols += [c for c in frame.columns if c.startswith(SIGNAL_PREFIX)]
    slim = frame[cols].copy() if len(frame) else frame
    for c in slim.columns:
        if slim[c].dtype.kind == "f":
            slim[c] = slim[c].round(5)
    for c in ("entry_date", "exit_date"):
        if c in slim.columns:
            slim[c] = slim[c].astype(str)

    config = _serialisable(kwargs, payload)
    spec = {
        "entry": config["entry"],
        "entry_code": config.get("entry_code", ""),
        "plan_code": config.get("plan_code", ""),
        "strategy_mode": payload.get("strategy_mode", "params"),
        "signals_code": config.get("signals_code", ""),
        "select": config["select"],
        "exit": config["exit"],
        "quantity": config["quantity"],
        "sizing": config.get("sizing"),
    }
    strategy = STORE.save_strategy(
        payload.get("strategy_name") or "未命名", spec, payload.get("strategy_note", "")
    )

    stats = result.stats()
    dist = result.distribution()
    prov = result.provenance()
    record = STORE.save_run(
        strategy=strategy,
        config=config,
        manifest=result.manifest(),
        events=result.raw.events.counts(),
        seconds=seconds,
        summary={
            "n": stats["count"],
            "net_pnl": float(stats.get("net_pnl", 0.0)),
            "win_rate": float(stats.get("win_rate", 0.0)),
            "median_pnl": float(dist.get("median", 0.0)) if stats["count"] else 0.0,
        },
        trades=slim,
        synthetic=bool(prov.get("all_synthetic")),
    )
    record["rows"] = json.loads(slim.to_json(orient="records")) if len(slim) else []
    # 说明分三类，位置固定，不混在一起：
    #   boundary   —— 这组数字的硬边界。必须显眼，因为它决定结论能不能用
    #   problems   —— 这次运行真出了问题。要能看到细节
    #   （组件缺口不在这里）—— 它是静态的，不随运行改变，位置在左边栏各层旁边。
    #   每跑一次都在结果区倒一遍同样的七条，看两次就开始无视，比不显示更糟。
    record["boundary"] = ""
    record["problems"] = []
    record["engine"] = kwargs["engine"]
    # 事件驱动独有的产出：权益曲线、组合指标、归因、情景。成交表拿不到的部分不编，
    # 直接不给这个键——前端据此决定显示还是隐藏，而不是显示一堆零。
    if kwargs["engine"] == "event_driven":
        try:
            equity = result.raw.equity
            record["equity"] = json.loads(
                equity.reset_index().to_json(orient="records", date_format="iso"))
            record["equity_metrics"] = result.equity_metrics()
            record["scenarios"] = json.loads(
                result.scenarios().reset_index().to_json(orient="records"))
        except Exception as exc:  # noqa: BLE001
            record["problems"].append(f"权益曲线相关指标计算失败：{type(exc).__name__}: {exc}")
    try:
        record["attribution"] = result.attribution().as_dict()
    except Exception as exc:  # noqa: BLE001
        record["problems"].append(f"盈亏归因失败：{type(exc).__name__}: {exc}")
    record["strategy_reused"] = strategy.get("reused", False)
    if prov.get("all_synthetic"):
        record["boundary"] = (
            "合成期权链：标的价真实，期权价由 Black-Scholes 生成，IV 是常数、无偏斜。"
            "**何时进场**的结论可信；**选什么 delta、止盈设多少**不可信。"
        )
    # 本次运行的诊断。与组件缺口不同，这些每次都不一样，而且是可查的——
    # 前端可以展开看具体是哪几天、为什么被丢弃。
    record["diagnostics"] = _run_diagnostics(result)
    # 异常扫描：规则式，不触发就是空列表——界面据此决定显示还是隐藏。
    try:
        from dataclasses import asdict

        record["anomalies"] = [asdict(f) for f in result.anomalies()]
    except Exception as exc:  # noqa: BLE001
        record["anomalies"] = []
        record["problems"].append(f"异常扫描失败：{type(exc).__name__}: {exc}")
    record["notes"] = [record["boundary"]] if record["boundary"] else []
    return record


def _run_diagnostics(result) -> list[dict[str, Any]]:
    """把运行事件汇总成"发生了什么、多少次、点开能看哪几条"。

    只给个次数没用——"候选被丢弃 45 次"不告诉你该不该在意。要能看到原因分布，
    才知道是"信号太严"还是"链上根本选不出合约"。
    """
    try:
        events = result.events
    except Exception:  # noqa: BLE001
        return []
    if events is None or not len(events):
        return []
    out = []
    for kind, group in events.groupby("kind", sort=False):
        reasons = (group["detail"].astype(str).str.slice(0, 80)
                   .value_counts().head(5).to_dict())
        out.append({
            "kind": str(kind),
            "count": int(len(group)),
            "first": str(group["date"].iloc[0]),
            "last": str(group["date"].iloc[-1]),
            "reasons": [{"why": k, "n": int(v)} for k, v in reasons.items()],
        })
    return sorted(out, key=lambda d: -d["count"])


USER_CODE_HELP = """可用的东西：

  view["sma_20"]        信号值，返回一个 Maybe
    .is_missing         预热期或数据缺口时为 True
    .unwrap()           取出数字（缺失时调用会报错，所以要先判断）
  view.day              当天日期（datetime.date）
  view.names()          全部已注册的信号名

必须返回 True / False。任一信号缺失时返回 False（不入场）是惯例——
缺失绝不能当成 0 参与比较。"""


PLAN_HELP = """计划函数拿到的 ctx：

  ctx.day                 决策日
  ctx.spot                标的当日价
  ctx.chain               当日全部可执行合约（DataFrame，一行一张）
  ctx.calls / ctx.puts    同上，已按类型筛好
  ctx.equity              当前权益      ← ⑥ 仓位靠它
  ctx.cash                可用现金
  ctx.positions           当前持仓数
  ctx.drawdown            当前回撤（负数，如 -0.08）
  ctx.signal(名字, default=...)  取信号；缺失且没给默认值会报错，不会悄悄给 0
  ctx.view[名字]          原始 Maybe，需要自己处理缺失时用

chain / calls / puts 的列：
  iv delta gamma vega theta   希腊字母与隐含波动率
  bid ask mid spread          报价；spread 是相对买卖价差
  dte                         剩余日历天数（市场惯例口径）
  sessions_left               剩余交易日（可执行性口径）
  moneyness                   正数一律表示虚值，看涨看跌同号
  strike expiry right         合约标识
  is_call / is_put            布尔，不用记 "C"/"P"

链已剔除 T+1 执行不了的合约，按希腊字母随便挑都不会挑到开不了的。

Plan 的字段：
  contract      从 chain 里挑的一行（必填）
  contracts / risk_pct / notional    仓位，三选一；都不给用全局仓位方法
  take_profit / stop_loss            不给则回落到参数模式里的全局值
  max_hold / dte_floor               同上
  tag           分组标签，存进明细，事后按它分类对比
  note          给人看的一句话，也存进明细
"""


def compile_plan(code: str):
    """把用户写的 Python 编成计划函数。

    与 ``compile_entry`` 同样在本机本进程执行。编译或执行出错时把完整错误抛回前端。
    """
    namespace: dict[str, Any] = {}
    from backtest.engine.plan import Plan

    namespace["Plan"] = Plan
    try:
        exec(compile(code, "<你写的交易计划>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(
            f"计划代码语法错误，第 {exc.lineno} 行：{exc.msg}\n\n{PLAN_HELP}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"计划代码执行出错：{type(exc).__name__}: {exc}") from exc

    fn = namespace.get("plan")
    if fn is None or not callable(fn):
        raise ValueError(
            "计划代码里必须定义一个名为 plan 的函数。\n\n" + PLAN_HELP
        )
    return fn


def compile_review(code: str):
    """把用户写的逐日复查函数编出来。``Close`` / ``Reduce`` / ``Add`` 直接给到命名空间。"""
    from backtest.engine.review import Add, Close, Reduce

    namespace: dict[str, Any] = {"Close": Close, "Reduce": Reduce, "Add": Add}
    try:
        exec(compile(code, "<你写的逐日复查>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(f"复查代码语法错误，第 {exc.lineno} 行：{exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"复查代码执行出错：{type(exc).__name__}: {exc}") from exc
    fn = namespace.get("review")
    if fn is None or not callable(fn):
        raise ValueError(
            "复查代码里必须定义一个名为 review 的函数：\n\n"
            "def review(ctx, pos):\n"
            "    if pos.mfe_so_far > 0.4 and pos.ratio < pos.mfe_so_far * 0.67:\n"
            "        return Close('回吐三分之一', kind='take_profit')\n"
            "    return None"
        )
    return fn


def compile_entry(code: str):
    """把用户写的 Python 编成入场函数。

    这段代码在**你自己的机器上、你自己的进程里**执行，和你在 Jupyter 里写一样。
    服务只监听 127.0.0.1，不对外。

    编译或执行出错时把完整错误抛回前端——在浏览器里看到"出错了"却不知道哪错了，
    比直接崩还糟。
    """
    namespace: dict[str, Any] = {}
    try:
        exec(compile(code, "<你写的入场逻辑>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(
            f"入场代码语法错误，第 {exc.lineno} 行：{exc.msg}\n\n{USER_CODE_HELP}"
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"入场代码执行出错：{type(exc).__name__}: {exc}") from exc

    fn = namespace.get("entry")
    if fn is None or not callable(fn):
        raise ValueError(
            "入场代码里必须定义一个名为 entry 的函数，例如：\n\n"
            "def entry(view):\n"
            "    rsi = view['rsi_14']\n"
            "    if rsi.is_missing:\n"
            "        return False\n"
            "    return rsi.unwrap() < 30\n\n" + USER_CODE_HELP
        )

    def guarded(view):
        try:
            out = fn(view)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"入场函数在 {getattr(view, 'day', '?')} 这天抛了异常："
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(out, bool):
            raise RuntimeError(
                f"入场函数必须返回 True 或 False，{getattr(view, 'day', '?')} 这天返回了 "
                f"{type(out).__name__}：{out!r}"
            )
        return out

    guarded.__name__ = "用户入场逻辑"
    return guarded


def compile_signals(code: str) -> dict[str, Any]:
    """把用户写的 Python 编成自定义信号。

    约定：定义一个名为 SIGNALS 的字典，``{"名字": lambda bars: 一个 Series}``。
    ``bars.adjusted("close")`` 取复权价——信号一律用复权价，否则除息日会出现虚假跳空。
    """
    namespace: dict[str, Any] = {}
    try:
        exec(compile(code, "<你写的信号>", "exec"), namespace)
    except SyntaxError as exc:
        raise ValueError(f"信号代码语法错误，第 {exc.lineno} 行：{exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"信号代码执行出错：{type(exc).__name__}: {exc}") from exc

    signals = namespace.get("SIGNALS")
    if signals is None:
        raise ValueError(
            "信号代码里必须定义一个名为 SIGNALS 的字典，例如：\n\n"
            "SIGNALS = {\n"
            "    'ma20_slope': lambda b: b.adjusted('close').rolling(20).mean().pct_change(5),\n"
            "}"
        )
    if not isinstance(signals, dict) or not all(callable(v) for v in signals.values()):
        raise ValueError("SIGNALS 必须是 {名字: 函数} 的字典")
    return signals


def _finite(obj):
    """把 NaN / ±Infinity 换成 ``None``，递归处理。

    语义上也对：夏普算不出来时它**不是 0，也不是某个数**，就是"没有"。
    前端拿到 null 会显示"—"，拿到 0 会显示"0.00"——后者是在编造一个数字。
    """
    import math

    if isinstance(obj, float):
        return None if not math.isfinite(obj) else obj
    if isinstance(obj, dict):
        return {k: _finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite(v) for v in obj]
    return obj


def _serialisable(kwargs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """把配置变成能 JSON 序列化的形式。

    代码模式下 ``entry`` 是个函数对象、``signals`` 是一堆 lambda——直接丢给 json.dumps
    会炸。这里换成可读的描述，既能送回前端展示，也能让运行记录看得懂用的是哪套规则。
    """
    out = dict(kwargs)
    if callable(out.get("entry")):
        code = payload.get("entry_code", "")
        first = next((ln.strip() for ln in code.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")), "自写代码")
        out["entry"] = f"（自写代码）{first}"
        out["entry_code"] = code
    if "signals" in out:
        out["signals"] = sorted(out["signals"])
        out["signals_code"] = payload.get("signals_code", "")
    return out


def _opt_int(v):
    return None if v in (None, "", "none") else int(v)


def _opt_float(v):
    return None if v in (None, "", "none") else float(v)


#: 这些类型必须带上 charset。不带的话浏览器按自己的默认编码去猜，中文直接乱码。
TEXT_TYPES = {"text/html", "text/css", "text/javascript", "application/javascript",
              "application/json", "text/plain"}


class Handler(SimpleHTTPRequestHandler):
    #: 标准库默认 HTTP/1.0：每个请求都要新建一次 TCP 连接。页面一次加载就有
    #: 五六个静态文件加若干 /api 调用，连接churn没必要。声明 1.1 之后浏览器能复用
    #: 连接。前提是**每个响应都必须带正确的 Content-Length**——否则浏览器会一直
    #: 等下去，页面转圈而两边都不报错。当前所有出口都满足：``_json`` 显式设置，
    #: 静态文件走 SimpleHTTPRequestHandler 的分支，错误页走 send_error。
    #: 以后新增任何直接写 wfile 的分支，必须一并设置 Content-Length。
    protocol_version = "HTTP/1.1"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def _serve_index(self):
        """发首页时给每个 js/css 加上按文件修改时间的版本号。

        浏览器会按启发式规则缓存 js/css，于是新 HTML 配上旧 JS，报出来的是
        "某个元素是 null" 或 "某函数未定义"——症状指向代码，原因却是没加载新文件。

        Cache-Control 只对以后生效，救不了已缓存的；改文件名要手工维护。
        按 mtime 生成 ``?v=``：文件一改 URL 就变，浏览器必然重新取。
        """
        index = STATIC / "index.html"
        if not index.exists():
            return self.send_error(404, "index.html 不在 %s" % STATIC)
        html = index.read_text(encoding="utf-8")

        def stamp(match):
            attr, name = match.group(1), match.group(2)
            target = STATIC / name
            if not target.exists():
                # 引用了不存在的文件。静默留着会让页面少一个脚本却不报错。
                sys.stderr.write(f"  index.html 引用了不存在的文件：{name}\n")
                return match.group(0)
            return f'{attr}="{name}?v={int(target.stat().st_mtime)}"'

        html = re.sub(r'(src|href)="([\w.\-/]+\.(?:js|css))"', stamp, html)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def end_headers(self):
        """静态文件一律要求浏览器回源校验。

        不加这条，Chrome 会按启发式规则缓存 js/css，改完刷新仍是旧代码，
        且不报任何错。本地工具不在乎这点带宽，正确性优先。
        """
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def guess_type(self, path):
        """给文本类型补上 charset=utf-8。

        标准库只按扩展名给出 ``text/html``，不带编码。页面全是中文，浏览器猜错一次
        就是满屏乱码——而且这属于静默失败：页面能打开、不报任何错，只是没法看。
        """
        base = super().guess_type(path)
        main = base.split(";")[0].strip()
        return f"{main}; charset=utf-8" if main in TEXT_TYPES else base

    def log_message(self, fmt, *args):  # 只打印出错，成功的请求不刷屏
        if not str(args[1] if len(args) > 1 else "").startswith("2"):
            sys.stderr.write("  %s\n" % (fmt % args))

    # ---- API ----
    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path in ("/", "/index.html"):
            return self._serve_index()
        if url.path == "/api/health":
            return self._json({"ok": True, **STORE.overview()})
        if url.path == "/api/tradeoff":
            try:
                return self._json(_tradeoff(q))
            except Exception as exc:  # noqa: BLE001
                return self._json({"error": f"{type(exc).__name__}: {exc}",
                                   "detail": traceback.format_exc()}, 400)
        if url.path == "/api/meta":
            return self._json(self._meta())
        if url.path == "/api/runs":
            return self._json({"runs": STORE.runs(
                strategy_name=q.get("strategy"), symbol=q.get("symbol"),
                verdict=q.get("verdict"), limit=int(q.get("limit", 200)))})
        if url.path == "/api/run":
            try:
                run_id = int(q.get("id", ""))
            except ValueError:
                return self._json({"error": "缺少或非法的 id 参数"}, 400)
            record = STORE.run(run_id)
            if record is None:
                return self._json({"error": f"没有第 {run_id} 次运行"}, 404)
            try:
                trades = STORE.trades(run_id)
            except (FileNotFoundError, KeyError) as exc:
                return self._json({"error": str(exc)}, 410)
            record["rows"] = json.loads(trades.to_json(orient="records")) if len(trades) else []
            record["notes_list"] = STORE.notes(run_id)
            record["notes"] = list(_describe_gaps())
            if record["synthetic"]:
                record["notes"].insert(0, SYNTHETIC_WARNING)
            return self._json(record)
        if url.path == "/api/strategies":
            name = q.get("name")
            return self._json({"strategies":
                STORE.strategy_versions(name) if name else STORE.strategies()})
        if url.path == "/api/notes":
            return self._json({"notes": STORE.notes(), "verdicts": VERDICTS})
        if url.path.startswith("/api/"):
            return self._json({"error": f"未知接口 {url.path}"}, 404)
        return super().do_GET()

    def do_HEAD(self):
        """API 路径上的 HEAD 走同一套路由。

        否则 HEAD /api/meta 会被当成静态文件返回 404：路径存在却报不存在。
        """
        if self.path.startswith("/api/"):
            self._head_only = True
            try:
                return self.do_GET()
            finally:
                self._head_only = False
        return super().do_HEAD()

    def do_POST(self):
        if self.path not in ("/api/run", "/api/note", "/api/run/delete"):
            return self._json({"error": f"未知接口 {self.path}"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": f"请求体不是合法 JSON：{exc}"}, 400)

        if self.path == "/api/note":
            try:
                note = STORE.add_note(
                    self._run_id(payload),
                    payload.get("body", ""),
                    payload.get("verdict", "open"),
                )
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"note": note})

        if self.path == "/api/run/delete":
            try:
                STORE.delete_run(self._run_id(payload))
            except (ValueError, KeyError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"ok": True})

        try:
            record = run_backtest(payload)
        except Exception as exc:  # noqa: BLE001
            # 整条错误链送回前端。静默失败是这个项目最不能接受的错误类型——
            # 浏览器里看到"出错了"却不知道哪错了，比直接崩还糟。
            detail = traceback.format_exc(limit=6)
            sys.stderr.write(detail)
            return self._json(
                {"error": f"{type(exc).__name__}: {exc}", "detail": detail}, 400
            )

        return self._json(record)

    # ---- 辅助 ----
    def _meta(self) -> dict[str, Any]:
        return _meta_payload()

    def _run_id(self, payload) -> int:
        """从请求体里取运行 id。

        取不到就报错，绝不退回 0。``payload.get("run_id", 0)`` 这种写法会把
        "前端没发 id" 悄悄变成"操作第 0 次运行"——笔记挂错地方、删错记录，
        而且全程不报任何错。
        """
        raw = payload.get("id", payload.get("run_id"))
        if raw is None:
            raise ValueError("请求体里没有运行 id（字段名 id）")
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"运行 id 不是整数：{raw!r}") from None

    def _json(self, obj, status: int = 200):
        """序列化并回包。

        序列化本身也可能失败（比如配置里混进了函数对象）。那种时候绝不能让连接
        直接断掉——前端只会看到"请求失败"，完全不知道哪错了。所以退回一个纯文本
        错误，把原因说清楚。
        """
        try:
            # allow_nan=False 是关键。默认会输出 NaN / Infinity 字面量——Python 自己
            # 读得回来，但 **JSON 标准不允许**，浏览器 JSON.parse 直接失败。
            # 表现是：服务端 200、日志干净，前端只说"返回的不是 JSON"，无从查起。
            # 典型触发：零交易时夏普是 NaN，整个响应随之报废。
            # 所以先把非有限数换成 null（"算不出来"本来就该是 null），
            # 再用 allow_nan=False 兜底——万一还有漏网的，宁可响亮报错也不要发出非法 JSON。
            body = json.dumps(_finite(obj), ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            status = 500
            body = json.dumps(
                {"error": f"服务端序列化响应失败：{exc}",
                 "detail": "这是服务端的 bug，请把这条连同操作步骤反馈。"},
                ensure_ascii=False,
            ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)


def main() -> int:
    if not STATIC.exists():
        sys.stderr.write(f"找不到前端目录：{STATIC}\n")
        return 1
    try:
        import backtest  # noqa: F401
    except ImportError as exc:
        sys.stderr.write(
            f"导入 backtest 失败：{exc}\n"
            f"请用装了依赖的 Python 启动，例如：\n"
            f"  /opt/anaconda3/bin/python3 {Path(__file__).relative_to(Path.cwd())}\n"
        )
        return 1

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"回测台已启动  →  http://localhost:{PORT}")
    print("Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
