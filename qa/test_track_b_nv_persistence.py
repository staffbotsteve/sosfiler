#!/usr/bin/env python3
"""Track B follow-up tests: NV silverflume persistence helper.

Verifies that _persist_filing_result writes the expected DB state for
both happy-path and needs_human_review outcomes. The full Playwright
flow is out of scope for unit tests; we exercise the helper directly
against an isolated temp SQLite file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")
# Provide a dummy TWOCAPTCHA_API_KEY so importing silverflume_filer
# doesn't blow up on the SilverFlumeFiler constructor check (the test
# never instantiates SilverFlumeFiler, but the env var must exist).
os.environ.setdefault("TWOCAPTCHA_API_KEY", "test-key-for-track-b-tests")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


def _bootstrap_schema(conn):
    conn.executescript(
        """
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'paid',
            entity_type TEXT NOT NULL DEFAULT 'LLC',
            state TEXT NOT NULL DEFAULT 'NV',
            business_name TEXT NOT NULL DEFAULT 'Track B LLC',
            filing_confirmation TEXT,
            submitted_at TEXT,
            approved_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE filing_jobs (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            action_type TEXT NOT NULL DEFAULT 'formation',
            state TEXT NOT NULL DEFAULT 'NV',
            entity_type TEXT NOT NULL DEFAULT 'LLC',
            status TEXT NOT NULL DEFAULT 'automation_started',
            automation_level TEXT NOT NULL DEFAULT 'manual_review',
            filing_method TEXT NOT NULL DEFAULT 'web_portal',
            evidence_summary TEXT,
            submitted_at TEXT,
            approved_at TEXT,
            updated_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE filing_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_job_id TEXT NOT NULL,
            order_id TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            is_evidence INTEGER NOT NULL DEFAULT 0,
            sha256_hex TEXT,
            superseded_by_artifact_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE status_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO orders (id) VALUES ('ORDER-NV');
        INSERT INTO filing_jobs (id, order_id) VALUES ('JOB-NV', 'ORDER-NV');
        """
    )
    conn.commit()


class SilverFlumePersistenceTests(unittest.TestCase):
    def setUp(self):
        import silverflume_filer

        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        conn = sqlite3.connect(self.tmp_db.name)
        _bootstrap_schema(conn)
        conn.close()
        self._silverflume = silverflume_filer
        self._original_db = silverflume_filer.DB_PATH
        silverflume_filer.DB_PATH = Path(self.tmp_db.name)

        # Place a real placeholder file so sha256_for_file_path returns a digest.
        self.tmp_screenshot = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self.tmp_screenshot.write(b"\x89PNG\r\n\x1a\nplaceholder")
        self.tmp_screenshot.close()
        self.screenshot_path = self.tmp_screenshot.name

    def tearDown(self):
        self._silverflume.DB_PATH = self._original_db
        for p in (self.tmp_db.name, self.screenshot_path):
            try:
                os.unlink(p)
            except OSError:
                pass

    def _read_state(self):
        conn = sqlite3.connect(self.tmp_db.name)
        conn.row_factory = sqlite3.Row
        try:
            job = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-NV'").fetchone()
            order = conn.execute("SELECT * FROM orders WHERE id = 'ORDER-NV'").fetchone()
            artifacts = conn.execute(
                "SELECT artifact_type, filename, is_evidence, sha256_hex FROM filing_artifacts "
                "WHERE filing_job_id = 'JOB-NV' ORDER BY id"
            ).fetchall()
            return dict(job) if job else None, dict(order) if order else None, [dict(a) for a in artifacts]
        finally:
            conn.close()

    def test_needs_human_review_flips_status_and_saves_screenshot(self):
        result = {
            "success": False,
            "needs_human_review": True,
            "reason": "Incapsula challenge not solved",
            "screenshots": [self.screenshot_path],
            "errors": [],
        }
        self._silverflume._persist_filing_result("ORDER-NV", result)

        job, order, artifacts = self._read_state()
        self.assertEqual(job["status"], "operator_required")
        self.assertEqual(order["status"], "operator_required")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["artifact_type"], "state_correspondence")
        self.assertEqual(artifacts[0]["is_evidence"], 1)
        self.assertIsNotNone(artifacts[0]["sha256_hex"])
        # status_updates row landed.
        conn = sqlite3.connect(self.tmp_db.name)
        try:
            row = conn.execute(
                "SELECT status, message FROM status_updates WHERE order_id = 'ORDER-NV'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "operator_required")
        self.assertIn("Incapsula", row[1])

    def test_successful_result_writes_filing_confirmation_canonical_json(self):
        result = {
            "success": True,
            "confirmation_number": "NV-2026-555111",
            "screenshots": [],
            "errors": [],
        }
        self._silverflume._persist_filing_result("ORDER-NV", result)

        _, order, _ = self._read_state()
        self.assertIsNotNone(order["filing_confirmation"])
        parsed = json.loads(order["filing_confirmation"])
        self.assertEqual(parsed["value"], "NV-2026-555111")
        self.assertEqual(parsed["source"], "adapter")

    def test_helper_is_safe_when_order_has_no_filing_job(self):
        # Remove the filing_job entirely; helper should silently skip.
        conn = sqlite3.connect(self.tmp_db.name)
        conn.execute("DELETE FROM filing_jobs")
        conn.commit()
        conn.close()
        # Should not raise.
        self._silverflume._persist_filing_result(
            "ORDER-NV",
            {"success": False, "needs_human_review": True, "reason": "x", "screenshots": []},
        )

    def test_helper_no_op_on_empty_order_id(self):
        self._silverflume._persist_filing_result(
            "", {"success": True, "confirmation_number": "X-1"}
        )
        # No state mutated.
        _, order, _ = self._read_state()
        self.assertIsNone(order["filing_confirmation"])

    def test_duplicate_screenshot_skipped_on_second_call(self):
        result = {
            "success": False,
            "needs_human_review": True,
            "reason": "first attempt blocked",
            "screenshots": [self.screenshot_path],
        }
        self._silverflume._persist_filing_result("ORDER-NV", result)
        # Run again — same filename. Helper must not double-insert.
        self._silverflume._persist_filing_result("ORDER-NV", result)
        _, _, artifacts = self._read_state()
        self.assertEqual(len(artifacts), 1)


if __name__ == "__main__":
    unittest.main()
