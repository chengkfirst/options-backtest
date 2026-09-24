"""研究库：策略、运行、结论的持久化。

长期研究积累的不是某一次的数字，是**判断**——"delta 0.2 那次为什么比 0.3 差"。
所以这里存三样东西，缺一不可：

    策略  一份命名的规则定义，改一次存一版
    运行  某个策略版本在某个区间上跑出来的结果，**不可变**
    结论  你在某次运行上写下的判断

三条设计约束：

1. **运行不可变。** 跑完就是历史，改不了。要改就重跑。否则三个月后回头看，
   "我当时到底是怎么跑的"就说不清了。
2. **策略带版本。** 运行引用的是策略的某一版，不是"策略现在长什么样"。
   这样才能回答"v2 和 v3 差在哪"。
3. **元数据与明细分开。** 元数据进 SQLite 好检索，交易明细进 parquet 扛体量。

存储位置：项目下的 ``research/``，可备份、可进 git。
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
    spec_hash   TEXT NOT NULL,          -- 内容指纹，用来认出"其实没改"
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
    config       TEXT NOT NULL,         -- JSON：完整配置，可复现的凭据
    manifest     TEXT NOT NULL,         -- JSON：数据来源 + 依赖版本
    events       TEXT NOT NULL,         -- JSON：运行事件计数
    n_trades     INTEGER NOT NULL,
    net_pnl      REAL NOT NULL,
    win_rate     REAL NOT NULL,
    median_pnl   REAL NOT NULL,
    synthetic    INTEGER NOT NULL,      -- 数据是不是合成的，决定结论可不可信
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

#: 结论的取值。刻意不设"成功/失败"——研究结论多数时候是"还看不出来"。
VERDICTS = {
    "open": "待定",
    "promising": "值得继续",
    "rejected": "已排除",
    "inconclusive": "样本不足，看不出",
}


def spec_fingerprint(spec: dict[str, Any]) -> str:
    """策略定义的内容指纹。用来认出"点了保存但其实什么都没改"。"""
    payload = json.dumps(spec, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class Store:
    """研究库。所有读写都经由它，别处不直接碰 SQLite。"""

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

    # ── 策略 ──────────────────────────────────────────────
    def save_strategy(self, name: str, spec: dict[str, Any], note: str = "") -> dict[str, Any]:
        """保存一版策略。

        内容没变时**不新增版本**，直接返回已有的那版——否则点几次保存就多出几个
        一模一样的版本，运行记录里再也看不出哪版是哪版。
        """
        name = (name or "").strip()
        if not name:
            raise ValueError("策略必须有名字")
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
        """每个策略的最新版，外加它跑过多少次。"""
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

    # ── 运行 ──────────────────────────────────────────────
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
        """落一次运行。交易明细写 parquet，元数据写库。

        parquet 而非 CSV：列式、带类型、体量小。几百次运行 × 上千笔交易在这个格式下
        才不会把目录撑爆，pandas 读回来也不会把日期变成字符串。
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
        """读回某次运行的交易明细。

        文件不在时**抛错而不是返回空表**——空表和"这次真没交易"长得一样，
        而那两件事的含义完全不同。
        """
        import pandas as pd

        row = self._db.execute("SELECT trades_file, n_trades FROM run WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"没有第 {run_id} 次运行")
        if not row["trades_file"]:
            if row["n_trades"]:
                raise FileNotFoundError(
                    f"第 {run_id} 次运行记录了 {row['n_trades']} 笔交易，但明细文件为空——数据损坏"
                )
            return pd.DataFrame()
        path = self.trades_dir / row["trades_file"]
        if not path.exists():
            raise FileNotFoundError(f"第 {run_id} 次的交易明细文件丢失：{path}")
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
            raise KeyError(f"没有第 {run_id} 次运行")
        if row["trades_file"]:
            (self.trades_dir / row["trades_file"]).unlink(missing_ok=True)
        self._db.execute("DELETE FROM run WHERE id=?", (run_id,))
        self._db.commit()

    # ── 结论 ──────────────────────────────────────────────
    def add_note(self, run_id: int, body: str, verdict: str = "open") -> dict[str, Any]:
        if verdict not in VERDICTS:
            raise ValueError(f"未知的结论取值 {verdict!r}，可用：{sorted(VERDICTS)}")
        if not body.strip():
            raise ValueError("结论不能是空的")
        if self._db.execute("SELECT 1 FROM run WHERE id=?", (run_id,)).fetchone() is None:
            raise KeyError(f"没有第 {run_id} 次运行")
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

    # ── 概况 ──────────────────────────────────────────────
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
