"""A run as a file.

A settled strategy is what the second use revolves around, and until this it had no form:
it lived in the arguments of whatever call produced it, and afterwards as a JSON blob in
the store that nothing could run again.

The property that makes a config file a carrier rather than a description is that **it
reproduces**. A file that writes out less than the fingerprint covers still looks complete;
the gap appears a year later, when the replay quietly differs. So most of what is checked
here is that, in the places where it could silently not hold.
"""

from __future__ import annotations

import pytest
import yaml

from backtest import run, run_from
from backtest.api.config_file import (
    KEYS, check_round_trip, fingerprint_of, load, save, to_mapping,
)
from backtest.core.errors import StartupError

BASE = dict(
    symbol="QQQ", start="2023-01-01", end="2023-06-30",
    entry="close < sma_20 * 0.98",
    select={"right": "call", "dte_lo": 21, "dte_hi": 45,
            "delta_lo": 0.25, "delta_hi": 0.45},
    exit={"hold_days": 20, "dte_floor": 7},
    engine="event_driven", sizing={"method": "fixed"}, quantity=1,
)


@pytest.fixture(scope="module")
def result():
    return run(**BASE)


class TestItReproduces:
    def test_a_plain_run_round_trips(self, result, tmp_path):
        check_round_trip(result, tmp_path / "plain.yaml")

    def test_a_run_with_a_component_round_trips(self, tmp_path):
        """The component's form and its parameters both have to survive the file, and
        both are in the fingerprint — so this fails loudly if either is dropped."""
        r = run(**BASE, components={"stop_loss": {"variant": "underlying",
                                                  "params": {"fraction": 0.03}}})
        check_round_trip(r, tmp_path / "component.yaml")

    def test_a_run_with_risk_sizing_round_trips(self, tmp_path):
        r = run(**(BASE | {"sizing": {"method": "risk_pct", "risk_pct": 0.02}}))
        check_round_trip(r, tmp_path / "sizing.yaml")

    def test_a_run_with_a_concurrency_limit_round_trips(self, tmp_path):
        """It changes which trades happen, so it is in the fingerprint and must be in
        the file."""
        r = run(**BASE, max_concurrent=2)
        check_round_trip(r, tmp_path / "limit.yaml")

    def test_a_run_with_a_longer_lag_round_trips(self, tmp_path):
        r = run(**BASE, execution_lag_days=2)
        check_round_trip(r, tmp_path / "lag.yaml")

    def test_the_replay_gives_the_same_trades(self, result, tmp_path):
        """The fingerprint matching is the mechanism; the trades matching is the point."""
        path = save(result, tmp_path / "trades.yaml")
        again = run_from(path)
        assert len(again.trades) == len(result.trades)
        assert again.trades["net_pnl"].sum() == pytest.approx(
            result.trades["net_pnl"].sum())

    def test_a_dropped_field_is_caught(self, monkeypatch, tmp_path):
        """The check has to be able to fail, or it proves nothing.

        The first version of this edited a field and watched the fingerprint change --
        which tests the fingerprint, not the round trip, and never called the thing it
        claimed to be checking. This drops a field from what gets written, which is the
        actual failure: a file that looks complete and replays to a different run.
        """
        from backtest.api import config_file

        complete = config_file.to_mapping

        def missing_the_limit(res):
            out = complete(res)
            out["run"].pop("max_concurrent", None)   # changes which trades happen
            return out

        monkeypatch.setattr(config_file, "to_mapping", missing_the_limit)
        limited = run(**BASE, max_concurrent=1)
        with pytest.raises(StartupError, match="does not reproduce the run"):
            config_file.check_round_trip(limited, tmp_path / "dropped.yaml")

    def test_the_round_trip_check_passes_when_nothing_is_dropped(self):
        """The other half: the check must not fire on a complete file, or it would be
        unfalsifiable in the opposite direction."""
        from backtest.api import config_file

        import tempfile

        with tempfile.TemporaryDirectory() as d:
            limited = run(**BASE, max_concurrent=1)
            assert config_file.check_round_trip(limited, f"{d}/ok.yaml")


class TestTheDocumentsShape:
    """The architecture document gives its own config example. The file follows it."""

    def test_the_three_blocks(self, result):
        """strategy / data / run, plus the document's lifecycle and timing beside them."""
        written = to_mapping(result)
        assert set(written) <= set(KEYS)
        assert {"strategy", "data", "run", "lifecycle", "timing"} <= set(written)

    def test_what_the_strategy_is_sits_apart_from_where_it_ran(self, result):
        """The separation a researcher uses: the same strategy on a later period, or two
        strategies on the same one."""
        written = to_mapping(result)
        assert "engine" not in written["strategy"]
        assert "symbol" not in written["strategy"]
        assert "entry" in written["strategy"]

    def test_a_component_is_variant_version_params(self):
        """The document's own spelling for every slot."""
        r = run(**BASE, components={"stop_loss": {"variant": "premium",
                                                  "params": {"fraction": 0.4}}})
        one = to_mapping(r)["strategy"]["components"]["stop_loss"]
        assert set(one) == {"variant", "version", "params"}

    def test_all_five_lifecycle_settings_are_written(self, result):
        """Omitting them would leave a reader assuming they were considered."""
        from backtest.signals.lifecycle import SETTINGS

        written = to_mapping(result)["lifecycle"]
        assert {s.name for s in SETTINGS} == set(written)

    def test_an_edited_lifecycle_value_is_refused(self, result, tmp_path):
        """Four of the five are fixed in the engine. A file carrying an edited value the
        run then ignores is a setting that looks honoured and is not."""
        path = save(result, tmp_path / "edited.yaml")
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.replace("reentry_policy: nothing enforces it",
                                    "reentry_policy: wait 5 days"), encoding="utf-8")
        with pytest.raises(StartupError, match="fixed in the engine"):
            load(path)

    def test_an_unknown_key_inside_a_block_is_refused(self, tmp_path):
        path = tmp_path / "nested.yaml"
        path.write_text("strategy:\n  entyr: close > sma_20\n", encoding="utf-8")
        with pytest.raises(StartupError, match="entyr"):
            load(path)


