"""文档里的代码必须能跑。

## 为什么需要这个

文档里跑不通的代码比没有文档更糟——它看起来是权威的。而文档腐烂是必然的：
接口改了、参数名换了、模块搬了家，文档不会自己跟着变。

这个项目已经踩过：README 里写着"等阶段 4 的事件驱动引擎"，而它早就在了；
界面上挂着"⑤ headwind 完全没有"，而减仓做完了。那些都是手写的静态描述。

这里只查**可执行的部分**：README 里的 Python 代码块。带 ``# 不跑`` 标记的跳过
（比如需要真实数据文件的例子）。跑不通就是文档错了，或者代码改了没同步。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md",)
#: 需要外部条件（你的数据文件、联网）的示例用这个标记跳过。
SKIP_MARK = "# 不跑"


def blocks(text: str) -> list[str]:
    return re.findall(r"```python\n(.*?)```", text, re.S)


def main() -> int:
    failures: list[tuple[str, int, str]] = []
    total = 0
    for name in DOCS:
        path = ROOT / name
        if not path.exists():
            continue
        for i, code in enumerate(blocks(path.read_text(encoding="utf-8")), 1):
            if SKIP_MARK in code:
                continue
            total += 1
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             encoding="utf-8") as fh:
                fh.write(code)
                tmp = fh.name
            r = subprocess.run([sys.executable, tmp], cwd=ROOT,
                               capture_output=True, text=True, timeout=300)
            Path(tmp).unlink(missing_ok=True)
            if r.returncode != 0:
                tail = (r.stderr.strip().splitlines() or ["(无输出)"])[-1]
                failures.append((name, i, tail))

    if failures:
        print(f"文档代码检查失败（{len(failures)}/{total} 个代码块跑不通）")
        for name, i, err in failures:
            print(f"  - {name} 第 {i} 个 python 代码块：{err}")
        print("\n文档里跑不通的代码比没有文档更糟——它看起来是权威的。")
        return 1
    print(f"文档代码检查通过（{total} 个代码块）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
