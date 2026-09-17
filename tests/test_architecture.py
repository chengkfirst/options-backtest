"""架构约束：依赖方向、定价的封闭性。文档拦不住 import，这里才拦得住。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import tools.check_deps as deps


def test_dependency_direction_holds():
    assert deps.check() == []


def test_detects_reverse_dependency(tmp_path, monkeypatch):
    """故意造一处反向导入时，检查必须失败——否则这道防线是假的。"""
    package = tmp_path / "backtest"
    (package / "data").mkdir(parents=True)
    (package / "data" / "leak.py").write_text(
        "from backtest.engine import trade_table\n", encoding="utf-8"
    )
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    problems = deps.check()
    assert any("反向依赖" in p for p in problems)


def test_detects_pricing_leak(tmp_path, monkeypatch):
    package = tmp_path / "backtest"
    (package / "engine").mkdir(parents=True)
    (package / "engine" / "leak.py").write_text(
        "from backtest.data.synthetic.pricing import bs_price\n", encoding="utf-8"
    )
    monkeypatch.setattr(deps, "ROOT", tmp_path)
    problems = deps.check()
    assert any("定价是合成链的内部工具" in p for p in problems)


def test_no_bare_optional_floats_across_module_boundaries():
    """跨模块的标量用 Maybe 表达缺失，不用裸 `float | None`。

    一个被当成 0 的缺失 delta 会让选约选出完全错误的合约，且不报任何错。
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
    assert offenders == [], "改用 Maybe[float]：\n" + "\n".join(offenders)


def test_regression_baseline_unchanged():
    """基线比对：代码改动带来的非预期结果变化必须被检出，不得静默通过。"""
    import tools.baseline as baseline

    diff = baseline.compare()
    assert diff is None, (
        "输出与基线不一致：\n"
        + diff.to_string(index=False)
        + "\n若该改动确实应当改变结果，显式执行 `python -m tools.baseline update`"
    )
