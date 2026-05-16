#!/usr/bin/env python3
"""Tests for EIN queue hardening and evidence completion."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class EinQueueHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_docs_dir = server.DOCS_DIR
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        self.old_jwt = os.environ.get("JWT_SECRET")
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["ADMIN_TOKEN"] = "test-admin"
        os.environ["JWT_SECRET"] = "test-jwt-secret-for-pii"
        server.DB_PATH = Path(self.tmp.name) / "ein.db"
        server.DOCS_DIR = Path(self.tmp.name) / "generated_docs"
        server.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        server.init_db()
        self.client = TestClient(server.app)
        self.headers = {"x-admin-token": "test-admin"}
        # Plan v2.6 PR4: insert_filing_artifact hashes evidence files.
        Path("/tmp/ein.pdf").write_bytes(b"%PDF-1.4 plan-v2.6 placeholder")

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        server.DOCS_DIR = self.old_docs_dir
        if self.old_mode is None:
            os.environ.pop("EXECUTION_PERSISTENCE_MODE", None)
        else:
            os.environ["EXECUTION_PERSISTENCE_MODE"] = self.old_mode
        if self.old_admin is None:
            os.environ.pop("ADMIN_TOKEN", None)
        else:
            os.environ["ADMIN_TOKEN"] = self.old_admin
        if self.old_jwt is None:
            os.environ.pop("JWT_SECRET", None)
        else:
            os.environ["JWT_SECRET"] = self.old_jwt
        self.tmp.cleanup()

    def create_order_with_vault_ssn(self):
        order_id = "ORD-EIN-1"
        vault_id = server.store_sensitive_value("order", order_id, "responsible_party_ssn", "123456789", "test")
        formation_data = {
            "responsible_party_ssn_vault_id": vault_id,
            "responsible_party_ssn_last4": "6789",
            "members": [{"name": "Test Owner", "is_responsible_party": True, "ssn_last4": "6789"}],
        }
        conn = server.get_db()
        conn.execute("""
            INSERT INTO orders (
                id, email, token, status, entity_type, state, business_name,
                formation_data, state_fee_cents, gov_processing_fee_cents,
                platform_fee_cents, total_cents, paid_at, approved_at
            )
            VALUES (?, 'ein-test@sosfiler.com', 'tok-ein', 'state_approved', 'LLC', 'CA', 'EIN Test LLC', ?, 0, 0, 4900, 4900, datetime('now'), datetime('now'))
        """, (order_id, json.dumps(formation_data)))
        order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
        conn.commit()
        conn.close()
        server.create_or_update_filing_job(order, "formation", "state_approved")
        queue = {
            "order_id": order_id,
            "status": "ready_for_submission",
            "ss4_data": {
                "responsible_party": {
                    "name": "Test Owner",
                    "ssn": "",
                    "ssn_vault_id": vault_id,
                    "ssn_last4": "6789",
                }
            },
        }
        path = server.ein_queue_path(order_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(queue))
        return order_id, vault_id

    def test_prepare_submission_audits_pii_access_inside_irs_hours(self):
        order_id, vault_id = self.create_order_with_vault_ssn()
        availability = {"open": True, "window": "test", "reason": "open", "timezone": "America/New_York", "current_time": "2026-05-11T09:00:00-04:00"}

        with patch.object(server, "irs_ein_availability", return_value=availability):
            res = self.client.post(
                f"/api/admin/ein-queue/{order_id}/prepare-submission",
                headers=self.headers,
                json={"actor": "tester"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "ready_for_browser_submission")
        queue = json.loads(server.ein_queue_path(order_id).read_text())
        self.assertEqual(queue["status"], "ready_for_browser_submission")
        self.assertNotIn("123456789", server.ein_queue_path(order_id).read_text())
        conn = server.get_db()
        access = conn.execute("SELECT * FROM pii_access_events WHERE vault_id = ?", (vault_id,)).fetchone()
        conn.close()
        self.assertIsNotNone(access)
        self.assertEqual(access["purpose"], "ein_submission_prepare")

    def test_prepare_submission_blocks_outside_irs_hours_without_pii_access(self):
        order_id, vault_id = self.create_order_with_vault_ssn()
        availability = {"open": False, "window": "test", "reason": "closed", "timezone": "America/New_York", "current_time": "2026-05-10T09:00:00-04:00"}

        with patch.object(server, "irs_ein_availability", return_value=availability):
            res = self.client.post(
                f"/api/admin/ein-queue/{order_id}/prepare-submission",
                headers=self.headers,
                json={"actor": "tester"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "blocked_outside_irs_hours")
        conn = server.get_db()
        access = conn.execute("SELECT * FROM pii_access_events WHERE vault_id = ?", (vault_id,)).fetchone()
        conn.close()
        self.assertIsNone(access)

    def test_complete_ein_requires_evidence_and_publishes_document(self):
        order_id, _ = self.create_order_with_vault_ssn()

        missing = self.client.post(
            f"/api/admin/ein-queue/{order_id}/complete",
            headers=self.headers,
            json={"actor": "tester", "ein": "12-3456789", "filename": "ein.pdf", "file_path": ""},
        )
        self.assertEqual(missing.status_code, 400)

        done = self.client.post(
            f"/api/admin/ein-queue/{order_id}/complete",
            headers=self.headers,
            json={"actor": "tester", "ein": "123456789", "filename": "ein.pdf", "file_path": "/tmp/ein.pdf"},
        )
        self.assertEqual(done.status_code, 200, done.text)
        conn = server.get_db()
        order = conn.execute("SELECT ein, status FROM orders WHERE id = ?", (order_id,)).fetchone()
        doc = conn.execute("SELECT * FROM documents WHERE order_id = ? AND doc_type = 'ein_confirmation_letter'", (order_id,)).fetchone()
        conn.close()
        self.assertEqual(order["ein"], "12-3456789")
        self.assertEqual(order["status"], "ein_received")
        self.assertIsNotNone(doc)


if __name__ == "__main__":
    unittest.main()
