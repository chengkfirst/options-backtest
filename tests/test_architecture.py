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


def test_no_bare_optional_floats_across_module_boundaries():
    """Scalars crossing module boundaries express absence with Maybe, never a bare
    `float | None`.

    A missing delta read as 0 selects a completely wrong contract and raises nothing.
    """
    offenders: list[str] = []
    for path in sorted(Path("backtest").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
                continue
            returns = node.returns
            if isinstance(returns, ast.BinOp) and isinstance(returns.op, ast.BitOr):
                text = ast.unparse(returns)
                if text.replace(" ", "") in {"float|None", "None|float"}:
                    offenders.append(f"{path}:{node.lineno} {node.name} -> {text}")
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
