"""Layer 7: the store and the validation endpoint.

This layer had no tests, which is worse than it sounds: the store holds every research
judgement the platform accumulates, and a schema change that reaches a new machine but
skips the one with the actual research in it is the quietest failure in the project.

What is checked here is the part that cannot be seen by looking at a result:

- the migration actually adds a column to a database that already exists
- the run fingerprint reaches the store under a name that cannot be confused with the
  store's own integer key
- the validation tools refuse rather than guess -- no levels, no engine, no draws
- a run that has aged out of memory says so instead of validating a different run
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import runner as runner_mod  # noqa: E402
from store import Store  # noqa: E402

from tests.kinds import kind, labels

#: What a failure here means, and where it is. See ``tests/kinds.py``.
pytestmark = labels("unit", "7")

#: Every strategy choice stated, because the platform refuses to supply one. This is what
#: the interface's form sends -- its fields have starting values a person can see and
#: change, which is the only place a default belongs.
PAYLOAD = {
    "symbol": "QQQ", "start": "2023-01-01", "end": "2023-06-30",
    "entry": "close < sma_20 * 0.98", "hold_days": 15, "take_profit": 0.8,
    "stop_loss": 0.4, "dte_floor": 7, "sizing": {"method": "fixed"}, "quantity": 1,
    "right": "call", "dte_lo": 21, "dte_hi": 45, "delta_lo": 0.25, "delta_hi": 0.45,
    "strategy_name": "probe",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh store, with the runner pointed at it so nothing touches the real one."""
    s = Store(tmp_path)
    monkeypatch.setattr(runner_mod, "STORE", s)
    runner_mod.RECENT.clear()
    return s


def _run(**over):
    return runner_mod.run_backtest({**PAYLOAD, "engine": "event_driven", **over})


class TestMigration:
    """A column added after the first release has to reach a database that exists."""

    #: The `run` table as it was before the columns were added. Written out rather than
    #: produced with ALTER TABLE DROP COLUMN, which needs SQLite 3.35 -- a test that fails
    #: on an older interpreter is reporting the environment, not the code.
    OLD_SCHEMA = """
    CREATE TABLE run (
        id INTEGER PRIMARY KEY, started TEXT NOT NULL, seconds REAL NOT NULL,
        strategy_id INTEGER, strategy_name TEXT NOT NULL, strategy_ver INTEGER NOT NULL,
        symbol TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
        config TEXT NOT NULL, manifest TEXT NOT NULL, events TEXT NOT NULL,
        n_trades INTEGER NOT NULL, net_pnl REAL NOT NULL, win_rate REAL NOT NULL,
        median_pnl REAL NOT NULL, synthetic INTEGER NOT NULL, trades_file TEXT NOT NULL
    );
    """

    def test_an_older_database_gains_the_columns(self, tmp_path):
        (tmp_path / "trades").mkdir(exist_ok=True)
        db = sqlite3.connect(tmp_path / "research.db")
        db.executescript(self.OLD_SCHEMA)
        db.commit()
        db.close()

        Store(tmp_path)  # the migration runs on open

        cols = {r[1] for r in
                sqlite3.connect(tmp_path / "research.db").execute("PRAGMA table_info(run)")}
        assert {"fingerprint", "sharpe_annualised"} <= cols

    def test_the_old_schema_really_lacks_them(self, tmp_path):
        """Or the migration test would pass against a table that never needed migrating."""
        db = sqlite3.connect(":memory:")
        db.executescript(self.OLD_SCHEMA)
        cols = {r[1] for r in db.execute("PRAGMA table_info(run)")}
        assert not ({"fingerprint", "sharpe_annualised"} & cols)

    def test_opening_twice_does_not_fail(self, tmp_path):
        """ALTER TABLE on a column that already exists is an error, so the migration has
        to check first -- otherwise the second start of the service crashes."""
        Store(tmp_path)
        Store(tmp_path)

    def test_a_foreign_database_is_refused(self, tmp_path):
        """Pointed at someone else's SQLite file, it says so rather than altering it."""
        from backtest.core.errors import StartupError

        (tmp_path / "trades").mkdir()
        db = sqlite3.connect(tmp_path / "research.db")
        db.execute("CREATE TABLE run (id INTEGER PRIMARY KEY)")
        db.commit()
        db.close()
        with pytest.raises(StartupError, match="not our database"):
            Store(tmp_path)


