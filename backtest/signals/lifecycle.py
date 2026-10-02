"""The trade lifecycle: four states, and the settings that are not decided yet.

## Why this is a module and not a paragraph

The architecture separates two scopes that are easy to conflate:

- a **candidate** belongs to an underlying, and there is one per underlying
- a **trade** belongs to a ``trade_id``, and one candidate can produce several over time

Without that separation, "can this underlying hold two trades at once" and "when may it
trade again after one closes" have nowhere to live, so they end up implicit in the
engine — decided by accident rather than chosen. Making them named settings is what lets
them be researched like the components themselves.

## What the engine actually does today

The engine has **no Armed state**. An entry condition becoming true produces an order in
the same step, which collapses Idle and Armed into one. That matters for two reasons:

1. There is nowhere for Setup / Regime to arm a candidate and then let Entry look for
   timing within the armed window — the two are one test.
2. ``reentry_policy`` and ``max_concurrent_trades`` are enforced today by
   ``allow_stacking`` and ``max_concurrent``, which are engine arguments rather than
   lifecycle settings, so they cannot differ per strategy.

:data:`SETTINGS` records each setting, who enforces it now, and what is missing. It is
read by ``tools/status.py``, so the gap cannot quietly go stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    """The four lifecycle states."""

    IDLE = "idle"        # candidate scope; no valid setup
    ARMED = "armed"      # candidate scope; the setup is valid and Entry may fire
    OPEN = "open"        # trade scope; the first fill has been received
    CLOSED = "closed"    # trade scope; fully exited or expired


#: Which component slots run in each state, and at which scope. Taken from the
#: architecture document; the slot keys match :data:`backtest.core.slots.COMPONENTS`.
RUNS_IN: dict[State, tuple[str, ...]] = {
    State.IDLE: ("setup",),
    State.ARMED: ("setup", "entry", "position"),      # position sizes it when entry fires
    State.OPEN: ("take_profit", "stop_loss", "headwind", "hedge"),
    State.CLOSED: (),                                  # the record is finalised
}

#: The scope each state belongs to. A candidate state is per underlying; a trade state
#: is per trade_id.
SCOPE: dict[State, str] = {
    State.IDLE: "candidate", State.ARMED: "candidate",
    State.OPEN: "trade", State.CLOSED: "trade",
}


@dataclass(frozen=True, slots=True)
class Setting:
    """One lifecycle setting: what it decides, who enforces it now, what is missing."""

    name: str
    #: The question it answers, in one line.
    question: str
    #: What enforces it today. Empty means nothing does.
    enforced_by: str = ""
    #: What is missing before it can be set per strategy. Empty means it is settled.
    gap: str = ""


#: The five lifecycle settings. The architecture leaves every one of them open on
#: purpose: they live here rather than inside a component so they can be researched the
#: same way the components are.
SETTINGS: tuple[Setting, ...] = (
    Setting(
        "open_state_order",
        "In what order do take profit, stop loss, headwind and hedge run each snapshot?",
        enforced_by="fixed in the engine: the static exit rules are tested first, then "
                    "the review hook",
        gap="not configurable, and headwind and hedge do not exist as separate slots, "
            "so there is nothing to order yet",
    ),
    Setting(
        "conflict_policy",
        "How are conflicting actions in the same snapshot resolved?",
        enforced_by="EXIT_PRIORITY in core.types, with stop loss ahead of take profit",
        gap="EXIT_PRIORITY settles conflicts between exit *reasons* within one engine "
            "step; it is not a policy over conflicting actions from several slots, "
            "because only one slot can act",
    ),
    Setting(
        "max_concurrent_trades",
        "How many open trades may one underlying hold at once?",
        enforced_by="Strategy.allow_stacking (one or unlimited) plus the engine's "
                    "max_concurrent across the whole book",
        gap="a per-underlying count cannot be expressed; allow_stacking is a boolean "
            "and max_concurrent is book-wide",
    ),
    Setting(
        "reentry_policy",
        "When may a candidate arm again after a trade closes?",
        enforced_by="",
        gap="nothing enforces it. With no Armed state, a candidate re-enters as soon as "
            "the entry condition is true again, which is the most permissive policy "
            "there is and was never chosen deliberately",
    ),
    Setting(
        "check_frequency",
        "At which snapshots do the Open-state slots run? It must match live trading "
        "exactly; no intra-snapshot stop or target fills are assumed.",
        enforced_by="once per trading day, at the close, fixed in the engine",
        gap="daily only. The architecture calls for intraday snapshots with a decision "
            "latency, which also needs intraday option quotes in the data layer",
    ),
)


def gaps() -> list[str]:
    """The lifecycle settings that are not yet settings.

    Generated from :data:`SETTINGS` rather than written by hand, for the same reason as
    :func:`backtest.core.slots.gaps`: a hand-written list goes stale once the gap closes.
    """
    return [f"lifecycle.{s.name}: {s.gap}" for s in SETTINGS if s.gap]


def describe():
    """The four states and what runs in each. The docs and the interface read this one
    table."""
    import pandas as pd

    return pd.DataFrame([{
        "state": st.value,
        "scope": SCOPE[st],
        "slots that run": ", ".join(RUNS_IN[st]) or "none",
    } for st in State]).set_index("state")


def describe_settings():
    """Each lifecycle setting, what enforces it now, and what is missing."""
    import pandas as pd

    return pd.DataFrame([{
        "setting": s.name,
        "decides": s.question,
        "enforced by": s.enforced_by or "nothing",
        "missing": s.gap or "—",
    } for s in SETTINGS]).set_index("setting")
