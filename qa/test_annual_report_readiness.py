#!/usr/bin/env python3
"""Tests for operator annual report readiness checklist."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class AnnualReportReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_docs_dir = server.DOCS_DIR
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["ADMIN_TOKEN"] = "test-admin"
        server.DB_PATH = Path(self.tmp.name) / "readiness.db"
        server.DOCS_DIR = Path(self.tmp.name) / "generated_docs"
        server.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        server.init_db()
        self.client = TestClient(server.app)
        self.headers = {"x-admin-token": "test-admin"}

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
        self.tmp.cleanup()

    def create_order(self, state="CA", status="paid"):
        order_id = f"ORD-{state}-AR"
        conn = server.get_db()
        conn.execute("""
            INSERT INTO orders (
                id, email, token, status, entity_type, state, business_name,
                formation_data, state_fee_cents, gov_processing_fee_cents,
                platform_fee_cents, total_cents, paid_at
            )
            VALUES (?, 'ops-test@sosfiler.com', 'tok-test', ?, 'LLC', ?, ?, '{}', 0, 0, 2500, 2500, datetime('now'))
        """, (order_id, status, state, f"{state} Annual LLC"))
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.commit()
        conn.close()
        return dict(row)

    def test_annual_report_detail_includes_operator_readiness_checklist(self):
        order = self.create_order("CA")
        job = server.create_or_update_filing_job(order, "annual_report", "ready_to_file")

        res = self.client.get(f"/api/admin/filing-jobs/{job['id']}", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        checklist = res.json()["job"]["readiness_checklist"]
        labels = {item["label"]: item for item in checklist}
        self.assertIn("Annual report requirement verified", labels)
        self.assertEqual(labels["Annual report requirement verified"]["status"], "ready")
        self.assertIn("Within 90 days", labels["Due window known"]["detail"])
        self.assertIn("$20", labels["Government fee identified"]["detail"])
        self.assertIn("Evidence gates configured", labels)

    def test_non_annual_report_jobs_show_general_filing_readiness_not_annual_readiness(self):
        order = self.create_order("CA")
        job = server.create_or_update_filing_job(order, "formation", "ready_to_file")

        res = self.client.get(f"/api/admin/filing-jobs/{job['id']}", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        checklist = res.json()["job"]["readiness_checklist"]
        labels = {item["label"] for item in checklist}
        self.assertIn("Payment authorization", labels)
        self.assertIn("Registered agent", labels)
        self.assertIn("State adapter contract", labels)
        self.assertNotIn("Annual report requirement verified", labels)

    def test_states_without_annual_report_requirement_are_flagged_for_review(self):
        order = self.create_order("AZ")
        job = server.create_or_update_filing_job(order, "annual_report", "ready_to_file")

        res = self.client.get(f"/api/admin/filing-jobs/{job['id']}", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        checklist = res.json()["job"]["readiness_checklist"]
        requirement = next(item for item in checklist if item["key"] == "annual_report_required")
        self.assertEqual(requirement["status"], "needs_review")
        self.assertIn("may not be required", requirement["detail"])

    def test_prepare_annual_report_packet_creates_artifact_and_customer_document(self):
        order = self.create_order("CA")
        job = server.create_or_update_filing_job(order, "annual_report", "ready_to_file")

        res = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/annual-report/prepare-packet",
            headers=self.headers,
            json={"actor": "tester"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        artifact = res.json()["artifact"]
        self.assertEqual(artifact["artifact_type"], "annual_report_packet")
        self.assertTrue(Path(artifact["file_path"]).exists())
        self.assertIn("Evidence Gates", Path(artifact["file_path"]).read_text())
        conn = server.get_db()
        doc = conn.execute(
            "SELECT * FROM documents WHERE order_id = ? AND doc_type = 'annual_report_packet'",
            (order["id"],),
        ).fetchone()
        conn.close()
        self.assertIsNotNone(doc)

    def test_annual_report_submit_approve_and_complete_are_evidence_gated(self):
        order = self.create_order("CA")
        job = server.create_or_update_filing_job(order, "annual_report", "ready_to_file")

        missing = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={"artifact_type": "submitted_receipt", "filename": "receipt.pdf", "file_path": ""},
        )
        self.assertEqual(missing.status_code, 400)

        submitted = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={
                "artifact_type": "submitted_receipt",
                "filename": "receipt.pdf",
                "file_path": "/tmp/annual-report-receipt.pdf",
                "message": "Annual report submitted.",
            },
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        self.assertEqual(submitted.json()["status"], "submitted")

        approved = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-approved",
            headers=self.headers,
            json={
                "artifact_type": "approved_certificate",
                "filename": "accepted.pdf",
                "file_path": "/tmp/annual-report-accepted.pdf",
                "message": "Annual report accepted.",
            },
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "approved")

        complete = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/annual-report/mark-complete",
            headers=self.headers,
            json={
                "artifact_type": "approved_certificate",
                "filename": "complete.pdf",
                "file_path": "/tmp/annual-report-complete.pdf",
                "message": "Annual report complete.",
            },
        )
        self.assertEqual(complete.status_code, 200, complete.text)
        self.assertEqual(complete.json()["status"], "complete")

        status = self.client.get(f"/api/status/{order['id']}?token=tok-test")
        self.assertEqual(status.status_code, 200, status.text)
        payload = status.json()
        timeline_statuses = [item["status"] for item in payload["timeline"]]
        self.assertIn("submitted", timeline_statuses)
        self.assertIn("approved", timeline_statuses)
        self.assertIn("complete", timeline_statuses)
        self.assertEqual(payload["filing_job"]["action_type"], "annual_report")


if __name__ == "__main__":
    unittest.main()
