"""选合约取舍面：虚值 × 期限的四张表。

这块的风险是**给出一个看起来合理但不对应任何真实合约的数字**——插值出来的格子、
用错了的中价、或者把看跌的虚值方向弄反。所以测试重点是：格子里的数必须来自链上
真实存在的一行，且方向正确。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.research.tradeoff import _spread_out, tradeoff_grid

SPOT = 400.0


def chain(asof=date(2024, 3, 1), dtes=(14, 21, 30, 45, 60, 90), right="C") -> pd.DataFrame:
    rows = []
    for dte in dtes:
        for m in (0.0, 0.02, 0.04, 0.06, 0.10):
            strike = SPOT * (1 + m) if right == "C" else SPOT * (1 - m)
            t = dte / 365
            mid = max(SPOT * 0.22 * np.sqrt(t) * np.exp(-((m / (0.22 * np.sqrt(t))) ** 2) / 2), 0.4)
            rows.append({
                "asof": asof, "symbol": "QQQ",
                "expiry": asof + timedelta(days=dte), "strike": float(strike),
                "right": right, "bid": mid * 0.98, "ask": mid * 1.02,
                "underlying_price": SPOT, "iv": 0.22,
                "delta": 0.5 * np.exp(-m * 8), "gamma": 0.02, "vega": 20.0,
                "theta": -mid / dte * 1.5, "dte": dte,
            })
    return pd.DataFrame(rows)


class TestGrid:
    def test_all_four_quantities(self):
        g = tradeoff_grid(chain(), spot=SPOT)
        assert set(g) == {"theta_pct_day", "delta_now", "delta_if_move", "breakeven_move"}

    def test_cells_come_from_real_contracts(self):
        """不插值。插出来的数看着像报价，但不对应任何你能买到的东西。"""
        # 链上只有 14 和 90 天；要 30 天时容差不够就该留空
        g = tradeoff_grid(chain(dtes=(14, 90)), spot=SPOT, tenors=(30,))
        assert g["theta_pct_day"].isna().all().all()

    def test_theta_worse_for_short_and_far_otm(self):
        """结构性事实：短期 + 虚值，每天烧掉的比例最高。"""
        t = tradeoff_grid(chain(), spot=SPOT)["theta_pct_day"]
        assert t.loc["10%", "14天"] > t.loc["0%", "14天"]
        assert t.loc["10%", "14天"] > t.loc["10%", "90天"]

    def test_breakeven_grows_with_moneyness(self):
        b = tradeoff_grid(chain(), spot=SPOT)["breakeven_move"]
        col = b["30天"].dropna()
        assert col.is_monotonic_increasing

    def test_breakeven_is_always_the_move_toward_the_thesis(self):
        """口径：正数 = 朝论点方向动多少，看涨看跌同号。

        与链上 moneyness 的口径一致。不统一的话，读表时得在脑子里换算方向，
        而换算方向是最容易出错的地方。
        """
        calls = tradeoff_grid(chain(right="C"), spot=SPOT, right="C")["breakeven_move"]
        puts = tradeoff_grid(chain(right="P"), spot=SPOT, right="P")["breakeven_move"]
        assert (calls.stack() > 0).all()
        assert (puts.stack() > 0).all()

    def test_delta_if_move_uses_a_model_and_is_labelled(self):
        """链上没有"如果涨 3% 会怎样"，必须重新定价——文档里说明了偏乐观。"""
        g = tradeoff_grid(chain(), spot=SPOT, move=0.03)
        assert g["delta_if_move"].notna().any().any()
        # 平值涨 3% 之后 delta 应当明显上升
        assert g["delta_if_move"].loc["0%", "30天"] > g["delta_now"].loc["0%", "30天"]

    def test_deep_otm_short_dated_exposure_collapses(self):
        """短期深虚值：论点兑现后暴露反而收缩。"""
        g = tradeoff_grid(chain(), spot=SPOT, move=0.03)
        short_deep = g["delta_if_move"].loc["10%", "14天"]
        long_deep = g["delta_if_move"].loc["10%", "90天"]
        assert short_deep < long_deep

    def test_empty_chain_returns_empty_not_zeros(self):
        g = tradeoff_grid(pd.DataFrame(), spot=SPOT)
        assert all(v.empty for v in g.values())

    def test_wrong_right_returns_empty(self):
        g = tradeoff_grid(chain(right="C"), spot=SPOT, right="P")
        assert all(v.empty for v in g.values())


class TestTenorPicking:
    def test_picks_from_what_exists(self):
        # 档位不多于要显示的数量时全留——挑选会丢掉"没人最接近它"的那些
        assert _spread_out([9, 16, 30, 58], (14, 21, 30, 45, 60, 90)) == [9, 16, 30, 58]
        # 档位很多时才挑
        many = list(range(7, 120, 7))
        assert len(_spread_out(many, (14, 21, 30, 45, 60, 90))) == 6

    def test_no_duplicates(self):
        picked = _spread_out([30], (14, 21, 30, 45, 60, 90))
        assert picked == [30]

    def test_empty(self):
        assert _spread_out([], (14, 30)) == []
