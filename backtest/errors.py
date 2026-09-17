"""三层错误处理的边界定义。见 design.md「错误处理策略」。

1. :class:`StartupError` —— 配置缺失或冲突、引擎不支持的规则、数据源不兼容。
   必须在第一个交易日之前暴露。
2. 运行中的非致命情况 —— 不抛异常，记入 :mod:`backtest.events` 的事件流。
3. :class:`InvariantError` —— 盈亏不守恒、状态自相矛盾这类代码 bug。立即中止，
   因为继续跑只会产出错误结论。
"""

from __future__ import annotations


class BacktestError(Exception):
    """本项目全部异常的基类。"""


class StartupError(BacktestError):
    """启动时即可判定的问题，必须在回测开始前抛出。"""


class InvariantError(BacktestError):
    """不变量被破坏，表示代码有 bug，必须立即中止。"""


class DataQualityError(BacktestError):
    """输入数据本身不合法，且无法以标记方式继续。"""
