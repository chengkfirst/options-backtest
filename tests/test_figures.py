"""What the architecture document's three diagrams require, which its text does not say.

## These were never read

The plan is a PDF and the working copy of it here is a text extraction. **The extraction
drops images**, and the document has three: the layer and event-flow diagram (p2), the
candidate lifecycle (p3), and the slot-interaction diagram (p6). Every inventory in this
repo was built from the text, so three figures' worth of requirements were never checked
against the code at all.

Two of them turned out to be satisfied and one was not:

- the **nine-stage order within a timestamp** matches `events.PHASE` exactly, including
  corporate actions before market data
- the **candidate state machine** matches `lifecycle.RUNS_IN` exactly, including
  Positioning sitting in Armed rather than in Open
- the **trade context** was supposed to "also hold read-only book state, positions, P&L,
  Greeks and fill status", and held none of it: the engine built every context with an
  empty mapping, so a slot could read what another slot wrote and nothing about the
  account it was trading

The p6 figure also names five influence channels, and the table behind it (p7) gives each
one a mechanism. Two of the five had no mechanism in the code.
"""

from __future__ import annotations

import pytest

from tests.kinds import kind, labels

pytestmark = labels("architecture", "cross")


class TestTheOrderWithinOneTimestamp:
    """Figure 1: "Fixed order within one timestamp: corp actions -> data -> signals ->
    portfolio -> orders -> fills -> expiry -> accounting -> end of day"."""

    @kind("guard")
    def test_corporate_actions_come_before_any_price(self):
        """First in the figure, and it has to be: an OCC adjustment changes a contract's
        strike and multiplier, so a quote priced before it is priced against the wrong
        contract."""
        from backtest.core.events import PHASE, EventType

        for priced in (EventType.MARKET, EventType.QUOTE):
            assert PHASE[EventType.CORPORATE_ACTION] < PHASE[priced]
            assert PHASE[EventType.CONTRACT_ADJUST] < PHASE[priced]

    @kind("guard")
    def test_the_nine_stages_are_in_the_documents_order(self):
        from backtest.core.events import PHASE, EventType

        stages = [
            EventType.CORPORATE_ACTION,   # corp actions
            EventType.MARKET,             # data
            EventType.SIGNAL,             # signals
            EventType.TARGET_PORTFOLIO,   # portfolio
            EventType.ORDER,              # orders
            EventType.FILL,               # fills
            EventType.EXPIRY,             # expiry
            EventType.ACCOUNTING,         # accounting
            EventType.STATE,              # end of day
        ]
        ranks = [PHASE[s] for s in stages]
        assert ranks == sorted(ranks), (
            f"the phases are not in the document's order: "
            f"{list(zip([s.name for s in stages], ranks))}"
        )

    def test_each_layer_publishes_the_events_the_figure_gives_it(self):
        """Data emits Market, Quote and Universe; signals emit Signal; portfolio emits
        TargetPortfolio and Risk; execution emits Order and Fill."""
        from backtest.core.events import EventType

        for name in ("MARKET", "QUOTE", "UNIVERSE", "SIGNAL", "TARGET_PORTFOLIO",
                     "RISK", "ORDER", "FILL"):
            assert hasattr(EventType, name), f"the figure names {name} and it does not exist"


class TestTheCandidateLifecycle:
    """Figure 2: Idle -> Armed -> Open -> Closed, with the slots that run in each."""

    def test_the_states_and_their_scopes_match(self):
        from backtest.signals.lifecycle import SCOPE, State

        assert SCOPE[State.IDLE] == SCOPE[State.ARMED] == "candidate"
        assert SCOPE[State.OPEN] == SCOPE[State.CLOSED] == "trade"

    @kind("guard")
    def test_positioning_runs_in_armed_and_not_in_open(self):
        """The figure puts "6 Positioning" in the Armed box beside the entry trigger --
        sizing happens when the entry fires, not while the trade is running. Easy to get
        wrong, because sizing feels like something a position does."""
        from backtest.signals.lifecycle import RUNS_IN, State

        assert "position" in RUNS_IN[State.ARMED]
        assert "position" not in RUNS_IN[State.OPEN]

    def test_the_open_state_holds_exactly_the_four_management_slots(self):
        from backtest.signals.lifecycle import RUNS_IN, State

        assert set(RUNS_IN[State.OPEN]) == {"take_profit", "stop_loss", "headwind",
                                            "hedge"}

    def test_closed_runs_nothing(self):
        """"Record finalized" -- a state that still ran a slot would be deciding about a
        trade that is over."""
        from backtest.signals.lifecycle import RUNS_IN, State

        assert RUNS_IN[State.CLOSED] == ()


