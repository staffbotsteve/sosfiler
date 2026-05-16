#!/usr/bin/env python3
"""PR4 tests: filing_artifacts INSERT centralization + mark-* confirmation.

Locks in three behaviors:

1. `insert_filing_artifact_row` (shared in execution_platform.py) writes the
   row with sha256_hex when provided.
2. The mark-submitted route 409s with MISSING_FILING_CONFIRMATION when the
   request omits filing_confirmation and the order has none on record.
3. The mark-approved + annual-report mark-complete routes both accept a
   pre-existing orders.filing_confirmation (set during mark-submitted) without
   requiring the value to be passed again.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


class InsertArtifactHelperTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
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
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_helper_writes_row_with_sha256(self):
        import sqlite3
        from execution_platform import insert_filing_artifact_row  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        rowid = insert_filing_artifact_row(
            conn,
            filing_job_id="JOB-1",
            order_id="ORDER-1",
            artifact_type="submitted_receipt",
            filename="r.pdf",
            file_path="/tmp/r.pdf",
            is_evidence=True,
            sha256_hex="deadbeef" * 8,
        )
        conn.commit()
        self.assertGreater(rowid, 0)
        row = conn.execute(
            "SELECT artifact_type, is_evidence, sha256_hex FROM filing_artifacts WHERE id = ?", (rowid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["artifact_type"], "submitted_receipt")
        self.assertEqual(row["is_evidence"], 1)
        self.assertEqual(row["sha256_hex"], "deadbeef" * 8)

    def test_helper_accepts_missing_sha256(self):
        import sqlite3
        from execution_platform import insert_filing_artifact_row  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        rowid = insert_filing_artifact_row(
            conn,
            filing_job_id="JOB-2",
            order_id="ORDER-2",
            artifact_type="state_correspondence",
            filename="c.pdf",
            file_path="/tmp/c.pdf",
            is_evidence=False,
        )
        conn.commit()
        row = conn.execute(
            "SELECT is_evidence, sha256_hex FROM filing_artifacts WHERE id = ?", (rowid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row["is_evidence"], 0)
        self.assertIsNone(row["sha256_hex"])


class ApplyConfirmationHelperTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        conn = sqlite3.connect(self.path)
        conn.executescript(
            """
            CREATE TABLE orders (
                id TEXT PRIMARY KEY,
                filing_confirmation TEXT,
                updated_at TEXT
            );
            """
        )
        conn.execute("INSERT INTO orders (id) VALUES ('ORDER-A')")
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _make_payload(self, value=None, source="operator"):
        # Lightweight stand-in for FilingEvidenceRequest / FilingTransitionRequest
        # since the helper reads attrs via getattr.
        from types import SimpleNamespace
        return SimpleNamespace(filing_confirmation=value, filing_confirmation_source=source)

    def test_helper_noop_for_non_evidence_target(self):
        import sqlite3
        from server import apply_filing_confirmation_for_transition  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        apply_filing_confirmation_for_transition(conn, "ORDER-A", "ready_to_file", self._make_payload())
        row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-A'").fetchone()
        conn.close()
        self.assertIsNone(row["filing_confirmation"])

    def test_helper_writes_when_payload_supplies_value(self):
        import sqlite3
        from server import apply_filing_confirmation_for_transition  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        apply_filing_confirmation_for_transition(
            conn, "ORDER-A", "submitted", self._make_payload(value="L25-99999", source="adapter")
        )
        conn.commit()
        row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-A'").fetchone()
        conn.close()
        parsed = json.loads(row["filing_confirmation"])
        self.assertEqual(parsed["value"], "L25-99999")
        self.assertEqual(parsed["source"], "adapter")

    def test_helper_409s_when_value_missing_and_no_existing(self):
        import sqlite3
        from fastapi import HTTPException

        from server import apply_filing_confirmation_for_transition  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        with self.assertRaises(HTTPException) as ctx:
            apply_filing_confirmation_for_transition(
                conn, "ORDER-A", "approved", self._make_payload()
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.detail["code"], "MISSING_FILING_CONFIRMATION")
        conn.close()

    def test_helper_accepts_existing_when_payload_empty(self):
        import sqlite3
        from server import apply_filing_confirmation_for_transition, build_filing_confirmation_payload  # type: ignore

        existing = build_filing_confirmation_payload("FILED-OK", "regex")
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE orders SET filing_confirmation = ? WHERE id = 'ORDER-A'", (existing,)
        )
        conn.commit()
        # No HTTPException expected.
        apply_filing_confirmation_for_transition(conn, "ORDER-A", "complete", self._make_payload())
        # Row unchanged.
        row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-A'").fetchone()
        conn.close()
        self.assertEqual(json.loads(row["filing_confirmation"])["value"], "FILED-OK")

    def test_helper_400s_on_malformed_value(self):
        import sqlite3
        from fastapi import HTTPException

        from server import apply_filing_confirmation_for_transition  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        with self.assertRaises(HTTPException) as ctx:
            apply_filing_confirmation_for_transition(
                conn, "ORDER-A", "submitted", self._make_payload(value="   ")
            )
        # Empty/whitespace value coerces to "no value supplied" → 409 (missing),
        # not 400 (malformed). The malformed-source path is what 400s.
        self.assertEqual(ctx.exception.status_code, 409)
        conn.close()

    def test_helper_400s_on_unknown_source(self):
        import sqlite3
        from fastapi import HTTPException

        from server import apply_filing_confirmation_for_transition  # type: ignore

        conn = sqlite3.connect(self.path)
        with self.assertRaises(HTTPException) as ctx:
            apply_filing_confirmation_for_transition(
                conn, "ORDER-A", "submitted", self._make_payload(value="X-1", source="hacked")
            )
        self.assertEqual(ctx.exception.status_code, 400)
        conn.close()


if __name__ == "__main__":
    unittest.main()
