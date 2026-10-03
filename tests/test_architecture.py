"""Architectural constraints: dependency direction, pricing staying confined. A
document cannot stop an import; this can."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tools.check_deps as deps


def test_dependency_direction_holds():
    assert deps.check() == []


def test_detects_reverse_dependency(tmp_path, monkeypatch):
    """With a reverse import planted deliberately, the check must fail — otherwise
    this defence is fake."""
    package = tmp_path / "backtest"
    (package / "data").mkdir(parents=True)
    (package / "data" / "leak.py").write_text(
        "from backtest.engine import trade_table\n", encoding="utf-8"
    )
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    problems = deps.check()
    assert any("reverse dependency" in p for p in problems)


def test_detects_pricing_leak(tmp_path, monkeypatch):
    package = tmp_path / "backtest"
    (package / "engine").mkdir(parents=True)
    (package / "engine" / "leak.py").write_text(
        "from backtest.data.options.pricing import bs_price\n", encoding="utf-8"
    )
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    problems = deps.check()
    assert any("pricing is confined to the data layer" in p for p in problems)


#: Where a float that might be absent is a **measurement**, so absence has to be
#: `Maybe[float]`. Elsewhere -- strategy rules, selection rules, plans -- `float | None`
#: means "no such rule" and is correct.
DATA_MODULES = ("backtest/core/types", "backtest/data", "backtest/accounting")


def test_no_bare_optional_floats_across_module_boundaries():
    """Scalars crossing module boundaries express absence with Maybe, never a bare
    `float | None`.

    A missing delta read as 0 selects a completely wrong contract and raises nothing.
    """
    def is_bare_optional_float(node) -> bool:
        """`float | None`, `None | float` and `Optional[float]`, which are the same type.

        The first version matched only the `|` spelling, so writing `Optional[float]`
        slipped past a check whose whole point is that the type is forbidden however it is
        written.
        """
        if node is None:
            return False
        text = ast.unparse(node).replace(" ", "")
        return text in {"float|None", "None|float", "Optional[float]",
                        "typing.Optional[float]"}

    offenders: list[str] = []
    for path in sorted(Path("backtest").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # A function's return type.
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                if is_bare_optional_float(node.returns):
                    offenders.append(f"{path}:{node.lineno} {node.name} -> "
                                     f"{ast.unparse(node.returns)}")
            # A dataclass field, which crosses module boundaries just as a return does
            # and was not looked at. Only in the modules that carry **observed** data: a
            # `delta: float | None` on a market record is read by every consumer and a
            # missing one read as 0 picks the wrong contract.
            #
            # Configuration is excluded, and that is the rule rather than an exemption. On
            # a strategy rule `stop_loss: float | None` means **there is no stop**, which
            # is a real configuration the code branches on explicitly -- the opposite of a
            # measurement that went missing. Forcing Maybe there would make "no rule" and
            # "unknown" the same shape, which is the confusion this whole convention
            # exists to prevent, in the other direction.
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id.startswith("_") or not any(
                        str(path).startswith(d) for d in DATA_MODULES):
                    continue
                if is_bare_optional_float(node.annotation):
                    offenders.append(f"{path}:{node.lineno} {node.target.id}: "
                                     f"{ast.unparse(node.annotation)}")
    assert offenders == [], "use Maybe[float] instead:\n" + "\n".join(offenders)


def test_regression_baseline_unchanged():
    """The baseline comparison: an unintended change in results must be caught, never
    pass silently."""
    import tools.baseline as baseline

    diff = baseline.compare()
    assert diff is None, (
        "the output differs from the baseline:\n"
        + diff.to_string(index=False)
        + "\nIf the change really should move the results, run "
          "`python -m tools.baseline update` explicitly"
    )
