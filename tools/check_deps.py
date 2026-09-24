"""依赖方向检查。

模块之间只允许自下而上依赖，且**定价不得越过数据层**：`selection`、`engine`、
`research` 不得直接导入 `backtest.pricing`——上层消费的是链上的报价与希腊字母字段，
不关心那些数字是市场观测、供应商给的、还是模型推的。这条约束保证换数据源时上层零改动。

定价本身是**市场模型**，不是合成链的私产：合成链用它造价格，文件源用它在供应商
不给 greeks 时补推。所以它在 `backtest.pricing`（第 0 层），允许数据层的各个实现使用。

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
    "maybe": 0, "errors": 0, "events": 0, "types": 0, "config": 0, "pricing": 0,
    #: components 是概念底座：七个组件各自在交易里干什么、它们怎么互相牵制。
    #: 它**不依赖任何东西**（只有文字与数据），所以在最底层；界面、文档、研究
    #: 工具都从它读，这样"缺口清单"不会在两处各写一份然后走样。
    "components": 0,
    "data": 1, "report": 1,
    "signals": 2, "selection": 2, "costs": 2,
    "portfolio": 2, "risk": 2,
    "engine": 3,
    #: research 会**调用引擎跑回测**（比如仓位方法对照要把同一策略跑很多遍），
    #: 所以它在引擎之上。risk 反过来被引擎导入（逐日希腊字母聚合），在引擎之下。
    "research": 4,
    "api": 5, "__init__": 5,
}

#: 只有数据层的实现可以导入定价工具——合成链造价格、文件源补 greeks 都在那一层。
#: 再往上（选约、引擎、研究）一律禁止：它们只许读链上的字段。
PRICING_MODULE = "backtest.pricing"
PRICING_ALLOWED_PREFIX = "backtest.data"

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
    """返回问题清单。空表示通过。

    **未在 LAYERS 登记的模块一律判为失败。** 早先的版本对不认识的模块直接跳过，
    结果新增的 portfolio 模块整个逃过了检查——防线自己有静默失败，比没有防线更糟。
    """
    problems: list[str] = []
    tops = {
        p.relative_to(ROOT / PACKAGE).parts[0].removesuffix(".py")
        for p in (ROOT / PACKAGE).rglob("*.py")
        if p.name != "__init__.py" or p.parent != ROOT / PACKAGE
    }
    for top in sorted(tops - set(LAYERS) - {"__pycache__"}):
        problems.append(
            f"模块 {PACKAGE}.{top} 没有在 LAYERS 里登记层级——它的依赖方向没有被检查。"
            f"请在 tools/check_deps.py 的 LAYERS 中为它指定层级。"
        )
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
