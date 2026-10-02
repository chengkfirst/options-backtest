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

from backtest.data.adapters import SCHEMA, check_frame, describe_schema, normalise
from backtest.errors import DataQualityError


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
    def test_clean_data_passes(self):
        assert check_frame(make_vendor()).ok

    def test_missing_required_column_is_fatal(self):
        r = check_frame(make_vendor().drop(columns=["underlying_price"]))
        assert not r.ok and any("underlying_price" in e for e in r.errors)

    def test_missing_optional_column_is_only_a_warning(self):
        r = check_frame(make_vendor())
        assert r.ok and any("volume" in w for w in r.warnings)

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
        from backtest.data.chain_registry import available, build_chain_source
        from backtest.errors import StartupError

        with pytest.raises(StartupError, match="unknown data source"):
            build_chain_source("nosuch:x", None, None)
        assert "synthetic" in available() and "file" in available()

    def test_object_must_implement_protocol(self):
        from backtest.data.chain_registry import build_chain_source
        from backtest.errors import StartupError

        with pytest.raises(StartupError, match="snapshot"):
            build_chain_source(object(), None, None)

    def test_duplicate_registration_refused(self):
        from backtest.data.chain_registry import register
        from backtest.errors import StartupError

        with pytest.raises(StartupError, match="already registered"):
            register("synthetic", lambda *a: None)
