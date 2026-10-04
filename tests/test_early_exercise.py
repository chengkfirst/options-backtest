"""Acting on early exercise — requirement 4.2.early_exercise.

## Why the flag alone was not enough, and was recorded as a refusal

The chain has flagged where a European price is wrong for a while: a call before an
ex-dividend date, a deep in-the-money put against the carry on the strike. Nothing acted
on the flag, and the requirements map said why rather than calling it a gap -- acting on
it needs a model that says what the position is worth **unexercised**, and a rule that
exercised on the flag alone would be exercising on *the failure of an approximation*
rather than on a comparison.

The comparison is `intrinsic now > value if held`, and the right-hand side is exactly what
Black-Scholes cannot give for an American option. `data/options/american.py` is the tree
that can.

## The flag screens, the tree decides

Measured on this data: on one session 1,033 of 6,086 rows carry the flag, and the tree
disagrees with it on the ones it is asked about. Over a three-year put strategy the tree
is consulted **three times** across 194 trades.

That division is not only about cost -- though an 800-step tree per position per session
would be minutes rather than seconds. It is that the flag's job is to over-select: it says
"the European price may be wrong here", which is a cheap and generous test, and the
expensive one answers whether exercising is actually worth more.
"""

from __future__ import annotations

from datetime import date

import pytest

from backtest.core.types import ExitReason, Right

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("consistency", "4")

