#!/bin/bash
# 双击这个文件就能起回测台。
#
# 做四件事：查 Python 版本 → 装依赖（只装一次）→ 起服务 → 开浏览器。
# 出任何问题都会停下来把原因打出来，而不是一闪而过关掉窗口——
# 那是这类脚本最气人的地方。

cd "$(dirname "$0")" || exit 1
echo "═══ 期权回测台 ═══"
echo

# ── 1. Python ──────────────────────────────────────────
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null)
    if [ -n "$v" ] && [ "$v" -ge 311 ] 2>/dev/null; then PY="$c"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "✗ 没找到 Python 3.11 或更新的版本。"
  echo
  echo "  去 https://www.python.org/downloads/ 下载安装，然后再双击这个文件。"
  echo
  read -r -p "按回车关闭。"
  exit 1
fi
echo "✓ Python: $($PY --version)"

# ── 2. 依赖 ────────────────────────────────────────────
if ! "$PY" -c "import backtest" >/dev/null 2>&1; then
  echo "  第一次运行，装依赖（一两分钟）…"
  if ! "$PY" -m pip install -q -e ".[dev]"; then
    echo
    echo "✗ 装依赖失败。上面是原因。"
    read -r -p "按回车关闭。"
    exit 1
  fi
  echo "✓ 依赖装好了"
else
  echo "✓ 依赖已就位"
fi

# ── 3. 起服务 ──────────────────────────────────────────
echo
echo "  地址：http://localhost:8765"
echo "  关掉这个窗口就停止服务。"
echo
( sleep 2 && open http://localhost:8765 ) &
"$PY" app/server.py

echo
read -r -p "服务已停止。按回车关闭。"
