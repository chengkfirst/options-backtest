"""前后端契约：界面声明它需要什么，后端必须给。

前后端各改各的不会报错，只会让功能安静地失效：

- 后端返回 ``id``、前端读 ``run_id`` —— 运行记录第一列显示 ``undefined``，
  而 ``RUNS.find()`` 因为两边都是 undefined 反而"匹配上了"，表面还能用
- 后端算出 ``tag``，列白名单没放行 —— 分组全是空
- 仓位方法改名后前端仍发旧名 —— 后端收不到，静默用默认值

**界面在 bench.js 里显式声明 ``CONTRACT``**，这里拿真实响应核对它。
扫 JS 源码猜字段名不可靠（漏掉 ``RUNS[0].x`` 这类写法），所以用声明。
界面自己在运行时也核对同一份声明，缺了会立刻提示。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "app" / "static" / "bench.js"


def contract() -> dict[str, list[str]]:
    """从 bench.js 里取出 CONTRACT 声明。"""
    js = BENCH.read_text(encoding="utf-8")
    m = re.search(r"const CONTRACT = \{(.*?)\n\};", js, re.S)
    if not m:
        raise SystemExit("bench.js 里找不到 CONTRACT 声明")
    body = re.sub(r"//.*", "", m.group(1))
    out: dict[str, list[str]] = {}
    for key, items in re.findall(r"(\w+):\s*\[(.*?)\]", body, re.S):
        out[key] = re.findall(r'"([^"]+)"', items)
    return out


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from app.server import _meta_payload, run_backtest

    spec = contract()
    problems: list[str] = []

    record = run_backtest({
        "symbol": "QQQ", "start": "2023-01-01", "end": "2023-06-30",
        "engine": "event_driven", "entry": "close < sma_20 * 0.98",
        "strategy_mode": "params", "entry_mode": "expr", "exit_mode": "fixed",
        "hold_days": 10, "dte_floor": 5, "quantity": 1,
        "take_profit": 0.8, "stop_loss": 0.4,
        "dte_lo": 21, "dte_hi": 45, "delta": 0.35,
        "sizing": {"method": "risk_pct", "risk_pct": 0.02},
    })
    for key in spec.get("run", []):
        if key not in record:
            problems.append(f"界面要 run.{key}，后端的运行记录里没有")
    for key in spec.get("run_optional", []):
        if key not in record:
            problems.append(f"界面要 run.{key}（事件驱动应当提供），这次没有")

    meta = _meta_payload()
    for key in spec.get("meta", []):
        if key not in meta:
            problems.append(f"界面要 META.{key}，/api/meta 没有")

    from backtest.engine.strategy import Sizing

    for method in spec.get("sizing_methods", []):
        try:
            Sizing(method=method)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"界面会发仓位方法 {method!r}，后端拒绝：{exc}")

    if problems:
        print("前后端契约检查失败：")
        for p in problems:
            print(f"  - {p}")
        print("\n这一类改动不会报错，只会让功能安静地失效。")
        return 1
    print(f"前后端契约检查通过（运行记录 {len(spec.get('run', []))} 项 · "
          f"meta {len(spec.get('meta', []))} 项 · 仓位方法 {len(spec.get('sizing_methods', []))} 种）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
