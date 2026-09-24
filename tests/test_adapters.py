"""自有数据接入：体检、列名映射、与合成链的可互换性。

这块的风险不是"读不进来"——读不进来会报错，很明显。真正的风险是**读进来了但读错了**：
right 列的 1/-1 被当成字符串、IV 是百分数当成小数、同一张合约一天出现两行。
这些都能让回测正常跑完并给出一个完全可信的错误结果。所以测试重点是**错的时候会不会说出来**。
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
        assert {"必需", "含义", "缺了会怎样"} <= set(table.columns)
        assert "underlying_price" in table.index

    def test_every_required_column_explains_itself(self):
        for name, meta in SCHEMA.items():
            assert meta["含义"] or meta["必需"] == "否", f"{name} 没有含义说明"


class TestNormalise:
    @pytest.mark.parametrize("raw,expect", [
        ("call", "C"), ("CALL", "C"), ("c", "C"), ("1", "C"),
        ("put", "P"), ("PUT", "P"), ("p", "P"), ("-1", "P"),
    ])
    def test_right_aliases(self, raw, expect):
        out = normalise(pd.DataFrame({"right": [raw]}))
        assert out["right"].iloc[0] == expect

    def test_unknown_right_raises(self):
        """认不出来就报错。猜一个方向会让整个回测做反。"""
        with pytest.raises(DataQualityError, match="认不出来的取值"):
            normalise(pd.DataFrame({"right": ["看涨"]}))

    def test_column_map_applied(self):
        out = normalise(pd.DataFrame({"trade_date": ["2023-01-03"]}),
                        {"trade_date": "asof"})
        assert "asof" in out.columns

    def test_normalise_does_not_fill(self):
        """规范化只改名与统一写法，绝不补值——补值必须先被看见。"""
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
        assert not r.ok and any("主键重复" in e for e in r.errors)

    def test_crossed_quote_is_fatal(self):
        frame = make_vendor()
        frame.loc[0, "ask"] = frame.loc[0, "bid"] - 1.0
        r = check_frame(frame)
        assert not r.ok and any("ask < bid" in e for e in r.errors)

    def test_percent_iv_is_flagged(self):
        """IV 写成 25 而不是 0.25 是最常见的接入错误，且不报错照样跑完。"""
        r = check_frame(make_vendor(iv=25.0))
        assert any("百分数" in w for w in r.warnings)

    def test_flat_iv_surface_is_flagged(self):
        """IV 没有变化意味着没有偏斜与期限结构，期权侧结论不成立。"""
        r = check_frame(make_vendor(iv=0.25))
        assert any("偏斜" in w for w in r.warnings)

    def test_report_summary_is_readable(self):
        text = check_frame(make_vendor()).summary(printout=False)
        assert "数据体检" in text and "行" in text

    def test_empty_frame_is_fatal_not_silent(self):
        r = check_frame(pd.DataFrame())
        assert not r.ok and r.errors


class TestRegistry:
    def test_unknown_source_names_the_alternatives(self):
        from backtest.data.chain_registry import available, build_chain_source
        from backtest.errors import StartupError

        with pytest.raises(StartupError, match="未知的数据源"):
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

        with pytest.raises(StartupError, match="已经注册"):
            register("synthetic", lambda *a: None)
