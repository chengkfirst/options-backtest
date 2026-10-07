"""Wiring in your own data: the health check, column mapping, interchangeability with
the synthetic chain.

The risk here is not failing to read the file — that raises, and is obvious. The real
risk is **reading it and reading it wrong**: 1/-1 in the right column taken as
strings, IV as a percentage taken as a decimal, the same contract appearing twice on
one day. Any of those lets the backtest finish normally and produce a wholly
believable wrong result. So these tests are about whether it says so when it is
wrong.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from backtest.data.options.adapters import SCHEMA, check_frame, describe_schema, normalise
from backtest.core.errors import DataQualityError

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("guard", "1")


def make_vendor(n_days=5, n_strikes=6, iv=0.25) -> pd.DataFrame:
    rows = []
    day0 = date(2023, 1, 3)
    for d in range(n_days):
        asof = day0 + timedelta(days=d)
        expiry = day0 + timedelta(days=40)
        for k in range(n_strikes):
            strike = 380 + k * 5
            for right in ("C", "P"):
                rows.append({
                    "asof": asof, "symbol": "QQQ", "expiry": expiry, "strike": float(strike),
                    "right": right, "bid": 4.0 + k * 0.1, "ask": 4.2 + k * 0.1,
                    "underlying_price": 400.0, "iv": iv,
                })
    return pd.DataFrame(rows)


class TestSchema:
    def test_schema_is_documented(self):
        table = describe_schema()
        assert {"required", "means", "if absent"} <= set(table.columns)
        assert "underlying_price" in table.index

    def test_every_required_column_explains_itself(self):
        for name, meta in SCHEMA.items():
            assert meta["means"] or meta["required"] == "no", \
                f"{name} has no description"


class TestNormalise:
    @pytest.mark.parametrize("raw,expect", [
        ("call", "C"), ("CALL", "C"), ("c", "C"), ("1", "C"),
        ("put", "P"), ("PUT", "P"), ("p", "P"), ("-1", "P"),
    ])
    def test_right_aliases(self, raw, expect):
        out = normalise(pd.DataFrame({"right": [raw]}))
        assert out["right"].iloc[0] == expect

    def test_unknown_right_raises(self):
        """An unrecognised value raises. Guessing the direction runs the whole
        backtest backwards."""
        with pytest.raises(DataQualityError, match="unrecognised values"):
            normalise(pd.DataFrame({"right": ["bullish"]}))

    def test_column_map_applied(self):
        out = normalise(pd.DataFrame({"trade_date": ["2023-01-03"]}),
                        {"trade_date": "asof"})
        assert "asof" in out.columns

    def test_scale_fixes_percent_iv(self):
        """With IV as a percentage, scale converts the unit and the warning goes
        away."""
        frame = make_vendor(iv=25.0)
        assert any("percentage" in w for w in check_frame(normalise(frame)).warnings)
        fixed = normalise(frame, scale={"iv": 0.01})
        assert fixed["iv"].max() == pytest.approx(0.25)
        assert not any("percentage" in w for w in check_frame(fixed).warnings)

    def test_scale_is_explicit_not_automatic(self):
        """**Nothing is converted automatically.** When the field itself is
        mislabelled, dividing by 100 produces a wrong result that looks right."""
        frame = make_vendor(iv=25.0)
        assert normalise(frame)["iv"].max() == pytest.approx(25.0)

    def test_scale_ignores_missing_column(self):
        normalise(make_vendor(), scale={"no_such_column": 0.01})

    def test_normalise_does_not_fill(self):
        """Normalising renames and unifies spellings and never fills in a value —
        filling in has to be seen first."""
        frame = make_vendor().drop(columns=["iv"])
        assert "iv" not in normalise(frame).columns


class TestChecker:
    """## Two checks that had no test at all

    ``check_frame`` flags a price offered **below intrinsic value** (arbitrage, so bad
    data -- a backtest buying there books a profit nobody could have taken) and a
    **locked market** (bid equal to ask, which the crossed-quote check misses and the
    fill model would happily price at zero cost).

    Both were written with a mutation entry naming ``TestBadDataIsFound``, a class that
    does not exist in this file. ``pytest`` exits non-zero for an id it cannot collect,
    and the mutation runner read that as "the mutation was caught" -- so both reported
    OK for months while nothing tested either. The runner now refuses an entry whose
    tests cannot be collected, which is how these two were found.
    """

    def test_a_price_below_intrinsic_value_is_flagged(self):
        """Against the **ask**: a price you could have paid below intrinsic is the
        arbitrage. A bid below it is ordinary -- nobody is obliged to bid fair value, so
        a check on the bid would flag healthy data all day."""
        frame = make_vendor()
        # A 380 call with the underlying at 400 is 20 intrinsic. Offered at 4.2.
        offered = check_frame(frame)
        assert any("below their intrinsic" in w for w in offered.warnings), (
            "a 380-strike call offered at 4.20 with the underlying at 400 is 15.80 of "
            "free money and nothing said so"
        )

    def test_a_bid_below_intrinsic_is_not_flagged(self):
        """The half that would make the check useless. A deep in-the-money contract with
        a wide spread has a bid below intrinsic most days."""
        import numpy as np

        frame = make_vendor()
        spot, strike = frame["underlying_price"], frame["strike"]
        intrinsic = np.where(frame["right"] == "C",
                             np.maximum(spot - strike, 0.0),
                             np.maximum(strike - spot, 0.0))
        # The ask sits above intrinsic for every row, which is what healthy data looks
        # like; the bid sits 1.50 **below** it, which is what a wide spread on a deep
        # in-the-money contract looks like and must not be flagged.
        frame.loc[:, "ask"] = intrinsic + 0.50
        frame.loc[:, "bid"] = intrinsic - 1.50
        frame.loc[frame["bid"] < 0, "bid"] = 0.05
        result = check_frame(frame)
        assert not any("below their intrinsic" in w for w in result.warnings), (
            "the check is reading the bid, so it flags every wide spread on a deep "
            "in-the-money contract"
        )

    def test_a_locked_market_is_flagged(self):
        """Bid equal to ask. Not crossed, so the crossed check misses it, and not
        tradable either -- a real quote has a spread, and a zero-width one is a feed
        artefact or a halt. The fill model would use the mid and report no cost."""
        frame = make_vendor()
        frame.loc[frame.index[:4], "ask"] = frame.loc[frame.index[:4], "bid"]
        result = check_frame(frame)
        assert any("locked market" in w for w in result.warnings)
        assert any("no cost" in w for w in result.warnings), (
            "the warning does not say what a locked quote costs a backtest, which is "
            "the only reason a reader would act on it"
        )

    def test_a_zero_zero_quote_is_not_called_a_locked_market(self):
        """A contract with no quote at all is a different thing, already flagged as a
        zero bid. Calling it locked would put the same row under two headings and
        overstate both counts."""
        frame = make_vendor()
        frame.loc[frame.index[:4], ["bid", "ask"]] = [0.0, 0.0]
        result = check_frame(frame)
        assert not any("locked market" in w for w in result.warnings)

    def test_clean_data_passes(self):
        assert check_frame(make_vendor()).ok

    def test_missing_required_column_is_fatal(self):
        r = check_frame(make_vendor().drop(columns=["underlying_price"]))
        assert not r.ok and any("underlying_price" in e for e in r.errors)

    def test_missing_optional_column_is_only_a_warning(self):
        r = check_frame(make_vendor())
        assert r.ok and any("volume" in w for w in r.warnings)

    @pytest.mark.req("1.quality")
    def test_duplicate_key_is_fatal(self):
        frame = make_vendor()
        r = check_frame(pd.concat([frame, frame.iloc[:1]], ignore_index=True))
        assert not r.ok and any("share a primary key" in e for e in r.errors)

    def test_crossed_quote_is_fatal(self):
        frame = make_vendor()
        frame.loc[0, "ask"] = frame.loc[0, "bid"] - 1.0
        r = check_frame(frame)
        assert not r.ok and any("ask < bid" in e for e in r.errors)

    def test_percent_iv_is_flagged(self):
        """IV as 25 rather than 0.25 is the commonest wiring error, and it finishes
        the run without raising."""
        r = check_frame(make_vendor(iv=25.0))
        assert any("percentage" in w for w in r.warnings)

    def test_flat_iv_surface_is_flagged(self):
        """IV that never varies means no skew and no term structure, so no
        option-side conclusion holds."""
        r = check_frame(make_vendor(iv=0.25))
        assert any("skew" in w for w in r.warnings)

    def test_report_summary_is_readable(self):
        text = check_frame(make_vendor()).summary(printout=False)
        assert "Data health check" in text and "rows" in text

    def test_empty_frame_is_fatal_not_silent(self):
        r = check_frame(pd.DataFrame())
        assert not r.ok and r.errors


class TestRegistry:
    def test_unknown_source_names_the_alternatives(self):
        from backtest.data.options.chain_registry import available, build_chain_source
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError, match="unknown data source"):
            build_chain_source("nosuch:x", None, None)
        assert "synthetic" in available() and "file" in available()

    def test_object_must_implement_protocol(self):
        from backtest.data.options.chain_registry import build_chain_source
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError, match="snapshot"):
            build_chain_source(object(), None, None)

    def test_duplicate_registration_refused(self):
        from backtest.data.options.chain_registry import register
        from backtest.core.errors import StartupError

        with pytest.raises(StartupError, match="already registered"):
            register("synthetic", lambda *a: None)


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


@kind("guard")
class TestTheFileSourceIsUsableOnRealVolumes:
    """A data path that works and is too slow to use is not a data path.

    Both findings here came from one attempt: build a vendor-shaped file from the
    fixture — different column names, IV as a percentage — and run a six-month backtest
    off it. It **timed out at ten minutes**, twice, for two separate reasons:

    - ``series()`` filtered the whole table with five boolean masks on every call, and
      the engine calls it once per open position per session
    - ``_prepare()`` called the vectorised ``greeks`` **once per row** through ``.iloc[i]``

    Together: 101 seconds to construct 208,000 rows and minutes to run. Now 1.9 seconds
    and 15 for 2.3 million.

    The thresholds below are deliberately loose — ten times the measured figure. What is
    being guarded is the **order of magnitude**, because the failure was not "a bit slow",
    it was "unusable", and a tight bound would go red on a slower machine for no reason.
    """

    @staticmethod
    def _vendor_file(market, chain, config, tmp_path, *, sessions=40):
        """A file with a vendor's column names and IV as a percentage."""
        rows = []
        for day in [d for d in market.calendar.sessions
                    if config.start <= d <= config.end][:sessions]:
            snap = chain.snapshot(day)
            if not len(snap):
                continue
            for _, r in snap.iterrows():
                rows.append({
                    "trade_date": r["asof"], "ticker": r["symbol"],
                    "exp_date": r["expiry"], "strike_px": r["strike"],
                    "call_put": r["right"], "best_bid": r["bid"],
                    "best_offer": r["ask"], "spot_px": r["underlying_price"],
                    "impl_vol_pct": float(r["iv"]) * 100.0,
                })
        path = tmp_path / "vendor.parquet"
        pd.DataFrame(rows).to_parquet(path)
        return path, len(rows)

    COLUMN_MAP = {
        "trade_date": "asof", "ticker": "symbol", "exp_date": "expiry",
        "strike_px": "strike", "call_put": "right", "best_bid": "bid",
        "best_offer": "ask", "spot_px": "underlying_price", "impl_vol_pct": "iv",
    }

    def test_construction_is_seconds_and_not_minutes(self, market, chain, config,
                                                     tmp_path):
        import time

        from backtest.data.options.adapters import FileChainSource

        path, count = self._vendor_file(market, chain, config, tmp_path)
        assert count > 100_000, "too few rows for this to say anything about volume"

        started = time.perf_counter()
        source = FileChainSource(path, market, config,
                                  column_map=self.COLUMN_MAP, scale={"iv": 0.01})
        took = time.perf_counter() - started
        assert took < 20.0, (
            f"{count:,} rows took {took:.1f}s to prepare. It was 101s when the greeks "
            f"were derived one row at a time; at that rate a backtest off a file is "
            f"unusable rather than slow"
        )
        assert len(source.snapshot(source._frame["asof"].iloc[0])) > 0

    def test_a_contract_lookup_does_not_scan_the_table(self, market, chain, config,
                                                       tmp_path):
        """``series()`` is called once per open position per session, so a full scan
        there is a backtest that does not finish."""
        import time

        from backtest.core.types import ContractId, Right
        from backtest.data.options.adapters import FileChainSource

        path, _ = self._vendor_file(market, chain, config, tmp_path)
        source = FileChainSource(path, market, config,
                                  column_map=self.COLUMN_MAP, scale={"iv": 0.01})
        row = source._frame.iloc[0]
        contract = ContractId(symbol=row["symbol"], expiry=row["expiry"],
                              strike=float(row["strike"]),
                              right=Right.CALL if row["right"] == "C" else Right.PUT)
        days = sorted(set(source._frame["asof"]))

        started = time.perf_counter()
        for _ in range(200):
            source.series(contract, days)
        took = time.perf_counter() - started
        assert took < 5.0, (
            f"200 contract lookups took {took:.1f}s. The engine makes one per open "
            f"position per session, so this is the difference between a backtest that "
            f"finishes and one that does not"
        )

    def test_a_derived_greek_equals_a_direct_call(self, market, chain, config, tmp_path):
        """The vectorised derivation has to agree with the formula, to the bit.

        Splitting the rows by right and calling `greeks` on arrays is a different
        computation from calling it per row, and "it is faster now" is not evidence that
        it is still right.
        """
        from backtest.core.types import Right
        from backtest.data.options.adapters import FileChainSource
        from backtest.data.options.pricing import greeks as bs_greeks

        path, _ = self._vendor_file(market, chain, config, tmp_path, sessions=5)
        source = FileChainSource(path, market, config,
                                  column_map=self.COLUMN_MAP, scale={"iv": 0.01})
        frame = source._frame
        checked = 0
        for _, row in frame.head(40).iterrows():
            tau = max((row["expiry"] - row["asof"]).days, 0) / 365.0
            if tau <= 0 or not np.isfinite(row["iv"]):
                continue
            expected = bs_greeks(
                float(row["underlying_price"]), float(row["strike"]), tau,
                config.fallback_risk_free_rate, float(row["iv"]), 0.0,
                Right.CALL if row["right"] == "C" else Right.PUT)
            for greek in ("delta", "gamma", "vega", "theta"):
                assert float(row[greek]) == pytest.approx(
                    float(np.asarray(expected[greek])), rel=1e-12, abs=1e-12), greek
            checked += 1
        assert checked >= 20, "too few rows checked for this to mean anything"

    def test_a_supplied_greek_is_not_overwritten(self, market, chain, config, tmp_path):
        """A vendor's delta and one I derived are not the same thing, and the derivation
        must fill gaps rather than replace the data."""
        from backtest.data.options.adapters import FileChainSource

        path, _ = self._vendor_file(market, chain, config, tmp_path, sessions=3)
        frame = pd.read_parquet(path)
        frame["delta"] = 0.4242                    # a value no formula would produce
        frame.to_parquet(path)

        source = FileChainSource(path, market, config,
                                  column_map=self.COLUMN_MAP, scale={"iv": 0.01})
        assert (source._frame["delta"] == 0.4242).all(), (
            "the supplied delta was overwritten by a derived one"
        )
        # The others were absent, so they are derived and the row says so.
        assert source._frame["gamma"].notna().any()
        assert source._frame["greeks_model_derived"].any()
