"""The research store: persisting strategies, runs and verdicts.

What accumulates over long-term research is not one run's numbers, it is the
**judgements** — why the delta 0.2 variant did worse than 0.3. So three things are
stored, and none can be dropped:

    strategy  a named rule definition; each edit is a new version
    run       one strategy version's result over one range, **immutable**
    verdict   the judgement you wrote down about one run

Three design constraints:

1. **A run is immutable.** Once it has run it is history and cannot be edited; to
   change it, run again. Otherwise three months later there is no saying how it was
   actually run.
2. **Strategies are versioned.** A run references one version of a strategy, not "what
   the strategy looks like now". That is what makes "how do v2 and v3 differ"
   answerable.
3. **Metadata and detail are kept apart.** Metadata goes to SQLite, which is good for
   querying; trade logs go to parquet, which handles the volume.

Location: ``research/`` under the project, so it can be backed up and committed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
HOME = ROOT / "research"
DB_PATH = HOME / "research.db"
TRADES_DIR = HOME / "trades"

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    created     TEXT NOT NULL,
    spec        TEXT NOT NULL,          -- JSON：entry / select / exit / signals
    spec_hash   TEXT NOT NULL,          -- a content fingerprint, to recognise "nothing actually changed"
    note        TEXT NOT NULL DEFAULT '',
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS run (
    id           INTEGER PRIMARY KEY,
    started      TEXT NOT NULL,
    seconds      REAL NOT NULL,
    strategy_id  INTEGER REFERENCES strategy(id),
    strategy_name TEXT NOT NULL,
    strategy_ver INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    start_date   TEXT NOT NULL,
    end_date     TEXT NOT NULL,
    config       TEXT NOT NULL,         -- JSON: the full configuration, what makes it reproducible
    manifest     TEXT NOT NULL,         -- JSON: provenance plus dependency versions
    events       TEXT NOT NULL,         -- JSON: run event counts
    n_trades     INTEGER NOT NULL,
    net_pnl      REAL NOT NULL,
    win_rate     REAL NOT NULL,
    median_pnl   REAL NOT NULL,
    synthetic    INTEGER NOT NULL,      -- whether the data was synthetic, which decides whether the conclusions can be trusted
    trades_file  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note (
    id       INTEGER PRIMARY KEY,
    run_id   INTEGER REFERENCES run(id) ON DELETE CASCADE,
    created  TEXT NOT NULL,
    verdict  TEXT NOT NULL DEFAULT 'open',  -- open / promising / rejected / inconclusive
    body     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_run_strategy ON run(strategy_name, strategy_ver);
CREATE INDEX IF NOT EXISTS idx_run_started  ON run(started DESC);
CREATE INDEX IF NOT EXISTS idx_note_run     ON note(run_id);
"""

#: The possible verdicts. Deliberately not success/failure — most research verdicts
#: are "cannot tell yet".
VERDICTS = {
    "open": "open",
    "promising": "worth continuing",
    "rejected": "ruled out",
    "inconclusive": "sample too small to tell",
}


