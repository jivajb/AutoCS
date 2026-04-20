"""
SQLite-backed store for workflow runs, traces, and review requests.
Customer account data is loaded from the mock JSON file at startup.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.workflow import ReviewRequest, RunStatus, WorkflowRun

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id      TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    completed_at TEXT,
    data        TEXT NOT NULL,   -- full WorkflowRun JSON blob
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    run_id        TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    data          TEXT NOT NULL,  -- full ReviewRequest JSON blob
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_account ON workflow_runs(account_id);
CREATE INDEX IF NOT EXISTS idx_runs_status  ON workflow_runs(status);
"""


class Store:
    def __init__(self, db_path: str = "autocs.db"):
        self.db_path = db_path
        self._accounts: Dict[str, Dict[str, Any]] = {}

    # ── Schema ────────────────────────────────────────────────────────────────

    def initialize(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
        logger.info("Store initialised at %s", self.db_path)

    # ── Account data (in-memory) ───────────────────────────────────────────────

    def load_accounts(self, accounts: List[Dict[str, Any]]) -> None:
        self._accounts = {a["account_id"]: a for a in accounts}
        logger.info("Loaded %d accounts into store", len(self._accounts))

    def get_account(self, account_id: str) -> Optional[Dict[str, Any]]:
        return self._accounts.get(account_id)

    def list_accounts(self) -> List[Dict[str, Any]]:
        return list(self._accounts.values())

    # ── Workflow runs ─────────────────────────────────────────────────────────

    def save_run(self, run: WorkflowRun) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO workflow_runs (run_id, account_id, status, started_at, completed_at, data)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.account_id,
                    run.status.value,
                    run.started_at.isoformat(),
                    run.completed_at.isoformat() if run.completed_at else None,
                    run.model_dump_json(),
                ),
            )

    def get_run(self, run_id: str) -> Optional[WorkflowRun]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return WorkflowRun.model_validate_json(row[0])

    def list_runs(self, account_id: Optional[str] = None) -> List[WorkflowRun]:
        with self._conn() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT data FROM workflow_runs WHERE account_id = ? ORDER BY started_at DESC",
                    (account_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT data FROM workflow_runs ORDER BY started_at DESC"
                ).fetchall()
        return [WorkflowRun.model_validate_json(r[0]) for r in rows]

    def list_pending_reviews(self) -> List[WorkflowRun]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT data FROM workflow_runs WHERE status = ? ORDER BY started_at DESC",
                (RunStatus.PENDING_REVIEW.value,),
            ).fetchall()
        return [WorkflowRun.model_validate_json(r[0]) for r in rows]

    # ── Reviews ───────────────────────────────────────────────────────────────

    def save_review(self, review: ReviewRequest) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reviews (run_id, account_id, data)
                VALUES (?, ?, ?)
                """,
                (review.run_id, review.account_id, review.model_dump_json()),
            )

    def get_review(self, run_id: str) -> Optional[ReviewRequest]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM reviews WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return ReviewRequest.model_validate_json(row[0])

    def update_review(self, review: ReviewRequest) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE reviews SET data = ?, reviewed_at = ? WHERE run_id = ?",
                (
                    review.model_dump_json(),
                    review.reviewed_at.isoformat() if review.reviewed_at else None,
                    review.run_id,
                ),
            )

    # ── Connection helper ─────────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        # For :memory: databases we must reuse the same connection; for file
        # databases a new connection per operation is fine (and thread-safe).
        if self.db_path == ":memory:":
            if not hasattr(self, "_mem_conn"):
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
            conn = self._mem_conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        else:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
