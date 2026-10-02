"""Values that may be absent.

Every scalar that crosses a module boundary carries its absence in ``Maybe``.
``None`` / ``NaN`` / ``0`` / ``-1`` must never stand in for "no value": a missing
delta read as ``0`` picks a completely wrong contract, and the backtest still
finishes and still raises nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")


class MissingValueError(LookupError):
    """Raised when ``unwrap`` is called on an absent value."""


@dataclass(frozen=True, slots=True)
class Maybe(Generic[T]):
    """A value, or its absence together with the reason for it.

    Construct only through :meth:`some` / :meth:`missing`. ``reason`` is required
    when absent — *why* there is no value matters as much as the absence itself,
    because it decides whether the caller skips, raises, or files it as unknown.
    """

    _value: T | None
    _reason: str | None

    def __post_init__(self) -> None:
        if self._value is None and self._reason is None:
            raise ValueError("an absent value must state a reason")
        if self._value is not None and self._reason is not None:
            raise ValueError("a present value must not carry a reason for absence")

    # ---- construction ----
    @classmethod
    def some(cls, value: T) -> "Maybe[T]":
        if value is None:
            raise ValueError("Maybe.some does not accept None; use Maybe.missing(reason)")
        return cls(value, None)

    @classmethod
    def missing(cls, reason: str) -> "Maybe[T]":
        if not reason:
            raise ValueError("the reason for absence must not be empty")
        return cls(None, reason)

    # ---- queries ----
    @property
    def is_present(self) -> bool:
        return self._value is not None

    @property
    def is_missing(self) -> bool:
        return self._value is None

    @property
    def reason(self) -> str | None:
        return self._reason

    def unwrap(self) -> T:
        if self._value is None:
            raise MissingValueError(f"read of an absent value: {self._reason}")
        return self._value

    def or_else(self, default: T) -> T:
        return default if self._value is None else self._value

    def map(self, fn: Callable[[T], U]) -> "Maybe[U]":
        if self._value is None:
            return Maybe(None, self._reason)
        return Maybe.some(fn(self._value))

    def __bool__(self) -> bool:  # stops `if maybe:` from reading absence as falsy
        raise TypeError("do not take the truth value of a Maybe; "
                        "use .is_present / .is_missing")

    def __repr__(self) -> str:
        if self._value is None:
            return f"Missing({self._reason!r})"
        return f"Some({self._value!r})"


def some(value: T) -> Maybe[T]:
    return Maybe.some(value)


def missing(reason: str) -> Maybe[T]:
    return Maybe.missing(reason)
