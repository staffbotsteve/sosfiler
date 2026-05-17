#!/usr/bin/env python3
"""Track B follow-up #2 tests: escalate_to_operator_required helper.

Plan v2.6 §4.5 / audit recommendation #2. The three live workers used
to log a worker_error event and stamp results['status']='error' but
never flipped filing_jobs/orders to operator_required. Cockpit work
queue never surfaced the failure. New shared helper closes the gap.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


def _bootstrap(conn):
    conn.executescript(
        """
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'paid',
            updated_at TEXT
        );
        CREATE TABLE filing_jobs (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'automation_started',
            evidence_summary TEXT,
            updated_at TEXT
        );
        CREATE TABLE filing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_job_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT,
            actor TEXT NOT NULL DEFAULT 'system',
            evidence_path TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE status_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO orders (id) VALUES ('ORD-ESC');
        INSERT INTO filing_jobs (id, order_id) VALUES ('JOB-ESC', 'ORD-ESC');
        """
    )
    conn.commit()


class EscalationHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        _bootstrap(self.conn)

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_happy_path_updates_all_four_rows(self):
        from execution_platform import escalate_to_operator_required  # type: ignore

        escalate_to_operator_required(
            self.conn,
            filing_job_id="JOB-ESC",
            order_id="ORD-ESC",
            source="ca_bizfile",
            error_message="TimeoutError: form did not load",
        )
        self.conn.commit()

        job = self.conn.execute("SELECT status, evidence_summary FROM filing_jobs WHERE id='JOB-ESC'").fetchone()
        order = self.conn.execute("SELECT status FROM orders WHERE id='ORD-ESC'").fetchone()
        event = self.conn.execute(
            "SELECT event_type, message, actor FROM filing_events WHERE filing_job_id='JOB-ESC'"
        ).fetchone()
        status_update = self.conn.execute(
            "SELECT status, message FROM status_updates WHERE order_id='ORD-ESC'"
        ).fetchone()

        self.assertEqual(job["status"], "operator_required")
        self.assertIn("form did not load", job["evidence_summary"])
        self.assertEqual(order["status"], "operator_required")
        self.assertEqual(event["event_type"], "ca_bizfile_worker_error")
        self.assertEqual(event["actor"], "ca_bizfile")
        self.assertIn("form did not load", event["message"])
        self.assertEqual(status_update["status"], "operator_required")

    def test_helper_no_op_on_missing_args(self):
        from execution_platform import escalate_to_operator_required  # type: ignore

        # Each missing arg should be a quiet no-op; the helper must never
        # raise from inside an already-failing worker's except block.
        escalate_to_operator_required(
            self.conn, filing_job_id="", order_id="ORD-ESC",
            source="x", error_message="x",
        )
        escalate_to_operator_required(
            self.conn, filing_job_id="JOB-ESC", order_id="",
            source="x", error_message="x",
        )
        job = self.conn.execute("SELECT status FROM filing_jobs WHERE id='JOB-ESC'").fetchone()
        self.assertEqual(job["status"], "automation_started")  # untouched

    def test_helper_uses_safe_default_when_message_empty(self):
        from execution_platform import escalate_to_operator_required  # type: ignore

        escalate_to_operator_required(
            self.conn, filing_job_id="JOB-ESC", order_id="ORD-ESC",
            source="sosdirect", error_message="",
        )
        self.conn.commit()
        evt = self.conn.execute(
            "SELECT message FROM filing_events WHERE filing_job_id='JOB-ESC'"
        ).fetchone()
        self.assertIn("unhandled exception", evt["message"])

    def test_helper_event_type_uses_source_prefix(self):
        from execution_platform import escalate_to_operator_required  # type: ignore

        for source in ("ca_bizfile", "sosdirect", "silverflume"):
            escalate_to_operator_required(
                self.conn, filing_job_id="JOB-ESC", order_id="ORD-ESC",
                source=source, error_message=f"{source} crashed",
            )
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT event_type FROM filing_events WHERE filing_job_id='JOB-ESC' ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [r["event_type"] for r in rows],
            ["ca_bizfile_worker_error", "sosdirect_worker_error", "silverflume_worker_error"],
        )

    def test_helper_swallows_db_errors(self):
        """The helper must never raise from inside a worker's except block."""
        from execution_platform import escalate_to_operator_required  # type: ignore

        # Closed connection — any execute() will sqlite3.ProgrammingError.
        self.conn.close()
        # Should not raise.
        escalate_to_operator_required(
            self.conn, filing_job_id="JOB-ESC", order_id="ORD-ESC",
            source="x", error_message="x",
        )
        # Reopen for tearDown's cleanup.
        self.conn = sqlite3.connect(self.tmp.name)


if __name__ == "__main__":
    unittest.main()
