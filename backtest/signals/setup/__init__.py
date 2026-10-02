"""Slot 1 · Setup / Regime variants.

A variant here answers one question: is this candidate ready to trade at all. It runs at
candidate scope (one per underlying) in the Idle and Armed states, reads only the
underlying's history and regime features, and emits nothing but arm or disarm.

``regime.py`` is the one variant: rule-based classification on volatility level, term
structure and trend. Statistical regimes (HMM, Bayesian) are deliberately not here —
with three or four complete cycles in ten years their states are hard to distinguish
from noise, and they have no names, so there is no way to explain why a day counts as
high volatility.
"""
