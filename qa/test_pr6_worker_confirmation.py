#!/usr/bin/env python3
"""PR6 tests: workers populate orders.filing_confirmation when promoting.

Plan v2.6 §4.5. Covers:
- execution_platform.extract_filing_confirmation regex helper.
- CA bizfile mark_submitted / mark_approved persist the canonical JSON when
  a confirmation value is supplied.
- TX SOSDirect attach_approval_document falls back to evidence_summary
  document_number / filing_number when the caller passes no value.
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

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


def _bootstrap_schema(conn):
    conn.executescript(
        """
        CREATE TABLE orders (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'paid',
            entity_type TEXT NOT NULL DEFAULT 'LLC',
            state TEXT NOT NULL DEFAULT 'CA',
            business_name TEXT NOT NULL DEFAULT 'Acme LLC',
            filing_confirmation TEXT,
            submitted_at TEXT,
            approved_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE filing_jobs (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            action_type TEXT NOT NULL DEFAULT 'formation',
            state TEXT NOT NULL DEFAULT 'CA',
            entity_type TEXT NOT NULL DEFAULT 'LLC',
            status TEXT NOT NULL DEFAULT 'ready_to_file',
            automation_level TEXT NOT NULL DEFAULT 'manual_review',
            filing_method TEXT NOT NULL DEFAULT 'web_portal',
            evidence_summary TEXT,
            submitted_at TEXT,
            approved_at TEXT,
            updated_at TEXT
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
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'pdf',
            category TEXT NOT NULL DEFAULT 'customer_document',
            visibility TEXT NOT NULL DEFAULT 'customer'
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
        INSERT INTO orders (id) VALUES ('ORDER-1');
        INSERT INTO filing_jobs (id, order_id) VALUES ('JOB-1', 'ORDER-1');
        """
    )
    conn.commit()


class ExtractConfirmationTests(unittest.TestCase):
    def test_regex_extracts_simple_filing_number(self):
        from execution_platform import extract_filing_confirmation  # type: ignore

        text = "Acknowledgement: Filing Number: 202612345678 has been recorded."
        pattern = r"Filing Number:\s*([0-9]+)"
        self.assertEqual(extract_filing_confirmation(text, pattern), "202612345678")

    def test_regex_returns_full_match_when_no_group(self):
        from execution_platform import extract_filing_confirmation  # type: ignore

        text = "Confirmation L25-99999 issued."
        self.assertEqual(extract_filing_confirmation(text, r"L25-99999"), "L25-99999")

    def test_regex_returns_none_on_empty_inputs(self):
        from execution_platform import extract_filing_confirmation  # type: ignore

        self.assertIsNone(extract_filing_confirmation("", r"."))
        self.assertIsNone(extract_filing_confirmation("any text", ""))
        self.assertIsNone(extract_filing_confirmation(None, "."))  # type: ignore[arg-type]

    def test_regex_returns_none_on_no_match(self):
        from execution_platform import extract_filing_confirmation  # type: ignore

        self.assertIsNone(extract_filing_confirmation("nothing here", r"[A-Z]{20}"))

    def test_regex_tolerates_invalid_pattern(self):
        from execution_platform import extract_filing_confirmation  # type: ignore

        # Unbalanced bracket — re.error should be swallowed.
        self.assertIsNone(extract_filing_confirmation("text", r"["))


class CaBizfileMarkPersistsConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        _bootstrap_schema(conn)
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_mark_submitted_writes_filing_confirmation_when_supplied(self):
        import ca_bizfile_worker  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            job = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-1'").fetchone()
            ca_bizfile_worker.mark_submitted(
                conn,
                job,
                "/tmp/ca-receipt.pdf",
                "Submitted via CA bizfile worker",
                filing_confirmation="202612345678",
            )
            conn.commit()
            row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-1'").fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row["filing_confirmation"])
        parsed = json.loads(row["filing_confirmation"])
        self.assertEqual(parsed["value"], "202612345678")
        self.assertEqual(parsed["source"], "adapter")

    def test_mark_submitted_skips_when_no_confirmation(self):
        import ca_bizfile_worker  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            job = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-1'").fetchone()
            ca_bizfile_worker.mark_submitted(conn, job, "/tmp/ca-receipt.pdf", "Submitted no conf")
            conn.commit()
            row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-1'").fetchone()
        finally:
            conn.close()
        # Quiet no-op: column remains untouched. Operator cockpit will populate via the
        # manual filing_confirmation input (PR4 UI) before the trigger gate enforces.
        self.assertIsNone(row["filing_confirmation"])

    def test_mark_approved_writes_filing_confirmation_when_supplied(self):
        import ca_bizfile_worker  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            job = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-1'").fetchone()
            ca_bizfile_worker.mark_approved(
                conn,
                job,
                "Approved",
                "/tmp/ca-approved.pdf",
                filing_confirmation="ABCD-1234-5678",
            )
            conn.commit()
            row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-1'").fetchone()
        finally:
            conn.close()

        parsed = json.loads(row["filing_confirmation"])
        self.assertEqual(parsed["value"], "ABCD-1234-5678")


class TxSosdirectAttachApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        _bootstrap_schema(conn)
        conn.execute(
            "UPDATE filing_jobs SET evidence_summary = ?, state = 'TX' WHERE id = 'JOB-1'",
            (json.dumps({"document_number": "987654321"}),),
        )
        conn.execute("UPDATE orders SET state = 'TX' WHERE id = 'ORDER-1'")
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def test_falls_back_to_evidence_summary_document_number(self):
        import tx_sosdirect_document_worker as tx  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            job = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-1'").fetchone()
            tx.attach_approval_document(conn, job, "cert.pdf", "/tmp/cert.pdf", "Approved")
            conn.commit()
            row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-1'").fetchone()
        finally:
            conn.close()
        parsed = json.loads(row["filing_confirmation"])
        self.assertEqual(parsed["value"], "987654321")

    def test_explicit_confirmation_wins_over_evidence_summary(self):
        import tx_sosdirect_document_worker as tx  # type: ignore

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            job = conn.execute("SELECT * FROM filing_jobs WHERE id = 'JOB-1'").fetchone()
            tx.attach_approval_document(
                conn, job, "cert.pdf", "/tmp/cert.pdf", "Approved",
                filing_confirmation="OVERRIDE-1111",
            )
            conn.commit()
            row = conn.execute("SELECT filing_confirmation FROM orders WHERE id = 'ORDER-1'").fetchone()
        finally:
            conn.close()
        parsed = json.loads(row["filing_confirmation"])
        self.assertEqual(parsed["value"], "OVERRIDE-1111")


if __name__ == "__main__":
    unittest.main()
