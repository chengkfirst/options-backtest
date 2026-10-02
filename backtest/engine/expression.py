"""Entry conditions written as expressions.

``"sma_20 > sma_50 and rsi_14 < 70"`` is far more direct than a lambda, and much
closer to how someone describes a strategy. Parsed with an ``ast`` allowlist rather
than ``eval``: only comparisons, boolean operators and arithmetic, and every name
must be a registered signal — so a misspelling raises **at startup**, not half way
through a run.

When any signal is absent that day (warm-up or a data gap) the whole condition is
``False``: no entry. An absence never takes part in a comparison as 0.
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
    """A signal was absent while evaluating, so the condition is false."""


def parse_condition(expression: str, available: Iterable[str]):
    """Compile the expression to ``(SignalView) -> bool`` and return the signal
    names it uses."""
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise StartupError(f"syntax error in the entry expression: {expression!r} "
                           f"— {exc}") from exc

    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise StartupError(
                f"the entry expression does not support {type(node).__name__}; only "
                f"comparisons, and/or/not and arithmetic are allowed"
            )
        if isinstance(node, ast.Name):
            names.add(node.id)

    known = set(available)
    unknown = sorted(names - known)
    if unknown:
        raise StartupError(f"the expression references unregistered signals "
                           f"{unknown}; available: {sorted(known)}")

    code = compile(tree, filename="<entry>", mode="eval")
    ordered = tuple(sorted(names))

    def condition(view: SignalView) -> bool:
        values = {}
        for name in ordered:
            value = view[name]
            if value.is_missing:
                return False
            values[name] = value.unwrap()
        return bool(eval(code, {"__builtins__": {}}, values))  # noqa: S307 - allowlisted

    return condition, ordered
