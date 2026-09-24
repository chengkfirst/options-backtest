"""数据层门面：一次构造，此后全是内存查询。

聚合标的行情（两套价格）、交易日历、无风险利率序列、分红事件与波动率代理。
外部请求只在构造时发生，之后回测反复查询都不再触网。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtest.config import BacktestConfig, TRADING_DAYS_PER_YEAR
from backtest.data.cache import CacheKey, ParquetCache
from backtest.data.calendar import TradingCalendar, expiry_dates
from backtest.data.sources import fetch_close_series, fetch_dividends, fetch_ohlcv
from backtest.data.underlying import UnderlyingBars, build_bars
from backtest.errors import StartupError
from backtest.events import EventKind, EventLog
from backtest.maybe import Maybe

#: 每个标的自身的波动率指数。纳指波动率系统性高于标普，套用同一个代理会整体低估
#: QQQ 期权的价格。
VOL_PROXY_BY_SYMBOL = {"QQQ": "^VXN", "SPY": "^VIX", "IWM": "^RVX", "DIA": "^VXD"}

#: 三个月期国债收益率。Yahoo 以百分数给出（4.5 表示 4.5%）。
RATE_SYMBOL = "^IRX"

#: 波动率代理缺失时回退所用的已实现波动率窗口（交易日）。
FALLBACK_RV_WINDOW = 20


class MarketData:
    """回测所需的全部市场数据。"""

    def __init__(
        self,
        config: BacktestConfig,
        *,
        cache: ParquetCache | None = None,
        events: EventLog | None = None,
        refresh: bool = False,
        bars: UnderlyingBars | None = None,
        rates: pd.Series | None = None,
        vol_proxy: pd.Series | None = None,
        dividends: pd.Series | None = None,
    ) -> None:
        """``bars`` / ``rates`` / ``vol_proxy`` / ``dividends`` 显式给出时不触网。

        这条路径供离线使用与测试：测试必须能在没有网络的机器上稳定跑完，否则它就
        不是回归防线，而是又一个不确定来源。
        """
        self.config = config
        self.events = events or EventLog()
        self._cache = cache or ParquetCache()
        symbol = config.symbol

        expected = VOL_PROXY_BY_SYMBOL.get(symbol)
        if expected is not None and config.contract.vol_proxy != expected:
            raise StartupError(
                f"{symbol} 的波动率代理应为 {expected}，当前配置为 "
                f"{config.contract.vol_proxy}。不同标的的隐含波动率水平存在系统性差异，"
                f"套用别的指数会让合成链整体偏离。"
            )

        # 指标需要预热，向前多取一段
        fetch_start = config.start - timedelta(days=400)
        self._offline = bars is not None
        self._bars = bars if bars is not None else self._load_bars(symbol, fetch_start, config.end, refresh)
        self.calendar = TradingCalendar(self._bars.dates[0], self._bars.dates[-1])
        if self._offline:
            self._rates = rates if rates is not None else pd.Series(dtype=float)
            self._vol_proxy = vol_proxy if vol_proxy is not None else pd.Series(dtype=float)
            self._dividends = dividends if dividends is not None else pd.Series(dtype=float)
        else:
            self._rates = self._load_rate_series(fetch_start, config.end, refresh)
            self._vol_proxy = self._load_vol_proxy(
                config.contract.vol_proxy, fetch_start, config.end, refresh
            )
            self._dividends = self._load_dividends(symbol, refresh)
        self._note_truncation()
        self._rv = self._realized_vol()
        self._expiries = expiry_dates(
            self.calendar,
            include_monthly=config.synthetic.include_monthly,
            include_weekly=config.synthetic.include_weekly,
            weekly_start=config.synthetic.weekly_start,
        )

    def _note_truncation(self) -> None:
        """可用区间窄于请求区间时给出提示，绝不静默返回不完整的数据。"""
        first, last = self._bars.dates[0], self._bars.dates[-1]
        if first > self.config.start:
            self.events.record(
                EventKind.RANGE_TRUNCATED,
                first,
                f"请求起始 {self.config.start}，实际可用自 {first}",
            )
        if last < self.config.end:
            self.events.record(
                EventKind.RANGE_TRUNCATED,
                last,
                f"请求结束 {self.config.end}，实际可用至 {last}",
            )

    # ---- 载入 ----
    def _load_bars(self, symbol: str, start: date, end: date, refresh: bool) -> UnderlyingBars:
        key = CacheKey("ohlcv", symbol, {"start": start, "end": end, "source": "yfinance", "prices": "raw+adj"})
        raw = self._cache.get_or_load(key, lambda: fetch_ohlcv(symbol, start, end), refresh=refresh)
        return build_bars(symbol, raw)

    def _load_series(self, kind: str, symbol: str, start: date, end: date, refresh: bool) -> pd.Series:
        key = CacheKey(kind, symbol, {"start": start, "end": end, "source": "yfinance", "prices": "close"})
        frame = self._cache.get_or_load(
            key, lambda: fetch_close_series(symbol, start, end).to_frame("close"), refresh=refresh
        )
        series = frame["close"].astype(float)
        series.index = pd.to_datetime(series.index)
        return series

    def _load_rate_series(self, start: date, end: date, refresh: bool) -> pd.Series:
        try:
            return self._load_series("rate", RATE_SYMBOL, start, end, refresh) / 100.0
        except Exception as exc:  # noqa: BLE001
            self.events.record(
                EventKind.RATE_FALLBACK, start, f"利率序列不可得，全程回退常数：{exc}"
            )
            return pd.Series(dtype=float)

    def _load_vol_proxy(self, symbol: str, start: date, end: date, refresh: bool) -> pd.Series:
        try:
            return self._load_series("volproxy", symbol, start, end, refresh) / 100.0
        except Exception as exc:  # noqa: BLE001
            self.events.record(
                EventKind.VOL_PROXY_FALLBACK, start, f"波动率代理不可得，全程回退已实现波动率：{exc}"
            )
            return pd.Series(dtype=float)

    def _load_dividends(self, symbol: str, refresh: bool) -> pd.Series:
        key = CacheKey("dividends", symbol, {"source": "yfinance"})
        frame = self._cache.get_or_load(
            key, lambda: fetch_dividends(symbol).to_frame("amount"), refresh=refresh
        )
        if frame.empty:
            reason = frame.attrs.get("unavailable_reason")
            self.events.record(
                EventKind.DIVIDEND_UNAVAILABLE,
                self.config.start,
                f"{symbol} 分红数据获取失败（{reason}），按零分红处理"
                if reason
                else f"{symbol} 在该区间无分红记录，按零分红处理",
            )
            return pd.Series(dtype=float)
        series = frame["amount"].astype(float)
        series.index = pd.to_datetime(series.index)
        return series

    def _realized_vol(self) -> pd.Series:
        """已实现波动率，按交易日 / 252 年化——与定价的日历日口径分工明确。"""
        log_ret = np.log(self._bars.adjusted("close")).diff()
        return log_ret.rolling(FALLBACK_RV_WINDOW).std() * np.sqrt(TRADING_DAYS_PER_YEAR)

    # ---- 查询 ----
    @property
    def bars(self) -> UnderlyingBars:
        return self._bars

    @property
    def expiries(self) -> tuple[date, ...]:
        return self._expiries

    def sessions(self) -> tuple[date, ...]:
        """回测区间内的交易日（不含预热段）。"""
        return self.calendar.sessions_in(self.config.start, self.config.end)

    def spot(self, day: date) -> Maybe[float]:
        """标的的**实际成交价**收盘。期权相关计算一律用它，绝不用复权价。"""
        return self._bars.traded_close_on(day)

    def risk_free_rate(self, day: date) -> float:
        """该交易日的无风险利率；序列缺该日时取最近的前值，全无则回退常数。"""
        if self._rates.empty:
            return self.config.fallback_risk_free_rate
        ts = pd.Timestamp(day)
        prior = self._rates.loc[:ts]
        if prior.empty or pd.isna(prior.iloc[-1]):
            self.events.record(EventKind.RATE_FALLBACK, day, "该日及之前无利率数据，回退常数")
            return self.config.fallback_risk_free_rate
        return float(prior.iloc[-1])

    def dividends_between(self, start: date, end: date) -> float:
        """区间内的每股分红合计（左开右闭），由实际除息事件求和。"""
        if self._dividends.empty:
            return 0.0
        mask = (self._dividends.index > pd.Timestamp(start)) & (
            self._dividends.index <= pd.Timestamp(end)
        )
        return float(self._dividends.loc[mask].sum())

    def dividend_yield(self, day: date, expiry: date, spot: float) -> float:
        """定价所需的连续分红率，由合约存续期内的实际除息事件导出。

        用年化常数代替会抹掉除息日这个关键时点，而除息时点恰恰决定了实值看涨是否
        存在提前行权价值。
        """
        years = self.config.time.year_fraction(day, expiry)
        if years <= 0 or spot <= 0:
            return 0.0
        cash = self.dividends_between(day, expiry)
        if cash <= 0:
            return 0.0
        return float(cash / (spot * years))

    @property
    def vol_proxy(self) -> pd.Series:
        """波动率代理的完整序列（QQQ→VXN，SPY→VIX）。

        **这是真实市场数据**，不是合成的。所以基于它的 IV 水平判断（IV rank、
        IV−RV 价差）即使在合成期权链上也成立——合成的是期权价格，不是这个。
        """
        return self._vol_proxy

    def volatility(self, day: date) -> Maybe[float]:
        """该日的波动率代理；不可得时回退该标的自身的已实现波动率。"""
        ts = pd.Timestamp(day)
        if not self._vol_proxy.empty:
            prior = self._vol_proxy.loc[:ts]
            if not prior.empty and not pd.isna(prior.iloc[-1]):
                return Maybe.some(float(prior.iloc[-1]))
        prior_rv = self._rv.loc[:ts]
        if prior_rv.empty or pd.isna(prior_rv.iloc[-1]):
            return Maybe.missing(f"{day} 波动率代理与已实现波动率均不可得")
        self.events.record(
            EventKind.VOL_PROXY_FALLBACK, day, "波动率代理缺该日，回退已实现波动率"
        )
        return Maybe.some(float(prior_rv.iloc[-1]))
