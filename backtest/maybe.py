"""可缺失值。

跨模块边界传递的每一个标量都用 ``Maybe`` 表达，禁止用 ``None`` / ``NaN`` / ``0`` /
``-1`` 充当缺失。理由见 design.md 决策「统一的可缺失值类型」：一个被当成 0 的缺失
delta 会让选约选出完全错误的合约，而回测照常跑完、不报任何错。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")
U = TypeVar("U")


class MissingValueError(LookupError):
    """对缺失值调用 ``unwrap`` 时抛出。"""


@dataclass(frozen=True, slots=True)
class Maybe(Generic[T]):
    """有值，或无值 + 无值原因。

    只经由 :meth:`some` / :meth:`missing` 构造。``reason`` 在缺失时必填——
    "为什么没有"和"没有"同样重要，它决定下游是跳过、报错还是归入未知组。
    """

    _value: T | None
    _reason: str | None

    def __post_init__(self) -> None:
        if self._value is None and self._reason is None:
            raise ValueError("缺失值必须给出原因")
        if self._value is not None and self._reason is not None:
            raise ValueError("有值时不应带缺失原因")

    # ---- 构造 ----
    @classmethod
    def some(cls, value: T) -> "Maybe[T]":
        if value is None:
            raise ValueError("Maybe.some 不接受 None；缺失请用 Maybe.missing(reason)")
        return cls(value, None)

    @classmethod
    def missing(cls, reason: str) -> "Maybe[T]":
        if not reason:
            raise ValueError("缺失原因不能为空")
        return cls(None, reason)

    # ---- 查询 ----
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
            raise MissingValueError(f"取用缺失值：{self._reason}")
        return self._value

    def or_else(self, default: T) -> T:
        return default if self._value is None else self._value

    def map(self, fn: Callable[[T], U]) -> "Maybe[U]":
        if self._value is None:
            return Maybe(None, self._reason)
        return Maybe.some(fn(self._value))

    def __bool__(self) -> bool:  # 防止 `if maybe:` 这种把缺失误当成 falsy 的写法
        raise TypeError("不要对 Maybe 取布尔值；请用 .is_present / .is_missing")

    def __repr__(self) -> str:
        if self._value is None:
            return f"Missing({self._reason!r})"
        return f"Some({self._value!r})"


def some(value: T) -> Maybe[T]:
    return Maybe.some(value)


def missing(reason: str) -> Maybe[T]:
    return Maybe.missing(reason)
