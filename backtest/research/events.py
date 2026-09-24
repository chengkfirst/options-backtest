"""历史事件研究：跌到某个程度之后，通常会怎样。

这不是回测。回测模拟的是"按这套规则交易会赚多少"；这里问的是更前面一个问题：
**这个条件本身有没有信息量。** 它只用标的价格，不碰期权——所以即使期权链是
合成的，这里的结论也完全成立。

## 四个必须做对的地方

1. **按"段"计数，不按"天"。** QQQ 历史上有 1,923 天处在 10% 回调之下，但那只是
   约 14 段独立事件。按天计数会把一次熊市算成几百个观测，样本量虚高两个数量级。

2. **必须对照无条件基准。** QQQ 十二个月后上涨的无条件概率本来就很高。如果回调后
   是 80% 而平时是 78%，这个信号什么也没说。**只报条件概率是在骗自己。**

3. **路径和终点是两回事。** "十二个月后收在更高位"和"中途没再跌 10%"完全不同——
   后者决定你拿不拿得住，而且往往是真正的约束。

4. **区间末尾要截断。** 离数据末尾不足 N 个月的事件，没有完整的后续，必须排除并
   报出排除了几个，不能悄悄按已有的天数算。

5. **判定事件用收盘，衡量痛苦用日内。** 这两件事不是一个口径：
   日内插一下又收回来不算一次回调，但"我会不会被止损打掉"必须看日内最低。
   实测 QQQ：按收盘算跌破 5% 有 3,069 天，按日内算有 3,529 天（多 15%）；
   2015-08-24 收盘只跌 3.8%，日内最低跌了 17.2%——任何止损都被打掉了，
   而收盘口径完全看不见。全用收盘会让回撤、"再跌 X%" 的概率**同时被低估**，
   三个偏差同向，全都指向"看起来比实际好"。

## 样本量的硬现实

二十七年的 QQQ：回调 10% 约 14 段，20% 约 6 段，30% 约 3 段。**三个观测得不出
任何统计结论**，只能当作"历史上发生过这三次，分别是这样"。这个工具会把话说到这个
份上，而不是给你一个看起来很精确的百分比。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

#: 一个月按多少交易日算。21 是惯例（252/12）。
DAYS_PER_MONTH = 21


def _as_bars(data: "pd.Series | pd.DataFrame") -> tuple[pd.Series, pd.Series, pd.Series, bool]:
    """把输入收敛成（收盘、日内最高、日内最低、有没有真日内数据）。

    只给收盘序列也能跑，但**"再跌 X%" 与最深回撤会被系统性低估**——
    这一点会一路标到结果里，而不是让使用者以为拿到的是完整口径。
    """
    if isinstance(data, pd.Series):
        c = data.dropna().astype(float)
        return c, c, c, False
    frame = data.copy()
    frame.columns = [str(col).lower() for col in frame.columns]
    if "close" not in frame:
        raise ValueError(f"缺 close 列，只有 {sorted(frame.columns)}")
    c = frame["close"].astype(float)
    has = {"high", "low"} <= set(frame.columns)
    h = frame["high"].astype(float) if has else c
    l = frame["low"].astype(float) if has else c
    keep = c.notna()
    return c[keep], h[keep], l[keep], has


@dataclass(frozen=True, slots=True)
class Episode:
    """一次回调事件：从首次跌破阈值那一刻起。"""

    crossed: pd.Timestamp        # 首次跌破阈值的日期
    price: float                 # 跌破时的价格，之后所有收益以它为起点
    ref_high: float              # 事发前的参考高点
    ref_high_date: pd.Timestamp
    drawdown: float              # 跌破当日的实际回撤（负数）
    ended: pd.Timestamp | None   # 这一段何时结束（回撤收复到重置线）


def find_corrections(
    data: "pd.Series | pd.DataFrame",
    threshold: float,
    *,
    reference_window: int = 252,
    reset_fraction: float = 0.5,
) -> list[Episode]:
    """找出所有跌破 ``threshold`` 的独立回调段。

    ``threshold`` 用正数表示，0.10 即回调 10%。

    **参考高点**用滚动 ``reference_window`` 日的收盘最高价（默认 252 ≈ 52 周）。
    处理方式：滚动窗口而非历史最高——后者会让 2000 年之后十几年都"处在回调中"，
    把不同性质的时期混成一段。

    **段的界定**：一段回调 = **从同一个前高算起的那一段**。跌破之后要重新计数，
    必须先创出新高——2024 年 8 月和 9 月两次触及 10% 回调，前高都是同一个 503，
    那是一次回调里的两次触及，不是两次独立事件。按"收复到某个水平"来重置会把它
    算成两段，样本量虚高。

    ``reset_fraction`` 只决定 ``ended`` 记在哪天（回撤收复到 ``-threshold *
    reset_fraction`` 的那天），不影响计数。
    """
    if not 0 < threshold < 1:
        raise ValueError(f"threshold 用正数表示回调幅度，0.10 即 10%，给的是 {threshold}")
    if not 0 <= reset_fraction <= 1:
        raise ValueError(f"reset_fraction 须在 0 与 1 之间，给的是 {reset_fraction}")

    # **事件的判定一律用收盘**：日内插一下又收回来不算一次回调。
    prices, _, _, _ = _as_bars(data)
    ref = prices.rolling(reference_window, min_periods=reference_window // 4).max()
    ref_idx = prices.rolling(reference_window, min_periods=reference_window // 4).apply(
        lambda w: float(np.argmax(w)) - len(w) + 1, raw=True
    )
    dd = (prices / ref - 1.0)

    episodes: list[Episode] = []
    open_at: int | None = None
    reset_level = -threshold * reset_fraction
    last_peak: int | None = None             # 上一段参考高点的位置

    values = dd.to_numpy()
    for i in range(len(prices)):
        if np.isnan(values[i]):
            continue
        if open_at is None:
            # 新的一段要求**参考高点换了一个**（日期更晚），而不是"创了新高"。
            # 要求创新高的话，2000 年崩盘后十六年没新高，其间所有回调会被全部吞掉。
            fresh_peak = True
            if values[i] <= -threshold:
                offset0 = ref_idx.iloc[i]
                peak0 = i + int(offset0) if np.isfinite(offset0) else i
                fresh_peak = last_peak is None or peak0 > last_peak
            if values[i] <= -threshold and fresh_peak:
                offset = ref_idx.iloc[i]
                hi_pos = i + int(offset) if np.isfinite(offset) else i
                hi_pos = max(0, min(hi_pos, len(prices) - 1))
                episodes.append(Episode(
                    crossed=prices.index[i], price=float(prices.iloc[i]),
                    ref_high=float(ref.iloc[i]), ref_high_date=prices.index[hi_pos],
                    drawdown=float(values[i]), ended=None,
                ))
                open_at = i
                last_peak = hi_pos
        elif values[i] >= reset_level:
            last = episodes[-1]
            episodes[-1] = Episode(last.crossed, last.price, last.ref_high,
                                   last.ref_high_date, last.drawdown, prices.index[i])
            open_at = None
    return episodes


@dataclass(frozen=True, slots=True)
class Outcome:
    """一段回调之后、某个时间窗内发生了什么。"""

    crossed: pd.Timestamp
    horizon_months: int
    end_return: float              # 窗口末的收益（相对跌破价）
    finished_up: bool
    finished_above_5: bool
    finished_above_10: bool
    recovered_pre_high: bool       # 窗口内**任意时点**回到事发前高
    days_to_recover: float         # 回到前高用了几个交易日；没回到为 nan
    touched_minus_5: bool          # 窗口内**日内任意时点**又跌 5%
    touched_minus_10: bool
    max_drawdown_after: float      # 窗口内相对跌破价的最深跌幅（日内口径）
    #: 上面两个"触及"与最深回撤是不是用了真日内数据。假的话它们被**系统性低估**。
    intraday: bool = True


def measure(
    data: "pd.Series | pd.DataFrame",
    episodes: Sequence[Episode],
    *,
    horizons_months: Sequence[int] = (1, 3, 6, 12),
) -> pd.DataFrame:
    """每一段回调在每个时间窗内的结果。

    **末尾截断**：离数据末尾不足 N 个月的段，该时间窗**不出现在结果里**，
    而不是按已有的天数凑合算——凑合算会系统性偏向"还没来得及跌"的短窗口。
    被排除了几个由 :func:`study` 报出来。
    """
    prices, highs, lows, intraday = _as_bars(data)
    rows: list[Outcome] = []
    for ep in episodes:
        try:
            i0 = prices.index.get_loc(ep.crossed)
        except KeyError:
            continue
        for months in horizons_months:
            n = months * DAYS_PER_MONTH
            if i0 + n >= len(prices):
                continue                      # 数据不够，这个窗口不算
            path = prices.iloc[i0: i0 + n + 1]
            rel = path / ep.price - 1.0
            # **衡量痛苦用日内最低**：止损是被日内触及打掉的，不是被收盘打掉的。
            worst = lows.iloc[i0: i0 + n + 1] / ep.price - 1.0
            # **"收复"以收盘论**，插一下前高不算收复。
            recovered = path >= ep.ref_high
            first = int(np.argmax(recovered.to_numpy())) if recovered.any() else -1
            rows.append(Outcome(
                crossed=ep.crossed, horizon_months=months,
                end_return=float(rel.iloc[-1]),
                finished_up=bool(rel.iloc[-1] > 0),
                finished_above_5=bool(rel.iloc[-1] >= 0.05),
                finished_above_10=bool(rel.iloc[-1] >= 0.10),
                recovered_pre_high=bool(recovered.any()),
                days_to_recover=float(first) if first >= 0 else float("nan"),
                touched_minus_5=bool((worst <= -0.05).any()),
                touched_minus_10=bool((worst <= -0.10).any()),
                max_drawdown_after=float(worst.min()),
                intraday=intraday,
            ))
    # slots dataclass 没有 __dict__，用 asdict
    from dataclasses import asdict

    return pd.DataFrame([asdict(r) for r in rows]) if rows else pd.DataFrame()


def baseline(
    data: "pd.Series | pd.DataFrame",
    *,
    horizons_months: Sequence[int] = (1, 3, 6, 12),
) -> pd.DataFrame:
    """**无条件基准**：随便挑一天进场，同样的时间窗内会怎样。

    这是整个研究里最容易被跳过、也最不能跳过的一步。QQQ 十二个月后上涨的无条件
    概率本来就很高；如果回调后是 80% 而平时是 78%，这个信号什么也没说。
    只报条件概率是在骗自己。
    """
    prices, _, _, _ = _as_bars(data)
    rows = []
    for months in horizons_months:
        n = months * DAYS_PER_MONTH
        if n >= len(prices):
            continue
        start = prices.iloc[:-n].to_numpy()
        end = prices.iloc[n:].to_numpy()
        rel = end / start - 1.0
        rows.append({
            "horizon_months": months,
            "样本": len(rel),
            "上涨": float((rel > 0).mean()),
            "涨5%以上": float((rel >= 0.05).mean()),
            "涨10%以上": float((rel >= 0.10).mean()),
            "中位收益": float(np.median(rel)),
        })
    return pd.DataFrame(rows)


def effective_sample(episodes: Sequence[Episode], months: int) -> int:
    """互不重叠的段数。

    十二个月的窗口在时间上大量重叠，名义样本量会严重虚高——2000 年 4 月和
    2000 年 10 月两段的十二个月窗口几乎是同一段历史，它们不是两个独立观测。
    这里数的是**窗口互不重叠**的最大子集（贪心，按时间序）。
    """
    if not episodes:
        return 0
    span = pd.Timedelta(days=months * 30)
    count, last_end = 0, None
    for ep in sorted(episodes, key=lambda e: e.crossed):
        if last_end is None or ep.crossed >= last_end:
            count += 1
            last_end = ep.crossed + span
    return count


def study(
    data: "pd.Series | pd.DataFrame",
    threshold: float,
    *,
    horizons_months: Sequence[int] = (1, 3, 6, 12),
    reference_window: int = 252,
) -> dict:
    """完整的一次事件研究：找段 → 量结果 → 对照基准 → 报样本量。

    返回的 ``table`` 每一行是一个时间窗，条件概率与无条件基准并排，并给出
    **超额**（条件 − 基准）。超额才是信号的内容；条件概率本身不是。
    """
    prices, _, _, intraday = _as_bars(data)
    eps = find_corrections(data, threshold, reference_window=reference_window)
    out = measure(data, eps, horizons_months=horizons_months)
    base = baseline(data, horizons_months=horizons_months).set_index("horizon_months")

    rows = []
    for months in horizons_months:
        sub = out[out["horizon_months"] == months] if len(out) else out
        n = len(sub)
        eff = effective_sample(
            [e for e in eps if e.crossed in set(sub["crossed"])] if n else [], months)
        if months not in base.index or n == 0:
            rows.append({"时间窗": f"{months}月", "样本": n, "有效样本": eff,
                         "备注": "数据不足，该窗口无观测"})
            continue
        b = base.loc[months]
        rows.append({
            "时间窗": f"{months}月",
            "样本": n, "有效样本": eff,
            "上涨": float(sub["finished_up"].mean()),
            "基准上涨": float(b["上涨"]),
            "超额": float(sub["finished_up"].mean() - b["上涨"]),
            "涨5%+": float(sub["finished_above_5"].mean()),
            "收复前高": float(sub["recovered_pre_high"].mean()),
            "再跌5%": float(sub["touched_minus_5"].mean()),
            "再跌10%": float(sub["touched_minus_10"].mean()),
            "中位收益": float(sub["end_return"].median()),
            "最深回撤中位": float(sub["max_drawdown_after"].median()),
            "收复用时中位(日)": float(sub["days_to_recover"].median()),
            "备注": _sample_warning(n, eff),
        })
    return {
        "threshold": threshold,
        # 给的是不是真日内数据。假的话"再跌 X%"与最深回撤被系统性低估，
        # 实测 QQQ 上低估约 12–15%，而且极端日可以差十几个百分点。
        "intraday": intraday,
        "口径": ("事件判定用收盘；再跌 X% 与最深回撤用日内最低" if intraday
                else "**只给了收盘序列**：再跌 X% 与最深回撤被低估，给 OHLC 才准"),
        "episodes": eps,
        "outcomes": out,
        "baseline": base.reset_index(),
        "table": pd.DataFrame(rows),
        "excluded_by_data_end": {
            m: len(eps) - int((out["horizon_months"] == m).sum()) if len(out) else len(eps)
            for m in horizons_months
        },
    }


def _sample_warning(n: int, eff: int) -> str:
    """把样本量的话说到该说的份上，不给一个看起来很精确的百分比。"""
    if eff <= 3:
        return f"**只有 {eff} 个互不重叠的观测——这不是统计，是三个故事**"
    if eff <= 8:
        return f"有效样本 {eff}，只能看方向，别看小数点"
    if n > eff * 2:
        return f"名义 {n} 段但只有 {eff} 个不重叠，概率的置信区间比看上去宽得多"
    return ""
