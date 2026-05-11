#!/usr/bin/env python3
"""Tests for state metadata, vault visibility, chat grounding, and filing safety gates."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class PlatformSafetyAndMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_docs_dir = server.DOCS_DIR
        self.old_stripe_key = server.STRIPE_SECRET_KEY
        self.old_stripe_webhook_secret = server.STRIPE_WEBHOOK_SECRET
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        self.old_openai = os.environ.get("OPENAI_API_KEY")
        self.old_pii_key = os.environ.get("PII_ENCRYPTION_KEY")
        self.old_corpnet = {
            key: os.environ.get(key)
            for key in (
                "CORPNET_API_BASE_URL",
                "CORPNET_API_KEY",
                "CORPNET_RA_QUOTE_PATH",
                "CORPNET_RA_ORDER_PATH",
                "CORPNET_RA_STATUS_PATH_TEMPLATE",
            )
        }
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["ADMIN_TOKEN"] = "test-admin"
        os.environ["PII_ENCRYPTION_KEY"] = "test-key-for-platform-safety-and-metadata"
        os.environ.pop("OPENAI_API_KEY", None)
        server.STRIPE_SECRET_KEY = None
        server.STRIPE_WEBHOOK_SECRET = None
        for key in self.old_corpnet:
            os.environ.pop(key, None)
        server.DB_PATH = Path(self.tmp.name) / "platform.db"
        server.DOCS_DIR = Path(self.tmp.name) / "generated_docs"
        server.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        server.init_db()
        self.client = TestClient(server.app)
        self.headers = {"x-admin-token": "test-admin"}

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        server.DOCS_DIR = self.old_docs_dir
        server.STRIPE_SECRET_KEY = self.old_stripe_key
        server.STRIPE_WEBHOOK_SECRET = self.old_stripe_webhook_secret
        if self.old_mode is None:
            os.environ.pop("EXECUTION_PERSISTENCE_MODE", None)
        else:
            os.environ["EXECUTION_PERSISTENCE_MODE"] = self.old_mode
        if self.old_admin is None:
            os.environ.pop("ADMIN_TOKEN", None)
        else:
            os.environ["ADMIN_TOKEN"] = self.old_admin
        if self.old_openai is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.old_openai
        if self.old_pii_key is None:
            os.environ.pop("PII_ENCRYPTION_KEY", None)
        else:
            os.environ["PII_ENCRYPTION_KEY"] = self.old_pii_key
        for key, value in self.old_corpnet.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def create_formation_order(self, *, ra_choice="sosfiler", status="payment_authorized"):
        order_id = "ORD-SAFETY-TX"
        formation_data = {
            "business_name": "Safety Gate LLC",
            "state": "TX",
            "entity_type": "LLC",
            "principal_address": "1 Main St",
            "principal_city": "Austin",
            "principal_state": "TX",
            "principal_zip": "78701",
            "ra_choice": ra_choice,
            "ra_name": "Jane Agent",
            "ra_address": "1 Main St",
            "ra_city": "Austin",
            "ra_state": "TX",
            "ra_zip": "78701",
            "members": [{
                "name": "Jane Owner",
                "address": "1 Main St",
                "city": "Austin",
                "state": "TX",
                "zip_code": "78701",
            }],
        }
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO orders (
                id, email, token, status, entity_type, state, business_name,
                formation_data, state_fee_cents, gov_processing_fee_cents,
                platform_fee_cents, total_cents, paid_at
            )
            VALUES (?, 'safety@sosfiler.com', 'tok-safe', ?, 'LLC', 'TX', 'Safety Gate LLC',
                    ?, 30000, 0, 4900, 34900, datetime('now'))
            """,
            (order_id, status, server.json.dumps(formation_data)),
        )
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.commit()
        conn.close()
        return dict(row)

    def test_shared_order_api_uses_requested_state_prefix(self):
        formation = {
            "email": "qa+co-prefix@sosfiler.com",
            "entity_type": "LLC",
            "state": "CO",
            "business_name": "Colorado Prefix Test LLC",
            "principal_address": "1 Market St",
            "principal_city": "Denver",
            "principal_state": "CO",
            "principal_zip": "80202",
            "ra_choice": "self",
            "ra_name": "Jane Agent",
            "ra_address": "1 Market St",
            "ra_city": "Denver",
            "ra_state": "CO",
            "ra_zip": "80202",
            "members": [{
                "name": "Jane Owner",
                "address": "1 Market St",
                "city": "Denver",
                "state": "CO",
                "zip_code": "80202",
                "ownership_pct": 100,
                "ssn_itin": "123456789",
                "is_responsible_party": True,
            }],
            "responsible_party_ssn": "123456789",
        }

        res = self.client.post("/api/orders", json={"product_type": "formation", "formation": formation})

        self.assertEqual(res.status_code, 200, res.text)
        order_id = res.json()["order_id"]
        self.assertTrue(order_id.startswith("CO-"), order_id)
        conn = server.get_db()
        order = conn.execute("SELECT id, state FROM orders WHERE id = ?", (order_id,)).fetchone()
        job = conn.execute("SELECT order_id, state FROM filing_jobs WHERE order_id = ?", (order_id,)).fetchone()
        pii_count = conn.execute("SELECT count(*) FROM pii_vault WHERE subject_id = ?", (order_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(order["state"], "CO")
        self.assertEqual(job["state"], "CO")
        self.assertEqual(pii_count, 1)

    def test_state_metadata_endpoint_summarizes_every_state(self):
        res = self.client.get("/api/admin/state-metadata?action_type=formation&entity_type=LLC", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertGreaterEqual(payload["summary"]["states"], 51)
        tx = next(record for record in payload["records"] if record["state"] == "TX")
        self.assertIn("filing_lane", tx)
        self.assertIn("blocker_level", tx)
        self.assertIn("portal_url", tx)
        self.assertIn("evidence_requirements", tx)
        self.assertIn("automation_readiness", tx)

    def test_formation_submission_blocks_missing_ra_assignment_then_allows_after_evidence(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")
        job = server.create_or_update_filing_job(order, "formation", "ready_to_file")
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO documents (order_id, doc_type, filename, file_path, format, category, visibility)
            VALUES (?, 'filing_authorization', 'authorization.pdf', '/tmp/authorization.pdf', 'pdf', 'authorization', 'customer')
            """,
            (order["id"],),
        )
        conn.commit()
        conn.close()

        blocked = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={"artifact_type": "submitted_receipt", "filename": "receipt.pdf", "file_path": "/tmp/receipt.pdf"},
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("registered_agent_ready", str(blocked.json()))

        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
            VALUES (?, ?, 'registered_agent_assignment', 'ra.pdf', '/tmp/ra.pdf', 1)
            """,
            (job["id"], order["id"]),
        )
        conn.commit()
        conn.close()
        submitted = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={"artifact_type": "submitted_receipt", "filename": "receipt.pdf", "file_path": "/tmp/receipt.pdf"},
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)
        duplicate = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={"artifact_type": "submitted_receipt", "filename": "receipt-2.pdf", "file_path": "/tmp/receipt-2.pdf"},
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("duplicate_filing_risk", str(duplicate.json()))

    def test_payment_reconcile_capture_gate_blocks_submission_until_captured(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="payment_authorized")
        job = server.create_or_update_filing_job(order, "formation", "ready_to_file")
        quote_id = "QUOTE-SAFETY-TX"
        line_items = [
            {"code": "platform_fee", "label": "SOSFiler service fee", "amount_cents": 4900, "kind": "revenue"},
            {"code": "government_fee", "label": "Texas state filing fee", "amount_cents": 30000, "kind": "passthrough"},
        ]
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO execution_quotes (
                id, order_id, product_type, entity_type, state, line_items,
                estimated_total_cents, authorized_total_cents, reconciliation_status, idempotency_key
            )
            VALUES (?, ?, 'formation', 'LLC', 'TX', ?, 34900, 34900, 'authorized', 'idem-payment-test')
            """,
            (quote_id, order["id"], server.json.dumps(line_items)),
        )
        conn.execute(
            """
            INSERT INTO documents (order_id, doc_type, filename, file_path, format, category, visibility)
            VALUES (?, 'filing_authorization', 'authorization.pdf', '/tmp/authorization.pdf', 'pdf', 'authorization', 'customer')
            """,
            (order["id"],),
        )
        conn.execute(
            """
            INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
            VALUES (?, ?, 'registered_agent_assignment', 'ra.pdf', '/tmp/ra.pdf', 1)
            """,
            (job["id"], order["id"]),
        )
        conn.execute(
            """
            INSERT INTO payment_ledger (
                id, order_id, quote_id, event_type, amount_cents, idempotency_key, raw_event
            )
            VALUES ('PAY-AUTH-SAFETY', ?, ?, 'authorized', 34900, 'idem-authorized-test', '{}')
            """,
            (order["id"], quote_id),
        )
        conn.commit()
        conn.close()

        blocked = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={"artifact_type": "submitted_receipt", "filename": "receipt.pdf", "file_path": "/tmp/receipt.pdf"},
        )
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("payment_capture_required", str(blocked.json()))

        reconciled = self.client.post(
            f"/api/admin/orders/{order['id']}/payment-reconcile",
            headers=self.headers,
            json={
                "final_government_fee_cents": 30000,
                "final_processing_fee_cents": 0,
                "actor": "qa",
            },
        )
        self.assertEqual(reconciled.status_code, 200, reconciled.text)
        self.assertEqual(reconciled.json()["reconciliation_status"], "ready_to_capture")
        self.assertTrue(reconciled.json()["can_capture"])

        captured = self.client.post(
            f"/api/admin/orders/{order['id']}/capture-payment",
            headers=self.headers,
            json={"actor": "qa"},
        )
        self.assertEqual(captured.status_code, 200, captured.text)
        self.assertTrue(captured.json()["captured"])
        self.assertTrue(captured.json()["dry_run"])

        submitted = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={"artifact_type": "submitted_receipt", "filename": "receipt.pdf", "file_path": "/tmp/receipt.pdf"},
        )
        self.assertEqual(submitted.status_code, 200, submitted.text)

    def test_adapter_matrix_runs_dry_preflight_for_every_state(self):
        res = self.client.get("/api/admin/adapter-matrix?action_type=formation&entity_type=LLC", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertGreaterEqual(payload["summary"]["states"], 51)
        self.assertEqual(len(payload["records"]), payload["summary"]["states"])
        tx = next(record for record in payload["records"] if record["state"] == "TX")
        self.assertIn(tx["dry_run_status"], {"ready", "operator_required", "blocked"})
        self.assertIn("preflight_passed", tx)

    def test_payment_readiness_surfaces_authorized_order_filing_prep_blockers(self):
        order = self.create_formation_order(ra_choice="self", status="payment_authorized")
        server.create_or_update_filing_job(order, "formation", "ready_to_file")
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO execution_quotes (
                id, order_id, product_type, entity_type, state, line_items,
                estimated_total_cents, authorized_total_cents, reconciliation_status, idempotency_key
            )
            VALUES ('QUOTE-READINESS-TX', ?, 'formation', 'LLC', 'TX', '[]', 34900, 34900, 'authorized', 'idem-readiness')
            """,
            (order["id"],),
        )
        conn.execute(
            """
            INSERT INTO payment_ledger (
                id, order_id, quote_id, event_type, amount_cents, idempotency_key, raw_event
            )
            VALUES ('PAY-READINESS-TX', ?, 'QUOTE-READINESS-TX', 'authorized', 34900, 'idem-readiness-ledger', '{}')
            """,
            (order["id"],),
        )
        conn.commit()
        conn.close()

        res = self.client.get("/api/admin/payment-readiness?state=TX", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        item = next(record for record in res.json()["orders"] if record["order_id"] == order["id"])
        self.assertEqual(item["readiness_status"], "needs_filing_prep")
        self.assertEqual(item["quote_count"], 1)
        self.assertEqual(item["ledger_count"], 1)
        blocked_codes = {check["code"] for check in item["checks"] if check["status"] == "blocked"}
        self.assertIn("filing_authorization", blocked_codes)

    def test_prepare_filing_generates_authorization_and_routes_ready_job(self):
        order = self.create_formation_order(ra_choice="self", status="paid")

        res = self.client.post(
            f"/api/admin/orders/{order['id']}/prepare-filing",
            headers=self.headers,
            json={"actor": "qa", "message": "Prepare filing from test."},
        )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["job"]["status"], "ready_to_file")
        blocker_codes = {item["code"] for item in payload["remaining_blockers"]}
        self.assertNotIn("filing_authorization", blocker_codes)
        generated_filenames = {doc["filename"] for doc in payload["generated_documents"]}
        self.assertIn("filing_authorization.pdf", generated_filenames)
        conn = server.get_db()
        auth_doc = conn.execute(
            "SELECT * FROM documents WHERE order_id = ? AND doc_type = 'filing_authorization' AND filename = 'filing_authorization.pdf'",
            (order["id"],),
        ).fetchone()
        order_status = conn.execute("SELECT status, documents_ready_at FROM orders WHERE id = ?", (order["id"],)).fetchone()
        conn.close()
        self.assertIsNotNone(auth_doc)
        self.assertEqual(order_status["status"], "ready_to_file")
        self.assertTrue(order_status["documents_ready_at"])

    def test_prepare_filing_attempts_partner_ra_and_keeps_blocker_without_credentials(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")

        res = self.client.post(
            f"/api/admin/orders/{order['id']}/prepare-filing",
            headers=self.headers,
            json={"actor": "qa"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertTrue(payload["registered_agent"]["dry_run"])
        self.assertFalse(payload["registered_agent"]["evidence_attached"])
        blocker_codes = {item["code"] for item in payload["remaining_blockers"]}
        self.assertIn("registered_agent_ready", blocker_codes)
        self.assertEqual(payload["job"]["status"], "operator_required")

    def test_prepare_filing_auto_attaches_successful_partner_ra_evidence(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")

        class SuccessfulClient:
            configured = True
            def create_registered_agent_order(self, payload):
                return {"ok": True, "data": {"external_order_id": "RA-PREP-123", "status": "assigned"}}

        with patch("server.CorpNetClient", return_value=SuccessfulClient()):
            res = self.client.post(
                f"/api/admin/orders/{order['id']}/prepare-filing",
                headers=self.headers,
                json={"actor": "qa"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertTrue(payload["registered_agent"]["evidence_attached"])
        self.assertEqual(payload["job"]["status"], "ready_to_file")
        blocker_codes = {item["code"] for item in payload["remaining_blockers"]}
        self.assertNotIn("registered_agent_ready", blocker_codes)
        conn = server.get_db()
        artifact_count = conn.execute(
            "SELECT count(*) FROM filing_artifacts WHERE order_id = ? AND artifact_type = 'registered_agent_assignment'",
            (order["id"],),
        ).fetchone()[0]
        doc_count = conn.execute(
            "SELECT count(*) FROM documents WHERE order_id = ? AND doc_type = 'registered_agent_assignment'",
            (order["id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(artifact_count, 1)
        self.assertEqual(doc_count, 1)

    def test_quote_backed_ready_job_still_requires_capture_before_submission(self):
        order = self.create_formation_order(ra_choice="self", status="paid")
        prep = self.client.post(
            f"/api/admin/orders/{order['id']}/prepare-filing",
            headers=self.headers,
            json={"actor": "qa"},
        )
        self.assertEqual(prep.status_code, 200, prep.text)
        job = prep.json()["job"]
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO execution_quotes (
                id, order_id, product_type, entity_type, state, line_items,
                estimated_total_cents, authorized_total_cents, reconciliation_status, idempotency_key
            )
            VALUES ('QUOTE-READY-NOT-CAPTURED', ?, 'formation', 'LLC', 'TX', '[]', 34900, 34900, 'authorized', 'idem-ready-not-captured')
            """,
            (order["id"],),
        )
        conn.commit()
        conn.close()

        blocked = self.client.post(
            f"/api/admin/filing-jobs/{job['id']}/mark-submitted",
            headers=self.headers,
            json={"artifact_type": "submitted_receipt", "filename": "receipt.pdf", "file_path": "/tmp/receipt.pdf"},
        )

        self.assertEqual(blocked.status_code, 400)
        self.assertIn("payment_capture_required", str(blocked.json()))

    def test_registered_agent_fulfillment_without_partner_credentials_stays_dry_run(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")
        job = server.create_or_update_filing_job(order, "formation", "ready_to_file")

        res = self.client.post(
            f"/api/admin/orders/{order['id']}/registered-agent/fulfill",
            headers=self.headers,
            json={"actor": "qa"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["dry_run"])
        self.assertFalse(res.json()["configured"])
        conn = server.get_db()
        count = conn.execute(
            "SELECT count(*) FROM filing_artifacts WHERE filing_job_id = ? AND artifact_type = 'registered_agent_assignment'",
            (job["id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_registered_agent_fulfillment_attaches_only_successful_partner_evidence(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")
        job = server.create_or_update_filing_job(order, "formation", "ready_to_file")

        class FailingClient:
            configured = True
            def create_registered_agent_order(self, payload):
                return {"ok": False, "error": "partner_rejected"}

        with patch("server.CorpNetClient", return_value=FailingClient()):
            failed = self.client.post(
                f"/api/admin/orders/{order['id']}/registered-agent/fulfill",
                headers=self.headers,
                json={"actor": "qa"},
            )
        self.assertEqual(failed.status_code, 200, failed.text)
        self.assertFalse(failed.json()["evidence_attached"])

        class SuccessfulClient:
            configured = True
            def create_registered_agent_order(self, payload):
                return {"ok": True, "data": {"external_order_id": "RA-123", "status": "assigned"}}

        with patch("server.CorpNetClient", return_value=SuccessfulClient()):
            succeeded = self.client.post(
                f"/api/admin/orders/{order['id']}/registered-agent/fulfill",
                headers=self.headers,
                json={"actor": "qa"},
            )
        self.assertEqual(succeeded.status_code, 200, succeeded.text)
        self.assertTrue(succeeded.json()["evidence_attached"])
        conn = server.get_db()
        count = conn.execute(
            "SELECT count(*) FROM filing_artifacts WHERE filing_job_id = ? AND artifact_type = 'registered_agent_assignment'",
            (job["id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_registered_agent_fulfillment_tracks_pending_partner_order_without_evidence(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")
        job = server.create_or_update_filing_job(order, "formation", "ready_to_file")

        class PendingClient:
            configured = True
            def create_registered_agent_order(self, payload):
                return {"ok": True, "data": {"external_order_id": "RA-PENDING-123", "status": "pending"}}

        with patch("server.CorpNetClient", return_value=PendingClient()):
            res = self.client.post(
                f"/api/admin/orders/{order['id']}/registered-agent/fulfill",
                headers=self.headers,
                json={"actor": "qa"},
            )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(res.json()["evidence_attached"])
        self.assertTrue(res.json()["partner_order_created"])
        conn = server.get_db()
        assignment_count = conn.execute(
            "SELECT count(*) FROM filing_artifacts WHERE filing_job_id = ? AND artifact_type = 'registered_agent_assignment'",
            (job["id"],),
        ).fetchone()[0]
        partner_count = conn.execute(
            "SELECT count(*) FROM filing_artifacts WHERE filing_job_id = ? AND artifact_type = 'registered_agent_partner_order'",
            (job["id"],),
        ).fetchone()[0]
        conn.close()
        self.assertEqual(assignment_count, 0)
        self.assertEqual(partner_count, 1)

    def test_registered_agent_reconciliation_dry_runs_without_credentials(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")
        server.create_or_update_filing_job(order, "formation", "ready_to_file")

        res = self.client.post(
            "/api/admin/corpnet/registered-agent/reconcile",
            headers=self.headers,
            json={"actor": "qa", "order_id": order["id"]},
        )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertTrue(payload["dry_run"])
        self.assertFalse(payload["configured"])
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertFalse(payload["candidates"][0]["assignment_attached"])

    def test_registered_agent_reconciliation_attaches_when_partner_status_ready(self):
        order = self.create_formation_order(ra_choice="sosfiler", status="paid")
        server.create_or_update_filing_job(order, "formation", "ready_to_file")
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO documents (order_id, doc_type, filename, file_path, format, category, visibility)
            VALUES (?, 'filing_authorization', 'authorization.pdf', '/tmp/authorization.pdf', 'pdf', 'authorization', 'customer')
            """,
            (order["id"],),
        )
        conn.commit()
        conn.close()

        class PendingThenReadyClient:
            configured = True
            def create_registered_agent_order(self, payload):
                return {"ok": True, "data": {"external_order_id": "RA-READY-123", "status": "pending"}}
            def get_registered_agent_order(self, external_order_id):
                return {"ok": True, "data": {"external_order_id": external_order_id, "status": "active"}}

        with patch("server.CorpNetClient", return_value=PendingThenReadyClient()):
            pending = self.client.post(
                f"/api/admin/orders/{order['id']}/registered-agent/fulfill",
                headers=self.headers,
                json={"actor": "qa"},
            )
            reconciled = self.client.post(
                "/api/admin/corpnet/registered-agent/reconcile",
                headers=self.headers,
                json={"actor": "qa", "order_id": order["id"]},
            )

        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(reconciled.status_code, 200, reconciled.text)
        self.assertEqual(reconciled.json()["evidence_attached"], 1)
        conn = server.get_db()
        artifact_count = conn.execute(
            "SELECT count(*) FROM filing_artifacts WHERE order_id = ? AND artifact_type = 'registered_agent_assignment'",
            (order["id"],),
        ).fetchone()[0]
        order_status = conn.execute("SELECT status FROM orders WHERE id = ?", (order["id"],)).fetchone()["status"]
        conn.close()
        self.assertEqual(artifact_count, 1)
        self.assertEqual(order_status, "ready_to_file")

    def test_stripe_authorization_webhook_is_idempotent(self):
        order = self.create_formation_order(ra_choice="self", status="pending_payment")
        quote_id = "QUOTE-WEBHOOK-TX"
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO execution_quotes (
                id, order_id, product_type, entity_type, state, line_items,
                estimated_total_cents, reconciliation_status, idempotency_key
            )
            VALUES (?, ?, 'formation', 'LLC', 'TX', '[]', 34900, 'authorization_started', 'idem-webhook-test')
            """,
            (quote_id, order["id"]),
        )
        conn.commit()
        conn.close()
        event = {
            "id": "evt_webhook_authorized",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_auth",
                    "amount_total": 34900,
                    "payment_intent": "pi_test_auth",
                    "metadata": {
                        "order_id": order["id"],
                        "quote_id": quote_id,
                        "capture_strategy": "authorize_then_capture",
                    },
                }
            },
        }
        first = self.client.post("/api/webhooks/stripe", json=event)
        second = self.client.post("/api/webhooks/stripe", json=event)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["duplicate"])
        conn = server.get_db()
        order_status = conn.execute("SELECT status, stripe_payment_intent FROM orders WHERE id = ?", (order["id"],)).fetchone()
        ledger_count = conn.execute("SELECT count(*) FROM payment_ledger WHERE order_id = ?", (order["id"],)).fetchone()[0]
        webhook_count = conn.execute("SELECT count(*) FROM stripe_webhook_events WHERE id = 'evt_webhook_authorized'").fetchone()[0]
        conn.close()
        self.assertIn(order_status["status"], {"payment_authorized", "ready_to_file"})
        self.assertEqual(order_status["stripe_payment_intent"], "pi_test_auth")
        self.assertEqual(ledger_count, 1)
        self.assertEqual(webhook_count, 1)

    def test_additional_authorization_dry_run_and_cutover_readiness(self):
        order = self.create_formation_order(ra_choice="self", status="payment_authorized")
        quote_id = "QUOTE-ADD-AUTH"
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO execution_quotes (
                id, order_id, product_type, entity_type, state, line_items,
                estimated_total_cents, authorized_total_cents, reconciliation_status, idempotency_key
            )
            VALUES (?, ?, 'formation', 'LLC', 'TX', '[]', 39900, 34900, 'additional_authorization_required', 'idem-add-auth')
            """,
            (quote_id, order["id"]),
        )
        conn.commit()
        conn.close()

        res = self.client.post(
            f"/api/admin/orders/{order['id']}/request-additional-authorization",
            headers=self.headers,
            json={"actor": "qa"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["dry_run"])
        self.assertEqual(res.json()["amount_cents"], 5000)

        readiness = self.client.get("/api/admin/persistence/cutover-readiness", headers=self.headers)
        self.assertEqual(readiness.status_code, 200, readiness.text)
        self.assertFalse(readiness.json()["ready"])
        self.assertIn("checks", readiness.json())

    def test_document_visibility_and_download_audit(self):
        order = self.create_formation_order(ra_choice="self", status="paid")
        visible = Path(self.tmp.name) / "visible.pdf"
        hidden = Path(self.tmp.name) / "hidden.pdf"
        visible.write_text("visible", encoding="utf-8")
        hidden.write_text("hidden", encoding="utf-8")
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO documents (order_id, doc_type, filename, file_path, format, category, visibility)
            VALUES (?, 'approved_certificate', 'visible.pdf', ?, 'pdf', 'official_evidence', 'customer')
            """,
            (order["id"], str(visible)),
        )
        conn.execute(
            """
            INSERT INTO documents (order_id, doc_type, filename, file_path, format, category, visibility)
            VALUES (?, 'ein_ss4_data', 'hidden.pdf', ?, 'pdf', 'sensitive_source', 'hidden')
            """,
            (order["id"], str(hidden)),
        )
        conn.commit()
        conn.close()

        listed = self.client.get(f"/api/documents/{order['id']}?token=tok-safe")
        self.assertEqual(listed.status_code, 200, listed.text)
        filenames = {doc["filename"] for doc in listed.json()["documents"]}
        self.assertEqual(filenames, {"visible.pdf"})

        downloaded = self.client.get(f"/api/documents/{order['id']}/download/visible.pdf?token=tok-safe")
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        hidden_res = self.client.get(f"/api/documents/{order['id']}/download/hidden.pdf?token=tok-safe")
        self.assertEqual(hidden_res.status_code, 404)
        audit = self.client.get(f"/api/admin/document-access-events?order_id={order['id']}", headers=self.headers)
        self.assertEqual(audit.status_code, 200, audit.text)
        self.assertEqual(len(audit.json()["events"]), 1)
        self.assertEqual(audit.json()["events"][0]["filename"], "visible.pdf")

    def test_chat_answers_verified_fee_and_escalates_uncovered_question(self):
        answered = self.client.post(
            "/api/chat",
            json={
                "message": "What is the filing fee and total cost in Texas?",
                "session_id": "chat-fee",
                "context": {"state": "TX", "entity_type": "llc", "product_type": "formation"},
            },
        )
        self.assertEqual(answered.status_code, 200, answered.text)
        self.assertFalse(answered.json()["escalated"])
        self.assertIn("estimated total", answered.json()["response"].lower())
        self.assertTrue(answered.json()["sources"])

        escalated = self.client.post(
            "/api/chat",
            json={
                "message": "Should I take venture capital next month?",
                "session_id": "chat-ticket",
                "context": {"state": "TX", "entity_type": "llc", "product_type": "formation"},
            },
        )
        self.assertEqual(escalated.status_code, 200, escalated.text)
        self.assertTrue(escalated.json()["escalated"])
        conn = server.get_db()
        count = conn.execute("SELECT count(*) FROM support_tickets WHERE session_id = 'chat-ticket'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
