#!/usr/bin/env python3
"""PR7 codex round-6 P1 — INSERT-time terminal-status guard.

The BEFORE INSERT trigger blocks any direct INSERT that lands in a
terminal status. Non-terminal INSERTs flow through normally (so
mark_* + create_or_update_filing_job + backfill all keep working).
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


class TerminalInsertGuardTests(unittest.TestCase):
    def setUp(self):
        import server  # type: ignore

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self._original = server.DB_PATH
        server.DB_PATH = Path(self.tmp.name)
        server.init_db()
        self._server = server
        self.conn = sqlite3.connect(self.tmp.name)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            INSERT INTO orders (
                id, email, token, status, entity_type, state, business_name,
                formation_data, state_fee_cents, gov_processing_fee_cents,
                platform_fee_cents, total_cents
            )
            VALUES ('ORDER-INS', 'q@a.test', 'tk', 'paid', 'LLC', 'WY',
                    'Insert Guard LLC', '{}', 10200, 0, 2900, 13100)
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass
        self._server.DB_PATH = self._original

    def _try_insert(self, job_id: str, status: str) -> str:
        try:
            self.conn.execute(
                """
                INSERT INTO filing_jobs (
                    id, order_id, action_type, state, entity_type, status,
                    automation_level, filing_method, state_fee_cents,
                    processing_fee_cents, total_government_cents
                )
                VALUES (?, 'ORDER-INS', 'formation', 'WY', 'LLC', ?,
                        'manual_review', 'web_portal', 10200, 0, 10200)
                """,
                (job_id, status),
            )
            self.conn.commit()
            return ""
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            return str(exc)

    def test_insert_in_ready_to_file_succeeds(self):
        err = self._try_insert("JOB-OK", "ready_to_file")
        self.assertEqual(err, "")

    def test_insert_in_pending_payment_succeeds(self):
        err = self._try_insert("JOB-OK2", "pending_payment")
        self.assertEqual(err, "")

    def test_insert_in_submitted_blocks(self):
        err = self._try_insert("JOB-BAD-SUB", "submitted")
        self.assertIn("cannot be INSERTed in a terminal status", err)

    def test_insert_in_state_approved_blocks(self):
        err = self._try_insert("JOB-BAD-SA", "state_approved")
        self.assertIn("cannot be INSERTed in a terminal status", err)

    def test_insert_in_complete_blocks(self):
        err = self._try_insert("JOB-BAD-CMP", "complete")
        self.assertIn("cannot be INSERTed in a terminal status", err)


if __name__ == "__main__":
    unittest.main()