class TestTheFiveInfluenceChannels:
    """Figure 3 and the table behind it (p7): "Slots influence each other only through
    the trade context and actions", with five named channels."""

    def test_gate_is_the_lifecycle_state(self):
        """"One slot decides whether another may run", mechanism: lifecycle state,
        example: Setup/Regime -> Entry. Entry does not run while a candidate is idle."""
        from backtest.signals.lifecycle import RUNS_IN, State

        assert "entry" not in RUNS_IN[State.IDLE]
        assert "entry" in RUNS_IN[State.ARMED]
        assert "setup" in RUNS_IN[State.IDLE]

    def test_pass_through_is_the_trade_context(self):
        """"One slot's output becomes another's input", mechanism: written to the trade
        context, read by later slots."""
        from backtest.core.trade_context import market_only

        ctx = market_only({})
        ctx.writer("setup").write("regime", "calm")
        assert ctx.of("setup")["regime"] == "calm"
        assert ctx.wrote("regime") == ("setup",)

    def test_shared_context_is_the_dataview(self):
        """"Market state visible to every slot", mechanism: features in the DataView."""
        from backtest.core.dataview import DataView

        assert hasattr(DataView, "signal") and hasattr(DataView, "history")

    @kind("guard")
    def test_override_is_a_conflict_policy_in_the_lifecycle_config(self):
        """"Two slots issue conflicting actions in the same period", mechanism: conflict
        policy in the lifecycle config.

        The policy existed as a constant in `core/types.py`, so 6.3's research tool --
        "compare Open-state run orders and conflict policies" -- had nothing to compare.
        """
        from backtest.core.types import CONFLICT_POLICIES, ExitReason, resolve_exit_reason
        from backtest.signals.lifecycle import Lifecycle

        assert "conflict_policy" in {f for f in Lifecycle.__dataclass_fields__}
        assert set(CONFLICT_POLICIES) >= {"stop_first", "take_profit_first"}

        both = [ExitReason.TAKE_PROFIT, ExitReason.STOP_LOSS]
        assert resolve_exit_reason(both, "stop_first") is ExitReason.STOP_LOSS
        assert resolve_exit_reason(both, "take_profit_first") is ExitReason.TAKE_PROFIT

    def test_an_unknown_conflict_policy_is_refused(self):
        from backtest.core.errors import StartupError
        from backtest.signals.lifecycle import Lifecycle

        with pytest.raises(StartupError, match="not a conflict policy"):
            Lifecycle(conflict_policy="whatever")

    def test_only_the_two_ambiguous_reasons_swap(self):
        """Every other pair keeps its order, because every other pair has a reason that
        is not a guess about intraday sequence."""
        from backtest.core.types import CONFLICT_POLICIES

        a, b = CONFLICT_POLICIES["stop_first"], CONFLICT_POLICIES["take_profit_first"]
        differ = [i for i, (x, y) in enumerate(zip(a, b)) if x is not y]
        assert len(differ) == 2, f"{len(differ)} positions differ, not 2"


class TestTheOpenStateRunOrder:
    """Figure 2 marks the Open-state order "TBD"; 6.3 asks for it to be comparable."""

    def test_the_default_is_the_documents_order(self):
        from backtest.engine.loop import OPEN_SLOTS
        from backtest.signals.lifecycle import RUNS_IN, State

        assert OPEN_SLOTS == RUNS_IN[State.OPEN]

    @kind("guard")
    def test_a_reordering_is_accepted_and_a_different_set_is_not(self):
        """A slot left out would silently stop running, which is the failure that makes
        an unvalidated order worse than a fixed one."""
        from backtest.core.errors import StartupError
        from backtest.signals.lifecycle import Lifecycle

        Lifecycle(open_order=("stop_loss", "take_profit", "headwind", "hedge"))
        with pytest.raises(StartupError, match="reordering"):
            Lifecycle(open_order=("stop_loss", "take_profit"))
        with pytest.raises(StartupError, match="reordering"):
            Lifecycle(open_order=("stop_loss", "take_profit", "headwind", "entry"))


