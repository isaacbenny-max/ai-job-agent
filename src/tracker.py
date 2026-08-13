"""SQLite-backed tracking of every job the agent has looked at or applied to.

Two jobs this module does:
1. Dedupe — never apply to the same job posting twice, even across runs
   on different days.
2. Audit trail — a local, queryable record of everything the agent did,
   which matters a lot for a tool that submits things on your behalf.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from src.models import ApplicationRecord, ApplicationStatus, JobPosting, Site

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    job_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    url TEXT,
    status TEXT NOT NULL,
    match_score INTEGER,
    match_reasoning TEXT,
    screenshot_path TEXT,
    error TEXT,
    applied_at TEXT NOT NULL,
    UNIQUE(site, job_id)
);
CREATE INDEX IF NOT EXISTS idx_applications_applied_at ON applications(applied_at);
"""


class ApplicationTracker:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def already_seen(self, job: JobPosting) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM applications WHERE site = ? AND job_id = ?",
                (job.site.value, job.job_id),
            ).fetchone()
            return row is not None

    def record(self, record: ApplicationRecord) -> None:
        job = record.job
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO applications
                    (site, job_id, title, company, location, url, status,
                     match_score, match_reasoning, screenshot_path, error, applied_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site, job_id) DO UPDATE SET
                    status=excluded.status,
                    match_score=excluded.match_score,
                    match_reasoning=excluded.match_reasoning,
                    screenshot_path=excluded.screenshot_path,
                    error=excluded.error,
                    applied_at=excluded.applied_at
                """,
                (
                    job.site.value,
                    job.job_id,
                    job.title,
                    job.company,
                    job.location,
                    job.url,
                    record.status.value,
                    record.match_score,
                    record.match_reasoning,
                    record.screenshot_path,
                    record.error,
                    record.applied_at.isoformat(),
                ),
            )

    def count_applications_today(self) -> int:
        today_prefix = date.today().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM applications
                WHERE status = ? AND applied_at LIKE ?
                """,
                (ApplicationStatus.SUBMITTED.value, f"{today_prefix}%"),
            ).fetchone()
            return row[0] if row else 0

    def summary(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM applications GROUP BY status"
            ).fetchall()
            return {status: count for status, count in rows}
