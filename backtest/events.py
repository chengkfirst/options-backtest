"""运行事件流。

市场的正常状态——报价缺失、选约失败、合约不可成交、了结日顺延——不是异常。
把它们当异常会让回测跑不完，静默吞掉会让统计失真。正确做法是记入一份可查询的
事件流，既不中断推进，也不丢失信息。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from enum import Enum

import pandas as pd


class EventKind(str, Enum):
    QUOTE_MISSING = "quote_missing"
    SELECTION_FAILED = "selection_failed"
    NOT_TRADABLE = "not_tradable"
    EXIT_DEFERRED = "exit_deferred"
    PASSIVE_EXPIRY = "passive_expiry"
    CANDIDATE_DROPPED = "candidate_dropped"
    VOL_PROXY_FALLBACK = "vol_proxy_fallback"
    RATE_FALLBACK = "rate_fallback"
    DIVIDEND_UNAVAILABLE = "dividend_unavailable"
    EARLY_EXERCISE_RISK = "early_exercise_risk"
    RANGE_TRUNCATED = "range_truncated"


@dataclass(frozen=True, slots=True)
class RunEvent:
    kind: EventKind
    when: date
    detail: str
    subject: str | None = None


class EventLog:
    """只增不改的事件集合。"""

    def __init__(self) -> None:
        self._events: list[RunEvent] = []

    def record(
        self, kind: EventKind, when: date, detail: str, subject: str | None = None
    ) -> None:
        self._events.append(RunEvent(kind, when, detail, subject))

    def __len__(self) -> int:
        return len(self._events)

    @property
    def events(self) -> tuple[RunEvent, ...]:
        return tuple(self._events)

    def counts(self) -> dict[str, int]:
        """按类型统计发生次数。"""
        return dict(Counter(e.kind.value for e in self._events))

    def of_kind(self, kind: EventKind) -> tuple[RunEvent, ...]:
        return tuple(e for e in self._events if e.kind is kind)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "kind": e.kind.value,
                    "date": e.when,
                    "subject": e.subject,
                    "detail": e.detail,
                }
                for e in self._events
            ],
            columns=["kind", "date", "subject", "detail"],
        )
