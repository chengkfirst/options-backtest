"""把**你自己的**期权数据接进来。

这个模块的存在理由只有一个：让"换掉合成链"是一件**机械的、可验证的**事，
而不是一次代码考古。

合成链是**测试夹具**，不是数据源。它的唯一用途是在没有真实数据时让整条管道
跑得起来。任何期权侧的结论（选什么 delta、止盈设多少、偏斜怎么用）在合成链上
都不成立——它的 IV 是一个常数，没有偏斜，没有期限结构。

## 换成你的数据，三步

1. **看格式**：``describe_schema()`` 打印需要哪些列、每列什么含义、可不可缺。
2. **查数据**：``check_file(路径)`` 在跑任何回测之前先告诉你数据哪里不对。
3. **接进去**：``run(..., chain="file:你的路径")``，或
   ``run(..., chain=FileChainSource(路径, market, config))``。

上层（选约、引擎、研究）**零改动**——它们只认 ``ChainSource`` 协议。

## 列名对不上怎么办

不要改你的文件。给 ``column_map`` 传一个 ``{你的列名: 标准列名}``：

    FileChainSource(路径, market, config,
                    column_map={"trade_date": "asof", "impl_vol": "iv"})

映射在读入时一次完成，之后全系统只见标准列名。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.data.chain import empty_chain, validate_chain
from backtest.errors import DataQualityError, StartupError
from backtest.types import CHAIN_COLUMNS, ContractId, Right

#: 每一列的含义与可缺性。``必需`` 的列缺了直接拒绝；其余缺了记为缺失值并说明影响。
SCHEMA: dict[str, dict[str, str]] = {
    "asof":             {"必需": "是", "含义": "报价日（该行是哪一天的快照）", "缺了会怎样": "—"},
    "symbol":           {"必需": "是", "含义": "标的代码，如 QQQ", "缺了会怎样": "—"},
    "expiry":           {"必需": "是", "含义": "到期日", "缺了会怎样": "—"},
    "strike":           {"必需": "是", "含义": "行权价", "缺了会怎样": "—"},
    "right":            {"必需": "是", "含义": "C 或 P（也接受 call/put、CALL/PUT）", "缺了会怎样": "—"},
    "bid":              {"必需": "是", "含义": "买一价", "缺了会怎样": "—"},
    "ask":              {"必需": "是", "含义": "卖一价", "缺了会怎样": "—"},
    "underlying_price": {"必需": "是", "含义": "该时点的标的价（**实际成交价，不是复权价**）",
                         "缺了会怎样": "—"},
    "last":             {"必需": "否", "含义": "最新成交价", "缺了会怎样": "不影响；成交价用 bid/ask 推"},
    "volume":           {"必需": "否", "含义": "当日成交量", "缺了会怎样": "成交量过滤会被跳过并标明"},
    "open_interest":    {"必需": "否", "含义": "未平仓合约数", "缺了会怎样": "未平仓过滤会被跳过并标明"},
    "iv":               {"必需": "否", "含义": "隐含波动率（小数，0.25 不是 25）",
                         "缺了会怎样": "**按 bid/ask 中价反解**，失败的行标记为缺失"},
    "delta":            {"必需": "否", "含义": "", "缺了会怎样": "**由 IV 用 Black-Scholes 推**，并标记来源"},
    "gamma":            {"必需": "否", "含义": "", "缺了会怎样": "同上"},
    "vega":             {"必需": "否", "含义": "", "缺了会怎样": "同上"},
    "theta":            {"必需": "否", "含义": "每**日历日**的时间价值衰减（负数）", "缺了会怎样": "同上"},
}

#: right 列的各种常见写法 → 标准值。
_RIGHT_ALIASES: dict[str, str] = {
    "c": "C", "call": "C", "calls": "C", "1": "C",
    "p": "P", "put": "P", "puts": "P", "-1": "P", "0": "P",
}


def describe_schema() -> pd.DataFrame:
    """打印需要什么列。接数据之前先看这张表。"""
    return pd.DataFrame(SCHEMA).T.rename_axis("列")


@dataclass(frozen=True, slots=True)
class FileReport:
    """一份数据体检报告。``ok`` 为假时**不要**拿它跑回测。"""

    ok: bool
    path: str
    rows: int
    days: int
    contracts: int
    date_range: tuple[date, date] | None
    errors: tuple[str, ...]        # 致命：不改就没法用
    warnings: tuple[str, ...]      # 能用，但结论要打折
    columns_present: tuple[str, ...]
    columns_missing: tuple[str, ...]

    def summary(self, printout: bool = True) -> str:
        head = "通过" if self.ok else "**不通过**"
        lines = [
            f"数据体检：{head}",
            f"  文件      {self.path}",
            f"  规模      {self.rows:,} 行 / {self.days:,} 个交易日 / {self.contracts:,} 张合约",
        ]
        if self.date_range:
            lines.append(f"  区间      {self.date_range[0]} → {self.date_range[1]}")
        if self.columns_missing:
            lines.append(f"  缺的列    {', '.join(self.columns_missing)}")
        if self.errors:
            lines += ["", "致命问题（不改没法用）："] + [f"  ✗ {e}" for e in self.errors]
        if self.warnings:
            lines += ["", "警告（能用，但要知道）："] + [f"  ! {w}" for w in self.warnings]
        if not self.errors and not self.warnings:
            lines += ["", "没发现问题。"]
        text = "\n".join(lines)
        if printout:
            print(text)
        return text


# ── 读入与规范化 ─────────────────────────────────────────────

def _read_any(path: Path) -> pd.DataFrame:
    """读 parquet / csv / 一个装着它们的目录。格式由扩展名决定，不猜。"""
    if path.is_dir():
        files = sorted([*path.glob("*.parquet"), *path.glob("*.csv")])
        if not files:
            raise StartupError(f"目录里没有 .parquet 或 .csv：{path}")
        return pd.concat([_read_any(f) for f in files], ignore_index=True)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix in (".csv", ".txt"):
        return pd.read_csv(path)
    raise StartupError(f"不认识的文件类型 {path.suffix}：只支持 .parquet 和 .csv（或装着它们的目录）")


def normalise(
    frame: pd.DataFrame,
    column_map: Mapping[str, str] | None = None,
    scale: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """把任意来源的表规范成标准列名与标准取值。

    只做**无损**的转换：改列名、按 ``scale`` 换单位、统一 right 的写法、
    把日期列转成日期。不做任何补值——补值属于"数据质量处理"，必须先被看见
    再决定，不能在读入时偷偷完成。

    ``scale`` 用来换单位，最常见的是 IV 写成了百分数：``{"iv": 0.01}``。
    **刻意要你显式写出来，而不是看到大数就自动除以 100**——自动换算会在
    字段本身标错（比如把别的列当成了 iv）时给出一个看起来正常的错结果。
    体检会提示你需不需要它。
    """
    out = frame.rename(columns=dict(column_map or {}))
    out.columns = [str(c).strip().lower() for c in out.columns]

    if "right" in out.columns:
        raw = out["right"].astype(str).str.strip().str.lower()
        mapped = raw.map(_RIGHT_ALIASES)
        unknown = sorted(set(raw[mapped.isna()]))
        if unknown:
            raise DataQualityError(
                f"right 列有认不出来的取值：{unknown[:8]}。"
                f"认识的写法：{sorted(set(_RIGHT_ALIASES))}"
            )
        out["right"] = mapped

    for col, factor in (scale or {}).items():
        if col in out.columns:
            out[col] = out[col].astype(float) * float(factor)

    for col in ("asof", "expiry"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.date
    return out


# ── 体检 ────────────────────────────────────────────────────

def check_frame(frame: pd.DataFrame, *, path: str = "(内存中的表)") -> FileReport:
    """在跑任何回测**之前**，先看看这份数据有没有问题。

    分两档：``errors`` 是不改就没法用的；``warnings`` 是能用但会影响结论可信度的。
    这个区分很重要——把所有问题一律当成致命，实际效果是逼人去关掉检查。
    """
    errors: list[str] = []
    warnings: list[str] = []

    required = [c for c, m in SCHEMA.items() if m["必需"] == "是"]
    present = [c for c in SCHEMA if c in frame.columns]
    missing = [c for c in SCHEMA if c not in frame.columns]
    for col in required:
        if col not in frame.columns:
            errors.append(f"缺必需列 {col}（{SCHEMA[col]['含义']}）")
    for col in missing:
        if col not in required:
            warnings.append(f"没有 {col} 列 → {SCHEMA[col]['缺了会怎样']}")

    if errors or frame.empty:
        return FileReport(
            ok=False, path=path, rows=len(frame), days=0, contracts=0, date_range=None,
            errors=tuple(errors or ("表是空的",)), warnings=tuple(warnings),
            columns_present=tuple(present), columns_missing=tuple(missing),
        )

    days = sorted({d for d in frame["asof"].dropna().unique()})
    contracts = frame.drop_duplicates(subset=["symbol", "expiry", "strike", "right"])

    if frame["asof"].isna().any():
        errors.append(f"{int(frame['asof'].isna().sum())} 行的 asof 解析不出日期")
    if frame["expiry"].isna().any():
        errors.append(f"{int(frame['expiry'].isna().sum())} 行的 expiry 解析不出日期")

    dup = frame.duplicated(subset=["asof", "symbol", "expiry", "strike", "right"])
    if dup.any():
        errors.append(f"{int(dup.sum())} 行主键重复（同一天同一张合约出现多次）")

    bid, ask = frame["bid"].astype(float), frame["ask"].astype(float)
    crossed = (ask < bid) & bid.notna() & ask.notna()
    if crossed.any():
        errors.append(f"{int(crossed.sum())} 行 ask < bid（交叉报价）")
    nonpos = (ask <= 0) | (bid < 0)
    if nonpos.any():
        warnings.append(f"{int(nonpos.sum())} 行报价非正，会被剔除")

    expired = pd.Series([e < a for a, e in zip(frame["asof"], frame["expiry"])])
    if expired.any():
        warnings.append(f"{int(expired.sum())} 行的到期日早于报价日，会被剔除")

    if "iv" in frame.columns:
        iv = frame["iv"].dropna().astype(float)
        if len(iv) and iv.max() > 5:
            warnings.append(f"iv 最大值 {iv.max():.1f}，看起来是百分数而不是小数（应当 0.25 而非 25）")
        if len(iv) and iv.nunique() <= 2:
            warnings.append(f"iv 只有 {iv.nunique()} 个不同取值——没有偏斜也没有期限结构，"
                            "期权侧的结论在这种数据上不成立")

    # 报价陈旧：同一张合约连续多日中价完全不变
    stale = _stale_share(frame)
    if stale > 0.20:
        warnings.append(f"{stale:.0%} 的报价与前一日完全相同（陈旧报价），"
                        "止盈止损的触发时点会偏晚")

    gaps = _missing_day_share(days)
    if gaps > 0.05:
        warnings.append(f"交易日缺口约 {gaps:.0%}——区间内有连续多日没有任何报价")

    return FileReport(
        ok=not errors, path=path, rows=len(frame), days=len(days), contracts=len(contracts),
        date_range=(days[0], days[-1]) if days else None,
        errors=tuple(errors), warnings=tuple(warnings),
        columns_present=tuple(present), columns_missing=tuple(missing),
    )


def check_file(path: str | Path, column_map: Mapping[str, str] | None = None,
               scale: Mapping[str, float] | None = None) -> FileReport:
    """读一个文件（或目录）并体检。这是接数据的第二步。"""
    path = Path(path)
    frame = normalise(_read_any(path), column_map, scale)
    return check_frame(frame, path=str(path))


def _stale_share(frame: pd.DataFrame) -> float:
    if not {"bid", "ask"} <= set(frame.columns):
        return 0.0
    mid = (frame["bid"].astype(float) + frame["ask"].astype(float)) / 2.0
    key = list(zip(frame["symbol"], frame["expiry"], frame["strike"], frame["right"]))
    tmp = pd.DataFrame({"k": key, "asof": frame["asof"], "mid": mid}).sort_values(["k", "asof"])
    same = tmp.groupby("k", sort=False)["mid"].diff().eq(0.0)
    return float(same.mean()) if len(same) else 0.0


def _missing_day_share(days: list) -> float:
    """区间内有多少比例的工作日完全没有数据。粗口径，不查交易日历。"""
    if len(days) < 2:
        return 0.0
    span = pd.bdate_range(days[0], days[-1])
    return 1.0 - len(days) / max(len(span), 1)


# ── 数据源实现 ───────────────────────────────────────────────

class FileChainSource:
    """从你的文件读期权链。实现 ``ChainSource`` 协议，与合成链可直接互换。

    缺的可选列会**补齐并标记来源**：

    - 没有 ``iv``：按中价反解，失败的行标为缺失（不是 0）
    - 没有 greeks：用 IV 按 Black-Scholes 推，``greeks_model_derived`` 置真

    这个标记很重要——供应商给的 delta 和我推出来的 delta 不是一回事，研究结论
    的可信度也不一样。所以标记一路带到交易明细里，而不是在这里抹平。
    """

    def __init__(
        self,
        path: str | Path,
        market,
        config: BacktestConfig | None = None,
        *,
        column_map: Mapping[str, str] | None = None,
        scale: Mapping[str, float] | None = None,
        strict: bool = True,
    ) -> None:
        self.path = Path(path)
        self.market = market
        self.config = config or market.config
        frame = normalise(_read_any(self.path), column_map, scale)
        report = check_frame(frame, path=str(self.path))
        if strict and not report.ok:
            raise DataQualityError(
                "数据没通过体检，拒绝用它跑回测：\n" + report.summary(printout=False)
            )
        self.report = report
        self._frame = self._prepare(frame)
        self._by_day: dict[date, pd.DataFrame] = {
            d: g.reset_index(drop=True) for d, g in self._frame.groupby("asof", sort=False)
        }

    # ---- 协议 ----
    def snapshot(self, day: date) -> pd.DataFrame:
        got = self._by_day.get(day)
        return empty_chain() if got is None else got

    def series(self, contract: ContractId, days: Iterable[date]) -> pd.DataFrame:
        wanted = set(days)
        f = self._frame
        hit = f.loc[
            f["asof"].isin(wanted)
            & (f["symbol"] == contract.symbol)
            & (f["expiry"] == contract.expiry)
            & (f["right"] == contract.right.value)
            & (f["strike"].astype(float) == float(contract.strike))
        ]
        return hit.reset_index(drop=True) if len(hit) else empty_chain()

    # ---- 补齐 ----
    def _prepare(self, frame: pd.DataFrame) -> pd.DataFrame:
        from backtest.pricing import greeks as bs_greeks, implied_vol

        out = frame.copy()
        for col in CHAIN_COLUMNS:
            if col not in out.columns:
                out[col] = np.nan
        out["source"] = out["source"].astype("object").fillna(f"file:{self.path.name}")
        out["quality_flags"] = out["quality_flags"].astype("object").fillna("")
        # 缺列时被建成了 float64 的 NaN 列，直接写 True 会触发 dtype 不兼容。
        # 先摆正类型，不要靠 pandas 的隐式转换——那条路在新版本里会直接报错。
        out["greeks_model_derived"] = out["greeks_model_derived"].astype("object")

        mid = (out["bid"].astype(float) + out["ask"].astype(float)) / 2.0
        tau = np.array([max((e - a).days, 0) / 365.0 for a, e in zip(out["asof"], out["expiry"])])
        spot = out["underlying_price"].astype(float).to_numpy()
        strike = out["strike"].astype(float).to_numpy()
        is_call = (out["right"] == Right.CALL.value).to_numpy()
        # 利率用配置里的回退常数。真实曲线接进来之后这里应当改成按日取值——
        # 现在是常数，短期限的期权上影响很小，但长期限的 theta 会有偏差。
        rate = self.config.fallback_risk_free_rate

        need_iv = out["iv"].isna()
        if need_iv.any():
            solved = []
            for i in np.flatnonzero(need_iv.to_numpy()):
                got = implied_vol(float(mid.iloc[i]), spot[i], strike[i], tau[i], rate,
                                  0.0, Right.CALL if is_call[i] else Right.PUT)
                solved.append(got.unwrap() if got.is_present else np.nan)
            out.loc[need_iv, "iv"] = solved
            out.loc[need_iv & out["iv"].isna(), "quality_flags"] += "|iv_unsolved"

        greek_cols = ["delta", "gamma", "vega", "theta"]
        need_greeks = out[greek_cols].isna().any(axis=1)
        if need_greeks.any():
            iv = out["iv"].astype(float).to_numpy()
            for i in np.flatnonzero(need_greeks.to_numpy()):
                if not np.isfinite(iv[i]) or tau[i] <= 0:
                    continue
                g = bs_greeks(spot[i], strike[i], tau[i], rate, iv[i],
                              0.0, Right.CALL if is_call[i] else Right.PUT)
                for col in greek_cols:
                    if pd.isna(out.iloc[i][col]):
                        out.iat[i, out.columns.get_loc(col)] = float(g[col])
            out.loc[need_greeks, "greeks_model_derived"] = True
        out["greeks_model_derived"] = out["greeks_model_derived"].fillna(False).astype(bool)

        out["quality_flags"] = out["quality_flags"].str.strip("|")
        frames = [validate_chain(g, asof=d) for d, g in out.groupby("asof", sort=False)]
        return pd.concat(frames, ignore_index=True) if frames else empty_chain()