def spec_fingerprint(spec: dict[str, Any]) -> str:
    """A content fingerprint of a strategy definition, to recognise a save that
    changed nothing."""
    payload = json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class Store:
    """The research store. Every read and write goes through it; nothing else touches
    SQLite directly."""

    def __init__(self, home: Path | str = HOME) -> None:
        self.home = Path(home)
        self.trades_dir = self.home / "trades"
        self.home.mkdir(parents=True, exist_ok=True)
        self.trades_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "research.db"
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ── strategies ────────────────────────────────────────
    def save_strategy(self, name: str, spec: dict[str, Any], note: str = "") -> dict[str, Any]:
        """Save one version of a strategy.

        When the content has not changed, **no new version is created** and the existing
        one is returned — otherwise clicking save a few times leaves several identical
        versions and the run history can no longer tell them apart.
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("a strategy must have a name")
        fp = spec_fingerprint(spec)

        same = self._db.execute(
            "SELECT * FROM strategy WHERE name=? AND spec_hash=? ORDER BY version DESC LIMIT 1",
            (name, fp),
        ).fetchone()
        if same:
            return dict(same) | {"reused": True}

        row = self._db.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM strategy WHERE name=?", (name,)
        ).fetchone()
        version = row["v"] + 1
        cur = self._db.execute(
            "INSERT INTO strategy (name, version, created, spec, spec_hash, note)"
            " VALUES (?,?,?,?,?,?)",
            (name, version, _now(), json.dumps(spec, ensure_ascii=False), fp, note),
        )
        self._db.commit()
        return dict(
            self._db.execute("SELECT * FROM strategy WHERE id=?", (cur.lastrowid,)).fetchone()
        ) | {"reused": False}

    def strategies(self) -> list[dict[str, Any]]:
        """Each strategy's latest version, with how many times it has been run."""
        rows = self._db.execute(
            """
            SELECT s.*, (SELECT COUNT(*) FROM run r WHERE r.strategy_name = s.name) AS runs
            FROM strategy s
            JOIN (SELECT name, MAX(version) AS v FROM strategy GROUP BY name) latest
              ON s.name = latest.name AND s.version = latest.v
            ORDER BY s.created DESC
            """
        ).fetchall()
        return [self._unpack_strategy(r) for r in rows]

    def strategy_versions(self, name: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM strategy WHERE name=? ORDER BY version DESC", (name,)
        ).fetchall()
        return [self._unpack_strategy(r) for r in rows]

    def strategy(self, strategy_id: int) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM strategy WHERE id=?", (strategy_id,)).fetchone()
        return self._unpack_strategy(row) if row else None

    @staticmethod
    def _unpack_strategy(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["spec"] = json.loads(d["spec"])
        return d

    # ── runs ──────────────────────────────────────────────
    def save_run(
        self,
        *,
        strategy: dict[str, Any],
        config: dict[str, Any],
        manifest: dict[str, Any],
        events: dict[str, int],
        seconds: float,
        summary: dict[str, Any],
        trades,
        synthetic: bool,
    ) -> dict[str, Any]:
        """Record one run. The trade log goes to parquet, the metadata to the database.

        Parquet rather than CSV: columnar, typed and compact. Hundreds of runs times
        thousands of trades only stay manageable in this format, and pandas reads dates
        back as dates rather than strings.
        """
        cur = self._db.execute(
            """
            INSERT INTO run (started, seconds, strategy_id, strategy_name, strategy_ver,
                             symbol, start_date, end_date, config, manifest, events,
                             n_trades, net_pnl, win_rate, median_pnl, synthetic, trades_file)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _now(), round(seconds, 2), strategy["id"], strategy["name"], strategy["version"],
                config["symbol"], str(config["start"]), str(config["end"]),
                json.dumps(config, ensure_ascii=False, default=str),
                json.dumps(manifest, ensure_ascii=False, default=str),
                json.dumps(events, ensure_ascii=False),
                summary["n"], summary["net_pnl"], summary["win_rate"], summary["median_pnl"],
                int(synthetic), "",
            ),
        )
        run_id = cur.lastrowid
        rel = f"run_{run_id:05d}.parquet"
        if len(trades):
            trades.to_parquet(self.trades_dir / rel, index=False)
        else:
            rel = ""
        self._db.execute("UPDATE run SET trades_file=? WHERE id=?", (rel, run_id))
        self._db.commit()
        return self.run(run_id)

    def runs(
        self,
        *,
        strategy_name: str | None = None,
        symbol: str | None = None,
        verdict: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where, args = [], []
        if strategy_name:
            where.append("r.strategy_name = ?"); args.append(strategy_name)
        if symbol:
            where.append("r.symbol = ?"); args.append(symbol)
        if verdict:
            where.append(
                "EXISTS (SELECT 1 FROM note n WHERE n.run_id = r.id AND n.verdict = ?)"
            )
            args.append(verdict)
        sql = (
            "SELECT r.*, (SELECT COUNT(*) FROM note n WHERE n.run_id = r.id) AS n_notes,"
            " (SELECT n.verdict FROM note n WHERE n.run_id = r.id ORDER BY n.id DESC LIMIT 1)"
            " AS last_verdict FROM run r"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.id DESC LIMIT ?"
        args.append(limit)
        return [self._unpack_run(r) for r in self._db.execute(sql, args).fetchall()]

    def run(self, run_id: int) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT r.*, (SELECT COUNT(*) FROM note n WHERE n.run_id=r.id) AS n_notes,"
            " (SELECT n.verdict FROM note n WHERE n.run_id=r.id ORDER BY n.id DESC LIMIT 1)"
            " AS last_verdict FROM run r WHERE r.id=?",
            (run_id,),
        ).fetchone()
        return self._unpack_run(row) if row else None

    def trades(self, run_id: int):
        """Read one run's trade log back.

        A missing file **raises rather than returning an empty frame** — an empty frame
        looks exactly like "this run really had no trades", and those two mean entirely
        different things.
        """
        import pandas as pd

        row = self._db.execute("SELECT trades_file, n_trades FROM run WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"there is no run {run_id}")
        if not row["trades_file"]:
            if row["n_trades"]:
                raise FileNotFoundError(
                    f"run {run_id} recorded {row['n_trades']} trades but its log file "
                    f"is empty: the data is corrupt"
                )
            return pd.DataFrame()
        path = self.trades_dir / row["trades_file"]
        if not path.exists():
            raise FileNotFoundError(f"run {run_id}'s trade log file is missing: {path}")
        return pd.read_parquet(path)

    @staticmethod
    def _unpack_run(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for k in ("config", "manifest", "events"):
            d[k] = json.loads(d[k])
        d["synthetic"] = bool(d["synthetic"])
        return d

    def delete_run(self, run_id: int) -> None:
        row = self._db.execute("SELECT trades_file FROM run WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"there is no run {run_id}")
        if row["trades_file"]:
            (self.trades_dir / row["trades_file"]).unlink(missing_ok=True)
        self._db.execute("DELETE FROM run WHERE id=?", (run_id,))
        self._db.commit()

    # ── verdicts ──────────────────────────────────────────
    def add_note(self, run_id: int, body: str, verdict: str = "open") -> dict[str, Any]:
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}; one of {sorted(VERDICTS)}")
        if not body.strip():
            raise ValueError("a verdict cannot be empty")
        if self._db.execute("SELECT 1 FROM run WHERE id=?", (run_id,)).fetchone() is None:
            raise KeyError(f"there is no run {run_id}")
        cur = self._db.execute(
            "INSERT INTO note (run_id, created, verdict, body) VALUES (?,?,?,?)",
            (run_id, _now(), verdict, body.strip()),
        )
        self._db.commit()
        return dict(self._db.execute("SELECT * FROM note WHERE id=?", (cur.lastrowid,)).fetchone())

    def notes(self, run_id: int | None = None) -> list[dict[str, Any]]:
        if run_id is None:
            rows = self._db.execute(
                "SELECT n.*, r.strategy_name, r.strategy_ver FROM note n"
                " JOIN run r ON r.id = n.run_id ORDER BY n.id DESC LIMIT 300"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM note WHERE run_id=? ORDER BY id DESC", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ── overview ──────────────────────────────────────────
    def overview(self) -> dict[str, Any]:
        one = lambda sql: self._db.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "strategies": one("SELECT COUNT(DISTINCT name) FROM strategy"),
            "versions": one("SELECT COUNT(*) FROM strategy"),
            "runs": one("SELECT COUNT(*) FROM run"),
            "notes": one("SELECT COUNT(*) FROM note"),
            "synthetic_runs": one("SELECT COUNT(*) FROM run WHERE synthetic=1"),
            "home": str(self.home),
        }

    def close(self) -> None:
        self._db.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
