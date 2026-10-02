"""The run event stream.

Normal states of the market — a missing quote, a selection that finds nothing,
a contract that cannot be traded, an exit that has to wait a day — are not
exceptions. Raising on them stops the run; swallowing them distorts the
statistics. Both are avoided by recording them in a queryable stream: the run
carries on and nothing is lost.
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
    """An append-only collection of events."""

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
        """How many times each kind occurred."""
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
