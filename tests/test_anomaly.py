"""Anomaly scanning.

Both directions have to be tested: **what should fire does**, and **what should not
fire never does**. The second matters more — an "insight" forced out of nothing is
noise, and noise is what makes real warnings get skipped.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.research.anomaly import Finding, scan

from tests.kinds import labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("research", "6")


def trades(n=100, seed=0, **over) -> pd.DataFrame:
    """A thoroughly unremarkable trade log: no rule should fire on it."""
    rng = np.random.default_rng(seed)
    entry = pd.date_range("2018-01-03", periods=n, freq="9D")
    # P&L alternates in order so the years contribute about equally — over 100 trades
    # a random normal easily lets one year take most of it, which would make the
    # fixture itself anomalous and defeat the "silent when normal" test.
    pnl = np.where(np.arange(n) % 3 == 0, 400.0, -150.0) + rng.normal(0, 20, n)
    frame = pd.DataFrame({
        "entry_date": entry,
        "exit_date": entry + pd.Timedelta(days=5),      # non-overlapping
        "net_pnl": pnl,
        "exit_reason": np.resize(["take_profit", "stop_loss", "time_exit"], n),
    })
    for k, v in over.items():
        frame[k] = v
    return frame


class TestQuietWhenNormal:
    def test_normal_result_finds_nothing(self):
        """**The most important one**: a normal result must report nothing at all."""
        assert scan(trades()) == []

    def test_empty_input(self):
        assert scan(pd.DataFrame()) == []

    def test_small_sample_does_not_trigger_count_rules(self):
        """With too small a sample the rules stay silent rather than concluding from
        five trades."""
        assert scan(trades(n=8)) == []

    def test_a_broken_rule_does_not_kill_the_scan(self):
        """One rule failing to compute must not stop the rest."""
        t = trades()
        t["exit_date"] = "not a date"        # make the overlap rule uncomputable
        assert scan(t) is not None

    def test_a_broken_rule_is_reported_rather_than_swallowed(self):
        """The old version of this accepted silent failure -- "it is enough that it does
        not raise".

        That contradicts the one principle this project is built on. A scan that came back
        with three findings when a rule could not run looks exactly like a scan where that
        rule found nothing, and "found nothing" is what a reader acts on.
        """
        t = trades()
        t["exit_date"] = "not a date"
        found = scan(t)
        failures = [f for f in found if f.kind == "rule_failed"]
        assert failures, "a rule could not run and the scan said nothing about it"
        assert failures[0].severity == "high"
        assert "has not been checked" in failures[0].detail

    def test_a_clean_scan_reports_no_failures(self):
        """The other half: the failure finding must not fire when nothing is broken."""
        assert not [f for f in scan(trades()) if f.kind == "rule_failed"]


class TestRulesFire:
    def kinds(self, t, **kw) -> set[str]:
        return {f.kind for f in scan(t, **kw)}

    def test_single_trade_dominates(self):
        t = trades(n=40, seed=1)
        t.loc[0, "net_pnl"] = t["net_pnl"].clip(lower=0).sum() * 3 + 5000
        assert "single_trade" in self.kinds(t)

    def test_one_year_carries(self):
        t = trades(n=90, seed=2)
        t["net_pnl"] = -50.0
        mask = pd.to_datetime(t["exit_date"]).dt.year == 2019
        t.loc[mask, "net_pnl"] = 900.0
        assert "one_year" in self.kinds(t)

    def test_edge_decay(self):
        t = trades(n=120, seed=3)
        t["net_pnl"] = [300.0] * 60 + [-300.0] * 60
        assert "decay" in self.kinds(t)

    def test_dead_exit_when_reason_missing(self):
        """The take-profit never fired — that level is decoration."""
        t = trades(n=60, seed=4)
        t["exit_reason"] = ["stop_loss"] * 30 + ["time_exit"] * 30
        assert "dead_exit" in self.kinds(t)

    def test_dead_exit_when_reason_is_rare(self):
        t = trades(n=100, seed=5)
        t["exit_reason"] = (["take_profit"] * 2 + ["stop_loss"] * 49
                            + ["time_exit"] * 49)
        assert "dead_exit" in self.kinds(t)

    def test_costs_eat_edge(self):
        found = self.kinds(trades(), costs={"fees_pct_of_gross": 0.55, "total": 9000})
        assert "costs" in found

    def test_costs_quiet_when_reasonable(self):
        assert "costs" not in self.kinds(trades(), costs={"fees_pct_of_gross": 0.12})

    def test_overlap(self):
        t = trades(n=60, seed=6)
        t["exit_date"] = pd.to_datetime(t["entry_date"]) + pd.Timedelta(days=200)
        assert "overlap" in self.kinds(t)

    def test_too_good_for_the_sample(self):
        assert "too_good" in self.kinds(trades(n=50), metrics={"sharpe": 3.4})

    def test_too_good_quiet_with_enough_trades(self):
        assert "too_good" not in self.kinds(trades(n=400), metrics={"sharpe": 3.4})

    def test_loss_concentration(self):
        t = trades(n=60, seed=7)
        t["net_pnl"] = [-100.0] * 40 + [100.0] * 20
        t["exit_reason"] = ["stop_loss"] * 40 + ["take_profit"] * 20
        assert "loss_concentration" in self.kinds(t)


class TestOutput:
    def test_high_severity_comes_first(self):
        t = trades(n=120, seed=8)
        t["net_pnl"] = [300.0] * 60 + [-300.0] * 60          # decay → high
        t["exit_date"] = pd.to_datetime(t["entry_date"]) + pd.Timedelta(days=300)  # overlap → note
        found = scan(t)
        assert found and found[0].severity == "high"

    def test_every_finding_says_what_and_why(self):
        t = trades(n=120, seed=9)
        t["net_pnl"] = [300.0] * 60 + [-300.0] * 60
        for f in scan(t):
            assert f.headline and len(f.detail) > 20
            assert f.severity in ("high", "note")
