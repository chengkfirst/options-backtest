"""The US trading calendar and the option expiry calendar.

A backtest advances in trading days: weekends and holidays are skipped and early
closes are handled. The expiry calendar follows the underlying's real cadence —
weekly expiries were rolled out gradually, and conjuring them up earlier than
that makes "pick 30 DTE" more available than it historically was.
"""

from __future__ import annotations

import functools
from datetime import date, timedelta

import pandas as pd
import pandas_market_calendars as mcal

from backtest.errors import StartupError

_EXCHANGE = "NYSE"


class TradingCalendar:
    """The trading sessions in a date range.

    Every session is fetched once at construction and all later queries are in
    memory — a backtest asks "what is the next trading day" over and over, and
    going to the calendar library each time is slow.
    """

    def __init__(self, start: date, end: date) -> None:
        if start > end:
            raise StartupError(f"calendar start {start} is after end {end}")
        schedule = mcal.get_calendar(_EXCHANGE).schedule(
            start_date=pd.Timestamp(start), end_date=pd.Timestamp(end)
        )
        self._sessions: tuple[date, ...] = tuple(d.date() for d in schedule.index)
        if not self._sessions:
            raise StartupError(f"no trading days between {start} and {end}")
        self._index = {d: i for i, d in enumerate(self._sessions)}
        early = mcal.get_calendar(_EXCHANGE).early_closes(schedule)
        self._early_closes = frozenset(d.date() for d in early.index)

    # ---- basic queries ----
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
        """An early close. Options stop trading early on these days, and quotes
        are not as executable as usual."""
        return day in self._early_closes

    def position(self, day: date) -> int:
        try:
            return self._index[day]
        except KeyError as exc:
            raise KeyError(f"{day} is not a trading day") from exc

    def shift(self, day: date, n: int) -> date | None:
        """Advance n trading days from ``day``; ``None`` past either end."""
        idx = self.position(day) + n
        if idx < 0 or idx >= len(self._sessions):
            return None
        return self._sessions[idx]

    def next_session(self, day: date) -> date | None:
        """The first session after ``day`` (``day`` need not be one itself)."""
        for i, session in enumerate(self._sessions):
            if session > day:
                return session
        return None

    def sessions_between(self, start: date, end: date) -> int:
        """Sessions between ``start`` and ``end``, excluding the first.

        This is the convention for days to expiry: 0 on the expiry day itself.
        """
        if end < start:
            return -self.sessions_between(end, start)
        lo = self.position(start) if start in self._index else self._bisect(start)
        hi = self.position(end) if end in self._index else self._bisect(end)
        return hi - lo

    def sessions_in(self, start: date, end: date) -> tuple[date, ...]:
        """The sessions in a closed interval."""
        return tuple(d for d in self._sessions if start <= d <= end)

    def _bisect(self, day: date) -> int:
        import bisect

        return bisect.bisect_left(self._sessions, day)


def third_friday(year: int, month: int) -> date:
    """The standard monthly expiry: the third Friday of the month."""
    d = date(year, month, 1)
    fridays = 0
    while True:
        if d.weekday() == 4:
            fridays += 1
            if fridays == 3:
                return d
        d += timedelta(days=1)


def _roll_to_session(day: date, calendar: TradingCalendar) -> date | None:
    """When an expiry lands on a holiday, move back to the nearest session
    (the historical rule)."""
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
    """The expiries that actually existed for this underlying in the range.

    No weekly expiries are produced before ``weekly_start`` — weekly options were
    introduced at a point in history, and conjuring them up earlier makes "pick
    30 DTE" easier in the early years than it really was.
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
