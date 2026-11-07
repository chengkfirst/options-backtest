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

    ## Why these two carry ``@pytest.mark.timing``

    The paragraph above turned out to be right about the risk and wrong about the size of
    it. On a GitHub two-core runner the construction measured **40.9s** and **21.1s** on
    two runs of near-identical code, against a 20s bound — so the ten-times headroom is
    not enough for hardware that is roughly twenty times slower than the machine the
    figure was taken on. The branch's CI went red and green alternately, on the runner it
    happened to land on rather than on anything in the diff.

    **The bound is not loosened**, because it is the only thing that states what
    "usable" meant, and raising it until CI is quiet would make it state nothing. Instead
    the two tests are marked, and the marker decides where they are a signal:

    - **locally they run exactly as before** — this is the machine the 1.9s and 101s
      figures were measured on, so a regression here is a regression in the code
    - **the blocking CI job deselects them** (``PYTEST_ADDOPTS: -m "not timing"``), since
      a red light that reports the runner's load is a red light nobody reads
    - **a non-blocking CI step runs them and prints the measured durations**, so the
      numbers stay visible and a real tenfold regression is still obvious in the log

    What is given up is stated plainly: a performance regression that only CI would catch
    no longer fails the build. That is the right trade only because the figure is
    machine-dependent and the guard is an order-of-magnitude one; it would be the wrong
    trade for any assertion about a result.
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

    @pytest.mark.timing
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

    @pytest.mark.timing
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


