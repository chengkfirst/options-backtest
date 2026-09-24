"""改这个文件，然后运行它。

    python3 跑回测.py

改完想看不同结果，就改下面 STRATEGY 里的数字，再跑一次。
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from backtest import run

# ══════════════════════════════════════════════════════════════
#  改这里
# ══════════════════════════════════════════════════════════════

STRATEGY = dict(
    symbol="QQQ",                    # 标的：QQQ / SPY
    start="2015-01-01",              # 回测起止
    end="2024-12-31",

    # ── 1. 什么时候进场 ──────────────────────────────────
    # 写成表达式。可用信号见文件末尾。
    entry="sma_20 > sma_50",

    # ── 2. 买哪张合约 ───────────────────────────────────
    select={
        "right": "call",             # call 看涨 / put 看跌
        "dte": 30,                   # 目标剩余天数
        "delta": 0.30,               # 目标 delta（0.2 更虚值、0.5 接近平值）
    },

    # ── 3. 什么时候出场（四个条件，先到先算）──────────────
    exit={
        "hold_days": 10,             # 最多持有几个交易日
        "take_profit": 1.0,          # 赚 100% 走
        "stop_loss": 0.5,            # 亏 50% 走
        "dte_floor": 5,              # 剩 5 个交易日强制走
    },

    quantity=1,                      # 每次买几张
)

# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    result = run(**STRATEGY)
    result.summary()

    # 想看明细就把下面几行的 # 去掉
    # print(result.trades.head(20).to_string())
    # print(result.by_exit_reason())
    # result.export("out/我的回测")

    # 想一次试多组参数，去掉下面的 #
    # sweep = result.sweep({"take_profit": [0.5, 1.0, 1.5], "stop_loss": [0.3, 0.5]})
    # print(sweep.top(10).to_string(index=False))


# ══════════════════════════════════════════════════════════════
#  可用信号（写在 entry 里）
# ══════════════════════════════════════════════════════════════
#   close         收盘价（复权）
#   sma_20        20 日均线      sma_50    sma_200
#   ema_20        20 日指数均线
#   rsi_14        RSI
#   atr_14        真实波幅
#   mom_20        20 日动量（涨跌幅）
#   rv_20         20 日已实现波动率（年化）
#   bb_width_20   布林带宽度
#
#  写法举例：
#   "sma_20 > sma_50"                       金叉状态
#   "sma_20 > sma_50 and rsi_14 < 70"       金叉且没超买
#   "close > sma_200 and mom_20 > 0.02"     长期趋势向上且近期涨了 2%
#   "rv_20 < 0.2"                           低波动环境
#
#  只能用 > < >= <= == != and or not 和加减乘除。信号名打错会立刻报错。
