from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class JobLease:
    run_id: int | None
    acquired: bool
    reason: str


class StateStore:
    """SQLite control plane; market and research data stay in Parquet."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS job_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
                attempt INTEGER NOT NULL DEFAULT 1,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                records_written INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(job_name, idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS watermarks (
                source TEXT NOT NULL,
                dataset TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '',
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(source, dataset, market, symbol)
            );

            CREATE TABLE IF NOT EXISTS source_health (
                source TEXT PRIMARY KEY,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                circuit_open_until TEXT,
                last_success_at TEXT,
                last_failure_at TEXT,
                last_error TEXT
            );
            """
        )
        self.connection.commit()

    def acquire_job(
        self,
        job_name: str,
        idempotency_key: str,
        metadata: dict[str, Any] | None = None,
        stale_after: timedelta = timedelta(hours=4),
    ) -> JobLease:
        now = utc_now()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM job_runs WHERE job_name=? AND idempotency_key=?",
                (job_name, idempotency_key),
            ).fetchone()
            if row is None:
                cursor = self.connection.execute(
                    """
                    INSERT INTO job_runs
                        (job_name, idempotency_key, status, started_at, metadata_json)
                    VALUES (?, ?, 'running', ?, ?)
                    """,
                    (job_name, idempotency_key, now.isoformat(), metadata_json),
                )
                self.connection.commit()
                return JobLease(int(cursor.lastrowid), True, "new")
            if row["status"] == "succeeded":
                self.connection.commit()
                return JobLease(int(row["id"]), False, "already_succeeded")
            started_at = datetime.fromisoformat(row["started_at"])
            if row["status"] == "running" and now - started_at < stale_after:
                self.connection.commit()
                return JobLease(int(row["id"]), False, "already_running")
            self.connection.execute(
                """
                UPDATE job_runs
                SET status='running', attempt=attempt+1, started_at=?, finished_at=NULL,
                    records_written=0, error=NULL, metadata_json=?
                WHERE id=?
                """,
                (now.isoformat(), metadata_json, row["id"]),
            )
            self.connection.commit()
            return JobLease(int(row["id"]), True, "retry")
        except Exception:
            self.connection.rollback()
            raise

    def complete_job(
        self,
        run_id: int,
        records_written: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if metadata is None:
            self.connection.execute(
                """
                UPDATE job_runs SET status='succeeded', finished_at=?, records_written=?
                WHERE id=? AND status='running'
                """,
                (utc_now().isoformat(), records_written, run_id),
            )
        else:
            self.connection.execute(
                """
                UPDATE job_runs
                SET status='succeeded', finished_at=?, records_written=?, metadata_json=?
                WHERE id=? AND status='running'
                """,
                (
                    utc_now().isoformat(),
                    records_written,
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )
        self.connection.commit()

    def fail_job(self, run_id: int, error: str) -> None:
        self.connection.execute(
            """
            UPDATE job_runs SET status='failed', finished_at=?, error=?
            WHERE id=? AND status='running'
            """,
            (utc_now().isoformat(), error[:4000], run_id),
        )
        self.connection.commit()

    def set_watermark(
        self, source: str, dataset: str, market: str, value: str, symbol: str = ""
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO watermarks(source, dataset, market, symbol, value, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, dataset, market, symbol) DO UPDATE SET
                value=excluded.value, updated_at=excluded.updated_at
            """,
            (source, dataset, market, symbol, value, utc_now().isoformat()),
        )
        self.connection.commit()

    def set_symbol_watermarks(
        self, source: str, dataset: str, market: str, values: dict[str, str]
    ) -> None:
        updated_at = utc_now().isoformat()
        self.connection.executemany(
            """
            INSERT INTO watermarks(source, dataset, market, symbol, value, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, dataset, market, symbol) DO UPDATE SET
                value=excluded.value, updated_at=excluded.updated_at
            """,
            [
                (source, dataset, market, symbol, value, updated_at)
                for symbol, value in values.items()
            ],
        )
        self.connection.commit()

    def get_watermark(self, source: str, dataset: str, market: str, symbol: str = "") -> str | None:
        row = self.connection.execute(
            """
            SELECT value FROM watermarks
            WHERE source=? AND dataset=? AND market=? AND symbol=?
            """,
            (source, dataset, market, symbol),
        ).fetchone()
        return str(row["value"]) if row else None

    def record_source_success(self, source: str) -> None:
        self.connection.execute(
            """
            INSERT INTO source_health(source, consecutive_failures, last_success_at)
            VALUES (?, 0, ?)
            ON CONFLICT(source) DO UPDATE SET
                consecutive_failures=0, circuit_open_until=NULL,
                last_success_at=excluded.last_success_at, last_error=NULL
            """,
            (source, utc_now().isoformat()),
        )
        self.connection.commit()

    def record_source_failure(
        self,
        source: str,
        error: str,
        threshold: int = 3,
        cooldown: timedelta = timedelta(hours=1),
    ) -> None:
        now = utc_now()
        row = self.connection.execute(
            "SELECT consecutive_failures FROM source_health WHERE source=?", (source,)
        ).fetchone()
        failures = (int(row["consecutive_failures"]) if row else 0) + 1
        circuit_until = (now + cooldown).isoformat() if failures >= threshold else None
        self.connection.execute(
            """
            INSERT INTO source_health
                (source, consecutive_failures, circuit_open_until, last_failure_at, last_error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                consecutive_failures=excluded.consecutive_failures,
                circuit_open_until=excluded.circuit_open_until,
                last_failure_at=excluded.last_failure_at,
                last_error=excluded.last_error
            """,
            (source, failures, circuit_until, now.isoformat(), error[:4000]),
        )
        self.connection.commit()

    def circuit_is_open(self, source: str) -> bool:
        row = self.connection.execute(
            "SELECT circuit_open_until FROM source_health WHERE source=?", (source,)
        ).fetchone()
        return bool(
            row
            and row["circuit_open_until"]
            and datetime.fromisoformat(row["circuit_open_until"]) > utc_now()
        )

    def status(self, limit: int = 20) -> dict[str, Any]:
        counts = {
            row["status"]: row["count"]
            for row in self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM job_runs GROUP BY status"
            )
        }
        recent = [
            dict(row)
            for row in self.connection.execute(
                "SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]
        sources = [dict(row) for row in self.connection.execute("SELECT * FROM source_health")]
        watermarks = [dict(row) for row in self.connection.execute("SELECT * FROM watermarks")]
        return {
            "job_counts": counts,
            "recent_jobs": recent,
            "sources": sources,
            "watermarks": watermarks,
        }

    def backup(self, destination: Path | str) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup_connection = sqlite3.connect(target)
        try:
            self.connection.backup(backup_connection)
        finally:
            backup_connection.close()
        return target

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
