"""依赖方向检查。

模块之间只允许自下而上依赖，且**定价被关在合成链内部**：`selection` 与 `engine`
不得直接导入 `data.synthetic.pricing`——上层消费的是链上的报价与希腊字母字段，
不关心那些数字是市场观测还是模型生成。这条约束保证接入真实数据时上层零改动。

单独成文而不是只写在文档里，是因为文档拦不住 import。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "backtest"

#: 层级：数字越小越底层。上层可依赖下层，反之不可。
#:
#: ``report`` 与 ``data`` 同层：它只读交易明细，不依赖任何上游模块，因此是叶子而非
#: 顶层。把它摆在顶层会让 ``engine`` 复用它的统计函数变成"反向依赖"，那是分层定错，
#: 不是代码有问题。真正要守住的约束由 ``REPORT_FORBIDDEN`` 单独表达。
LAYERS: dict[str, int] = {
    "maybe": 0, "errors": 0, "events": 0, "types": 0, "config": 0,
    "data": 1, "report": 1,
    "signals": 2, "selection": 2, "costs": 2,
    "engine": 3,
    "api": 4, "__init__": 4,
}

#: 只有 `data.synthetic` 内部可以导入定价工具。
PRICING_MODULE = "backtest.data.synthetic.pricing"
PRICING_ALLOWED_PREFIX = "backtest.data.synthetic"

#: `report` 只读交易明细，不应依赖引擎或数据层。
REPORT_FORBIDDEN = {"data", "engine", "signals", "selection", "costs"}


def _layer_of(module: str) -> int | None:
    top = module.split(".")[1] if module.startswith(f"{PACKAGE}.") else None
    if top is None:
        return None
    return LAYERS.get(top)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return {m for m in found if m.startswith(PACKAGE)}


def check() -> list[str]:
    problems: list[str] = []
    for path in sorted((ROOT / PACKAGE).rglob("*.py")):
        rel = path.relative_to(ROOT).with_suffix("")
        module = ".".join(rel.parts)
        own_layer = _layer_of(module)
        if own_layer is None:
            continue
        own_top = module.split(".")[1]

        for imported in _imports(path):
            target_layer = _layer_of(imported)
            if target_layer is None:
                continue
            target_top = imported.split(".")[1]

            if imported.startswith(PRICING_MODULE) and not module.startswith(
                PRICING_ALLOWED_PREFIX
            ):
                problems.append(
                    f"{module} 直接导入了 {imported}；定价是合成链的内部工具，"
                    f"上层应消费链上的字段"
                )
            if own_top == "report" and target_top in REPORT_FORBIDDEN:
                problems.append(f"{module} 不应依赖 {imported}；report 只读交易明细")
            if target_layer > own_layer:
                problems.append(
                    f"{module}（第 {own_layer} 层）导入了 {imported}（第 {target_layer} 层）：反向依赖"
                )
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("依赖方向检查失败：")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("依赖方向检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
