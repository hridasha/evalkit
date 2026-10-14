"""Every run is saved in one SQLite file, so versions can be compared later."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from evalkit.core.models import CaseResult, TestCase

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    config TEXT NOT NULL DEFAULT '{}',
    summary TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS results (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    repeat INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL,
    PRIMARY KEY (run_id, case_id, repeat)
);
CREATE TABLE IF NOT EXISTS testsets (
    name TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    cases TEXT NOT NULL
);
"""


class RunNotFoundError(KeyError):
    pass


class Storage:
    def __init__(self, path: str | Path = "evalkit.db"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------------------------------ runs
    def create_run(self, name: str, version: str = "", config: dict | None = None) -> int:
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO runs (name, version, created_at, config) VALUES (?, ?, ?, ?)",
                (name, version, time.time(), json.dumps(config or {})),
            )
            self.db.commit()
            return int(cur.lastrowid)

    def add_result(self, run_id: int, result: CaseResult) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO results (run_id, case_id, repeat, data) VALUES (?, ?, ?, ?)",
                (run_id, result.case_id, result.repeat, json.dumps(result.to_dict())),
            )
            self.db.commit()

    def finish_run(self, run_id: int, summary: dict, status: str = "done") -> None:
        with self.lock:
            self.db.execute(
                "UPDATE runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
                (time.time(), status, json.dumps(summary), run_id),
            )
            self.db.commit()

    @staticmethod
    def _run_row(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"],
            "name": r["name"],
            "version": r["version"],
            "created_at": r["created_at"],
            "finished_at": r["finished_at"],
            "status": r["status"],
            "config": json.loads(r["config"]),
            "summary": json.loads(r["summary"]),
        }

    def get_run(self, run_id: int) -> dict:
        with self.lock:
            r = self.db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if r is None:
            raise RunNotFoundError(run_id)
        return self._run_row(r)

    def list_runs(self, name: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM runs", []
        if name:
            sql += " WHERE name = ?"
            args.append(name)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.lock:
            return [self._run_row(r) for r in self.db.execute(sql, args).fetchall()]

    def latest_run(self, name: str | None = None, before: int | None = None) -> dict | None:
        sql, args = "SELECT * FROM runs WHERE status = 'done'", []
        if name:
            sql += " AND name = ?"
            args.append(name)
        if before is not None:
            sql += " AND id < ?"
            args.append(before)
        with self.lock:
            r = self.db.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return self._run_row(r) if r else None

    def get_results(self, run_id: int) -> list[CaseResult]:
        with self.lock:
            rows = self.db.execute(
                "SELECT data FROM results WHERE run_id = ? ORDER BY case_id, repeat", (run_id,)
            ).fetchall()
        return [CaseResult.from_dict(json.loads(r["data"])) for r in rows]

    def delete_run(self, run_id: int) -> None:
        self.get_run(run_id)
        with self.lock:
            self.db.execute("DELETE FROM results WHERE run_id = ?", (run_id,))
            self.db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
            self.db.commit()

    # --------------------------------------------------------------------------------------- test sets
    def save_testset(self, name: str, cases: list[TestCase]) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO testsets (name, created_at, cases) VALUES (?, ?, ?)",
                (name, time.time(), json.dumps([c.to_dict() for c in cases])),
            )
            self.db.commit()

    def get_testset(self, name: str) -> list[TestCase]:
        with self.lock:
            r = self.db.execute("SELECT cases FROM testsets WHERE name = ?", (name,)).fetchone()
        if r is None:
            raise KeyError(name)
        return [TestCase.from_dict(d) for d in json.loads(r["cases"])]

    def list_testsets(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT name, created_at, cases FROM testsets ORDER BY name").fetchall()
        return [{"name": r["name"], "created_at": r["created_at"], "count": len(json.loads(r["cases"]))} for r in rows]

    def delete_testset(self, name: str) -> None:
        with self.lock:
            self.db.execute("DELETE FROM testsets WHERE name = ?", (name,))
            self.db.commit()