class TestRunRecords:
    def test_the_fingerprint_is_stored_and_is_not_the_integer_key(self, store):
        rec = _run()
        rows = store.run_records()
        assert len(rows) == 1
        assert rows[0]["run_id"] == rec["manifest"]["run_id"]
        assert rows[0]["run_id"] != rec["id"]
        assert isinstance(rows[0]["run_id"], str) and len(rows[0]["run_id"]) == 16

    def test_the_same_configuration_twice_is_one_trial(self, store):
        """What the fingerprint exists for: a rerun must not inflate the trial count."""
        from backtest.research import tracker

        _run()
        _run()
        trials = tracker.count_trials(store.run_records("probe"), idea="probe")
        assert trials.runs == 2
        assert trials.distinct == 1

    def test_two_engines_are_two_trials(self, store):
        """They used to share a fingerprint, which made a search look smaller than it
        was -- and a smaller search makes the deflated result look better."""
        from backtest.research import tracker

        _run()
        _run(engine="trade_table")
        trials = tracker.count_trials(store.run_records("probe"), idea="probe")
        assert trials.distinct == 2

    def test_the_trade_table_stores_no_sharpe(self, store):
        """It builds no equity curve. NULL, not a zero that reads as a real Sharpe."""
        _run(engine="trade_table")
        assert store.run_records()[0]["sharpe"] is None

    def test_the_event_driven_engine_stores_one(self, store):
        _run()
        assert store.run_records()[0]["sharpe"] is not None