class TestTheFile:
    def test_the_fingerprint_is_written_as_a_comment(self, result, tmp_path):
        """Derived, not a field: a field would invite editing the hash instead of
        re-running to get a new one."""
        path = save(result, tmp_path / "head.yaml")
        assert fingerprint_of(path) == result.manifest()["run_id"]
        assert "fingerprint" not in yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_the_right_is_written_as_a_word(self, result, tmp_path):
        """A file is read by a person before it is read by the parser, and "C" tells
        them nothing."""
        assert to_mapping(result)["strategy"]["select"]["right"] == "call"

    def test_a_component_carries_its_version(self, tmp_path):
        r = run(**BASE, components={"stop_loss": {"variant": "premium",
                                                  "params": {"fraction": 0.4}}})
        assert to_mapping(r)["strategy"]["components"]["stop_loss"]["version"] == 1

    def test_a_stale_component_version_is_refused(self, tmp_path):
        """Replaying against a later version would give a different result from a file
        that looks unchanged."""
        r = run(**BASE, components={"stop_loss": {"variant": "premium",
                                                  "params": {"fraction": 0.4}}})
        data = load(save(r, tmp_path / "v.yaml"))
        data["components"]["stop_loss"]["version"] = 99
        with pytest.raises(StartupError, match="cannot reproduce that run"):
            run(**data)

    def test_every_written_key_is_a_known_key(self, result, tmp_path):
        """Otherwise a file this system writes could not be read back by it."""
        from backtest.api.config_file import DATA_KEYS, RUN_KEYS, STRATEGY_KEYS

        written = to_mapping(result)
        assert set(written) <= set(KEYS)
        assert set(written["strategy"]) <= set(STRATEGY_KEYS)
        assert set(written["data"]) <= set(DATA_KEYS)
        assert set(written["run"]) <= set(RUN_KEYS)


class TestLoading:
    def test_an_unknown_key_raises(self, tmp_path):
        """As the document asks. A misspelled key that was ignored would run something
        other than what the file says, while the file looked right."""
        path = tmp_path / "typo.yaml"
        path.write_text("symbol: QQQ\nentyr: close > sma_20\n", encoding="utf-8")
        with pytest.raises(StartupError, match="entyr"):
            load(path)

    def test_a_missing_file_says_so(self, tmp_path):
        with pytest.raises(StartupError, match="no config file"):
            load(tmp_path / "nope.yaml")

    def test_broken_yaml_says_so(self, tmp_path):
        path = tmp_path / "bad.yaml"
        path.write_text("symbol: [QQQ\n", encoding="utf-8")
        with pytest.raises(StartupError, match="not valid YAML"):
            load(path)

    def test_a_file_that_is_not_a_mapping_raises(self, tmp_path):
        path = tmp_path / "list.yaml"
        path.write_text("- QQQ\n- SPY\n", encoding="utf-8")
        with pytest.raises(StartupError, match="should hold a mapping"):
            load(path)

    def test_all_three_spellings_of_the_tenor_window_work(self):
        """The interface sends dte_lo/dte_hi, a file holds dte_window, and a person may
        write a point plus a window. A file this system wrote has to be readable by it."""
        from backtest.api.sdk import _selection_from

        a = _selection_from({"right": "call", "dte_lo": 21, "dte_hi": 45, "delta": 0.3})
        b = _selection_from({"right": "call", "dte_window": [21, 45], "delta": 0.3})
        c = _selection_from({"right": "call", "dte": 33, "dte_window": (21, 45),
                             "delta": 0.3})
        assert a.dte_window == b.dte_window == c.dte_window == (21, 45)


class TestWhatCannotBeWritten:
    """Raising beats writing a file that silently drops the part that did the deciding."""

    def test_a_plan_cannot_be_written(self, tmp_path):
        def plan(ctx):
            return None

        r = run(**{k: v for k, v in BASE.items() if k != "entry"}, plan=plan)
        with pytest.raises(StartupError, match="plan"):
            to_mapping(r)

    def test_a_custom_entry_function_cannot_be_written(self):
        r = run(**(BASE | {"entry": lambda view: False}))
        with pytest.raises(StartupError, match="Python function rather than an "
                                               "expression"):
            to_mapping(r)

    def test_a_custom_selector_cannot_be_written(self):
        def chooser(chain, view):
            calls = chain[chain["is_call"]]
            return calls.iloc[0] if len(calls) else None

        r = run(**(BASE | {"select": chooser}))
        with pytest.raises(StartupError, match="custom function"):
            to_mapping(r)
