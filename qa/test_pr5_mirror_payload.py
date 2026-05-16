#!/usr/bin/env python3
"""PR5 tests: Supabase mirror payload includes filing_confirmation +
artifact sha256_hex / superseded_by.

These exercise the SupabaseExecutionRepository serializers in isolation —
no live Postgres connection — so they prove the dual-write payloads carry
the right fields. Once `supabase db push` applies the new migration, the
mirror will accept and store them; until then the dual-write soft-fails
with UndefinedColumn, which is expected in local QA mode.
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


class FilingJobPayloadTests(unittest.TestCase):
    def test_filing_job_payload_carries_confirmation_when_supplied(self):
        from execution_repository import SupabaseExecutionRepository  # type: ignore

        job = {
            "id": "JOB-1",
            "order_id": "ORDER-1",
            "product_type": "formation",
            "action_type": "formation",
            "state": "WY",
            "entity_type": "LLC",
            "status": "submitted",
            "filing_confirmation_raw": '{"value":"L25000012345","source":"adapter","issued_at":"2026-05-16T00:00:00Z"}',
        }
        payload = SupabaseExecutionRepository.filing_job_payload(job)
        self.assertEqual(
            payload["filing_confirmation"],
            '{"value":"L25000012345","source":"adapter","issued_at":"2026-05-16T00:00:00Z"}',
        )

    def test_filing_job_payload_omits_confirmation_when_absent(self):
        from execution_repository import SupabaseExecutionRepository  # type: ignore

        job = {
            "id": "JOB-2",
            "order_id": "ORDER-2",
            "product_type": "formation",
            "action_type": "formation",
            "state": "FL",
            "entity_type": "LLC",
            "status": "ready_to_file",
        }
        payload = SupabaseExecutionRepository.filing_job_payload(job)
        self.assertIsNone(payload["filing_confirmation"])

    def test_serialize_filing_job_with_confirmation_merges_orders_value(self):
        import server  # type: ignore

        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        conn = sqlite3.connect(tmp_db.name)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE filing_jobs (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    action_type TEXT NOT NULL DEFAULT 'formation',
                    state TEXT NOT NULL DEFAULT 'WY',
                    entity_type TEXT NOT NULL DEFAULT 'LLC',
                    status TEXT NOT NULL DEFAULT 'ready_to_file',
                    automation_level TEXT NOT NULL DEFAULT 'manual_review',
                    filing_method TEXT NOT NULL DEFAULT 'web_portal'
                );
                CREATE TABLE orders (
                    id TEXT PRIMARY KEY,
                    filing_confirmation TEXT
                );
                """
            )
            conn.execute("INSERT INTO orders (id, filing_confirmation) VALUES ('ORDER-X', ?)", (
                '{"value":"FL-99999","source":"regex","issued_at":"2026-05-16T00:00:00Z"}',
            ))
            conn.execute("INSERT INTO filing_jobs (id, order_id) VALUES ('JOB-X', 'ORDER-X')")
            conn.commit()
            row = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-X'").fetchone()
            payload = server.serialize_filing_job_with_confirmation(conn, row)
        finally:
            conn.close()
            try:
                os.unlink(tmp_db.name)
            except OSError:
                pass

        self.assertEqual(
            payload["filing_confirmation_raw"],
            '{"value":"FL-99999","source":"regex","issued_at":"2026-05-16T00:00:00Z"}',
        )

    def test_serialize_filing_job_with_confirmation_handles_missing_order(self):
        import server  # type: ignore

        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        conn = sqlite3.connect(tmp_db.name)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript(
                """
                CREATE TABLE filing_jobs (
                    id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    action_type TEXT NOT NULL DEFAULT 'formation',
                    state TEXT NOT NULL DEFAULT 'WY',
                    entity_type TEXT NOT NULL DEFAULT 'LLC',
                    status TEXT NOT NULL DEFAULT 'ready_to_file',
                    automation_level TEXT NOT NULL DEFAULT 'manual_review',
                    filing_method TEXT NOT NULL DEFAULT 'web_portal'
                );
                CREATE TABLE orders (id TEXT PRIMARY KEY, filing_confirmation TEXT);
                """
            )
            # filing_job exists but the matching order row does not.
            conn.execute("INSERT INTO filing_jobs (id, order_id) VALUES ('JOB-Y', 'ORDER-MISSING')")
            conn.commit()
            row = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-Y'").fetchone()
            payload = server.serialize_filing_job_with_confirmation(conn, row)
        finally:
            conn.close()
            try:
                os.unlink(tmp_db.name)
            except OSError:
                pass

        # Helper should degrade gracefully; mirror payload simply lacks the raw blob.
        self.assertNotIn("filing_confirmation_raw", payload)


if __name__ == "__main__":
    unittest.main()
