"""Cleaning reaches the run fingerprint, and a chain that cannot clean says so.

## The document's second data rule

"Cleaning, adjustment and IV calculation are replayable: same raw data and rule version
give the same result." Replayable was already true — the rules are pure functions of the
frame and nothing writes a price. **Checkable was not**: the rule set never reached the run
id, so a run that dropped every crossed quote and a run that kept them had one fingerprint
between them, and a sensitivity test over cleaning — which the register asks for by name —
would have reported one run repeated.

Sixth time this class of gap has appeared in this project: something that moves the result
reaching the engine without reaching the id.

## And the silent half

`chain={"kind":"synthetic","clean":[...]}` accepted the rules and used none of them. That
is the failure the project refuses everywhere else — a researcher who believes a rule is in
force and is reading the result without it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from backtest.core.errors import StartupError
from tests.kinds import kind, labels

pytestmark = labels("guard", "1")


def _file(market, chain, config, tmp_path):
    """A few sessions of the synthetic chain written out as a file source, so the
    cleaning stage has real rows to act on."""
    rows = []
    for day in [d for d in market.calendar.sessions
                if config.start <= d <= config.end][:4]:
        snap = chain.snapshot(day)
        for _, r in snap.iloc[::max(len(snap) // 20, 1)].iterrows():
            rows.append({"asof": r["asof"], "symbol": r["symbol"], "expiry": r["expiry"],
                         "strike": r["strike"], "right": r["right"], "bid": r["bid"],
                         "ask": r["ask"], "iv": r["iv"],
                         "underlying_price": r["underlying_price"]})
    path = tmp_path / "chain.parquet"
    pd.DataFrame(rows).to_parquet(path)
    return path


class TestTheRuleSetReachesTheRunId:
    def test_the_version_is_the_declaration_not_the_matcher(self):
        """Name, action and reason — a function object has no stable identity across
        processes, so hashing the matcher would make the id depend on where it was built.
        The commit hash in the fingerprint covers an edit to a matcher's body."""
        from backtest.data.options.clean import Cleaning, crossed_quote, no_quote

        empty = Cleaning()
        assert empty.version() == ()

        one = Cleaning(rules=(crossed_quote(),))
        two = Cleaning(rules=(crossed_quote(), no_quote()))
        assert len(one.version()) == 1 and len(two.version()) == 2
        assert one.version() != two.version()
        for name, action, reason in two.version():
            assert name and action and reason, (
                "a rule in the version with no reason would make two different rule sets "
                "hash alike"
            )

    def test_the_order_of_the_rules_is_part_of_the_version(self):
        """It decides attribution: a row matching two rules is acted on by the first, and
        the report says which. Two orders are two rule sets."""
        from backtest.data.options.clean import Cleaning, crossed_quote, no_quote

        assert (Cleaning(rules=(crossed_quote(), no_quote())).version()
                != Cleaning(rules=(no_quote(), crossed_quote())).version())

    @kind("limit")
    def test_two_runs_differing_only_in_cleaning_do_not_share_an_id(
            self, market, chain, config, tmp_path):
        from backtest.api.sdk import run

        path = _file(market, chain, config, tmp_path)
        base = dict(symbol=config.symbol, start=config.start, end=config.end,
                    entry="close < sma_20 * 1.02",
                    select={"right": "call", "dte_lo": 7, "dte_hi": 60, "delta_lo": 0.2, "delta_hi": 0.6},
                    exit={"hold_days": 10, "dte_floor": 3},
                    engine="event_driven", sizing={"method": "fixed"}, quantity=1)

        plain = run(**(base | {"chain": {"kind": "file", "path": str(path)}}))
        cleaned = run(**(base | {"chain": {"kind": "file", "path": str(path),
                                            "clean": ["crossed_quote", "no_quote"]}}))
        one = run(**(base | {"chain": {"kind": "file", "path": str(path),
                                        "clean": ["no_quote"]}}))

        ids = {plain.manifest()["run_id"], cleaned.manifest()["run_id"],
               one.manifest()["run_id"]}
        assert len(ids) == 3, (
            "no cleaning, one rule and two rules produced fewer than three run ids, so "
            "the rule set is not in the fingerprint"
        )

    @kind("limit")
    def test_the_data_version_names_the_rules_a_reader_can_see(
            self, market, chain, config, tmp_path):
        """The id alone says two runs differ; the data version says how."""
        from backtest.api.sdk import run

        path = _file(market, chain, config, tmp_path)
        result = run(symbol=config.symbol, start=config.start, end=config.end,
                     entry="close < sma_20 * 1.02",
                     select={"right": "call", "dte_lo": 7, "dte_hi": 60, "delta_lo": 0.2, "delta_hi": 0.6},
                     exit={"hold_days": 10, "dte_floor": 3},
                     engine="event_driven", sizing={"method": "fixed"}, quantity=1,
                     chain={"kind": "file", "path": str(path),
                            "clean": ["crossed_quote"]})
        assert "crossed_quote" in result.manifest()["data_version"]


class TestTheSyntheticChainRefusesWhatItCannotDo:
    def test_cleaning_rules_on_a_synthetic_chain_are_refused(self):
        """It priced every row itself from Black-Scholes, so there are no crossed quotes
        and nothing below intrinsic. A rule set here reports zero rows touched and reads
        as "the data was clean"."""
        from backtest.data.options.chain_registry import build_chain_source

        with pytest.raises(StartupError) as caught:
            build_chain_source({"kind": "synthetic", "clean": ["crossed_quote"]},
                               None, None)
        assert "clean" in str(caught.value)
        assert "file" in str(caught.value), (
            "the refusal should say where cleaning does belong, or it is a dead end"
        )

    def test_any_other_option_is_refused_too(self, market, config):
        """Not a special case for `clean`: the source used **none** of what it was
        handed, so every key was silently dropped."""
        from backtest.data.options.chain_registry import build_chain_source

        with pytest.raises(StartupError, match="takes no options"):
            build_chain_source({"kind": "synthetic", "scale": {"iv": 0.01}},
                               market, config)

    def test_the_plain_synthetic_chain_still_builds(self, market, config):
        """Both spellings, since the default path passes None rather than a dict."""
        from backtest.data.options.chain_registry import build_chain_source

        assert build_chain_source(None, market, config) is not None
        assert build_chain_source({"kind": "synthetic"}, market, config) is not None
        assert build_chain_source("synthetic", market, config) is not None
