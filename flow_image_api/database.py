from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any


class QueueFullError(RuntimeError):
    pass


class JobStateError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    model TEXT NOT NULL,
                    aspect_ratio TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'processing', 'succeeded', 'failed')
                    ),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    lease_expires_at REAL,
                    worker_id TEXT,
                    error_code TEXT,
                    mime_type TEXT,
                    file_name TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_queue
                ON jobs(status, created_at);

                CREATE TABLE IF NOT EXISTS worker_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    worker_id TEXT NOT NULL,
                    last_seen_at REAL NOT NULL
                );
                """
            )

    def ping(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("SELECT 1").fetchone()

    def create_job(
        self,
        *,
        prompt: str,
        model: str,
        aspect_ratio: str,
        max_pending_jobs: int,
    ) -> dict[str, Any]:
        now = time.time()
        job_id = str(uuid.uuid4())
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'processing')"
            ).fetchone()[0]
            if pending >= max_pending_jobs:
                raise QueueFullError("The generation queue is full")
            connection.execute(
                """
                INSERT INTO jobs (
                    id, prompt, model, aspect_ratio, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, prompt, model, aspect_ratio, now, now),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return dict(row)

    def heartbeat(self, worker_id: str) -> float:
        now = time.time()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                INSERT INTO worker_state (singleton, worker_id, last_seen_at)
                VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    worker_id = excluded.worker_id,
                    last_seen_at = excluded.last_seen_at
                """,
                (worker_id, now),
            )
            connection.commit()
        return now

    def worker_state(self) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT worker_id, last_seen_at FROM worker_state WHERE singleton = 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def lease_next(self, *, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                   OR (status = 'processing' AND lease_expires_at < ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE jobs
                SET status = 'processing', worker_id = ?, lease_expires_at = ?,
                    updated_at = ?, error_code = NULL
                WHERE id = ?
                """,
                (worker_id, now + lease_seconds, now, row["id"]),
            )
            connection.commit()
            return self.get_job(row["id"])
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def complete_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        mime_type: str,
        file_name: str,
    ) -> dict[str, Any]:
        return self._finish_job(
            job_id=job_id,
            worker_id=worker_id,
            status="succeeded",
            mime_type=mime_type,
            file_name=file_name,
            error_code=None,
        )

    def fail_job(self, *, job_id: str, worker_id: str, error_code: str) -> dict[str, Any]:
        return self._finish_job(
            job_id=job_id,
            worker_id=worker_id,
            status="failed",
            mime_type=None,
            file_name=None,
            error_code=error_code,
        )

    def _finish_job(
        self,
        *,
        job_id: str,
        worker_id: str,
        status: str,
        mime_type: str | None,
        file_name: str | None,
        error_code: str | None,
    ) -> dict[str, Any]:
        now = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, worker_id FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] != "processing" or row["worker_id"] != worker_id:
                raise JobStateError("Job lease is no longer owned by this worker")
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, updated_at = ?, lease_expires_at = NULL,
                    error_code = ?, mime_type = ?, file_name = ?
                WHERE id = ?
                """,
                (status, now, error_code, mime_type, file_name, job_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_job(job_id)
