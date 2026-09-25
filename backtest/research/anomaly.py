"""异常扫描：把结果里真正反常的地方挑出来。

## 设计取舍：规则，不是 LLM

用固定阈值的规则扫，不让模型去"找找看有什么有趣的"。三个理由：

1. **规则可测试**——每条都能构造反例验证它真的会触发
2. **规则不会编**——模型面对一份正常的结果，仍然倾向于说点什么
3. 已经手工发现过的几个真问题（止盈档位几乎不触发、条件概率等于无条件基准、
   希腊字母排序全落在可行域边界）都是固定模式，规则抓得住

模型适合把扫出来的东西讲成人话，不适合负责"找"。

## 不触发就什么都不说

每条规则都有阈值。硬凑出来的"洞察"是噪音，而噪音会让真的警告也被跳过。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class Finding:
    """一条发现。``severity``：``high`` 值得停下来看，``note`` 知道就行。"""

    kind: str
    severity: str
    headline: str
    detail: str


def scan(
    trades: pd.DataFrame,
    *,
    equity: "pd.DataFrame | None" = None,
    metrics: "dict[str, Any] | None" = None,
    costs: "dict[str, Any] | None" = None,
) -> list[Finding]:
    """扫一遍，返回触发了的条目。什么都没触发就返回空列表。"""
    if trades is None or trades.empty:
        return []
    out: list[Finding] = []
    for rule in (_single_trade_dominates, _one_year_carries, _edge_decays,
                 _exit_never_fires, _costs_eat_edge, _positions_overlap,
                 _too_good_for_the_sample, _all_losers_same_reason):
        try:
            found = rule(trades, equity=equity, metrics=metrics, costs=costs)
        except Exception:  # noqa: BLE001
            continue                      # 某条规则算不了，不该拖垮整个扫描
        if found:
            out.append(found)
    return sorted(out, key=lambda f: 0 if f.severity == "high" else 1)


# ── 规则 ────────────────────────────────────────────────────

def _single_trade_dominates(t, **_) -> "Finding | None":
    """一笔占了总盈亏的大头——那不是策略，是一次运气。"""
    total = t["net_pnl"].sum()
    if total <= 0 or len(t) < 10:
        return None
    top = t["net_pnl"].max()
    share = top / total
    if share < 0.35:
        return None
    row = t.loc[t["net_pnl"].idxmax()]
    return Finding(
        "single_trade", "high",
        f"单笔贡献了总盈亏的 {share:.0%}",
        f"{row['entry_date']} 那笔赚了 {top:,.0f}，而全部 {len(t)} 笔合计 {total:,.0f}。"
        "去掉它之后这个策略还剩什么，值得单独看一眼。",
    )


def _one_year_carries(t, **_) -> "Finding | None":
    """收益全来自某一年——换个环境可能就没了。"""
    if len(t) < 30:
        return None
    by_year = t.groupby(pd.to_datetime(t["exit_date"]).dt.year)["net_pnl"].sum()
    if len(by_year) < 3:
        return None
    total = by_year.sum()
    # 总盈亏接近零时，"某年占 101%" 这种话没有意义——分母太小，比例失去含义。
    # 要求总盈亏至少达到各年绝对值之和的三成，比较才站得住。
    scale = by_year.abs().sum()
    if total <= 0 or scale <= 0 or total < scale * 0.30:
        return None
    best_year = by_year.idxmax()
    share = by_year.max() / total
    if share < 0.70:
        return None
    others = by_year.drop(best_year)
    headline = (f"{best_year} 年贡献了全部盈亏，其余年份净亏损"
                if share > 1.0 else f"{best_year} 年贡献了 {share:.0%} 的盈亏")
    return Finding(
        "one_year", "high", headline,
        f"其余 {len(others)} 年合计 {others.sum():,.0f}，其中 "
        f"{int((others < 0).sum())} 年是亏的。这个策略可能只在某一种环境下成立。",
    )


def _edge_decays(t, **_) -> "Finding | None":
    """前后半段胜率差太多——edge 可能在衰减，或者前半段是过拟合。"""
    if len(t) < 60:
        return None
    half = len(t) // 2
    a = float((t["net_pnl"].iloc[:half] > 0).mean())
    b = float((t["net_pnl"].iloc[half:] > 0).mean())
    if abs(a - b) < 0.20:
        return None
    worse = b < a
    return Finding(
        "decay", "high" if worse else "note",
        f"前半段胜率 {a:.0%}，后半段 {b:.0%}",
        "差了 20 个百分点以上。" + (
            "后半段变差，可能是 edge 在衰减，也可能前半段碰上了特别有利的环境。"
            if worse else "后半段变好，先确认不是样本太少造成的。"),
    )


def _exit_never_fires(t, **_) -> "Finding | None":
    """某个了结条件几乎不触发——那个参数是装饰品。"""
    if len(t) < 30 or "exit_reason" not in t:
        return None
    share = t["exit_reason"].value_counts(normalize=True)
    dist = {k: round(float(v), 2) for k, v in share.items()}
    for reason in ("take_profit", "stop_loss"):
        if reason not in share.index:
            return Finding(
                "dead_exit", "high",
                f"{reason} 一次都没触发",
                f"这个档位完全没起作用，旁边的设置在替它做决定。当前分布：{dist}",
            )
        if share[reason] < 0.05:
            n = int((t["exit_reason"] == reason).sum())
            return Finding(
                "dead_exit", "high",
                f"{reason} 只触发了 {n} 次（{share[reason]:.0%}）",
                f"这个档位定得太远，实际上不起作用。当前分布：{dist}",
            )
    return None


def _costs_eat_edge(t, *, costs=None, **_) -> "Finding | None":
    """成本占毛盈亏比例过高——边际 edge 撑不过真实执行。"""
    if not costs:
        return None
    pct = costs.get("fees_pct_of_gross")
    if pct is None or not np.isfinite(pct) or pct < 0.30:
        return None
    return Finding(
        "costs", "high",
        f"成本吃掉了毛盈亏的 {pct:.0%}",
        f"佣金与滑点合计 {costs.get('total', 0):,.0f}。实盘成本通常高于回测，"
        "这个比例下 edge 很可能撑不过真实执行。",
    )


def _positions_overlap(t, **_) -> "Finding | None":
    """持仓高度重叠——名义笔数虚高，它们不是独立观测。"""
    if len(t) < 20:
        return None
    spans = pd.DataFrame({
        "a": pd.to_datetime(t["entry_date"]),
        "b": pd.to_datetime(t["exit_date"]),
    }).sort_values("a")
    count, last_end = 0, None
    for _, r in spans.iterrows():
        if last_end is None or r["a"] >= last_end:
            count += 1
            last_end = r["b"]
    if count >= len(t) * 0.5:
        return None
    return Finding(
        "overlap", "note",
        f"{len(t)} 笔里只有 {count} 笔互不重叠",
        "同时持有的仓位会一起涨跌，它们不是独立观测。按名义笔数算出来的胜率"
        "与置信区间都偏乐观。",
    )


def _too_good_for_the_sample(t, *, metrics=None, **_) -> "Finding | None":
    """夏普很高但样本很少——通常是运气，不是 edge。"""
    if not metrics:
        return None
    sharpe = metrics.get("夏普")
    if sharpe is None or not np.isfinite(sharpe) or sharpe < 2.0 or len(t) > 100:
        return None
    return Finding(
        "too_good", "high",
        f"夏普 {sharpe:.1f}，但只有 {len(t)} 笔",
        "这个样本量下，这么高的夏普更可能是运气。先加长区间或换标的，看它还在不在。",
    )


def _all_losers_same_reason(t, **_) -> "Finding | None":
    """亏损几乎全来自同一个了结原因——那一条在主导结果。"""
    if len(t) < 30 or "exit_reason" not in t:
        return None
    losers = t[t["net_pnl"] <= 0]
    if len(losers) < 10:
        return None
    share = losers["exit_reason"].value_counts(normalize=True)
    top, pct = share.index[0], float(share.iloc[0])
    if pct < 0.85:
        return None
    return Finding(
        "loss_concentration", "note",
        f"{pct:.0%} 的亏损笔都以 {top} 了结",
        f"{len(losers)} 笔亏损里有 {int(pct * len(losers))} 笔是同一个原因。"
        "调这一档对结果的影响会远大于调别的。",
    )
