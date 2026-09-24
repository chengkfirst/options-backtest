"""发布完整性：本地能跑的，别人 clone 下来也要能跑。

## 为什么需要这个

`.gitignore` 里写了 `research/`，本意是排除本地研究记录。但**不带前导斜杠会匹配
任何层级的同名目录**，于是 `backtest/research/` 这个代码包整个没上传——六个模块
（MFE/MAE、regime 分层、仓位对照、事件研究、取舍面）在远程根本不存在，而它们的
测试上去了。别人 clone 下来直接崩。

一直没发现，是因为这类错**没有任何迹象**：`git status` 干净，`git add -A` 不报错，
本地测试全绿。只有在另一台机器上 clone 才看得出来。

所以这条规则必须由工具守：**包里每个 .py 都必须被 git 跟踪**。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: 这些目录里的代码必须发布。研究记录、缓存那些本来就该排除，不在此列。
SHIPPED = ("backtest", "tests", "tools", "app")


def tracked() -> set[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return set(out.stdout.splitlines())


def main() -> int:
    known = tracked()
    if not known:
        print("不是 git 仓库，跳过发布完整性检查")
        return 0

    missing: list[str] = []
    for top in SHIPPED:
        base = ROOT / top
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.suffix not in (".py", ".js", ".css", ".html"):
                continue
            rel = str(path.relative_to(ROOT))
            if rel not in known:
                missing.append(rel)

    if missing:
        print("发布完整性检查失败：这些文件本地有、git 里没有")
        for m in sorted(missing):
            reason = subprocess.run(["git", "check-ignore", "-v", m],
                                    cwd=ROOT, capture_output=True, text=True).stdout.strip()
            print(f"  - {m}" + (f"\n      被这条规则忽略：{reason.split(':')[0]}:{reason.split(':')[1]}"
                                if reason else "  （还没 git add）"))
        print("\n别人 clone 下来会缺这些文件。检查 .gitignore 的规则是不是写宽了"
              "——不带前导斜杠会匹配任何层级的同名目录。")
        return 1

    print("发布完整性检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
