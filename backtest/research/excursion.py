"""MFE / MAE：止盈止损该设在哪。

**最大有利偏移（MFE）** 是一笔交易在持有期内浮盈最多到过哪，**最大不利偏移（MAE）**
是浮亏最深到过哪。两者的分布直接回答两个问题：

- 如果八成交易的浮盈从没超过 +60%，止盈设 +100% 就是在等一个很少来的事
- 如果最终盈利的交易里有一半曾经浮亏超过 -40%，止损设 -30% 会把它们全砍掉

这比扫参数直接得多：**扫参数是把各种阈值试一遍看哪个结果好，MFE/MAE 是看价格本来
到过哪**。前者容易过拟合到具体的历史路径，后者是分布事实。

数据只能在持仓过程中采，事后补不回来——所以事件驱动引擎每天都在记。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

#: 默认考察的阈值档位（相对入场价的比例）。
PROFIT_LEVELS = (0.25, 0.50, 0.75, 1.00, 1.50, 2.00)
LOSS_LEVELS = (0.20, 0.30, 0.40, 0.50, 0.60, 0.80)


@dataclass(frozen=True, slots=True)
class ExcursionStudy:
    frame: pd.DataFrame          # 每笔一行：mfe / mae / 实际盈亏 / 是否盈利
    n: int
    censoring: tuple[str, ...] = ()   # 分布被现有规则截断的警告

    def profit_target_table(self, levels: Sequence[float] = PROFIT_LEVELS) -> pd.DataFrame:
        """每个止盈档位有多少比例的交易**曾经触及**。

        「触及率」不等于「按这个止盈了结的收益」——触及之后价格可能继续涨。这张表
        回答的是"这个目标现实吗"，不是"这个目标赚多少"。后者要跑回测。
        """
        rows = []
        for lv in levels:
            hit = self.frame["mfe"] >= lv
            rows.append({
                "止盈档": f"+{lv:.0%}",
                "触及笔数": int(hit.sum()),
                "触及率": float(hit.mean()),
                "触及者最终平均盈亏": float(self.frame.loc[hit, "net_pnl"].mean()) if hit.any() else np.nan,
                "未触及者最终平均盈亏": float(self.frame.loc[~hit, "net_pnl"].mean()) if (~hit).any() else np.nan,
            })
        return pd.DataFrame(rows).set_index("止盈档")

    def stop_loss_table(self, levels: Sequence[float] = LOSS_LEVELS) -> pd.DataFrame:
        """每个止损档位会砍掉多少**最终本来会盈利**的交易。

        这是止损的真实代价：它砍掉亏损，也砍掉那些"先深跌再涨回来"的赢家。
        """
        winners = self.frame["net_pnl"] > 0
        rows = []
        for lv in levels:
            touched = self.frame["mae"] <= -lv
            killed = touched & winners
            rows.append({
                "止损档": f"-{lv:.0%}",
                "触及笔数": int(touched.sum()),
                "触及率": float(touched.mean()),
                "其中最终本会盈利": int(killed.sum()),
                "误杀的盈利金额": float(self.frame.loc[killed, "net_pnl"].sum()),
                "避开的亏损金额": float(self.frame.loc[touched & ~winners, "net_pnl"].sum()),
            })
        return pd.DataFrame(rows).set_index("止损档")

    def summary(self) -> dict[str, Any]:
        f = self.frame
        winners, losers = f[f["net_pnl"] > 0], f[f["net_pnl"] <= 0]
        return {
            "n": self.n,
            "mfe_median": float(f["mfe"].median()),
            "mfe_q75": float(f["mfe"].quantile(0.75)),
            "mfe_q90": float(f["mfe"].quantile(0.90)),
            "mae_median": float(f["mae"].median()),
            "mae_q25": float(f["mae"].quantile(0.25)),
            "winners_mae_median": float(winners["mae"].median()) if len(winners) else np.nan,
            "losers_mfe_median": float(losers["mfe"].median()) if len(losers) else np.nan,
        }

    def text(self) -> str:
        s = self.summary()
        warn = ["", "!! 分布被截断，下面的结论不能直接用来定阈值："] + \
               [f"   {c}" for c in self.censoring] if self.censoring else []
        return "\n".join([
            f"MFE / MAE 研究 · {s['n']} 笔",
            "─" * 56,
            f"  浮盈最多到过（MFE）  中位 {s['mfe_median']:+.0%}"
            f"  75% 分位 {s['mfe_q75']:+.0%}  90% 分位 {s['mfe_q90']:+.0%}",
            f"  浮亏最深到过（MAE）  中位 {s['mae_median']:+.0%}"
            f"  25% 分位 {s['mae_q25']:+.0%}",
            "",
            f"  最终盈利的交易，浮亏中位到过 {s['winners_mae_median']:+.0%}"
            f"   ← 止损设得比这紧，会砍掉一半赢家",
            f"  最终亏损的交易，浮盈中位到过 {s['losers_mfe_median']:+.0%}"
            f"   ← 止盈设得比这松，这些本可以保住的利润会还回去",
            *warn,
        ])


def study(trades: pd.DataFrame, exit_rule=None) -> ExcursionStudy:
    """从交易明细构造 MFE/MAE 研究。

    需要 ``mfe`` 与 ``mae`` 两列——只有事件驱动引擎会采集它们。成交表引擎各笔独立、
    不逐日推进，采不到，所以这里直接报错而不是给一份空结果。

    **传入 ``exit_rule`` 以检测截断。** 这是本模块最容易踩的坑：如果回测时止损设在
    -50%，浮亏根本到不了 -60%，MAE 分布在 -50% 处被硬生生截断。拿这份分布去定止损
    是循环论证——你只会看到"止损设在现在这个位置很合理"。

    要研究止损该设在哪，得先用**很宽或关闭**的止损跑一遍，让价格自己走完。止盈同理。
    """
    for col in ("mfe", "mae"):
        if col not in trades.columns:
            raise KeyError(
                f"交易明细里没有 {col} 列。MFE/MAE 只能在持仓过程中采集，"
                f"请用 engine=\"event_driven\" 重跑——成交表引擎采不到这个。"
            )
    if len(trades) and trades["mfe"].isna().all():
        # 列在但全是 NaN —— 成交表引擎产出的记录就是这样。说"没有可用交易"会误导，
        # 让人以为是策略没成交；真正的原因是这条引擎采不到逐日偏移。
        raise KeyError(
            f"这 {len(trades)} 笔交易的 mfe/mae 全是空值——它们由成交表引擎产出，"
            f"而成交表不逐日推进，采不到持仓期内的偏移。"
            f"请用 engine=\"event_driven\" 重跑。"
        )
    frame = trades.dropna(subset=["mfe", "mae", "net_pnl"]).copy()
    if frame.empty:
        raise ValueError("没有可用于 MFE/MAE 研究的交易")

    censoring: list[str] = []
    if exit_rule is not None:
        if getattr(exit_rule, "stop_loss", None) is not None:
            censoring.append(
                f"止损 -{exit_rule.stop_loss:.0%} 生效中：浮亏到此即被砍，"
                f"MAE 分布在这里被截断。要定止损请先用很宽或关闭的止损重跑。"
            )
        if getattr(exit_rule, "take_profit", None) is not None:
            censoring.append(
                f"止盈 +{exit_rule.take_profit:.0%} 生效中：浮盈到此即了结，"
                f"MFE 分布在这里被截断。要定止盈请先用很高或关闭的止盈重跑。"
            )
        if getattr(exit_rule, "hold_days", None) is not None:
            censoring.append(
                f"持有上限 {exit_rule.hold_days} 个交易日：更长的路径没被观察到。"
            )
    return ExcursionStudy(frame=frame, n=len(frame), censoring=tuple(censoring))
