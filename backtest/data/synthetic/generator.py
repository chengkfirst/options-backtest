"""合成期权链。

**用途被严格限定**：引擎正确性验证、测试夹具、以及标的信号层研究的占位。
MUST NOT 用于期权侧参数（选约、止盈、止损）的调优——合成链没有波动率偏斜、
没有真实的隐含波动率动态、没有随流动性变化的价差，在它上面调出来的是"该合成模型下
的最优解"，与真实市场没有可靠关系。这条由 :mod:`backtest.engine` 的守卫强制执行。

刻意保持粗糙：全部行权价共用一个波动率。一个粗糙且标注清晰的模型，比一个看起来精细、
实则仍然错误的模型更安全——后者会诱使人相信回测结论。

**按需生成**：不预先算出十年全表（那是千万行量级），而是查哪天算哪天。真实数据源
按日查询，合成链也按日生成，两者的访问模式因此一致。
"""

from __future__ import annotations

import math
from datetime import date
from functools import lru_cache
from typing import Iterable

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig
from backtest.data.chain import UNAVAILABLE, empty_chain, validate_chain
from backtest.data.market import MarketData
from backtest.data.synthetic.pricing import bs_price, greeks, has_early_exercise_value
from backtest.types import CHAIN_COLUMNS, ContractId, QuoteSource, Right


class SyntheticChainSource:
    """由标的收盘价、波动率代理与 Black-Scholes 生成的期权链。"""

    def __init__(self, market: MarketData, config: BacktestConfig | None = None) -> None:
        self.market = market
        self.config = config or market.config
        self._cache: dict[date, pd.DataFrame] = {}

    # ---- 接口 ----
    def snapshot(self, day: date) -> pd.DataFrame:
        if day not in self._cache:
            self._cache[day] = self._build(day)
        return self._cache[day]

    def series(self, contract: ContractId, days: Iterable[date]) -> pd.DataFrame:
        """某张合约的逐日报价。缺标的收盘价或波动率的日期直接不出现。"""
        rows = []
        for day in days:
            if day > contract.expiry:
                break
            row = self._quote(day, contract)
            if row is not None:
                rows.append(row)
        if not rows:
            return empty_chain()
        return pd.DataFrame(rows, columns=list(CHAIN_COLUMNS))

    # ---- 生成 ----
    def _early_exercise_flags(self, strikes, theo, s: float, day: date, expiry: date, right: Right):
        """深度实值看涨在除息前可能有提前行权价值，欧式价在那里是错的。

        标在 ``quality_flags`` 上而不是让上层去算：上层消费链上的字段，不关心
        这些数字怎么来的。
        """
        if self.config.contract.exercise.value != "american":
            return [""] * len(strikes)
        cash = self.market.dividends_between(day, expiry)
        if cash <= 0:
            return [""] * len(strikes)
        intrinsic = (
            np.maximum(s - strikes, 0.0) if right is Right.CALL else np.maximum(strikes - s, 0.0)
        )
        return [
            "early_exercise_value"
            if has_early_exercise_value(
                right=right, option_price=float(p), intrinsic=float(i), dividend_in_window=cash
            )
            else ""
            for p, i in zip(theo, intrinsic)
        ]

    def _inputs(self, day: date, expiry: date):
        spot = self.market.spot(day)
        vol = self.market.volatility(day)
        if spot.is_missing or vol.is_missing:
            return None
        s = spot.unwrap()
        return (
            s,
            vol.unwrap(),
            self.market.risk_free_rate(day),
            self.market.dividend_yield(day, expiry, s),
            self.config.time.year_fraction(day, expiry),
        )

    def _strike_grid(self, spot: float) -> np.ndarray:
        step = self.config.contract.strike_step
        width = self.config.synthetic.strike_range
        lo = math.floor(spot * (1 - width) / step) * step
        hi = math.ceil(spot * (1 + width) / step) * step
        return np.round(np.arange(lo, hi + step / 2, step), 3)

    def _quotes(self, strikes, s, k_vol, t, r, q, right: Right):
        theo = bs_price(s, strikes, t, r, k_vol, q, right)
        theo = np.maximum(np.asarray(theo, dtype=float), 0.0)
        g = greeks(s, strikes, t, r, k_vol, q, right)
        half = np.maximum(
            theo * self.config.synthetic.spread_pct, self.config.synthetic.spread_floor
        ) / 2.0
        bid = np.maximum(theo - half, 0.0)
        ask = theo + half
        return theo, bid, ask, g

    def _build(self, day: date) -> pd.DataFrame:
        cfg = self.config
        expiries = [
            e
            for e in self.market.expiries
            if day < e <= day + pd.Timedelta(days=cfg.synthetic.max_dte).to_pytimedelta()
        ]
        if not expiries:
            return empty_chain()

        spot = self.market.spot(day)
        if spot.is_missing:
            return empty_chain()
        strikes = self._strike_grid(spot.unwrap())

        blocks = []
        for expiry in expiries:
            prepared = self._inputs(day, expiry)
            if prepared is None:
                continue
            s, vol, r, q, t = prepared
            for right in (Right.CALL, Right.PUT):
                theo, bid, ask, g = self._quotes(strikes, s, vol, t, r, q, right)
                blocks.append(
                    pd.DataFrame(
                        {
                            "asof": day,
                            "symbol": cfg.symbol,
                            "expiry": expiry,
                            "strike": strikes,
                            "right": right.value,
                            "bid": np.round(bid, 4),
                            "ask": np.round(ask, 4),
                            "last": np.round(theo, 4),
                            "volume": UNAVAILABLE,
                            "open_interest": UNAVAILABLE,
                            "iv": vol,
                            "delta": g["delta"],
                            "gamma": g["gamma"],
                            "vega": g["vega"],
                            "theta": g["theta"],
                            "underlying_price": s,
                            "source": QuoteSource.SYNTHETIC.value,
                            "quality_flags": self._early_exercise_flags(
                                strikes, theo, s, day, expiry, right
                            ),
                            # 这些希腊字母由生成报价的同一个模型给出，是模型自洽产物
                            # 而非市场观测。按 delta 选约在合成链上因此是循环的。
                            "greeks_model_derived": True,
                        }
                    )
                )
        if not blocks:
            return empty_chain()
        frame = pd.concat(blocks, ignore_index=True)
        return validate_chain(frame, asof=day)

    def _quote(self, day: date, contract: ContractId) -> dict | None:
        prepared = self._inputs(day, contract.expiry)
        if prepared is None:
            return None
        s, vol, r, q, t = prepared
        theo, bid, ask, g = self._quotes(
            np.array([contract.strike]), s, vol, t, r, q, contract.right
        )
        return {
            "asof": day,
            "symbol": contract.symbol,
            "expiry": contract.expiry,
            "strike": contract.strike,
            "right": contract.right.value,
            "bid": round(float(bid[0]), 4),
            "ask": round(float(ask[0]), 4),
            "last": round(float(theo[0]), 4),
            "volume": UNAVAILABLE,
            "open_interest": UNAVAILABLE,
            "iv": vol,
            "delta": float(g["delta"][0]),
            "gamma": float(g["gamma"][0]),
            "vega": float(g["vega"][0]),
            "theta": float(g["theta"][0]),
            "underlying_price": s,
            "source": QuoteSource.SYNTHETIC.value,
            "quality_flags": self._early_exercise_flags(
                np.array([contract.strike]), theo, s, day, contract.expiry, contract.right
            )[0],
            "greeks_model_derived": True,
        }
