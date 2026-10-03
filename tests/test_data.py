"""The trading calendar, the two price sets, the chain's column contract and quality
checks."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.core.clock import TradingCalendar, expiry_dates, third_friday
from backtest.data.options.chain import UNAVAILABLE, relative_spread, validate_chain
from backtest.data.underlying import build_bars, validate_bars
from backtest.core.errors import DataQualityError, StartupError
from backtest.core.types import CHAIN_COLUMNS, ContractId, Right
from tests.conftest import make_raw_bars


class TestCalendar:
    @pytest.fixture(scope="class")
    def cal(self):
        return TradingCalendar(date(2024, 1, 1), date(2024, 12, 31))

    def test_session_count(self, cal):
        assert len(cal) == 252

    def test_skips_holiday(self, cal):
        """Thanksgiving is not a trading day."""
        assert date(2024, 11, 28) not in cal

    def test_detects_half_day(self, cal):
        assert cal.is_early_close(date(2024, 11, 29))

    def test_third_friday(self):
        assert third_friday(2024, 3) == date(2024, 3, 15)

    def test_shift_out_of_range_returns_none(self, cal):
        assert cal.shift(cal.last, 1) is None

    def test_sessions_between_is_trading_days(self, cal):
        assert cal.sessions_between(date(2024, 3, 1), date(2024, 3, 15)) == 10

    def test_rejects_inverted_range(self):
        with pytest.raises(StartupError):
            TradingCalendar(date(2024, 12, 31), date(2024, 1, 1))

    def test_weekly_expiries_absent_before_introduction(self, cal):
        """Weekly options were rolled out gradually; conjuring them up earlier makes
        selection in the early years easier than it was."""
        later = expiry_dates(cal, weekly_start=date(2030, 1, 1))
        assert all((e.month, e.day) != (1, 5) for e in later)
        assert len(later) == 12  # monthlies only

    def test_monthly_and_weekly_together(self, cal):
        both = expiry_dates(cal, weekly_start=date(2010, 6, 4))
        assert len(both) > 12 and date(2024, 3, 15) in both


class TestUnderlyingBars:
    @pytest.fixture(scope="class")
    def bars(self):
        return build_bars("QQQ", make_raw_bars())

    def test_two_price_series_exist(self, bars):
        assert not bars.traded("close").equals(bars.adjusted("close"))

    def test_adjusted_is_lower_when_dividends_paid(self, bars):
        assert (bars.adjusted("close") <= bars.traded("close")).all()

    def test_adjustment_applied_to_all_fields(self, bars):
        """Adjusting only the close distorts indicators that use the intraday range,
        such as ATR."""
        ratio_close = bars.adjusted("close") / bars.traded("close")
        ratio_high = bars.adjusted("high") / bars.traded("high")
        assert np.allclose(ratio_close, ratio_high)

    def test_unknown_field_raises(self, bars):
        with pytest.raises(KeyError):
            bars.traded("vwap")

    def test_rejects_high_below_low(self, bars):
        bad = bars.frame.copy()
        bad.iloc[3, bad.columns.get_loc("low")] = 1e9
        with pytest.raises(DataQualityError, match="high below its low"):
            validate_bars("QQQ", bad)

    def test_rejects_duplicate_dates(self, bars):
        bad = pd.concat([bars.frame, bars.frame.iloc[[5]]]).sort_index()
        with pytest.raises(DataQualityError, match="duplicate dates"):
            validate_bars("QQQ", bad)

    def test_rejects_non_positive_price(self, bars):
        bad = bars.frame.copy()
        bad.iloc[2, bad.columns.get_loc("close")] = 0.0
        with pytest.raises(DataQualityError, match="non-positive prices"):
            validate_bars("QQQ", bad)


class TestChainContract:
    def test_snapshot_has_full_vendor_schema(self, chain, market):
        """The column set follows a commercial vendor's; what the synthetic chain
        cannot supply is marked unavailable and never dropped from the interface."""
        snap = chain.snapshot(market.sessions()[60])
        assert list(snap.columns) == list(CHAIN_COLUMNS)

    def test_unavailable_fields_are_marked_not_zero(self, chain, market):
        snap = chain.snapshot(market.sessions()[60])
        assert snap["volume"].isna().all() and snap["open_interest"].isna().all()

    def test_ask_strictly_above_bid(self, chain, market):
        snap = chain.snapshot(market.sessions()[60])
        assert (snap["ask"] > snap["bid"]).all()

    def test_marked_synthetic_and_model_derived(self, chain, market):
        snap = chain.snapshot(market.sessions()[60])
        assert set(snap["source"]) == {"synthetic"}
        assert snap["greeks_model_derived"].all()

    def test_strike_grid_covers_configured_range(self, chain, market, config):
        day = market.sessions()[60]
        snap = chain.snapshot(day)
        spot = market.spot(day).unwrap()
        width = config.synthetic.strike_range
        assert snap["strike"].min() <= spot * (1 - width) + config.contract.strike_step
        assert snap["strike"].max() >= spot * (1 + width) - config.contract.strike_step

    def test_series_stops_at_expiry(self, chain, market):
        day = market.sessions()[60]
        snap = chain.snapshot(day)
        expiry = sorted(snap["expiry"].unique())[0]
        contract = ContractId("QQQ", expiry, Right.CALL, float(snap["strike"].median()))
        days = [d for d in market.sessions() if d >= day][:60]
        series = chain.series(contract, days)
        assert (pd.to_datetime(series["asof"]).dt.date <= expiry).all()

    def test_validate_drops_inverted_quotes(self):
        frame = pd.DataFrame(
            [{c: 0 for c in CHAIN_COLUMNS} for _ in range(2)]
        )
        frame["asof"] = date(2024, 1, 2)
        frame["symbol"] = "QQQ"
        frame["expiry"] = date(2024, 2, 16)
        frame["strike"] = [400.0, 401.0]
        frame["right"] = "C"
        frame["bid"] = [1.0, 5.0]
        frame["ask"] = [1.2, 3.0]  # the second row has ask < bid
        frame["quality_flags"] = ""
        kept = validate_chain(frame, asof=date(2024, 1, 2))
        assert len(kept) == 1 and kept["strike"].iloc[0] == 400.0

    def test_validate_drops_expired(self):
        frame = pd.DataFrame([{c: 0 for c in CHAIN_COLUMNS}])
        frame["asof"] = date(2024, 3, 1)
        frame["symbol"] = "QQQ"
        frame["expiry"] = date(2024, 2, 16)
        frame["strike"] = 400.0
        frame["right"] = "C"
        frame["bid"], frame["ask"] = 1.0, 1.2
        frame["quality_flags"] = ""
        assert validate_chain(frame, asof=date(2024, 3, 1)).empty

    def test_relative_spread_nan_when_quote_missing(self):
        assert np.isnan(relative_spread(np.nan, 2.0))


class TestMarketData:
    def test_rejects_mismatched_vol_proxy(self, config):
        """Nasdaq volatility runs systematically above the S&P's; sharing one proxy
        understates QQQ option prices across the board."""
        from backtest.data.market import MarketData
        from backtest.core.types import ContractSpec

        bad = BacktestConfigWithProxy(config, "^VIX")
        with pytest.raises(StartupError, match="volatility proxy"):
            MarketData(bad)

    def test_dividend_yield_from_actual_ex_dates(self, market):
        day = date(2023, 3, 1)
        expiry = date(2023, 4, 21)
        spot = market.spot(day).unwrap()
        assert market.dividend_yield(day, expiry, spot) > 0

    def test_dividend_yield_zero_without_ex_date(self, market):
        day = date(2023, 4, 3)
        expiry = date(2023, 5, 19)
        spot = market.spot(day).unwrap()
        assert market.dividend_yield(day, expiry, spot) == 0.0

    def test_spot_returns_missing_off_session(self, market):
        assert market.spot(date(2023, 1, 1)).is_missing


def BacktestConfigWithProxy(config, proxy):
    import dataclasses

    from backtest.core.types import ContractSpec

    return dataclasses.replace(
        config, contract=ContractSpec(symbol=config.symbol, vol_proxy=proxy)
    )


class TestPriceUsageAlignment:
    """Each price set's use: everything option-related takes traded prices, never
    adjusted ones.

    Judging a historical contract's moneyness off adjusted prices raises nothing; it
    simply shifts every historical selection, with an error that grows monotonically
    with cumulative dividends. So a test pins it down.
    """

    def test_chain_underlying_is_traded_not_adjusted(self, chain, market):
        day = market.sessions()[120]
        snap = chain.snapshot(day)
        traded = market.bars.traded_close_on(day).unwrap()
        adjusted = float(market.bars.adjusted("close").loc[pd.Timestamp(day)])
        assert traded != adjusted, \
            "the fixture must include dividends, or this test cannot discriminate"
        assert float(snap["underlying_price"].iloc[0]) == pytest.approx(traded)

    def test_spot_returns_traded_price(self, market):
        day = market.sessions()[120]
        adjusted = float(market.bars.adjusted("close").loc[pd.Timestamp(day)])
        assert market.spot(day).unwrap() != pytest.approx(adjusted)

    def test_signals_use_adjusted_price(self, market, signals):
        """Signals, conversely, must take adjusted prices, or ex-dividend days produce
        false gaps."""
        day = market.sessions()[120]
        adjusted = float(market.bars.adjusted("close").loc[pd.Timestamp(day)])
        assert signals.on(day)["close"].unwrap() == pytest.approx(adjusted)

    def test_range_truncation_is_reported(self, config):
        """A narrower available range than requested has to be reported; incomplete
        data is never returned silently."""
        import dataclasses

        from backtest.data.market import MarketData
        from backtest.data.underlying import build_bars
        from tests.conftest import make_raw_bars

        narrow = dataclasses.replace(config, end=date(2030, 1, 1))
        bars = build_bars("QQQ", make_raw_bars())
        idx = bars.frame.index
        market = MarketData(
            narrow, bars=bars,
            rates=pd.Series(0.045, index=idx), vol_proxy=pd.Series(0.22, index=idx),
        )
        assert "range_truncated" in market.events.counts()


class TestEarlyExerciseFlag:
    def test_deep_itm_call_before_dividend_is_flagged(self, chain, market):
        """A European model on an American option is an approximation; where it fails
        has to be flagged rather than taken as fair value."""
        day = date(2023, 3, 1)
        snap = chain.snapshot(day)
        calls = snap.loc[snap["right"] == "C"]
        assert (calls["quality_flags"] == "early_exercise_value").any()

    def test_otm_contracts_not_flagged(self, chain, market):
        day = date(2023, 3, 1)
        snap = chain.snapshot(day)
        spot = float(snap["underlying_price"].iloc[0])
        otm_calls = snap.loc[(snap["right"] == "C") & (snap["strike"] > spot * 1.05)]
        assert (otm_calls["quality_flags"] == "").all()


class TestAvailability:
    """The document's first data rule: nothing enters a backtest without an availability
    date.

    It did not hold. There was no such column, and `DataView`'s own first line said the
    cut was made on one. The two dates coincide for a clean daily snapshot -- which is why
    nothing had broken -- and they come apart the moment a feed arrives late, revises a
    figure, or backfills a gap. A row that reached you two days after the session it
    describes is real data about that session and could not have been acted on in it.
    """

    @staticmethod
    def _frame(late_strike: float | None = 400.0):
        import pandas as pd

        rows = []
        for i in range(10):
            day = date(2023, 1, 3) + timedelta(days=i)
            for strike in (380.0, 390.0, 400.0):
                row = {
                    "asof": day, "symbol": "QQQ", "expiry": date(2023, 3, 17),
                    "strike": strike, "right": "C", "bid": 5.0, "ask": 5.2,
                    "underlying_price": 390.0,
                }
                if late_strike is not None:
                    row["available_at"] = (day + timedelta(days=2)
                                           if strike == late_strike else day)
                rows.append(row)
        return pd.DataFrame(rows)

    @staticmethod
    def _source(frame, tmp_path, market, config):
        from backtest.data.options.adapters import FileChainSource

        path = tmp_path / "chain.parquet"
        frame.to_parquet(path)
        return FileChainSource(path, market, config)

    def test_a_row_that_arrived_late_is_withheld(self, tmp_path, market, config):
        source = self._source(self._frame(), tmp_path, market, config)
        visible = source.snapshot(date(2023, 1, 5))
        assert sorted(visible["strike"].tolist()) == [380.0, 390.0]

    def test_withheld_rows_are_counted_not_silently_dropped(self, tmp_path, market,
                                                            config):
        """A feed that routinely arrives a day late should show up as a number, not as
        slightly fewer trades."""
        source = self._source(self._frame(), tmp_path, market, config)
        for i in range(6):
            source.snapshot(date(2023, 1, 3) + timedelta(days=i))
        assert source._withheld > 0

    def test_a_late_row_is_never_tradable_on_its_own_day(self, tmp_path, market, config):
        """Not on that day, and not later either: by the time it arrives the market has
        moved, so it is history rather than a quote to act on."""
        source = self._source(self._frame(), tmp_path, market, config)
        assert 400.0 not in source.snapshot(date(2023, 1, 5))["strike"].tolist()
        assert 400.0 not in source.snapshot(date(2023, 1, 7))["strike"].tolist()

    def test_a_late_row_is_good_history_once_it_has_arrived(self, tmp_path, market,
                                                            config):
        """The other question the same method answers: what is known **now** about
        **then**. A quote for Tuesday that arrived Thursday is fine history by Friday."""
        source = self._source(self._frame(), tmp_path, market, config)
        later = source.snapshot(date(2023, 1, 5), known_by=date(2023, 1, 9))
        assert 400.0 in later["strike"].tolist()

    def test_asking_from_before_the_day_raises(self, tmp_path, market, config):
        from backtest.core.dataview import LookAheadError

        source = self._source(self._frame(), tmp_path, market, config)
        with pytest.raises(LookAheadError):
            source.snapshot(date(2023, 1, 5), known_by=date(2023, 1, 4))

    def test_data_with_no_availability_is_assumed_and_says_so(self, tmp_path, market,
                                                             config):
        """The assumption is defensible. Being unable to tell whether it was made is not."""
        source = self._source(self._frame(late_strike=None), tmp_path, market, config)
        assert source.availability_assumed is True
        assert len(source.snapshot(date(2023, 1, 5))) == 3

    def test_supplied_availability_is_not_reported_as_assumed(self, tmp_path, market,
                                                             config):
        source = self._source(self._frame(), tmp_path, market, config)
        assert source.availability_assumed is False

    def test_the_synthetic_chain_assumes_nothing(self, chain):
        """Each row is generated on the day it describes, so the two coincide by
        construction rather than by guess."""
        assert chain.availability_assumed is False

    def test_both_sources_carry_the_column(self, chain, tmp_path, market, config):
        """Code that reads one must not break on the other."""
        from backtest.core.types import CHAIN_COLUMNS

        assert "available_at" in CHAIN_COLUMNS
        snap = chain.snapshot(date(2023, 1, 4))
        if len(snap):
            assert "available_at" in snap.columns

    def test_a_run_reports_whether_availability_was_assumed(self):
        """A run whose look-ahead protection rests on an assumption should not read the
        same as one where the data carried it."""
        from backtest.api.sdk import run

        result = run(symbol="QQQ", start="2023-01-01", end="2023-03-31",
                     entry="close < sma_20 * 0.98",
                     select={"right": "call", "dte_lo": 21, "dte_hi": 45,
                             "delta_lo": 0.25, "delta_hi": 0.45},
                     exit={"hold_days": 20, "dte_floor": 7}, engine="event_driven",
                     sizing={"method": "fixed"}, quantity=1)
        provenance = result.provenance()
        assert provenance["availability_assumed"] is False
        assert "withheld" in provenance["availability_note"]
