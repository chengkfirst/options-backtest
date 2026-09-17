"""美股交易日历与期权到期日历。

回测的时间推进以交易日为单位：跳过周末与休市日，并正确处理半日市。到期日历按标的
真实的到期节奏生成——周度到期是逐步推出的，在推出之前不应凭空出现，否则"选 30DTE"
的可得性会比历史上真实的情况更好。
"""

from __future__ import annotations

import functools
from datetime import date, timedelta

import pandas as pd
import pandas_market_calendars as mcal

from backtest.errors import StartupError

_EXCHANGE = "NYSE"


class TradingCalendar:
    """某个日期区间内的交易日序列。

    构造时一次性取出全部交易日，之后的查询都是内存操作——回测会反复问
    "下一个交易日是哪天"，每次去查日历库会很慢。
    """

    def __init__(self, start: date, end: date) -> None:
        if start > end:
            raise StartupError(f"日历区间起始日 {start} 晚于结束日 {end}")
        schedule = mcal.get_calendar(_EXCHANGE).schedule(
            start_date=pd.Timestamp(start), end_date=pd.Timestamp(end)
        )
        self._sessions: tuple[date, ...] = tuple(d.date() for d in schedule.index)
        if not self._sessions:
            raise StartupError(f"区间 {start} 至 {end} 内没有交易日")
        self._index = {d: i for i, d in enumerate(self._sessions)}
        early = mcal.get_calendar(_EXCHANGE).early_closes(schedule)
        self._early_closes = frozenset(d.date() for d in early.index)

    # ---- 基本查询 ----
    @property
    def sessions(self) -> tuple[date, ...]:
        return self._sessions

    @property
    def first(self) -> date:
        return self._sessions[0]

    @property
    def last(self) -> date:
        return self._sessions[-1]

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, day: date) -> bool:
        return day in self._index

    def is_early_close(self, day: date) -> bool:
        """半日市。期权在这些日子提前收盘，报价的可成交性与平日不同。"""
        return day in self._early_closes

    def position(self, day: date) -> int:
        try:
            return self._index[day]
        except KeyError as exc:
            raise KeyError(f"{day} 不是交易日") from exc

    def shift(self, day: date, n: int) -> date | None:
        """从 ``day`` 起推进 n 个交易日；越界返回 ``None``。"""
        idx = self.position(day) + n
        if idx < 0 or idx >= len(self._sessions):
            return None
        return self._sessions[idx]

    def next_session(self, day: date) -> date | None:
        """``day`` 之后的第一个交易日（``day`` 本身不必是交易日）。"""
        for i, session in enumerate(self._sessions):
            if session > day:
                return session
        return None

    def sessions_between(self, start: date, end: date) -> int:
        """``start`` 到 ``end`` 之间的交易日数，左开右闭。

        这是"剩余到期交易日数"的口径：到期当日为 0。
        """
        if end < start:
            return -self.sessions_between(end, start)
        lo = self.position(start) if start in self._index else self._bisect(start)
        hi = self.position(end) if end in self._index else self._bisect(end)
        return hi - lo

    def sessions_in(self, start: date, end: date) -> tuple[date, ...]:
        """闭区间内的交易日。"""
        return tuple(d for d in self._sessions if start <= d <= end)

    def _bisect(self, day: date) -> int:
        import bisect

        return bisect.bisect_left(self._sessions, day)


def third_friday(year: int, month: int) -> date:
    """标准月度期权到期日：当月第三个星期五。"""
    d = date(year, month, 1)
    fridays = 0
    while True:
        if d.weekday() == 4:
            fridays += 1
            if fridays == 3:
                return d
        d += timedelta(days=1)


def _roll_to_session(day: date, calendar: TradingCalendar) -> date | None:
    """到期日落在休市日时向前调整到最近的交易日（历史上的规则）。"""
    for _ in range(7):
        if day in calendar:
            return day
        day -= timedelta(days=1)
    return None


@functools.lru_cache(maxsize=32)
def _weekly_fridays(start: date, end: date) -> tuple[date, ...]:
    days = []
    d = start
    while d.weekday() != 4:
        d += timedelta(days=1)
    while d <= end:
        days.append(d)
        d += timedelta(days=7)
    return tuple(days)


def expiry_dates(
    calendar: TradingCalendar,
    *,
    include_monthly: bool = True,
    include_weekly: bool = True,
    weekly_start: date | None = None,
) -> tuple[date, ...]:
    """标的在该区间内实际存在的到期日集合。

    ``weekly_start`` 之前不产生周度到期——周度期权是在历史上某个时点才推出的，
    凭空提前会让早年的"选 30DTE"比真实情况更容易得手。
    """
    found: set[date] = set()
    if include_monthly:
        year, month = calendar.first.year, calendar.first.month
        while date(year, month, 1) <= calendar.last:
            adjusted = _roll_to_session(third_friday(year, month), calendar)
            if adjusted is not None and calendar.first <= adjusted <= calendar.last:
                found.add(adjusted)
            month += 1
            if month == 13:
                year, month = year + 1, 1
    if include_weekly:
        begin = max(calendar.first, weekly_start) if weekly_start else calendar.first
        if begin <= calendar.last:
            for friday in _weekly_fridays(begin, calendar.last):
                adjusted = _roll_to_session(friday, calendar)
                if adjusted is not None and calendar.first <= adjusted <= calendar.last:
                    found.add(adjusted)
    return tuple(sorted(found))
