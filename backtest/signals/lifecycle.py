"""The trade lifecycle: four states, and the settings that are not decided yet.

## Why this is a module and not a paragraph

The architecture separates two scopes that are easy to conflate:

- a **candidate** belongs to an underlying, and there is one per underlying
- a **trade** belongs to a ``trade_id``, and one candidate can produce several over time

Without that separation, "can this underlying hold two trades at once" and "when may it
trade again after one closes" have nowhere to live, so they end up implicit in the
engine — decided by accident rather than chosen. Making them named settings is what lets
them be researched like the components themselves.

## What the engine does

The Armed state is real. Each session the setup slot arms or disarms the candidate, and
entry is asked **only** inside the armed window -- so slot 1 and slot 2 are two
decisions, which is what makes either of them measurable. While they were one test no
ablation could say what a regime filter was worth, because removing it also removed the
timing.

``reentry_policy`` and ``max_concurrent_trades`` are :class:`Lifecycle` settings on the
strategy rather than engine arguments, so two strategies being compared can differ on
them and they can be swept.

**Every default reproduces the old behaviour exactly**: ``setup=None`` is always armed and
``reentry_policy="immediate"`` waits for nothing, which together are what the loop did
before any of this existed. The regression baseline does not move and the two engines stay
row-identical. That is deliberate -- the point of naming these is to find out what they
cost, which needs the before as much as the after. Measured over three years with a
ten-day hold: 136 trades unconstrained, 115 under ``next_session``, 63 under a twenty
session cooldown, 21 with one trade per candidate, 1 under ``once``.

:data:`SETTINGS` records each setting, who enforces it now, and what is still missing. It
is read by ``tools/status.py``, so a gap cannot quietly go stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
        enforced_by="Lifecycle.max_concurrent_trades, counted per candidate. "
                    "Strategy.allow_stacking still decides whether the **same contract** "
                    "may be held twice, and the engine's max_concurrent is the book-wide "
                    "limit; the three answer different questions",
    ),
    Setting(
        "reentry_policy",
        "When may a candidate arm again after a trade closes?",
        enforced_by="Lifecycle.reentry_policy: immediate, next_session, cooldown or "
                    "once. The default is immediate, which is what the engine always "
                    "did -- so it is now a choice that was made rather than one that "
                    "happened, and it can be measured",
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


# ── the candidate, as a thing with a state ────────────────────────────────────

#: The reentry policies a candidate may follow after one of its trades closes.
#:
#: ``immediate`` is the **default, and it is what the engine has always done** -- the
#: entry condition becoming true again arms the candidate again, with nothing in between.
#: It is the most permissive policy there is and it was never chosen; making it the
#: default here changes no result and turns an accident into a setting somebody can
#: measure. In live trading it is also the least plausible, which is the argument for
#: measuring it.
REENTRY_POLICIES: tuple[str, ...] = ("immediate", "next_session", "cooldown", "once")


@dataclass(frozen=True, slots=True)
class Lifecycle:
    """The lifecycle settings, per strategy rather than per engine.

    These were engine arguments -- ``allow_stacking``, ``max_concurrent`` -- which meant
    they could not differ between two strategies being compared, and could not be swept.
    They are a strategy's choices about its own candidates, so they live with the
    strategy.

    **Every default reproduces the current behaviour exactly.** A settings object that
    changed results on arrival would make every stored run incomparable with every new
    one, and the point of naming these is to find out what they are worth -- which needs
    the before as much as the after.
    """

    #: How many open trades one candidate (one underlying) may hold at once. ``None``
    #: means no limit, which with ``allow_stacking=False`` on the strategy is what the
    #: engine does today: one trade per contract, any number per underlying.
    max_concurrent_trades: int | None = None

    #: When a candidate may arm again after one of its trades closes.
    reentry_policy: str = "immediate"

    #: Sessions to wait under ``cooldown``. Required there and meaningless elsewhere,
    #: which ``__post_init__`` enforces rather than silently ignoring.
    cooldown_sessions: int | None = None

    #: **Which exit wins when a stop and a take-profit both fire on one day.**
    #:
    #: The document's "Override" influence channel: "two slots issue conflicting actions
    #: in the same period", with the mechanism given as "conflict policy in the lifecycle
    #: config". The policy existed and was a constant in `core/types.py`, so the one
    #: research tool the document asks for here -- 6.3's "compare Open-state run orders
    #: and conflict policies" -- had nothing to compare.
    #:
    #: ``stop_first`` is the default and is what the system has always done: a daily bar
    #: does not say which level was touched first, and assuming the losing side came
    #: first cannot flatter the strategy. ``take_profit_first`` is the other reading of
    #: the same ambiguity, and the gap between them is a number rather than an argument.
    #:
    #: **With the built-in rules this can never bind**, and that is worth knowing before
    #: a sweep over it reports no difference and gets read as "the choice does not
    #: matter". Both rules test the *same* daily premium ratio -- one for ``>= target``
    #: and one for ``<= -stop`` -- and a single number cannot satisfy both. The conflict
    #: is reachable only when the two slots read different things, which is what a
    #: component variant is for: ``stop_loss/underlying`` fires on the underlying's move
    #: while ``take_profit/premium`` fires on the premium, and an IV spike can move those
    #: in opposite directions on one session.
    conflict_policy: str = "stop_first"

    #: **The order the Open-state slots are consulted in**, or None for the document's
    #: own order. Figure 2 of the architecture document marks this "order TBD", and the
    #: same research tool asks for it to be comparable.
    #:
    #: It does not change which exit wins -- that is the conflict policy above, applied
    #: after every slot has been asked. What it changes is **what each slot can see**:
    #: under the pass-through channel a slot writes to the trade context and later slots
    #: read it, so a headwind that runs before the stop is an input to it and one that
    #: runs after is not.
    open_order: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        from backtest.core.errors import StartupError

        if self.reentry_policy not in REENTRY_POLICIES:
            raise StartupError(
                f"{self.reentry_policy!r} is not a reentry policy; one of "
                f"{', '.join(REENTRY_POLICIES)}"
            )
        from backtest.core.types import CONFLICT_POLICIES

        if self.conflict_policy not in CONFLICT_POLICIES:
            raise StartupError(
                f"{self.conflict_policy!r} is not a conflict policy; one of "
                f"{', '.join(sorted(CONFLICT_POLICIES))}"
            )
        if self.open_order is not None:
            wanted, allowed = tuple(self.open_order), RUNS_IN[State.OPEN]
            if sorted(wanted) != sorted(allowed):
                raise StartupError(
                    f"open_order must be a reordering of the Open-state slots "
                    f"{allowed}, and {wanted} is not. A slot left out would silently "
                    f"stop running, and one added here does not run in this state"
                )
        if self.reentry_policy == "cooldown" and not self.cooldown_sessions:
            raise StartupError(
                "reentry_policy='cooldown' needs cooldown_sessions -- how long to wait "
                "is the whole content of the policy, and a default would be the "
                "platform choosing a research parameter"
            )
        if self.reentry_policy != "cooldown" and self.cooldown_sessions is not None:
            raise StartupError(
                f"cooldown_sessions={self.cooldown_sessions} was given with "
                f"reentry_policy={self.reentry_policy!r}, which does not wait. A setting "
                f"that is silently ignored is one somebody believes is in force"
            )
        if self.max_concurrent_trades is not None and self.max_concurrent_trades < 1:
            raise StartupError(
                f"max_concurrent_trades={self.max_concurrent_trades} allows no trades at "
                f"all. Use 1, or None for no limit"
            )


@dataclass(slots=True)
class Candidate:
    """One underlying's candidate, and the state it is in.

    Mutable, unlike nearly everything else here, because a state machine is the one
    thing that is **about** changing: a frozen candidate would mean a new object per
    transition and the identity of "this underlying's candidate" would be gone, which is
    exactly what the trade/candidate distinction exists to keep.
    """

    symbol: str
    state: State = State.IDLE
    #: Trades this candidate has opened that are still open.
    open_trades: int = 0
    #: Trades it has opened in total, which is what ``once`` turns on.
    opened: int = 0
    #: The session its last trade closed on, which is what the waiting policies turn on.
    last_closed: "date | None" = None
    #: Why it is in the state it is in, for the event log and for a person reading it.
    reason: str = "no setup has been seen yet"

    def arm(self, reason: str) -> None:
        """Setup says this environment is one to trade in.

        Arming does not open anything. That separation is the point of the state: it
        gives slot 1 (setup / regime) and slot 2 (entry) different jobs, and until it
        existed the two were one test -- so no ablation could say what the regime filter
        was worth, because removing it also removed the timing.
        """
        self.state = State.ARMED
        self.reason = reason

    def disarm(self, reason: str) -> None:
        self.state = State.IDLE
        self.reason = reason

    def may_enter(self, day: "date", lifecycle: Lifecycle, *, calendar) -> str:
        """"" if the candidate may open a trade today, else why not.

        A sentence rather than a bool, because every refusal here is a decision that
        changes the result and a researcher reading a trade count needs to know which
        policy removed the trades.
        """
        if self.state is not State.ARMED:
            return f"the candidate is {self.state.value}: {self.reason}"
        if (lifecycle.max_concurrent_trades is not None
                and self.open_trades >= lifecycle.max_concurrent_trades):
            return (f"{self.symbol} already holds {self.open_trades} open trade(s) and "
                    f"max_concurrent_trades is {lifecycle.max_concurrent_trades}")

        policy = lifecycle.reentry_policy
        # `once` is not a waiting policy and must not sit behind the guard below. It
        # turns on **how many trades have been opened**, not on how long ago one closed
        # -- and under an execution lag nothing has closed yet while the first several
        # are still queueing, so a `once` placed after that guard allowed eleven.
        if policy == "once" and self.opened >= 1:
            return (f"{self.symbol} has already opened {self.opened} trade(s) and "
                    f"reentry_policy is 'once'")
        if policy in ("immediate", "once") or self.last_closed is None:
            return ""
        if policy == "next_session":
            return ("" if day > self.last_closed
                    else f"{self.symbol} closed a trade today and reentry_policy is "
                         f"'next_session'")
        waited = calendar.sessions_between(self.last_closed, day)
        if waited >= (lifecycle.cooldown_sessions or 0):
            return ""
        return (f"{self.symbol} closed a trade {waited} session(s) ago and the cooldown "
                f"is {lifecycle.cooldown_sessions}")

    def opened_trade(self) -> None:
        """One more trade from this candidate.

        **The candidate's state does not become Open.** ``SCOPE`` says Open and Closed
        are *trade* states, and the first version of this set the candidate to Open
        anyway -- which then made the next session's arming clobber it, so the count was
        kept and the state was meaningless. Worse, a candidate allowed two concurrent
        trades is Armed *and* has one open, and a single exclusive state cannot say both.

        So the candidate carries Idle or Armed, which is its setup status, and the trade
        count beside it. The two answer different questions and neither is the other.
        """
        self.open_trades += 1
        self.opened += 1

    def closed_trade(self, day: "date") -> None:
        self.open_trades = max(0, self.open_trades - 1)
        self.last_closed = day
