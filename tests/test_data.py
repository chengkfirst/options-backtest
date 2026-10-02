"""The trading calendar, the two price sets, the chain's column contract and quality
checks."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from backtest.data.calendar import TradingCalendar, expiry_dates, third_friday
from backtest.data.chain import UNAVAILABLE, relative_spread, validate_chain
from backtest.data.underlying import build_bars, validate_bars
from backtest.errors import DataQualityError, StartupError
from backtest.types import CHAIN_COLUMNS, ContractId, Right
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
        from backtest.types import ContractSpec

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

    from backtest.types import ContractSpec

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