@kind("guard")
class TestWhichIVModelSolvedIt:
    """The register's own group-1 trade-off: "American vs European IV model — European
    is faster; American is accurate for equity and ETF options. **Compare IV and Greeks
    on a sample.**"

    So both exist, European is the default, and every row says which solved it.

    ## Why European is the default and not American

    Measured, not argued. One American solve is a bisection over a 200-step tree —
    **13 milliseconds**. On 200,000 rows that is 43 minutes, and screening does not help:
    the put case applies whenever rates are positive, which is every put.

    The bias is one-sided and is stated rather than hidden. An American option is worth
    at least its European counterpart, so a European solve returns an IV that is too
    **low** wherever the early-exercise premium is real.
    """

    @staticmethod
    def _file(market, chain, config, tmp_path, *, sessions=3):
        """Rows with **no** iv column, so the solve has to run."""
        rows = []
        for day in [d for d in market.calendar.sessions
                    if config.start <= d <= config.end][:sessions]:
            snap = chain.snapshot(day)
            if not len(snap):
                continue
            # **Across the strikes, not the first thirty.** `snapshot` comes back
            # ordered, so `head(30)` is thirty deep in-the-money calls -- every one of
            # them almost all intrinsic, and every one excluded below as numerically
            # hopeless. The test then had nothing left to compare and said so, which is
            # the right failure and was still my sampling rather than the code.
            for _, r in snap.iloc[::max(len(snap) // 40, 1)].iterrows():
                rows.append({
                    "asof": r["asof"], "symbol": r["symbol"], "expiry": r["expiry"],
                    "strike": r["strike"], "right": r["right"], "bid": r["bid"],
                    "ask": r["ask"], "underlying_price": r["underlying_price"],
                })
        path = tmp_path / "noiv.parquet"
        pd.DataFrame(rows).to_parquet(path)
        return path

    def test_the_solved_iv_reprices_the_mid_at_the_dividend_yield_it_was_given(
            self, config, tmp_path):
        """**The round trip, and the only test that can catch a zeroed dividend.**

        Price the row back with the IV that was solved for it, at the dividend yield the
        market reported. If the solve used a different yield -- 0.0, as it did for a long
        time -- the re-priced value misses the mid, and it misses by more the deeper the
        strike and the longer the tenor.

        A test that only checks the IV is *plausible* cannot see this: a zero-dividend
        solve returns a perfectly reasonable-looking number that is simply the wrong one.

        The yield comes from a **stub market with a deliberately large one** rather than
        from the fixture, which pays nothing inside any expiry a near-dated contract
        reaches -- so the fixture cannot tell the two solves apart, and a test built on
        it would pass either way. 4% separates them by far more than the solve's own
        tolerance.
        """
        from backtest.core.config import CALENDAR_DAYS_PER_YEAR
        from backtest.core.types import Right
        from backtest.data.options.adapters import FileChainSource
        from backtest.data.options.pricing import bs_price

        YIELD = 0.04

        class _Market:
            """Only one method of the market is used by the solve."""

            def dividend_yield(self, day, expiry, spot):
                return YIELD

        asof = date(2023, 1, 3)
        expiry = date(2023, 12, 15)          # long enough for the dividend to matter
        spot = 300.0
        rows = []
        for strike in (240.0, 270.0, 300.0, 330.0, 360.0):
            for right in ("C", "P"):
                # A mid priced **at** a known vol with the same yield, so the solve has a
                # well-posed problem and the round trip has a target.
                tau = (expiry - asof).days / CALENDAR_DAYS_PER_YEAR
                fair = bs_price(spot, strike, tau,
                                config.fallback_risk_free_rate, 0.25, q=YIELD,
                                right=Right(right))
                rows.append({"asof": asof, "symbol": config.symbol, "expiry": expiry,
                             "strike": strike, "right": right,
                             "bid": round(fair - 0.05, 4), "ask": round(fair + 0.05, 4),
                             "underlying_price": spot})
        path = tmp_path / "dividend.parquet"
        pd.DataFrame(rows).to_parquet(path)

        source = FileChainSource(path, market=_Market(), config=config)
        snap = source.snapshot(asof)
        solved = snap[snap["iv"].notna()]
        assert len(solved) >= 8, f"only {len(solved)} of {len(rows)} rows solved"

        tau = (expiry - asof).days / CALENDAR_DAYS_PER_YEAR
        for _, row in solved.iterrows():
            mid = (float(row["bid"]) + float(row["ask"])) / 2.0
            back = bs_price(spot, float(row["strike"]), tau,
                            config.fallback_risk_free_rate, float(row["iv"]), q=YIELD,
                            right=Right(row["right"]))
            assert abs(back - mid) < max(0.01, mid * 0.002), (
                f"the IV {row['iv']:.4f} solved for {row['right']} {row['strike']} "
                f"reprices to {back:.4f} against a mid of {mid:.4f} at the {YIELD:.1%} "
                f"yield the market reported. The solve and this check disagree about an "
                f"input, and the dividend is the one that was hardcoded to zero"
            )

        # And the IV recovered is the one the mid was built at, which is the stronger
        # statement: a solve at the wrong yield would be self-consistent with itself and
        # still return the wrong number.
        assert abs(float(solved["iv"].median()) - 0.25) < 0.01, (
            f"the mids were priced at a vol of 0.25 and the median solve came back "
            f"{float(solved['iv'].median()):.4f}"
        )

    def test_the_default_is_european(self):
        """Read from the signature, which is where a default lives."""
        import inspect

        from backtest.data.options.adapters import FileChainSource

        parameter = inspect.signature(FileChainSource.__init__).parameters["iv_model"]
        assert parameter.default == "european"

    def test_every_solved_row_is_flagged_with_its_model(self, market, chain, config,
                                                        tmp_path):
        from backtest.data.options.adapters import FileChainSource

        path = self._file(market, chain, config, tmp_path)
        european = FileChainSource(path, market, config)
        flags = european._frame["quality_flags"].astype(str)
        assert flags.str.contains("iv_european").all(), (
            "rows were solved and nothing says which model did it"
        )
        assert not flags.str.contains("iv_american").any()

    def test_the_american_solve_can_be_asked_for_and_is_flagged(self, market, chain,
                                                                config, tmp_path):
        from backtest.data.options.adapters import FileChainSource

        path = self._file(market, chain, config, tmp_path, sessions=1)
        american = FileChainSource(path, market, config, iv_model="american")
        flags = american._frame["quality_flags"].astype(str)
        assert flags.str.contains("iv_american").all()

    def test_the_two_models_disagree_in_the_direction_theory_requires(
            self, market, chain, config, tmp_path):
        """**The sample comparison the register asks for**, as a test.

        An American option is worth at least its European counterpart, so solving one
        price with a European model needs *more* volatility. Asserted as a direction
        rather than a size: how much more depends on the strike, the tenor and the rate.
        """
        from backtest.data.options.adapters import FileChainSource

        path = self._file(market, chain, config, tmp_path, sessions=1)
        european = FileChainSource(path, market, config)._frame
        american = FileChainSource(path, market, config,
                                    iv_model="american")._frame

        # **Only where both solves are meaningful.**
        #
        # A deep in-the-money option's mid is almost all intrinsic, so the little that is
        # left carries the whole of σ and the solve becomes numerically hopeless in both
        # models: one row here returns 116% from the European solve and 116% from the
        # American, differing in the fourth decimal and in the wrong direction. That is
        # not a theory violation, it is two solvers failing at the same place -- and
        # asserting the direction there would be asserting the shape of a rounding error.
        #
        # Rows above 150% volatility are excluded on exactly that ground, and the test
        # says so rather than widening a tolerance until it passes.
        pairs, hopeless = 0, 0
        for (_, eu), (_, am) in zip(european.iterrows(), american.iterrows()):
            if not (np.isfinite(eu["iv"]) and np.isfinite(am["iv"])):
                continue
            # The real criterion is **how much of the mid is time value**, not how
            # high the IV came out. A high IV is a symptom; a mid that is 99% intrinsic
            # is the cause, and my first attempt excluded on the symptom and let the
            # same row back in at 1.157 against a threshold of 1.5.
            #
            # Below 2% time value the solve is reading σ out of rounding: the two models
            # agreed to four decimals there and differed in the fifth, in the wrong
            # direction, which is two solvers failing at the same place rather than a
            # theory violation.
            mid = (float(eu["bid"]) + float(eu["ask"])) / 2.0
            spot, strike = float(eu["underlying_price"]), float(eu["strike"])
            intrinsic = (max(spot - strike, 0.0) if eu["right"] == "C"
                         else max(strike - spot, 0.0))
            if mid <= 0 or (mid - intrinsic) / mid < 0.02:
                hopeless += 1
                continue
            # **The tolerance is the tree's, and it is why one is needed at all.**
            #
            # The American solve bisects a 200-step tree to a price tolerance of 1e-4,
            # so its IV carries the tree's own discretisation error -- measured at about
            # 0.0006 on a no-dividend call, where the two models price **identically**
            # and the only difference possible is numerical. Asserting the direction to
            # 1e-6 was asserting that a discrete tree equals a closed form.
            #
            # 0.005 is an order of magnitude above that error and well below any real
            # early-exercise premium, so a genuine violation still fails.
            assert eu["iv"] >= am["iv"] - 0.005, (
                f"the European IV {eu['iv']:.4f} is below the American {am['iv']:.4f} "
                f"on {eu['right']} {eu['strike']} by more than the tree's own "
                f"discretisation error. An American option is worth at least a European "
                f"one, so the European solve must reach for more volatility, never less"
            )
            pairs += 1
        assert pairs >= 20, (
            f"only {pairs} comparable rows ({hopeless} excluded as numerically "
            f"hopeless), which is too few to mean anything"
        )

    def test_an_unknown_model_is_refused_with_the_cost(self, market, chain, config,
                                                       tmp_path):
        from backtest.core.errors import StartupError

        from backtest.data.options.adapters import FileChainSource

        path = self._file(market, chain, config, tmp_path, sessions=1)
        with pytest.raises(StartupError) as caught:
            FileChainSource(path, market, config, iv_model="binomial")
        text = str(caught.value)
        assert "european" in text and "american" in text
        assert "43 minutes" in text, (
            "the refusal does not say what the American model costs, which is the one "
            "thing someone choosing between them needs"
        )

    def test_a_supplied_iv_is_not_solved_at_all(self, market, chain, config, tmp_path):
        """Neither model runs when the vendor supplied the column, which is the reason
        to supply it: faster, and the vendor's solve is also where a bad quote became an
        absent IV rather than a wrong one."""
        from backtest.data.options.adapters import FileChainSource

        path = self._file(market, chain, config, tmp_path, sessions=1)
        frame = pd.read_parquet(path)
        frame["iv"] = 0.2222
        frame.to_parquet(path)

        source = FileChainSource(path, market, config, iv_model="american")
        assert (source._frame["iv"] == 0.2222).all()
        flags = source._frame["quality_flags"].astype(str)
        assert not flags.str.contains("iv_american").any()
        assert not flags.str.contains("iv_european").any()