class TestTheContextHoldsTheBook:
    """Figure 3: the trade context "also holds read-only book state, positions, P&L,
    Greeks and fill status". It held an empty mapping."""

    RUN = dict(
        symbol="QQQ", start="2022-06-01", end="2023-06-30",
        entry="close < sma_20 * 0.98",
        select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                "delta_lo": 0.25, "delta_hi": 0.45},
        exit={"hold_days": 20, "dte_floor": 7},
        engine="event_driven", sizing={"method": "fixed"}, quantity=1,
    )

    @staticmethod
    def _watcher(seen: list):
        from backtest.core.component import Param, Variant, register

        name = f"watch{len(seen)}{id(seen) % 1000}"
        register(Variant(
            slot="stop_loss", name=name, version=1,
            describes="records the book state it was given; never exits",
            params=(Param("x", "float", "unused", lo=0.0, hi=1.0),),
            decide=lambda ctx, *, x: bool(seen.append(dict(ctx.get("book") or {}))),
        ))
        return name

    @kind("limit")
    def test_a_slot_can_read_the_whole_book(self):
        from backtest.api.sdk import run

        seen: list = []
        name = self._watcher(seen)
        run(**(self.RUN | {"components": {
            "stop_loss": {"slot": "stop_loss", "variant": name, "params": {"x": 0.5}}}}))

        assert seen, "the slot was never asked"
        book = seen[-1]
        for key in ("equity", "cash", "premium_at_risk", "positions", "drawdown",
                    "delta", "gamma", "vega", "theta", "filled", "intended"):
            assert key in book, f"the figure names {key} and the slot cannot read it"
        assert book["equity"] > 0 and book["cash"] > 0

    @kind("guard")
    def test_the_book_is_separate_from_the_market_and_from_the_position(self):
        """`ctx["delta"]` is this trade's and `ctx["book"]["delta"]` is the account's.
        Flattening them into one mapping would make reading the wrong one silent."""
        from backtest.signals.exit_context import exit_context

        ctx = exit_context(premium_ratio=0.1, underlying_move=0.0, days_held=1,
                           days_to_expiry=30, book={"delta": 42.0})
        assert ctx["book"]["delta"] == 42.0
        assert "delta" not in ctx

    def test_a_context_built_without_a_book_gets_an_empty_mapping(self):
        """Not None: a variant doing `ctx["book"].get("equity")` should get None rather
        than a TypeError when an engine supplies none."""
        from backtest.signals.exit_context import exit_context

        ctx = exit_context(premium_ratio=0.0, underlying_move=0.0, days_held=1,
                           days_to_expiry=30)
        assert ctx["book"] == {}
        assert ctx["book"].get("equity") is None


class TestThePortfolioVerdictReachesTheSlots:
    """The p7 table's second system link: a constraint may "scale, veto or force actions;
    the outcome (for example 'vetoed by premium limit') is written back to the trade
    context so slots can see it". It was in the event log and on the bus, and neither is
    readable by a component deciding."""

    @kind("limit")
    def test_a_slot_sees_that_the_premium_budget_refused_an_entry(self):
        from backtest.api.sdk import run

        seen: list = []
        name = TestTheContextHoldsTheBook._watcher(seen)
        run(**(TestTheContextHoldsTheBook.RUN | {
            "premium_budget": {"max_premium_pct": 0.004},
            "components": {"stop_loss": {"slot": "stop_loss", "variant": name,
                                          "params": {"x": 0.5}}}}))

        vetoed = [b for b in seen if b.get("vetoed")]
        assert vetoed, "the budget refused entries and no slot was told"
        assert "premium budget" in vetoed[-1]["vetoed"]

    def test_the_verdict_is_last_sessions_and_that_is_deliberate(self):
        """Today's entry veto happens after the exits are evaluated, so a slot reading it
        would be reading a decision not yet made. One session of lag, like everything
        else a component sees."""
        import inspect

        from backtest.engine.loop import EventDrivenEngine

        source = inspect.getsource(EventDrivenEngine.run)
        assert "_last_entry_veto" in source
        assert "previous" in source.lower()
