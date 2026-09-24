"""一条命令跑完所有守卫。

    python -m tools.check          # 全部
    python -m tools.check --fast   # 跳过耗时的（测试套件、文档代码）

## 这个项目的规范只有一条

**能被工具检查的才叫规范，写在文档里的只是愿望。**

每一道守卫对应一类**静默失败**——跑完了、有结果、不报错，但结果是错的：

=====================  ====================================================
守卫                    对应的失败形态
=====================  ====================================================
测试套件                行为回归
依赖方向                定价越过数据层；未登记的模块绕过检查
回归基线                改动带来的非预期结果变化
发布完整性              本地有、git 里没有——别人 clone 下来缺文件
文档代码                README 里的示例跑不通
组件定义                表单字段不是 run() 的参数，填了没效果也不报错
=====================  ====================================================

新增一条规范的标准：**先有一个具体的错，再有守卫**。未发生过的风险不设防，
否则守卫本身会变成没人看的噪音。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: (名字, 命令, 是不是耗时)
GUARDS: tuple[tuple[str, list[str], bool], ...] = (
    ("依赖方向",   [sys.executable, "tools/check_deps.py"], False),
    ("发布完整性", [sys.executable, "tools/check_shipped.py"], False),
    ("组件定义",   [sys.executable, "-m", "pytest", "tests/test_components.py", "-q"], False),
    ("回归基线",   [sys.executable, "-m", "tools.baseline", "check"], False),
    ("测试套件",   [sys.executable, "-m", "pytest", "tests", "-q"], True),
    ("文档代码",   [sys.executable, "tools/check_docs.py"], True),
)


def main() -> int:
    ap = argparse.ArgumentParser(description="跑完所有守卫")
    ap.add_argument("--fast", action="store_true", help="跳过耗时的守卫")
    args = ap.parse_args()

    results: list[tuple[str, bool, float, str]] = []
    for name, cmd, slow in GUARDS:
        if slow and args.fast:
            continue
        t0 = time.time()
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        ok = r.returncode == 0
        tail = ""
        if not ok:
            lines = [x for x in (r.stdout + r.stderr).strip().splitlines() if x.strip()]
            tail = lines[-1] if lines else "(无输出)"
        results.append((name, ok, time.time() - t0, tail))
        print(f"  {'✓' if ok else '✗'} {name:10s} {time.time() - t0:5.1f}s"
              + (f"   {tail[:70]}" if tail else ""))

    bad = [n for n, ok, _, _ in results if not ok]
    print()
    if bad:
        print(f"✗ {len(bad)} 道守卫没过：{'、'.join(bad)}")
        print("  看上面的提示；要看完整输出就单独跑那一条。")
        return 1
    print(f"✓ {len(results)} 道守卫全过" + ("（--fast，跳过了耗时的）" if args.fast else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
