"""数据处理流程：每一步做了什么、为什么这么做、代价是什么、在哪里改。

**为什么要有这个模块。** 数据处理的每一步都是取舍：复权要不要施加到开高低、缺失值
是填充还是标记、异常数据是拒绝还是放行。这些选择不写下来，三个月后回头看只能靠读
代码猜；而且换一个标的、换一个数据源，合适的选择可能就变了。

所以这里把流程显式化：每一步声明**方法**、**理由**、**备选方案及其代价**、**在哪个
文件改**，并带一个**检查器**——检查器跑在真实数据上，输出通过与否和具体数字，而不是
一句"看起来没问题"。

用法：

    report = inspect_pipeline(market)
    print(report.text())        # 人看
    report.as_dict()            # 送给界面

检查器只报告，不修改数据。修数据的责任在各自的模块里——这里是体检，不是治疗。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

PASS, WARN, FAIL = "pass", "warn", "fail"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """一步的检查结论。"""

    status: str                       # pass / warn / fail
    detail: str                       # 一句话说清楚检查了什么、结果是什么
    numbers: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status != FAIL


@dataclass(frozen=True, slots=True)
class Step:
    """流程中的一步。"""

    id: str
    name: str
    method: str                       # 这一步实际怎么做的
    why: str                          # 为什么这么做
    alternatives: list[tuple[str, str]]   # [(备选方案, 它的代价)]
    where: str                        # 想改的话改哪个文件
    check: Callable[[Any], CheckResult]

    def run(self, market) -> "StepReport":
        try:
            result = self.check(market)
        except Exception as exc:  # noqa: BLE001
            # 检查器自己挂了也必须说出来——静默跳过一个检查，等于没有这个检查
            result = CheckResult(FAIL, f"检查器本身出错：{type(exc).__name__}: {exc}")
        return StepReport(step=self, result=result)


@dataclass(frozen=True, slots=True)
class StepReport:
    step: Step
    result: CheckResult

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.step.id,
            "name": self.step.name,
            "method": self.step.method,
            "why": self.step.why,
            "alternatives": [{"option": o, "cost": c} for o, c in self.step.alternatives],
            "where": self.step.where,
            "status": self.result.status,
            "detail": self.result.detail,
            "numbers": self.result.numbers,
        }


@dataclass(frozen=True, slots=True)
class PipelineReport:
    steps: list[StepReport]

    @property
    def failed(self) -> list[StepReport]:
        return [s for s in self.steps if s.result.status == FAIL]

    @property
    def warned(self) -> list[StepReport]:
        return [s for s in self.steps if s.result.status == WARN]

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.as_dict() for s in self.steps],
            "summary": {
                "total": len(self.steps),
                "passed": sum(1 for s in self.steps if s.result.status == PASS),
                "warned": len(self.warned),
                "failed": len(self.failed),
            },
        }

    def text(self) -> str:
        mark = {PASS: "✓", WARN: "!", FAIL: "✗"}
        out = ["数据处理流程", "─" * 64]
        for s in self.steps:
            out += [
                f"\n{mark[s.result.status]} {s.step.name}",
                f"    方法  {s.step.method}",
                f"    理由  {s.step.why}",
                f"    实测  {s.result.detail}",
                "    备选  " + " ｜ ".join(f"{o} → {c}" for o, c in s.step.alternatives),
                f"    改动  {s.step.where}",
            ]
        d = self.as_dict()["summary"]
        out += ["", "─" * 64, f"{d['passed']} 通过 · {d['warned']} 警告 · {d['failed']} 失败"]
        return "\n".join(out)

# ══════════════════════════════════════════════════════════════
#  各步的检查器
# ══════════════════════════════════════════════════════════════

def _check_source(market) -> CheckResult:
    bars = market.bars
    n = len(bars)
    first, last = bars.dates[0], bars.dates[-1]
    cfg = market.config
    late = first > cfg.start
    return CheckResult(
        WARN if late else PASS,
        f"读入 {n} 个交易日，{first} 至 {last}。"
        + (f"请求起始是 {cfg.start}，实际可用晚了 {(first - cfg.start).days} 天。" if late
           else "覆盖请求区间。"),
        {"sessions": n, "first": str(first), "last": str(last)},
    )


def _check_dual_price(market) -> CheckResult:
    bars = market.bars
    traded, adjusted = bars.traded("close"), bars.adjusted("close")
    diff = (traded - adjusted)
    same = bool(np.allclose(traded, adjusted))
    ratio_close = (adjusted / traded)
    ratio_high = (bars.adjusted("high") / bars.traded("high"))
    consistent = bool(np.allclose(ratio_close, ratio_high))
    if not consistent:
        return CheckResult(FAIL, "复权因子在收盘价与最高价上不一致——说明复权只施加到了部分字段")
    if same:
        return CheckResult(
            WARN,
            "两套价格完全相同：这段区间内没有分红也没有拆股，或者数据源没给复权价。"
            "后者会让期权的价值状态判断在长区间上系统性偏移。",
            {"max_gap": 0.0},
        )
    return CheckResult(
        PASS,
        f"两套价格已分离，最大差额 {diff.abs().max():.2f}（{(diff.abs() / traded).max() * 100:.2f}%），"
        f"复权因子在开高低收四个字段上一致。",
        {"max_gap": round(float(diff.abs().max()), 4),
         "max_gap_pct": round(float((diff.abs() / traded).max()) * 100, 3)},
    )


def _check_calendar(market) -> CheckResult:
    cal, bars = market.calendar, market.bars
    off = [d for d in bars.dates if d not in cal]
    if off:
        return CheckResult(FAIL, f"{len(off)} 个行情日期不在交易日历上，首个是 {off[0]}",
                           {"off_calendar": len(off)})
    sessions = market.sessions()
    return CheckResult(
        PASS,
        f"行情日期全部落在交易日历上；回测区间内 {len(sessions)} 个交易日，"
        f"其中 {sum(1 for d in sessions if cal.is_early_close(d))} 个半日市。",
        {"sessions_in_range": len(sessions),
         "early_closes": sum(1 for d in sessions if cal.is_early_close(d))},
    )


def _check_quality(market) -> CheckResult:
    f = market.bars.frame
    issues = {
        "非正价格": int((f[["open", "high", "low", "close"]] <= 0).any(axis=1).sum()),
        "最高低于最低": int((f["high"] < f["low"]).sum()),
        "重复日期": int(f.index.duplicated().sum()),
        "缺失价格": int(f[["open", "high", "low", "close"]].isna().any(axis=1).sum()),
    }
    bad = {k: v for k, v in issues.items() if v}
    if bad:
        return CheckResult(FAIL, "行情存在异常：" + "、".join(f"{k} {v} 条" for k, v in bad.items()), issues)
    return CheckResult(PASS, f"{len(f)} 行全部通过：价格为正、高低有序、日期不重复、无缺失。", issues)


def _check_rates(market) -> CheckResult:
    rates = market._rates
    if rates.empty:
        return CheckResult(
            WARN,
            f"利率序列不可得，全程回退常数 {market.config.fallback_risk_free_rate:.2%}。"
            f"区间跨越加息周期时，这会让长期限合约的定价整体偏移。",
            {"constant": market.config.fallback_risk_free_rate},
        )
    lo, hi = float(rates.min()), float(rates.max())
    return CheckResult(
        PASS,
        f"利率序列 {len(rates)} 个点，区间内从 {lo:.2%} 到 {hi:.2%}"
        + ("（跨越了明显的利率周期，用序列而非常数是必要的）" if hi - lo > 0.02 else "。"),
        {"points": len(rates), "min": round(lo, 5), "max": round(hi, 5)},
    )


def _check_dividends(market) -> CheckResult:
    div = market._dividends
    if div.empty:
        return CheckResult(
            WARN,
            "没有分红数据，定价按零分红处理。这会高估看涨期权的理论价，"
            "也会让提前行权的判断失效。",
            {"events": 0},
        )
    return CheckResult(
        PASS,
        f"{len(div)} 次除息，合计每股 {float(div.sum()):.2f}。"
        f"定价用的分红率由存续期内的实际除息导出，不是年化常数。",
        {"events": len(div), "total_per_share": round(float(div.sum()), 4)},
    )


def _check_vol_proxy(market) -> CheckResult:
    proxy = market._vol_proxy
    symbol = market.config.contract.vol_proxy
    if proxy.empty:
        return CheckResult(
            WARN, f"波动率代理 {symbol} 不可得，全程回退该标的自身的已实现波动率。",
            {"symbol": symbol, "points": 0},
        )
    return CheckResult(
        PASS,
        f"波动率代理 {symbol}，{len(proxy)} 个点，"
        f"区间内 {float(proxy.min()):.1%} 至 {float(proxy.max()):.1%}。代理与标的匹配。",
        {"symbol": symbol, "points": len(proxy),
         "min": round(float(proxy.min()), 4), "max": round(float(proxy.max()), 4)},
    )


def _check_expiries(market) -> CheckResult:
    exp = market.expiries
    if not exp:
        return CheckResult(FAIL, "没有生成任何到期日，选约必然全部失败")
    sessions = market.sessions()
    if not sessions:
        return CheckResult(FAIL, "回测区间内没有交易日")
    probe = sessions[len(sessions) // 2]
    near = [e for e in exp if 21 <= (e - probe).days <= 45]
    return CheckResult(
        PASS if near else WARN,
        f"{len(exp)} 个到期日。以 {probe} 为例，21–45 天窗口内有 {len(near)} 个可选到期"
        + ("。" if near else "——该窗口选不到合约，选约会失败。"),
        {"expiries": len(exp), "in_window_sample": len(near)},
    )


def _check_chain(market, chain=None) -> CheckResult:
    if chain is None:
        return CheckResult(WARN, "未提供期权链来源，跳过链的检查")
    sessions = market.sessions()
    probe = sessions[len(sessions) // 2]
    snap = chain.snapshot(probe)
    if snap.empty:
        return CheckResult(FAIL, f"{probe} 的期权链为空")
    unavailable = [c for c in ("volume", "open_interest") if snap[c].isna().all()]
    inverted = int((snap["ask"] < snap["bid"]).sum())
    if inverted:
        return CheckResult(FAIL, f"{probe} 的链上有 {inverted} 条卖价低于买价")
    src = set(snap["source"])
    return CheckResult(
        WARN if "synthetic" in src else PASS,
        f"{probe} 的链有 {len(snap)} 条，{snap['expiry'].nunique()} 个到期 × "
        f"{snap['strike'].nunique()} 个行权价；来源 {'/'.join(sorted(src))}"
        + (f"；{'、'.join(unavailable)} 字段不可用（已标记，下游会跳过相关判断）" if unavailable else "")
        + ("。合成链只能验证引擎与研究标的信号层，期权侧参数不可据此调优。"
           if "synthetic" in src else "。"),
        {"rows": len(snap), "expiries": int(snap["expiry"].nunique()),
         "strikes": int(snap["strike"].nunique()), "unavailable": unavailable,
         "source": sorted(src)},
    )


# ══════════════════════════════════════════════════════════════
#  流程定义
# ══════════════════════════════════════════════════════════════

def build_steps(chain=None) -> list[Step]:
    """流程定义。

    刻意写得短：``method`` 一个短语，``alternatives`` 每条一句话封顶。
    这是给人**扫读复核**用的，不是文档——长了就没人看，也就失去了作用。
    """
    return [
        Step(
            id="source", name="① 读入原始行情",
            method="日线 + 400 天预热；parquet 缓存",
            why="不预热则长周期指标在区间开头大面积缺失",
            alternatives=[
                ("不预热", "开头一年的长周期信号全缺"),
                ("缓存键只用标的+区间", "两套价格会串用"),
            ],
            where="data/sources.py · data/cache.py",
            check=_check_source,
        ),
        Step(
            id="dual_price", name="② 拆成两套价格",
            method="Adj Close / Close 比例，施加到开高低收",
            why="行权价是当时的实际价；复权价会被此后每次分红改写",
            alternatives=[
                ("只存复权价", "历史合约的价值状态系统性偏移"),
                ("只存实际价", "除息日与拆股日出现虚假跳空"),
                ("只复权收盘价", "ATR、布林带宽失真"),
            ],
            where="data/underlying.py build_bars()",
            check=_check_dual_price,
        ),
        Step(
            id="calendar", name="③ 对齐交易日历",
            method="NYSE 日历；定价按日历日、波动率按交易日",
            why="期权周末照常衰减，波动只在交易日产生",
            alternatives=[
                ("统一用日历日", "波动率低估约 17%"),
                ("统一用交易日", "周末的时间衰减被吞掉"),
                ("自维护假日表", "历史假日变更要自己跟"),
            ],
            where="data/calendar.py · config.py TimeConventions",
            check=_check_calendar,
        ),
        Step(
            id="quality", name="④ 质量校验",
            method="行情异常直接拒绝；链上异常剔除并标记",
            why="行情坏了整段不可信，个别合约坏了只影响那一张",
            alternatives=[
                ("全部只标记", "坏行情静默进入回测"),
                ("全部拒绝", "真实数据跑不完"),
                ("自动插值修复", "修出来的值与真实观测无法区分"),
            ],
            where="data/underlying.py validate_bars() · data/chain.py validate_chain()",
            check=_check_quality,
        ),
        Step(
            id="rates", name="⑤ 无风险利率",
            method="三个月期国债序列（^IRX），缺失取前值",
            why="区间跨加息周期时常数会扭曲长期限定价",
            alternatives=[
                ("固定常数", "长 DTE 合约定价整体偏移"),
                ("接 FRED", "多一个数据源与失败点"),
                ("按期限插值", "对中频日线影响在小数点后几位"),
            ],
            where="data/market.py _load_rate_series()",
            check=_check_rates,
        ),
        Step(
            id="dividends", name="⑥ 分红",
            method="实际除息事件导出分红率，非年化常数",
            why="除息时点决定深度实值看涨是否值得提前行权",
            alternatives=[
                ("年化常数", "除息日被抹平，提前行权判断失效"),
                ("忽略分红", "看涨期权理论价被高估"),
            ],
            where="data/market.py dividend_yield()",
            check=_check_dividends,
        ),
        Step(
            id="vol_proxy", name="⑦ 波动率代理",
            method="每标的用自己的波动率指数，不匹配即报错",
            why="纳指波动率系统性高于标普，套用会整体低估价格",
            alternatives=[
                ("共用 VIX", "非标普标的的期权价被低估"),
                ("直接用已实现波动率", "缺少风险溢价，价格整体偏低"),
                ("拟合波动率曲面", "无真实链校准时拟合出来的同样是编的"),
            ],
            where="data/market.py VOL_PROXY_BY_SYMBOL",
            check=_check_vol_proxy,
        ),
        Step(
            id="expiries", name="⑧ 期权到期日历",
            method="月度第三个周五 + 周度，按历史推出时点",
            why="周度期权是逐步推出的，凭空提前会高估可执行性",
            alternatives=[
                ("只生成月度", "30DTE 目标经常落空"),
                ("全区间都有周度", "早年可选合约比真实情况多"),
                ("全部日度到期", "合约数量爆炸且与市场不符"),
            ],
            where="data/calendar.py expiry_dates()",
            check=_check_expiries,
        ),
        Step(
            id="chain", name="⑨ 期权链",
            method="按商业数据源字段集定义；合成链按需生成",
            why="接口按真实数据源定型，接入付费数据时上层零改动",
            alternatives=[
                ("按合成链能力裁剪接口", "接入真实数据时接口得重做"),
                ("预生成全表", "十年千万行，存储与生成都不可接受"),
                ("缺字段填 0", "下游把 0 当真实值，流动性过滤误判"),
            ],
            where="data/chain.py · data/synthetic/generator.py",
            check=lambda m: _check_chain(m, chain),
        ),
    ]


def inspect_pipeline(market, chain=None) -> PipelineReport:
    """在真实载入的数据上把整条流程体检一遍。"""
    return PipelineReport([step.run(market) for step in build_steps(chain)])