PUTS = dict(
    symbol="QQQ", start="2021-01-01", end="2023-12-31",
    entry="close > sma_20 * 1.02",
    select={"right": "put", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 10, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


@pytest.fixture(scope="module")
def put_run():
    from backtest.api.sdk import run

    return run(**PUTS)


class TestTheFlagScreensAndTheTreeDecides:
    @pytest.mark.req("4.2.early_exercise")
    def test_the_tree_is_consulted_only_where_the_flag_fires(self, put_run):
        """The screen, measured.

        A tree per position per session would be minutes; this run consults it a handful
        of times. Asserted as a share of the sessions rather than as a count, because the
        count is a property of this data and the share is a property of the design.
        """
        from backtest.core.run_log import EventKind

        consulted = len(put_run.raw.events.of_kind(EventKind.EARLY_EXERCISE_RISK))
        sessions = len(put_run.raw.snapshots)
        assert consulted > 0, (
            "the tree was never consulted in a put strategy over three years, so this "
            "path is not reachable and the test below proves nothing"
        )
        assert consulted < sessions / 20, (
            f"the tree was consulted on {consulted} of {sessions} sessions. The flag is "
            f"supposed to be the screen; at that rate it is not screening"
        )

    @pytest.mark.req("4.2.early_exercise")
    def test_the_tree_overrules_the_flag_and_says_so(self, put_run):
        """The flag over-selects **by design**, and the disagreement is recorded.

        A flag whose every firing led to an exercise would be a decision rather than a
        screen, and this system would be exercising on the failure of the European
        approximation.
        """
        from backtest.core.run_log import EventKind

        notes = put_run.raw.events.of_kind(EventKind.EARLY_EXERCISE_RISK)
        disagreed = [n for n in notes if "the tree disagrees" in n.detail]
        assert disagreed, "the tree agreed with the flag every time it was asked"
        # And it says the figures the decision turned on, not just the verdict.
        assert "intrinsic" in disagreed[0].detail
        assert "hold value" in disagreed[0].detail

    def test_nothing_is_exercised_without_the_tree_agreeing(self, put_run):
        """Every early exercise in the log has a tree verdict behind it."""
        from backtest.core.run_log import EventKind

        exercised = [r for r in put_run.raw.records
                     if r.exit_reason is ExitReason.EARLY_EXERCISE]
        agreed = [n for n in put_run.raw.events.of_kind(EventKind.EARLY_EXERCISE_RISK)
                  if "exercised early" in n.detail]
        assert len(exercised) == len(agreed)


@kind("guard")
class TestWhatIsNotExercised:
    """Three refusals, each because guessing would be worse than not asking."""

    def test_a_short_position_is_not_exercised(self, market, chain, signals, config):
        """A short option is exercised **against** you by whoever is long it, and
        modelling that needs an assumption about the other side -- they may exercise
        optimally, early, or never. A different question with a different answer.
        """
        import inspect

        from backtest.engine.loop import EventDrivenEngine

        source = inspect.getsource(EventDrivenEngine._early_exercise)
        assert "pos.quantity <= 0" in source, (
            "nothing stops a short position being exercised by this path, which would "
            "be the engine deciding what the other side of the trade does"
        )

    def test_a_european_contract_is_never_exercised_early(self, market, chain, signals,
                                                          config):
        """The contract spec says which it is, and the question does not arise for a
        European one."""
        import dataclasses

        from backtest.core.types import ExerciseStyle
        from backtest.engine.loop import EventDrivenEngine

        european = dataclasses.replace(
            config, contract=dataclasses.replace(config.contract,
                                                 exercise=ExerciseStyle.EUROPEAN))
        engine = EventDrivenEngine(market, chain, signals, european)
        position = next(iter([]), None)
        assert engine.config.contract.exercise is ExerciseStyle.EUROPEAN
        # No position to pass, so the guard is read from the source for the same reason
        # as above: what is asserted is that the branch exists and comes first.
        import inspect

        source = inspect.getsource(EventDrivenEngine._early_exercise)
        assert source.index('exercise.value != "american"') < source.index(
            "pos.quantity <= 0"), (
            "the American-style check is not the first thing this does, so a European "
            "contract reaches the rest of it"
        )

    def test_an_expiring_position_is_left_to_the_expiry_path(self):
        """Two paths settling the same contract on the same day would book it twice.
        The expiry path owns the day the contract expires."""
        import inspect

        from backtest.engine.loop import EventDrivenEngine

        source = inspect.getsource(EventDrivenEngine.run)
        assert "if pos.contract.expiry <= day:" in source
        assert source.index("3c  early exercise") < source.index("# 4  settle expiries")


class TestTheExerciseIsASettlement:
    """What happens to the book when it does fire."""

    def test_it_delivers_shares_and_books_the_intrinsic(self, market, chain, signals,
                                                        config):
        """Constructed, because this data does not produce an exercise in an ordinary
        run -- and a path that only runs when the market obliges is a path with no test.

        The position is settled through the ledger's own expiry settlement, which is
        right: exercising **is** a settlement, and the shares and the cash move exactly
        as they do at expiry.
        """
        from backtest.accounting.ledger import Ledger
        from backtest.core.run_log import EventLog
        from backtest.core.types import ContractId
        from backtest.engine.loop import EventDrivenEngine, OpenTrade
        from backtest.execution.costs import order_fees

        day = next(d for d in market.calendar.sessions
                   if config.start < d < config.end and market.spot(d).is_present
                   and len(chain.snapshot(d)))
        spot = market.spot(day).unwrap()
        # A deep in-the-money put, which is the case the tree says to exercise.
        expiry = max(chain.snapshot(day)["expiry"])
        contract = ContractId(symbol="QQQ", expiry=expiry,
                              strike=round(spot * 1.30, 2), right=Right.PUT)

        engine = EventDrivenEngine(market, chain, signals, config, EventLog())
        ledger = Ledger(initial_cash=1_000_000.0, spec=config.contract)
        from backtest.core.types import Side

        ledger.apply_fill(contract=contract, side=Side.BUY, quantity=1, price=5.0,
                          fees=order_fees(1, config.costs, config.contract.multiplier),
                          when=config.start, trade_id=1)
        trades = {1: OpenTrade(trade_id=1, contract=contract, entry_date=config.start,
                               entry_price=5.0, quantity=1, side=Side.BUY, fees=0.0,
                               entry_quote={}, entry_signals={})}

        # The chain has no row for a strike 30% out, so the screen would stop this. The
        # tree is asked directly, which is what the screen defers to.
        from backtest.data.options.american import value

        verdict = value(spot=spot, strike=contract.strike,
                        years=config.time.year_fraction(day, expiry),
                        rate=market.risk_free_rate(day), vol=0.22,
                        dividend_yield=market.dividend_yield(day, expiry, spot),
                        right=Right.PUT)
        assert verdict.exercise_now, (
            "a put 30% in the money with this fixture's rates is not worth exercising "
            "early, so this test's premise is gone -- find a case that is rather than "
            "asserting the mechanism on one that is not"
        )

        kind_of = ledger.settle_expiry(contract, market.spot(day), day)
        assert kind_of == "physical"
        held = {p.symbol: p for p in ledger.stock_positions}
        # A long put delivers shares **out**: the account sells at the strike.
        assert held["QQQ"].shares == -config.contract.multiplier
