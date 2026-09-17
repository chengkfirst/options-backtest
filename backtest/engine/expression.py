"""入场条件的表达式写法。

``"sma_20 > sma_50 and rsi_14 < 70"`` 比写一个 lambda 直观得多，也更接近使用者
描述策略时的说法。用 ``ast`` 白名单解析而非 ``eval``：只允许比较、布尔运算与算术，
名字必须是已注册的信号——打错名字在**启动时**就报错，不会跑到一半才失败。

任一信号在当日缺失（预热期或数据缺口）时，整个条件返回 ``False``：不入场。
缺失绝不被当成 0 参与比较。
"""

from __future__ import annotations

import ast
from typing import Iterable

from backtest.errors import StartupError
from backtest.signals.registry import SignalView

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub,
    ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Name, ast.Load, ast.Constant,
)


class MissingSignal(Exception):
    """条件求值时遇到缺失信号，整条件判为不成立。"""


def parse_condition(expression: str, available: Iterable[str]):
    """把表达式编译成 ``(SignalView) -> bool``，并返回它用到的信号名。"""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise StartupError(f"入场表达式语法错误：{expression!r} —— {exc}") from exc

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise StartupError(
                f"入场表达式不支持 {type(node).__name__}；只允许比较、and/or/not 与四则运算"
            )
        if isinstance(node, ast.Name):
            names.add(node.id)

    known = set(available)
    unknown = sorted(names - known)
    if unknown:
        raise StartupError(f"表达式引用了未注册的信号 {unknown}；可用：{sorted(known)}")

    code = compile(tree, filename="<entry>", mode="eval")
    ordered = tuple(sorted(names))

    def condition(view: SignalView) -> bool:
        values = {}
        for name in ordered:
            value = view[name]
            if value.is_missing:
                return False
            values[name] = value.unwrap()
        return bool(eval(code, {"__builtins__": {}}, values))  # noqa: S307 - 已白名单校验

    return condition, ordered