class TestExportConfig:
    """A stored run has to come back as something runnable, in the shape the loader reads.

    The first version dumped the stored flat configuration, which is not the file format,
    so it wrote a file this system could not read back. Nothing caught it, because nothing
    ran the export through the loader -- which is what this does.
    """

    @pytest.mark.req("7.1.store")
    def test_an_exported_run_replays_to_the_same_fingerprint(self, store, tmp_path):
        from backtest import run_from

        rec = _run()
        path = store.export_config(rec["id"], tmp_path / "exported.yaml")
        assert run_from(path).manifest()["run_id"] == rec["manifest"]["run_id"]

    def test_the_export_is_in_the_documents_shape(self, store, tmp_path):
        import yaml

        rec = _run()
        path = store.export_config(rec["id"], tmp_path / "shape.yaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert {"strategy", "data", "run"} <= set(data)

    def test_the_fingerprint_is_carried_not_recomputed(self, store, tmp_path):
        """A recomputed one would agree with itself even if the code had moved on, and
        disagreeing is the entire signal."""
        from backtest.api.config_file import fingerprint_of

        rec = _run()
        path = store.export_config(rec["id"], tmp_path / "fp.yaml")
        assert fingerprint_of(path) == rec["manifest"]["run_id"]

    def test_a_run_with_no_fingerprint_refuses_rather_than_writing_one(self, store,
                                                                      tmp_path):
        """A row saved before the column existed cannot be checked against the run it
        came from, so it does not get a file that claims it can."""
        rec = _run()
        # Both places it could come from: the column, and the manifest JSON the column was
        # lifted out of. A row with the column blank but the manifest intact is recoverable
        # and is recovered -- this is the genuinely old row, from before either existed.
        store._db.execute("UPDATE run SET fingerprint='', manifest='{}' WHERE id=?",
                          (rec["id"],))
        store._db.commit()
        with pytest.raises(ValueError, match="before the reproducibility fingerprint"):
            store.export_config(rec["id"], tmp_path / "old.yaml")

    def test_a_blank_column_falls_back_to_the_manifest(self, store, tmp_path):
        """A row saved before the column existed still carries the fingerprint inside its
        manifest. Recovering it beats refusing a run that can in fact be identified."""
        from backtest.api.config_file import fingerprint_of

        rec = _run()
        store._db.execute("UPDATE run SET fingerprint='' WHERE id=?", (rec["id"],))
        store._db.commit()
        path = store.export_config(rec["id"], tmp_path / "recovered.yaml")
        assert fingerprint_of(path) == rec["manifest"]["run_id"]

    def test_a_missing_run_says_so(self, store, tmp_path):
        with pytest.raises(KeyError):
            store.export_config(9999, tmp_path / "nope.yaml")


class TestValidationsOffered:
    def test_a_run_declares_what_it_can_answer(self, store):
        assert "counterfactual" in _run()["validations"]

    def test_the_trade_table_does_not_offer_the_counterfactual(self, store):
        """It keeps no shadow prices, so the question is unanswerable rather than
        answerable-but-wrong. The interface renders its buttons from this list."""
        assert "counterfactual" not in _run(engine="trade_table")["validations"]
        assert "baselines" in _run(engine="trade_table")["validations"]

    def test_every_offered_tool_is_declared(self, store):
        offered = set(_run()["validations"])
        assert offered <= set(runner_mod.VALIDATIONS)


class TestValidationRefusals:
    """Each of these refuses rather than filling in a number on the user's behalf."""

    def test_an_unknown_tool(self, store):
        rec = _run()
        with pytest.raises(ValueError, match="no validation called"):
            runner_mod.validate(rec["id"], "nope", {})

    def test_the_counterfactual_needs_its_levels(self, store):
        """Which levels are worth asking about is the research question. A default here
        would be the platform answering it and then reporting the winner as a finding."""
        rec = _run()
        with pytest.raises(ValueError, match="needs the levels"):
            runner_mod.validate(rec["id"], "counterfactual", {})

    def test_the_counterfactual_needs_the_right_engine(self, store):
        rec = _run(engine="trade_table")
        with pytest.raises(ValueError, match="needs the event_driven engine"):
            runner_mod.validate(rec["id"], "counterfactual",
                                {"take_profits": [0.5], "stop_losses": [0.3]})

    def test_random_entry_needs_enough_draws(self, store):
        rec = _run()
        with pytest.raises(ValueError, match="at least 10 draws"):
            runner_mod.validate(rec["id"], "baselines", {"draws": 5})

    @kind("limit")
    def test_a_run_that_aged_out_says_so(self, store):
        """Validating a *rebuilt* run silently instead would validate something slightly
        different from the run on screen."""
        rec = _run()
        runner_mod.RECENT.clear()
        with pytest.raises(ValueError, match="no longer in memory"):
            runner_mod.validate(rec["id"], "ablation", {})

    def test_only_the_last_few_runs_are_kept(self, store):
        """Bounded, because an event-driven result holds a daily snapshot and a path per
        position per day."""
        # One more run than the cache holds, whatever the cache holds. The first version
        # sliced a fixed list of five, so raising RECENT_KEEP above 3 would have failed
        # the test for a reason that had nothing to do with the behaviour.
        wanted = runner_mod.RECENT_KEEP + 1
        ids = [_run(take_profit=round(0.40 + 0.05 * i, 2))["id"] for i in range(wanted)]
        assert len(runner_mod.RECENT) == runner_mod.RECENT_KEEP
        assert ids[-1] in runner_mod.RECENT
        assert ids[0] not in runner_mod.RECENT


class TestValidationOutput:
    def test_every_report_states_its_boundary_before_the_numbers(self, store):
        """The synthetic chain makes every option-side conclusion a statement about
        Black-Scholes. The boundary going *after* the numbers is the same as no
        boundary."""
        rec = _run()
        for kind, params in [("ablation", {}),
                             ("baselines", {"draws": 10}),
                             ("counterfactual", {"take_profits": [0.5, 1.0],
                                                 "stop_losses": [0.3, 0.5]})]:
            text = runner_mod.validate(rec["id"], kind, params)["text"]
            mark = text.find("!!")
            assert mark > 0, f"{kind} carries no boundary"
            # No escape hatch. The first version allowed the check to pass whenever the
            # word "synthetic" appeared anywhere near the boundary -- which it always
            # does, since the boundary says it -- so the ordering was never enforced.
            #
            # The numbers start where the report's own table or figures do. Taking the
            # first digit after the heading rules would catch the heading's own rule
            # characters, so this looks for the first line that holds a digit beyond the
            # boundary block.
            body = [i for i, line in enumerate(text.splitlines())
                    if any(c.isdigit() for c in line)]
            boundary_line = text[:mark].count("\n")
            assert body, f"{kind} reports no numbers at all"
            assert boundary_line < body[-1], (
                f"{kind} puts its boundary after its last number"
            )
            first_number_line = next(
                (i for i in body if i > text[:text.find("─")].count("\n")), body[0])
            assert boundary_line < first_number_line, (
                f"{kind}: the boundary is on line {boundary_line} and the first number "
                f"is on line {first_number_line}"
            )

    def test_the_trial_count_refuses_to_guess_the_period_count(self, store):
        """The stored Sharpe is annualised. Passing days against it overstates the
        deflated result badly, so the unit is asked for rather than assumed."""
        rec = _run()
        text = runner_mod.validate(rec["id"], "trials", {})["text"]
        assert "years" in text

    def test_the_deflated_sharpe_refusal_is_shown_not_swallowed(self, store):
        """A six-month backtest genuinely cannot carry a deflated Sharpe. The refusal is
        the answer; showing a number anyway is what the tool exists to prevent."""
        rec = _run()
        text = runner_mod.validate(rec["id"], "trials", {"years": 0.4})["text"]
        assert "cannot be computed" in text
