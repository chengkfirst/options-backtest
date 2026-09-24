"""一屏看完整个项目的状态。

## 为什么要有这个

一个项目失去统一性，几乎总是同一个原因：**同一件事在多处各写一份，然后慢慢走样。**
这个项目已经踩过几次——

- 界面上挂着"⑤ headwind 完全没有"，而减仓其实早就做完了
- ROADMAP 写着"不是验证已定型策略的工具"，而使用者明确说了它就是
- README 写着"等阶段 4 的事件驱动引擎"，而它已经在了

共同点是：那些话都是**手写的**，写的时候是对的，代码往前走了它们没跟上。
而且它们看起来很权威，比没有更糟。

## 对策

1. **每个事实只有一处定义。** 组件是什么 → ``backtest/components.py``；
   依赖方向 → ``tools/check_deps.py``；结果是否变了 → ``tools/baseline.py``。
2. **派生视图一律生成，不手写。** 界面的表单、缺口清单、以及这份状态。
3. **能被工具检查的约束，就别只写在文档里**——文档拦不住代码。

这个脚本是第 2 条的落点：大 plan 的视角**算出来**，所以不会过期。

    python -m tools.status
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = {"done": "✅", "partial": "⚠️ ", "missing": "○ "}


def _components() -> list[str]:
    sys.path.insert(0, str(ROOT))
    from backtest.components import COMPONENTS, LAYERS

    lines = []
    for layer, what in LAYERS:
        members = [c for c in COMPONENTS if c.layer == layer]
        worst = ("missing" if any(c.status == "missing" for c in members)
                 else "partial" if any(c.status == "partial" for c in members) else "done")
        lines.append(f"\n  {MARK[worst]}{layer}　{what}")
        for c in members:
            tail = ""
            if c.origin == "discovered":
                tail = "　← 填不出来，要研究" if c.status != "done" else ""
            lines.append(f"      {MARK[c.status]}{c.name}　{c.output}{tail}")
            if c.gap:
                lines.append(f"          缺：{c.gap[:64]}")
    return lines


def _requirements() -> list[str]:
    """需求.md 里的验收标准统计。

    这些状态目前是**手写的**——已知的不统一点。理想是从代码推出来，但"界面清晰"
    "统计诚实"这类标准本来就没法自动判定。所以这里只统计、并把它标明为手写，
    而不是假装它是算出来的。
    """
    path = ROOT / "需求.md"
    if not path.exists():
        return ["  （需求.md 不在）"]
    text = path.read_text(encoding="utf-8")
    rows = re.findall(r"^\| (\d+\.\d+) \| (.+?) \| (.+?) \|", text, re.M)
    counts = {"✅": 0, "⚠️": 0, "○": 0}
    for _, _, st in rows:
        for k in counts:
            if k in st:
                counts[k] += 1
                break
    total = sum(counts.values())
    return [
        f"  {total} 条验收标准（**手写**，需求.md）："
        f"做完 {counts['✅']} · 不到位 {counts['⚠️']} · 没有 {counts['○']}",
    ]


def _guards() -> list[str]:
    out = []
    for name, cmd in (
        ("依赖方向", [sys.executable, "tools/check_deps.py"]),
        ("回归基线", [sys.executable, "-m", "tools.baseline", "check"]),
    ):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        out.append(f"  {'✅' if r.returncode == 0 else '✗ '}{name}")
    n = len(list((ROOT / "tests").glob("test_*.py")))
    out.append(f"  ·  {n} 个测试文件（跑一遍：pytest）")
    return out


def main() -> int:
    print("═" * 62)
    print("  期权回测平台 · 当前状态")
    print("═" * 62)
    print("\n【七个组件】按交易里发生的顺序分四层")
    print("\n".join(_components()))
    print("\n【验收】")
    print("\n".join(_requirements()))
    print("\n【守卫】能被工具检查的约束")
    print("\n".join(_guards()))
    print("\n【下一步】见 需求.md 的「待做顺序」")
    print("═" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
