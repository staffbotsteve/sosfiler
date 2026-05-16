#!/usr/bin/env python3
"""Tests for plan v2.6 §4.2.2 / §4.2.3.1 evidence schema additions.

These run against a temp SQLite database so they cannot collide with the
production sosfiler.db file. The goal is to lock in:

1. The `filing_artifacts` migration adds `sha256_hex` and
   `superseded_by_artifact_id`, plus the composite index.
2. The `FilingEvidenceRequest` schema accepts the widened type set.
3. The `sha256_for_evidence_path` helper returns a stable digest for an
   existing file and `None` for a missing path.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Plan v2.6 / codex P2 #4: prevent server import from mutating the working
# SQLite file. Must be set BEFORE the first `import server` happens anywhere.
os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


class EvidenceSchemaTests(unittest.TestCase):
    def setUp(self):
        # Minimal isolated DB; mirrors the columns the migration cares about.
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

    def _apply_migration(self):
        # Mimic server.init_db()'s _ensure_column block for the two new columns.
        from server import _ensure_column  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        _ensure_column(conn, "filing_artifacts", "sha256_hex", "TEXT")
        _ensure_column(conn, "filing_artifacts", "superseded_by_artifact_id", "INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_filing_artifacts_job_type "
            "ON filing_artifacts(filing_job_id, artifact_type, is_evidence)"
        )
        conn.commit()
        conn.close()

    def test_migration_adds_sha256_hex_column(self):
        self._apply_migration()
        conn = sqlite3.connect(self.path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(filing_artifacts)").fetchall()}
        conn.close()
        self.assertIn("sha256_hex", cols)
        self.assertIn("superseded_by_artifact_id", cols)

    def test_migration_adds_composite_index(self):
        self._apply_migration()
        conn = sqlite3.connect(self.path)
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(filing_artifacts)").fetchall()}
        conn.close()
        self.assertIn("idx_filing_artifacts_job_type", indexes)

    def test_migration_is_idempotent(self):
        self._apply_migration()
        # Running again must not raise.
        self._apply_migration()
        conn = sqlite3.connect(self.path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(filing_artifacts)").fetchall()}
        conn.close()
        self.assertIn("sha256_hex", cols)

    def test_filing_evidence_request_accepts_widened_types(self):
        from server import FilingEvidenceRequest  # type: ignore

        for artifact_type in (
            "state_acknowledgment",
            "state_filed_document",
            "registered_agent_partner_order",
        ):
            req = FilingEvidenceRequest(
                artifact_type=artifact_type,
                filename=f"{artifact_type}.pdf",
                file_path=f"/tmp/{artifact_type}.pdf",
            )
            self.assertEqual(req.artifact_type, artifact_type)

    def test_filing_evidence_request_still_rejects_unknown_types(self):
        from pydantic import ValidationError

        from server import FilingEvidenceRequest  # type: ignore

        with self.assertRaises(ValidationError):
            FilingEvidenceRequest(
                artifact_type="not_a_real_type",
                filename="x.pdf",
                file_path="/tmp/x.pdf",
            )


class Sha256HelperTests(unittest.TestCase):
    def test_returns_none_for_missing_path(self):
        from server import sha256_for_evidence_path  # type: ignore

        self.assertIsNone(sha256_for_evidence_path("/nonexistent/path/never.pdf"))
        self.assertIsNone(sha256_for_evidence_path(""))

    def test_returns_hex_digest_for_existing_file(self):
        from server import sha256_for_evidence_path  # type: ignore

        with tempfile.NamedTemporaryFile(delete=False) as fh:
            fh.write(b"plan v2.6 evidence payload")
            path = fh.name
        try:
            digest = sha256_for_evidence_path(path)
        finally:
            os.unlink(path)
        expected = hashlib.sha256(b"plan v2.6 evidence payload").hexdigest()
        self.assertEqual(digest, expected)


if __name__ == "__main__":
    unittest.main()
