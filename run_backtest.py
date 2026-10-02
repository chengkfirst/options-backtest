"""Edit this file, then run it.

    python3 run_backtest.py

To see a different result, change the numbers in STRATEGY below and run it again.
"""

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from backtest import run

# ══════════════════════════════════════════════════════════════
#  edit here
# ══════════════════════════════════════════════════════════════

STRATEGY = dict(
    symbol="QQQ",                    # the underlying: QQQ / SPY
    start="2015-01-01",              # the range
    end="2024-12-31",

    # ── 1. when to enter ─────────────────────────────────
    # Written as an expression. The available signals are listed at the end.
    entry="sma_20 > sma_50",

    # ── 2. which contract to buy ─────────────────────────
    select={
        "right": "call",             # call or put
        "dte": 30,                   # target days to expiry
        "delta": 0.30,               # target delta (0.2 further out, 0.5 near the money)
    },

    # ── 3. when to exit (four conditions, first to fire wins) ──
    exit={
        "hold_days": 10,             # at most this many trading days
        "take_profit": 1.0,          # out after doubling
        "stop_loss": 0.5,            # out after losing 50%
        "dte_floor": 5,              # forced out with 5 trading days left
    },

    quantity=1,                      # contracts per trade
)

# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    result = run(**STRATEGY)
    result.summary()

    # Uncomment to see the detail
    # print(result.trades.head(20).to_string())
    # print(result.by_exit_reason())
    # result.export("out/my_run")

    # Uncomment to try several parameter sets at once
    # sweep = result.sweep({"take_profit": [0.5, 1.0, 1.5], "stop_loss": [0.3, 0.5]})
    # print(sweep.top(10).to_string(index=False))


# ══════════════════════════════════════════════════════════════
#  The available signals (for use in `entry`)
# ══════════════════════════════════════════════════════════════
#   close         closing price (adjusted)
#   sma_20        20-day moving average    sma_50    sma_200
#   ema_20        20-day exponential moving average
#   rsi_14        RSI
#   atr_14        average true range
#   mom_20        20-day momentum (the return)
#   rv_20         20-day realised volatility (annualised)
#   bb_width_20   Bollinger band width
#
#  Examples:
#   "sma_20 > sma_50"                       in a golden cross
#   "sma_20 > sma_50 and rsi_14 < 70"       crossed, and not overbought
#   "close > sma_200 and mom_20 > 0.02"     long-term uptrend, up 2% recently
#   "rv_20 < 0.2"                           a low-volatility environment
#
#  Only > < >= <= == != and or not and arithmetic are allowed. A misspelled signal
#  name raises at once.
