"""Slot 2 · Entry variants.

A variant here answers one question: fire now, or not yet. It runs at candidate scope
while Armed, and emits OPEN with its legs or nothing.

``expression.py`` is the one variant: an allowlisted expression over registered signals
(``"sma_20 > sma_50 and rsi_14 < 70"``). A custom callable is the other form, and it is
accepted directly by ``run(entry=...)`` rather than registered here, because there is no
variant registry yet.

Turning the entry into specific contracts is a separate job, in
``backtest/signals/contracts.py``.
"""
