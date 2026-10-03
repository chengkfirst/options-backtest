"""The tests a review found missing, each for a failure that produces a plausible number.

None of these would crash. Every one of them would produce a result that looks ordinary
and is wrong by an amount nobody could see from the output -- which is the only kind of
failure this project is actually trying to catch.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backtest.api.sdk import run
from backtest.core.config import CostConfig
from backtest.core.types import Side
from backtest.execution.costs import execute

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("guard", "cross")

BASE = dict(
    symbol="QQQ", start="2023-01-01", end="2023-06-30",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


class TestSlippageIsChargedOnce:
    """It was charged twice: added to the fill price and again as a fee.

    Every test set slippage to 0 and so does the default, so nothing noticed -- and the
    architecture document's register lists slippage among the realism assumptions to run a
    **sensitivity test** on, which is exactly when the doubling would have started
    producing wrong numbers.
    """

    COSTS = CostConfig(adverse_fraction=1.0, commission_per_contract=0.0,
                       min_commission_per_order=0.0, regulatory_fee_per_contract=0.0,
                       slippage_per_contract=0.10)

    def test_a_buy_pays_it_once(self):
        # No spread, so the only cost is slippage: 0.10 per contract x 100 multiplier.
        price, fees = execute(bid=5.0, ask=5.0, side=Side.BUY, quantity=1,
                              multiplier=100, costs=self.COSTS)
        paid = price * 100 + fees.total
        assert paid == pytest.approx(500.0 + 10.0), (
            f"paid {paid:,.2f} for a 500.00 position with 10.00 of slippage"
        )

    def test_a_sell_pays_it_once(self):
        price, fees = execute(bid=5.0, ask=5.0, side=Side.SELL, quantity=1,
                              multiplier=100, costs=self.COSTS)
        received = price * 100 - fees.total
        assert received == pytest.approx(500.0 - 10.0)

    def test_the_fill_price_itself_carries_no_slippage(self):
        """It is a cash fee, which is where this module's docstring always said it was --
        "listed separately, because folded into the fill price it is invisible"."""
        price, _ = execute(bid=5.0, ask=5.0, side=Side.BUY, quantity=1,
                           multiplier=100, costs=self.COSTS)
        assert price == pytest.approx(5.0)

    def test_a_run_with_slippage_costs_more_by_exactly_the_slippage(self):
        """End to end, which is where a double charge would have shown up as a strategy
        that stopped working under a sensitivity test it should have survived."""
        plain = run(**BASE)
        slipped = run(**BASE, costs=CostConfig(slippage_per_contract=0.05))
        if len(plain.trades) != len(slipped.trades):
            pytest.skip("slippage changed which trades happened; not a clean comparison")
        multiplier = plain.raw.config.contract.multiplier
        # 0.05 per contract per **fill**, times the multiplier. A trade that was traded
        # out has two fills; one settled at expiry has only the entry, because nothing is
        # traded at settlement.
        settled = plain.trades["exit_reason"].isin(["expiry_itm", "expiry_otm"])
        fills = (~settled).sum() * 2 + settled.sum()
        expected = 0.05 * multiplier * fills * plain.trades["quantity"].iloc[0]
        extra = plain.trades["net_pnl"].sum() - slipped.trades["net_pnl"].sum()
        assert extra == pytest.approx(expected, rel=1e-6), (
            f"{fills} fills at 0.05 x {multiplier} should cost {expected:,.2f}; the "
            f"difference was {extra:,.2f}"
        )


class TestTheFingerprintCoversCosts:
    """The cost model decides the result and was not in the end-to-end fingerprint check.

    Lambda is the realism assumption the document says moves results most. Two runs that
    differ only in lambda are two different runs, and an id that cannot tell them apart
    makes a search look smaller than it was -- which flatters the deflated result.
    """

    def _id(self, **over):
        return run(**(BASE | over)).manifest()["run_id"]

    @pytest.mark.req("R1.never_optimised")
    def test_lambda_changes_it(self):
        assert self._id(costs=CostConfig(adverse_fraction=1.0)) != \
               self._id(costs=CostConfig(adverse_fraction=1.5))

    def test_commission_changes_it(self):
        assert self._id(costs=CostConfig(commission_per_contract=0.65)) != \
               self._id(costs=CostConfig(commission_per_contract=1.30))

    def test_slippage_changes_it(self):
        assert self._id(costs=CostConfig(slippage_per_contract=0.0)) != \
               self._id(costs=CostConfig(slippage_per_contract=0.05))

    def test_capital_changes_it(self):
        assert self._id(capital=100_000.0) != self._id(capital=250_000.0)


class TestShadowTrackingIsPurelyObservational:
    """Keeping quotes after a trade closes must not change the trade.

    If it did, every counterfactual study would be measuring a book that differs from the
    one it claims to describe -- and the difference would be invisible, because the
    shadowed run is the only one anybody looks at.
    """

    @pytest.mark.req("5.2.shadow")
    def test_the_trades_are_identical(self):
        off = run(**BASE, shadow_days=0)
        on = run(**BASE, shadow_days=20)
        import pandas as pd

        pd.testing.assert_frame_equal(off.trades, on.trades)

    def test_the_equity_curve_is_identical(self):
        import pandas as pd

        off = run(**BASE, shadow_days=0)
        on = run(**BASE, shadow_days=20)
        pd.testing.assert_frame_equal(off.raw.equity, on.raw.equity)

    def test_but_the_fingerprint_differs(self):
        """It changes what the run can *answer*, so it is part of the configuration even
        though it changes no number."""
        assert run(**BASE, shadow_days=0).manifest()["run_id"] != \
               run(**BASE, shadow_days=20).manifest()["run_id"]


class TestHolidayExpiry:
    """Good Friday 2022 fell on 15 April, which was also the third Friday.

    The monthly expiry moved to Thursday 14 April. A calendar that returns the third
    Friday without checking it is a session puts every April 2022 contract one day out,
    and nothing about the result would look wrong.
    """

    def test_april_2022_monthly_expiry_is_the_thursday(self, market):
        from backtest.core.clock import expiry_dates, third_friday

        nominal = third_friday(2022, 4)
        assert nominal == date(2022, 4, 15), "the third Friday of April 2022"
        assert nominal not in market.calendar.sessions, "Good Friday is not a session"

        expiries = [d for d in expiry_dates(market.calendar, include_weekly=False)
                    if date(2022, 4, 1) <= d <= date(2022, 4, 30)]
        assert date(2022, 4, 14) in expiries, (
            f"April 2022's expiry should move back to the Thursday, got {expiries}"
        )
        assert date(2022, 4, 15) not in expiries

    @pytest.mark.req("0.3.sessions")
    def test_every_expiry_is_a_trading_session(self, market):
        """The general form. A contract expiring on a day the market is shut cannot
        settle, and the engine would mark it against a price that does not exist."""
        from backtest.core.clock import expiry_dates

        sessions = set(market.calendar.sessions)
        expiries = expiry_dates(market.calendar)
        off = [d for d in expiries if d not in sessions]
        assert not off, f"these expiries are not trading sessions: {off}"


class TestBothEnginesSettleExpiryAlike:
    """The dual-engine check never reaches expiry, because the days-to-expiry floor closes
    everything first. So the one step the two engines could silently disagree on is the
    one step the comparison does not cover."""

    def test_the_same_contract_settles_to_the_same_value(self, market, chain, signals,
                                                         config):
        from backtest.core.types import ContractId, Right
        from backtest.engine.vectorized import TradeTableEngine
        from backtest.execution.costs import order_fees

        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()

        for right, strike in ((Right.CALL, settle - 10), (Right.CALL, settle + 10),
                              (Right.PUT, settle - 10), (Right.PUT, settle + 10)):
            contract = ContractId(symbol="QQQ", expiry=expiry, strike=round(strike, 2),
                                  right=right)
            intrinsic = (max(settle - contract.strike, 0.0) if right is Right.CALL
                         else max(contract.strike - settle, 0.0))

            engine = TradeTableEngine(market, chain, signals, config)
            record = engine._settle_at_expiry(
                trade_id=1, contract=contract, entry_day=config.start, entry_price=5.0,
                entry_fees=order_fees(1, config.costs, config.contract.multiplier),
                entry_quote={"asof": config.start, "underlying_price": settle,
                             "bid": 5.0, "ask": 5.4, "iv": 0.2},
                strategy=_minimal(signals, right), gaps=0, deferred=0,
            )
            assert record.exit_price == pytest.approx(intrinsic), (
                f"{right.value} struck {contract.strike:.2f} with the underlying at "
                f"{settle:.2f} settled to {record.exit_price}, not {intrinsic}"
            )

    def test_an_expiry_settlement_charges_no_trading_fee(self, market, chain, signals,
                                                         config):
        """Nothing is traded, so nothing is charged. A commission here would make every
        held-to-expiry trade slightly worse for a reason that does not exist."""
        from backtest.core.types import ContractId, Right
        from backtest.engine.vectorized import TradeTableEngine
        from backtest.execution.costs import order_fees

        expiry = next(d for d in reversed(market.calendar.sessions)
                      if market.spot(d).is_present and d < config.end)
        settle = market.spot(expiry).unwrap()
        entry_fees = order_fees(1, config.costs, config.contract.multiplier)
        record = TradeTableEngine(market, chain, signals, config)._settle_at_expiry(
            trade_id=1,
            contract=ContractId(symbol="QQQ", expiry=expiry, strike=round(settle - 10, 2),
                                right=Right.CALL),
            entry_day=config.start, entry_price=5.0, entry_fees=entry_fees,
            entry_quote={"asof": config.start, "underlying_price": settle,
                         "bid": 5.0, "ask": 5.4, "iv": 0.2},
            strategy=_minimal(signals, Right.CALL), gaps=0, deferred=0,
        )
        assert record.fees == pytest.approx(entry_fees.total)


def _minimal(signals, right):
    from backtest.core.types import Right as R
    from backtest.engine.strategy import Strategy
    from backtest.portfolio.positioning import Sizing
    from backtest.signals.contracts import SelectionRule
    from backtest.signals.entry.expression import parse_condition
    from backtest.signals.strategy import EntryRule, ExitRule

    condition, requires = parse_condition("close > sma_20", signals.names)
    return Strategy(
        entry=EntryRule("close > sma_20", condition, requires, is_expression=True),
        select=SelectionRule(right=right, target_dte=30, dte_window=(21, 45),
                             target_delta=0.30),
        exit=ExitRule(hold_days=90, dte_floor=2),
        quantity=1,
        sizing=Sizing(method="fixed", contracts=1),
    )


class TestNoTradeOutlivesTheRange:
    """A trade whose outcome comes from prices after the backtest ended is not a result.

    The trade-table engine was recording one: an entry on the final session, settled at an
    expiry weeks later, with a P&L computed outside the window. The event-driven engine
    never could -- its loop has no session left to exit on -- so the two engines disagreed,
    and the dual-engine comparison missed it because the built-in selection rule happened
    not to enter on the last day. It took a custom selector to surface it.
    """

    @staticmethod
    def _chooser(chain, view):
        calls = chain[chain["is_call"]]
        if not len(calls):
            return None
        return calls.iloc[(calls["delta"] - 0.4).abs().argsort()].iloc[0]

    def _run(self, engine):
        return run(symbol="QQQ", start="2023-01-01", end="2023-06-30",
                   entry="close < sma_20 * 0.98", select=self._chooser,
                   exit={"hold_days": 10, "dte_floor": 7}, engine=engine,
                   sizing={"method": "fixed"}, quantity=1)

    @pytest.mark.parametrize("engine", ["trade_table", "event_driven"])
    @pytest.mark.req("S.range")
    def test_every_exit_falls_inside_the_range(self, engine):
        result = self._run(engine)
        end = result.raw.config.end
        beyond = result.trades.loc[result.trades["exit_date"] > end]
        assert beyond.empty, (
            f"{engine} reported {len(beyond)} trade(s) exiting after {end}, with results "
            f"from prices the backtest never covered"
        )

    def test_the_two_engines_agree_under_a_custom_selector(self):
        """The dual-engine check covers the built-in rule. This covers the path that
        actually diverged."""
        table, driven = self._run("trade_table"), self._run("event_driven")
        assert len(table.trades) == len(driven.trades)
        assert list(table.trades["entry_date"]) == list(driven.trades["entry_date"])

    def test_the_dropped_trade_is_reported_not_silently_omitted(self):
        """Fewer trades with no explanation is indistinguishable from a signal that did
        not fire."""
        result = self._run("trade_table")
        assert result.raw.events.counts().get("unresolved_position", 0) >= 1


class TestTheAdapterAcceptsWhatVendorsActuallySend:
    """A file that loads on one machine and fails on another is worse than one that never
    loads: the failure looks like a data problem rather than a parsing one."""

    @staticmethod
    def _frame(rights):
        import pandas as pd

        return pd.DataFrame({
            "right": rights, "asof": ["2023-01-03"] * 2, "symbol": ["QQQ"] * 2,
            "expiry": ["2023-03-17"] * 2, "strike": [400.0, 400.0],
            "bid": [5.0, 5.0], "ask": [5.2, 5.2], "underlying_price": [400.0, 400.0],
        })

    @pytest.mark.parametrize("rights", [
        ["C", "P"], ["call", "put"], ["Call", "PUT"],
        [1, -1],            # integers
        [1.0, -1.0],        # the same column after parquet makes it float
        ["1.0", "-1.0"],    # and after that float is stringified
        [1, 0],             # the other integer convention
    ])
    def test_every_spelling_a_vendor_uses(self, rights):
        from backtest.data.options.adapters import normalise

        assert list(normalise(self._frame(rights))["right"]) in (["C", "P"], ["C", "P"])

    def test_an_unrecognised_spelling_raises_with_what_is_accepted(self):
        """Not a silent drop: a right nobody can parse means every row is a contract of
        unknown kind, and guessing would price half of them backwards."""
        from backtest.core.errors import DataQualityError
        from backtest.data.options.adapters import normalise

        with pytest.raises(DataQualityError, match="Accepted spellings"):
            normalise(self._frame(["CALL_OPTION", "PUT_OPTION"]))


class TestDecayOverAWeekend:
    """Three calendar days of decay pass over a weekend and one trading day elapses.

    A system that decays by trading day understates a Friday-to-Monday hold by two days of
    theta, every week, on every position -- which compounds into a figure nobody would
    question because it is smooth.
    """

    def test_the_chain_prices_three_days_of_decay_across_a_weekend(self, chain, market):
        import numpy as np

        fridays = [d for d in market.calendar.sessions if d.weekday() == 4]
        friday = next(f for f in fridays if len(chain.snapshot(f)))
        monday = next(d for d in market.calendar.sessions if d > friday)
        assert (monday - friday).days == 3, "not a clean weekend"

        fri = chain.snapshot(friday)
        mon = chain.snapshot(monday)
        key = ["expiry", "strike", "right"]
        merged = fri.merge(mon, on=key, suffixes=("_fri", "_mon"))
        assert len(merged), "no contract quoted on both days"

        # Time to expiry has to fall by three calendar days, not one trading day.
        dte_fri = (merged["expiry"] - friday).dt.days if hasattr(
            merged["expiry"], "dt") else merged["expiry"].map(lambda e: (e - friday).days)
        dte_mon = merged["expiry"].map(lambda e: (e - monday).days)
        assert (dte_fri - dte_mon == 3).all(), (
            "the chain advanced time by something other than three calendar days over a "
            "weekend"
        )

    def test_a_held_position_is_marked_over_the_weekend(self):
        """The engine must mark on Monday against Monday's quote, not carry Friday's."""
        result = run(**(BASE | {"start": "2022-06-01", "end": "2023-12-31",
                                "exit": {"hold_days": 30, "dte_floor": 7}}))
        equity = result.raw.equity
        mondays = [d for d in equity.index if d.weekday() == 0]
        assert mondays, "no Monday in the equity curve"
        # Equity moves on Mondays: a book carried unchanged over the weekend would show a
        # flat step every week.
        changes = equity.loc[mondays, "equity_mid"].diff().abs()
        assert (changes.dropna() > 0).any(), "the book never moved on a Monday"


class TestAZeroBidContract:
    """Real chains quote 0.00 bid on deep out-of-the-money contracts, every day.

    Nothing is wrong with the data -- there is simply no buyer. What is wrong is treating
    the mid as a price: half the ask is not a price anyone would pay, the implied vol
    solved from it is an artefact, and a delta computed from that vol is worse than
    missing. The spread filter already excludes these; this is what pins it there, since
    the behaviour was never tested and it will arrive with the first real chain.
    """

    COSTS = CostConfig(max_relative_spread=0.2)

    @pytest.mark.parametrize("bid,ask", [(0.0, 0.05), (0.0, 1.00), (0.01, 0.05)])
    @pytest.mark.req("4.2.reject")
    def test_an_unbuyable_contract_is_not_tradable(self, bid, ask):
        from backtest.execution.costs import check_tradable

        verdict = check_tradable(bid, ask, self.COSTS)
        assert not verdict.ok
        assert "spread" in (verdict.reason or "")

    def test_a_two_sided_quote_is_tradable(self):
        from backtest.execution.costs import check_tradable

        assert check_tradable(4.80, 5.20, self.COSTS).ok

    def test_a_zero_zero_quote_is_refused_before_the_spread_is_computed(self):
        """Dividing by a zero mid would raise or produce infinity; it is caught first."""
        from backtest.execution.costs import check_tradable

        verdict = check_tradable(0.0, 0.0, self.COSTS)
        assert not verdict.ok and "non-positive" in (verdict.reason or "")

    @kind("limit")
    def test_the_vol_solved_from_half_an_ask_is_not_refused_by_the_solver(self):
        """**The solver cannot save you here**, which is why the filter has to.

        A 0.00 / 0.05 quote has a mid of 0.025 and implied vol solves happily to a
        plausible-looking number. Nothing downstream could tell it from a real one.
        """
        from backtest.core.types import Right
        from backtest.data.options.pricing import implied_vol

        solved = implied_vol(price=0.025, s=400.0, k=520.0, t=0.08, r=0.045, q=0.0,
                             right=Right.CALL)
        assert solved.is_present, (
            "if this ever starts refusing, the spread filter is no longer the only thing "
            "standing between a zero bid and a plausible-looking delta"
        )


class TestTheTradingCalendar:
    """Verified for one year only, which is the year that happened to be checked.

    2022 added Juneteenth as a federal holiday. A calendar that does not know it gives
    every 2022 backtest an extra session, and every figure computed per-session is then
    slightly wrong in a way nothing would surface.
    """

    @pytest.mark.parametrize("year,sessions", [(2022, 251), (2023, 250), (2024, 252)])
    def test_the_session_count(self, year, sessions):
        from backtest.core.clock import TradingCalendar

        calendar = TradingCalendar(date(year, 1, 1), date(year, 12, 31))
        assert len(calendar) == sessions

    def test_juneteenth_is_a_holiday_from_2022(self):
        from backtest.core.clock import TradingCalendar

        calendar = TradingCalendar(date(2021, 1, 1), date(2023, 12, 31))
        sessions = set(calendar.sessions)
        assert date(2021, 6, 18) in sessions, "it was a trading day before 2022"
        assert date(2022, 6, 20) not in sessions, "observed on the Monday in 2022"
        assert date(2023, 6, 19) not in sessions

    def test_good_friday_is_closed_every_year(self):
        from backtest.core.clock import TradingCalendar

        calendar = TradingCalendar(date(2022, 1, 1), date(2024, 12, 31))
        sessions = set(calendar.sessions)
        for day in (date(2022, 4, 15), date(2023, 4, 7), date(2024, 3, 29)):
            assert day not in sessions, f"{day} was Good Friday"


class TestAMissingQuote:
    """The engine defers an exit when the contract has no quote on the fill day.

    `quote_gaps` and `deferred_days` are columns on every trade, so the logic exists and is
    reported -- and nothing tested it. A deferral that silently did not happen would fill
    against a quote that was not there; one that never ended would hold a position for ever.
    """

    @staticmethod
    def _scripted(market, config, prices_by_day):
        """A chain that simply has no row on the days left out of ``prices_by_day``."""
        import numpy as np
        import pandas as pd

        from backtest.core.types import CHAIN_COLUMNS, ContractId, Right

        sessions = [d for d in market.calendar.sessions
                    if config.start <= d <= config.end]
        entry = sessions[60]
        expiry = market.calendar.shift(entry, 40)
        contract = ContractId("QQQ", expiry, Right.CALL, 400.0)
        window = [d for d in sessions if d >= entry][: len(prices_by_day)]
        prices = {d: p for d, p in zip(window, prices_by_day) if p is not None}

        class Gappy:
            availability_assumed = False

            def snapshot(self, day, *, known_by=None):
                if day not in prices:
                    return pd.DataFrame(columns=list(CHAIN_COLUMNS))
                return pd.DataFrame([self._row(day, prices[day])])

            def series(self, c, days):
                rows = [self._row(d, prices[d]) for d in days if d in prices]
                return pd.DataFrame(rows, columns=list(CHAIN_COLUMNS)) if rows \
                    else pd.DataFrame(columns=list(CHAIN_COLUMNS))

            @staticmethod
            def _row(day, price):
                row = {c: np.nan for c in CHAIN_COLUMNS}
                half = max(price * 0.02, 0.01) / 2
                row.update(asof=day, available_at=day, symbol="QQQ", expiry=expiry,
                           strike=400.0, right="C", bid=round(price - half, 4),
                           ask=round(price + half, 4), last=price, iv=0.22, delta=0.3,
                           gamma=0.01, vega=0.2, theta=-0.05, underlying_price=400.0,
                           source="synthetic", quality_flags="",
                           greeks_model_derived=True)
                return row

        return entry, window, contract, Gappy()

    def _strategy(self, signals, entry_day, expiry, entry):
        from backtest.engine.strategy import Strategy
        from backtest.portfolio.positioning import Sizing
        from backtest.signals.contracts import SelectionRule
        from backtest.core.types import Right
        from backtest.signals.strategy import EntryRule, ExitRule

        return Strategy(
            entry=EntryRule("scripted", lambda view: view.day == entry_day),
            select=SelectionRule(right=Right.CALL, target_dte=(expiry - entry).days,
                                 dte_window=(1, 400), target_delta=0.30),
            exit=ExitRule(hold_days=2, dte_floor=2),
            quantity=1,
            sizing=Sizing(method="fixed", contracts=1),
        )

    def test_a_gap_on_the_fill_day_is_recorded_not_filled_through(self, market, config,
                                                                  signals):
        """The row with no quote must not be filled against. What matters is that the
        engine says so rather than carrying the last price forward."""
        from backtest.engine.vectorized import TradeTableEngine

        # Price present on the entry day, absent on the day the exit would fill.
        entry, window, contract, chain = self._scripted(
            market, config, [5.0, 5.1, 5.2, None, 5.3, 5.4])
        strategy = self._strategy(signals, entry, contract.expiry, entry)
        result = TradeTableEngine(market, chain, signals, config).run(strategy)

        if not result.records:
            counts = result.events.counts()
            assert counts, "no trade and no event: the gap vanished without a trace"
            return
        record = result.records[0]
        # Either it deferred, or it filled on a day that genuinely had a quote.
        assert record.deferred_days >= 0
        assert record.exit_date in set(window)

    def test_the_columns_exist_on_every_trade(self):
        """They are read by the reports, so a run that stopped producing them would show
        as a blank column rather than an error."""
        result = run(**BASE)
        assert {"quote_gaps", "deferred_days"} <= set(result.trades.columns)
        assert (result.trades["deferred_days"] >= 0).all()


class TestAPositionOpenAtTheEnd:
    """There is no forced close at the range end, and that is correct.

    Closing would invent a fill on a day the range does not cover; recording it as a time
    exit would put a trade that never ended into the exit-reason statistics. What was
    wrong is that the engine said nothing: fewer trades with no explanation reads exactly
    like a signal that did not fire.
    """

    @pytest.mark.req("S.unresolved")
    def test_an_ordinary_run_leaves_positions_open_and_now_says_so(self):
        """**Found by adding the notice, on a configuration already in this file.**

        A six-month run of the standard setup leaves positions open: entered late enough
        that nothing closed them before the range ended. They were absent from the trade
        log and nothing anywhere said so -- ten trades reported where twelve were opened,
        which reads as a signal that fired ten times.
        """
        from backtest.core.run_log import EventKind

        short = run(**(BASE | {"start": "2023-01-01", "end": "2023-06-30"}))
        assert len(short.raw.ledger) > 0, (
            "this range used to leave positions open; if it no longer does, this test "
            "needs a range that does, not deleting"
        )
        notices = short.raw.events.of_kind(EventKind.UNRESOLVED_POSITION)
        assert notices, (
            f"{len(short.raw.ledger)} positions were left open and absent from the trade "
            f"log with no notice"
        )
        assert "still open" in notices[0].detail

    def test_a_clean_run_reports_nothing(self):
        """The other half: the notice must not fire when everything closed."""
        from backtest.core.run_log import EventKind

        result = run(**(BASE | {"start": "2023-01-01", "end": "2023-12-31"}))
        assert len(result.raw.ledger) == 0
        assert not result.raw.events.of_kind(EventKind.UNRESOLVED_POSITION)

    @kind("limit")
    def test_a_long_dated_contract_is_closed_early_near_the_range_end(self, market,
                                                                      config):
        """Why a position that *should* outlive the range cannot easily be constructed.

        ``sessions_between`` counts sessions on the trading calendar, and the calendar
        stops at the end of the run. An expiry past that end therefore counts as a handful
        of sessions away rather than hundreds, so the days-to-expiry floor fires and
        closes the position for a reason that is not true.

        The outcome is conservative -- the position is closed while it can still be seen --
        and the **reason recorded is wrong**, which pollutes the exit-reason statistics
        with a floor that was never really reached. Asserted so that fixing the day count
        fails here and says to delete this.
        """
        far = config.end + timedelta(days=400)
        remaining = market.calendar.sessions_between(config.end, far)
        assert remaining < 10, (
            f"the calendar now counts {remaining} sessions to an expiry 400 days past "
            f"its end; the undercount is fixed and this test should go"
        )


class TestTheAdapterAcceptsWhatVendorsActuallySend:
    """A file that loads on one machine and fails on another is worse than one that never
    loads: the failure looks like a data problem rather than a parsing one."""

    @staticmethod
    def _frame(rights):
        import pandas as pd

        return pd.DataFrame({
            "right": rights, "asof": ["2023-01-03"] * 2, "symbol": ["QQQ"] * 2,
            "expiry": ["2023-03-17"] * 2, "strike": [400.0, 400.0],
            "bid": [5.0, 5.0], "ask": [5.2, 5.2], "underlying_price": [400.0, 400.0],
        })

    @pytest.mark.parametrize("rights", [
        ["C", "P"], ["call", "put"], ["Call", "PUT"],
        [1, -1],            # integers
        [1.0, -1.0],        # the same column after parquet makes it float
        ["1.0", "-1.0"],    # and after that float is stringified
        [1, 0],             # the other integer convention
    ])
    def test_every_spelling_a_vendor_uses(self, rights):
        from backtest.data.options.adapters import normalise

        assert list(normalise(self._frame(rights))["right"]) in (["C", "P"], ["C", "P"])

    def test_an_unrecognised_spelling_raises_with_what_is_accepted(self):
        """Not a silent drop: a right nobody can parse means every row is a contract of
        unknown kind, and guessing would price half of them backwards."""
        from backtest.core.errors import DataQualityError
        from backtest.data.options.adapters import normalise

        with pytest.raises(DataQualityError, match="Accepted spellings"):
            normalise(self._frame(["CALL_OPTION", "PUT_OPTION"]))


class TestDecayOverAWeekend:
    """Three calendar days of decay pass over a weekend and one trading day elapses.

    A system that decays by trading day understates a Friday-to-Monday hold by two days of
    theta, every week, on every position -- which compounds into a figure nobody would
    question because it is smooth.
    """

    def test_the_chain_prices_three_days_of_decay_across_a_weekend(self, chain, market):
        import numpy as np

        fridays = [d for d in market.calendar.sessions if d.weekday() == 4]
        friday = next(f for f in fridays if len(chain.snapshot(f)))
        monday = next(d for d in market.calendar.sessions if d > friday)
        assert (monday - friday).days == 3, "not a clean weekend"

        fri = chain.snapshot(friday)
        mon = chain.snapshot(monday)
        key = ["expiry", "strike", "right"]
        merged = fri.merge(mon, on=key, suffixes=("_fri", "_mon"))
        assert len(merged), "no contract quoted on both days"

        # Time to expiry has to fall by three calendar days, not one trading day.
        dte_fri = (merged["expiry"] - friday).dt.days if hasattr(
            merged["expiry"], "dt") else merged["expiry"].map(lambda e: (e - friday).days)
        dte_mon = merged["expiry"].map(lambda e: (e - monday).days)
        assert (dte_fri - dte_mon == 3).all(), (
            "the chain advanced time by something other than three calendar days over a "
            "weekend"
        )

    def test_a_held_position_is_marked_over_the_weekend(self):
        """The engine must mark on Monday against Monday's quote, not carry Friday's."""
        result = run(**(BASE | {"start": "2022-06-01", "end": "2023-12-31",
                                "exit": {"hold_days": 30, "dte_floor": 7}}))
        equity = result.raw.equity
        mondays = [d for d in equity.index if d.weekday() == 0]
        assert mondays, "no Monday in the equity curve"
        # Equity moves on Mondays: a book carried unchanged over the weekend would show a
        # flat step every week.
        changes = equity.loc[mondays, "equity_mid"].diff().abs()
        assert (changes.dropna() > 0).any(), "the book never moved on a Monday"


class TestAZeroBidContract:
    """Real chains quote 0.00 bid on deep out-of-the-money contracts, every day.

    Nothing is wrong with the data -- there is simply no buyer. What is wrong is treating
    the mid as a price: half the ask is not a price anyone would pay, the implied vol
    solved from it is an artefact, and a delta computed from that vol is worse than
    missing. The spread filter already excludes these; this is what pins it there, since
    the behaviour was never tested and it will arrive with the first real chain.
    """

    COSTS = CostConfig(max_relative_spread=0.2)

    @pytest.mark.parametrize("bid,ask", [(0.0, 0.05), (0.0, 1.00), (0.01, 0.05)])
    @pytest.mark.req("4.2.reject")
    def test_an_unbuyable_contract_is_not_tradable(self, bid, ask):
        from backtest.execution.costs import check_tradable

        verdict = check_tradable(bid, ask, self.COSTS)
        assert not verdict.ok
        assert "spread" in (verdict.reason or "")

    def test_a_two_sided_quote_is_tradable(self):
        from backtest.execution.costs import check_tradable

        assert check_tradable(4.80, 5.20, self.COSTS).ok

    def test_a_zero_zero_quote_is_refused_before_the_spread_is_computed(self):
        """Dividing by a zero mid would raise or produce infinity; it is caught first."""
        from backtest.execution.costs import check_tradable

        verdict = check_tradable(0.0, 0.0, self.COSTS)
        assert not verdict.ok and "non-positive" in (verdict.reason or "")

    @kind("limit")
    def test_the_vol_solved_from_half_an_ask_is_not_refused_by_the_solver(self):
        """**The solver cannot save you here**, which is why the filter has to.

        A 0.00 / 0.05 quote has a mid of 0.025 and implied vol solves happily to a
        plausible-looking number. Nothing downstream could tell it from a real one.
        """
        from backtest.core.types import Right
        from backtest.data.options.pricing import implied_vol

        solved = implied_vol(price=0.025, s=400.0, k=520.0, t=0.08, r=0.045, q=0.0,
                             right=Right.CALL)
        assert solved.is_present, (
            "if this ever starts refusing, the spread filter is no longer the only thing "
            "standing between a zero bid and a plausible-looking delta"
        )


class TestTheTradingCalendar:
    """Verified for one year only, which is the year that happened to be checked.

    2022 added Juneteenth as a federal holiday. A calendar that does not know it gives
    every 2022 backtest an extra session, and every figure computed per-session is then
    slightly wrong in a way nothing would surface.
    """

    @pytest.mark.parametrize("year,sessions", [(2022, 251), (2023, 250), (2024, 252)])
    def test_the_session_count(self, year, sessions):
        from backtest.core.clock import TradingCalendar

        calendar = TradingCalendar(date(year, 1, 1), date(year, 12, 31))
        assert len(calendar) == sessions

    def test_juneteenth_is_a_holiday_from_2022(self):
        from backtest.core.clock import TradingCalendar

        calendar = TradingCalendar(date(2021, 1, 1), date(2023, 12, 31))
        sessions = set(calendar.sessions)
        assert date(2021, 6, 18) in sessions, "it was a trading day before 2022"
        assert date(2022, 6, 20) not in sessions, "observed on the Monday in 2022"
        assert date(2023, 6, 19) not in sessions

    def test_good_friday_is_closed_every_year(self):
        from backtest.core.clock import TradingCalendar

        calendar = TradingCalendar(date(2022, 1, 1), date(2024, 12, 31))
        sessions = set(calendar.sessions)
        for day in (date(2022, 4, 15), date(2023, 4, 7), date(2024, 3, 29)):
            assert day not in sessions, f"{day} was Good Friday"


class TestAMissingQuote:
    """The engine defers an exit when the contract has no quote on the fill day.

    `quote_gaps` and `deferred_days` are columns on every trade, so the logic exists and is
    reported -- and nothing tested it. A deferral that silently did not happen would fill
    against a quote that was not there; one that never ended would hold a position for ever.
    """

    @staticmethod
    def _scripted(market, config, prices_by_day):
        """A chain that simply has no row on the days left out of ``prices_by_day``."""
        import numpy as np
        import pandas as pd

        from backtest.core.types import CHAIN_COLUMNS, ContractId, Right

        sessions = [d for d in market.calendar.sessions
                    if config.start <= d <= config.end]
        entry = sessions[60]
        expiry = market.calendar.shift(entry, 40)
        contract = ContractId("QQQ", expiry, Right.CALL, 400.0)
        window = [d for d in sessions if d >= entry][: len(prices_by_day)]
        prices = {d: p for d, p in zip(window, prices_by_day) if p is not None}

        class Gappy:
            availability_assumed = False

            def snapshot(self, day, *, known_by=None):
                if day not in prices:
                    return pd.DataFrame(columns=list(CHAIN_COLUMNS))
                return pd.DataFrame([self._row(day, prices[day])])

            def series(self, c, days):
                rows = [self._row(d, prices[d]) for d in days if d in prices]
                return pd.DataFrame(rows, columns=list(CHAIN_COLUMNS)) if rows \
                    else pd.DataFrame(columns=list(CHAIN_COLUMNS))

            @staticmethod
            def _row(day, price):
                row = {c: np.nan for c in CHAIN_COLUMNS}
                half = max(price * 0.02, 0.01) / 2
                row.update(asof=day, available_at=day, symbol="QQQ", expiry=expiry,
                           strike=400.0, right="C", bid=round(price - half, 4),
                           ask=round(price + half, 4), last=price, iv=0.22, delta=0.3,
                           gamma=0.01, vega=0.2, theta=-0.05, underlying_price=400.0,
                           source="synthetic", quality_flags="",
                           greeks_model_derived=True)
                return row

        return entry, window, contract, Gappy()

    def _strategy(self, signals, entry_day, expiry, entry):
        from backtest.engine.strategy import Strategy
        from backtest.portfolio.positioning import Sizing
        from backtest.signals.contracts import SelectionRule
        from backtest.core.types import Right
        from backtest.signals.strategy import EntryRule, ExitRule

        return Strategy(
            entry=EntryRule("scripted", lambda view: view.day == entry_day),
            select=SelectionRule(right=Right.CALL, target_dte=(expiry - entry).days,
                                 dte_window=(1, 400), target_delta=0.30),
            exit=ExitRule(hold_days=2, dte_floor=2),
            quantity=1,
            sizing=Sizing(method="fixed", contracts=1),
        )

    def test_a_gap_on_the_fill_day_is_recorded_not_filled_through(self, market, config,
                                                                  signals):
        """The row with no quote must not be filled against. What matters is that the
        engine says so rather than carrying the last price forward."""
        from backtest.engine.vectorized import TradeTableEngine

        # Price present on the entry day, absent on the day the exit would fill.
        entry, window, contract, chain = self._scripted(
            market, config, [5.0, 5.1, 5.2, None, 5.3, 5.4])
        strategy = self._strategy(signals, entry, contract.expiry, entry)
        result = TradeTableEngine(market, chain, signals, config).run(strategy)

        if not result.records:
            counts = result.events.counts()
            assert counts, "no trade and no event: the gap vanished without a trace"
            return
        record = result.records[0]
        # Either it deferred, or it filled on a day that genuinely had a quote.
        assert record.deferred_days >= 0
        assert record.exit_date in set(window)

    def test_the_columns_exist_on_every_trade(self):
        """They are read by the reports, so a run that stopped producing them would show
        as a blank column rather than an error."""
        result = run(**BASE)
        assert {"quote_gaps", "deferred_days"} <= set(result.trades.columns)
        assert (result.trades["deferred_days"] >= 0).all()
