"""Event studies: after a fall of a given size, what usually happens.

The danger here is not an arithmetic error, it is **a wrong number that looks
precise**: counting one bear market as hundreds of daily observations, reporting the
conditional probability with no baseline, or computing a horizon over however many
days exist. These tests are about whether any of that happens.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.research.events import (
    baseline,
    effective_sample,
    find_corrections,
    measure,
    study,
)


def series(values, start="2000-01-03") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def ramp(up_days, down_days, peak=100.0, trough=80.0):
    """Rise to peak, fall to trough, rise back.

    ``down_days`` has to be short enough — a rolling reference high drifts down with a
    slow decline, and a slow fall is not a correction under this convention (see
    test_slow_grind_is_not_a_correction).
    """
    return ([*np.linspace(peak * 0.8, peak, up_days),
             *np.linspace(peak, trough, down_days),
             *np.linspace(trough, peak * 1.1, up_days)])


class TestFindCorrections:
    def test_counts_episodes_not_days(self):
        """A correction lasting tens of days is one episode, not tens of
        observations."""
        s = series(ramp(300, 25))
        eps = find_corrections(s, 0.10, reference_window=60)
        assert len(eps) == 1

    def test_slow_grind_is_not_a_correction(self):
        """**A property of the method**: the rolling reference high drifts down with a
        slow decline.

        A slow fall from 100 to 80 over two hundred days is at most about -7% deep on a
        60-day rolling basis and never crosses the threshold. That is not a bug — a
        correction is by definition a rapid fall from a recent high. But anyone using
        this tool has to know that a slow bear market does not appear in the results.
        """
        s = series(ramp(300, 200))
        assert find_corrections(s, 0.10, reference_window=60) == []

    def test_new_episode_needs_a_new_peak(self):
        """Two touches under the same prior high are one event."""
        # Rise -> cross -> small rebound (no new high) -> cross again
        vals = [*np.linspace(80, 100, 120), *np.linspace(100, 88, 20),
                *np.linspace(88, 95, 10), *np.linspace(95, 87, 15)]
        eps = find_corrections(series(vals), 0.10, reference_window=60)
        assert len(eps) == 1, f"one prior high should give one episode, got {len(eps)}"

    def test_threshold_must_be_positive_fraction(self):
        with pytest.raises(ValueError, match="threshold"):
            find_corrections(series([1, 2, 3]), -0.1)

    def test_records_reference_high(self):
        eps = find_corrections(series(ramp(200, 120)), 0.10, reference_window=60)
        assert eps and eps[0].ref_high > eps[0].price
        assert eps[0].drawdown <= -0.10

    def test_no_episode_when_never_falls(self):
        assert find_corrections(series(np.linspace(100, 200, 300)), 0.10) == []


class TestMeasure:
    def test_excludes_windows_past_data_end(self):
        """A horizon without enough data **does not appear**, rather than being
        computed over however many days exist."""
        s = series(ramp(200, 100))
        eps = find_corrections(s, 0.10, reference_window=60)
        out = measure(s, eps, horizons_months=(1, 120))
        assert (out["horizon_months"] == 1).any()
        assert not (out["horizon_months"] == 120).any()

    def test_path_measures_are_not_endpoint_measures(self):
        """"fell another 10% on the way" is about the path; the endpoint misses it."""
        # After the crossing, fall deep then return to the start: flat at the end,
        # but it fell on the way.
        vals = [*np.linspace(80, 100, 120), *np.linspace(100, 88, 20),
                *np.linspace(88, 70, 20), *np.linspace(70, 88, 20)]
        s = series(vals)
        eps = find_corrections(s, 0.10, reference_window=60)
        out = measure(s, eps, horizons_months=(2,))
        assert out["touched_minus_10"].iloc[0]
        # The endpoint is near the start but the path fell a long way — the two have
        # to be measured separately.
        assert abs(out["end_return"].iloc[0]) < 0.05
        assert out["max_drawdown_after"].iloc[0] < -0.15


class TestIntradayConvention:
    """Detect on the close, measure the pain intraday. Mixing the two biases the
    conclusions systematically."""

    def bars(self):
        """Mild closes with intraday spikes down: the close never breaks 5%, the
        intraday low breaks 12%."""
        # The spike stretch has to be long enough, or the end-of-range censoring rule
        # correctly excludes it (that rule has its own test).
        close = [*np.linspace(80, 100, 120), *[97.0] * 40]
        low = [*np.linspace(80, 100, 120), *[88.0] * 40]     # intraday spikes down
        idx = pd.bdate_range("2000-01-03", periods=len(close))
        return pd.DataFrame({"close": close, "high": close, "low": low}, index=idx)

    def test_event_detection_ignores_intraday_spikes(self):
        """An intraday dip that recovers by the close is not a correction."""
        assert find_corrections(self.bars(), 0.10, reference_window=60) == []

    def test_pain_measures_use_intraday_low(self):
        """A stop is hit intraday. On closes alone this would say it never fell
        10%."""
        from backtest.research.events import Episode

        b = self.bars()
        ep = Episode(b.index[120], 97.0, 100.0, b.index[119], -0.03, None)
        with_low = measure(b, [ep], horizons_months=(1,))
        close_only = measure(b["close"], [ep], horizons_months=(1,))
        assert with_low["touched_minus_5"].iloc[0]
        assert not close_only["touched_minus_5"].iloc[0]
        assert with_low["max_drawdown_after"].iloc[0] < close_only["max_drawdown_after"].iloc[0]

    def test_close_only_input_is_flagged(self):
        """With only closes the basis has to be stated, not left to look complete."""
        s = series(ramp(300, 25))
        assert study(s, 0.10)["intraday"] is False
        assert "understated" in study(s, 0.10)["basis"]

    def test_ohlc_input_is_flagged_too(self):
        vals = ramp(300, 25)
        idx = pd.bdate_range("2000-01-03", periods=len(vals))
        b = pd.DataFrame({"close": vals, "high": vals, "low": vals}, index=idx)
        assert study(b, 0.10)["intraday"] is True

    def test_missing_close_column_raises(self):
        with pytest.raises(ValueError, match="close"):
            find_corrections(pd.DataFrame({"high": [1.0], "low": [1.0]}), 0.10)


class TestBaseline:
    def test_baseline_exists_for_each_horizon(self):
        b = baseline(series(np.linspace(100, 200, 600)), horizons_months=(1, 3))
        assert set(b["horizon_months"]) == {1, 3}

    def test_rising_market_has_high_unconditional_rate(self):
        """Exactly why the baseline is needed: in a one-way rise the unconditional
        probability is already near 1."""
        b = baseline(series(np.linspace(100, 300, 600)), horizons_months=(3,))
        assert b["up"].iloc[0] > 0.95


class TestEffectiveSample:
    def test_overlapping_episodes_collapse(self):
        """With twelve-month windows overlapping heavily, the effective sample is far
        below the nominal one."""
        from backtest.research.events import Episode

        eps = [Episode(pd.Timestamp(f"2020-0{m}-01"), 100.0, 120.0,
                       pd.Timestamp("2019-12-01"), -0.17, None) for m in (1, 2, 3, 4)]
        assert effective_sample(eps, 12) == 1
        # A one-month window is 30 days: 1 Jan -> 31 Jan, so 1 Feb passes;
        # 1 Feb + 30 = 2 Mar, so 1 Mar falls inside and is swallowed.
        assert effective_sample(eps, 1) == 3

    def test_empty(self):
        assert effective_sample([], 12) == 0


class TestStudy:
    @pytest.fixture(scope="class")
    def result(self):
        rng = np.random.default_rng(7)
        # A random walk with drift, long enough to produce several corrections.
        steps = rng.normal(0.0004, 0.014, 4000)
        return study(series(100 * np.exp(np.cumsum(steps))), 0.10)

    def test_table_pairs_conditional_with_baseline(self):
        """**The conditional probability must sit beside the baseline**; reporting only
        the former is self-deception."""
        rng = np.random.default_rng(7)
        s = series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.014, 4000))))
        t = study(s, 0.10)["table"]
        assert {"up", "baseline up", "excess"} <= set(t.columns)

    def test_excess_is_the_difference(self, result):
        t = result["table"]
        usable = t[t["up"].notna()]
        for _, r in usable.iterrows():
            assert r["excess"] == pytest.approx(r["up"] - r["baseline up"])

    def test_reports_effective_sample(self, result):
        assert (result["table"]["effective"] <= result["table"]["sample"]).all()

    def test_tiny_sample_says_so_bluntly(self):
        """Three observations should say "this is not statistics, it is 3 stories"."""
        from backtest.research.events import _sample_warning

        assert "3 stories" in _sample_warning(3, 3)
        assert _sample_warning(40, 30) == ""

    def test_reports_what_was_excluded(self, result):
        assert set(result["excluded_by_data_end"]) == {1, 3, 6, 12}
