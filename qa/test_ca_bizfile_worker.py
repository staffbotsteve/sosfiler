#!/usr/bin/env python3
"""Tests for the California BizFile worker safety and status mapping."""

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import ca_bizfile_worker as worker  # noqa: E402


class CaliforniaBizFileWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db = worker.DB_PATH
        self.old_docs = worker.DOCS_DIR
        self.old_protocol_manifest = worker.PROTOCOL_MANIFEST_PATH
        self.old_env = {key: os.environ.get(key) for key in worker.RAW_CARD_ENV_KEYS}
        self.old_payment_mode = os.environ.get("CA_BIZFILE_PAYMENT_MODE")
        worker.DB_PATH = Path(self.tmp.name) / "sosfiler.db"
        worker.DOCS_DIR = Path(self.tmp.name) / "generated_docs"
        worker.PROTOCOL_MANIFEST_PATH = Path(self.tmp.name) / "ca_bizfile_protocol_manifest.json"
        worker.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def tearDown(self):
        worker.DB_PATH = self.old_db
        worker.DOCS_DIR = self.old_docs
        worker.PROTOCOL_MANIFEST_PATH = self.old_protocol_manifest
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self.old_payment_mode is None:
            os.environ.pop("CA_BIZFILE_PAYMENT_MODE", None)
        else:
            os.environ["CA_BIZFILE_PAYMENT_MODE"] = self.old_payment_mode
        self.tmp.cleanup()

    def init_db(self):
        conn = sqlite3.connect(worker.DB_PATH)
        conn.executescript(
            """
            CREATE TABLE orders (
                id TEXT PRIMARY KEY,
                business_name TEXT,
                email TEXT,
                token TEXT,
                status TEXT,
                formation_data TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                submitted_at TEXT,
                approved_at TEXT
            );
            CREATE TABLE filing_jobs (
                id TEXT PRIMARY KEY,
                order_id TEXT,
                state TEXT,
                status TEXT,
                submitted_at TEXT,
                approved_at TEXT,
                updated_at TEXT,
                evidence_summary TEXT
            );
            CREATE TABLE filing_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_job_id TEXT,
                order_id TEXT,
                event_type TEXT,
                message TEXT,
                actor TEXT,
                evidence_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE filing_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filing_job_id TEXT,
                order_id TEXT,
                artifact_type TEXT,
                filename TEXT,
                file_path TEXT,
                is_evidence INTEGER
            );
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                doc_type TEXT,
                filename TEXT,
                file_path TEXT,
                format TEXT
            );
            CREATE TABLE status_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                status TEXT,
                message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        formation = {
            "business_name": "California Autopilot LLC",
            "principal_address": "100 Market St",
            "principal_city": "Sacramento",
            "principal_state": "CA",
            "principal_zip": "95814",
            "ra_name": "Casey Agent",
            "ra_address": "100 Market St",
            "ra_city": "Sacramento",
            "ra_zip": "95814",
            "management_type": "member-managed",
        }
        conn.execute(
            """
            INSERT INTO orders (id, business_name, email, token, status, formation_data)
            VALUES ('CA-TEST', 'California Autopilot LLC', 'qa@sosfiler.com', 'tok', 'submitted_to_state', ?)
            """,
            (json.dumps(formation),),
        )
        conn.execute(
            """
            INSERT INTO filing_jobs (id, order_id, state, status)
            VALUES ('FIL-CA-TEST', 'CA-TEST', 'CA', 'submitted_to_state')
            """
        )
        conn.commit()
        conn.close()

    def test_classifies_ca_status_text(self):
        self.assertEqual(worker.classify_ca_status("Articles of Organization - CA LLC Status: Pending Review"), "pending_state_review")
        self.assertEqual(worker.classify_ca_status("California Autopilot LLC Active Filed"), "approved")
        self.assertEqual(worker.classify_ca_status("Needs Correction: organizer signature missing"), "rejected_or_needs_correction")
        self.assertEqual(worker.classify_ca_status("No matching record"), "unknown")

    def test_payment_configuration_blocks_raw_card_environment(self):
        os.environ["CA_BIZFILE_CARD_NUMBER"] = "4242424242424242"
        os.environ["CA_BIZFILE_PAYMENT_MODE"] = "saved_portal_method"

        config = worker.payment_configuration()

        self.assertFalse(config["ready"])
        self.assertEqual(config["mode"], "blocked_raw_card_env")
        self.assertIn("CA_BIZFILE_CARD_NUMBER", config["raw_card_env_keys"])

    def test_dry_run_status_finds_submitted_ca_jobs_without_browser(self):
        payload = asyncio.run(worker.run_worker(operation="status", dry_run=True, limit=10))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["results"][0]["order_id"], "CA-TEST")
        self.assertEqual(payload["results"][0]["status"], "would_status")

    def test_filing_payload_uses_customer_values(self):
        conn = worker.get_db()
        order = conn.execute("SELECT * FROM orders WHERE id = 'CA-TEST'").fetchone()
        conn.close()

        payload = worker.filing_payload_from_order(order)

        self.assertEqual(payload.business_name, "California Autopilot LLC")
        self.assertEqual(payload.principal_city, "Sacramento")
        self.assertEqual(payload.ra_first_name, "Casey")
        self.assertEqual(payload.ra_last_name, "Agent")
        self.assertEqual(worker.missing_payload_fields(payload), [])

    def test_status_worker_block_does_not_downgrade_submitted_job(self):
        conn = worker.get_db()
        job = conn.execute("SELECT * FROM filing_jobs WHERE id = 'FIL-CA-TEST'").fetchone()

        worker.record_access_block(conn, job, worker.BizFileBlocked("credentials_missing", "login required"))
        conn.commit()

        status = conn.execute("SELECT status FROM filing_jobs WHERE id = 'FIL-CA-TEST'").fetchone()[0]
        event_count = conn.execute(
            "SELECT count(*) FROM filing_events WHERE event_type = 'ca_bizfile_worker_blocked'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(status, "submitted_to_state")
        self.assertEqual(event_count, 1)

    def test_protocol_manifest_redacts_identity_query_values(self):
        url = (
            "https://accounts.google.com/signin/oauth?"
            "client_id=abc&code=secret-code&state=session-state&redirect_uri=https%3A%2F%2Fsosfiler.com"
        )

        redacted = worker.redact_url(url)

        self.assertIn("client_id=abc", redacted)
        self.assertIn("code=REDACTED", redacted)
        self.assertIn("state=REDACTED", redacted)
        self.assertNotIn("secret-code", redacted)
        self.assertNotIn("session-state", redacted)

    def test_protocol_endpoint_classification_finds_candidates(self):
        self.assertEqual(
            worker.classify_protocol_endpoint("GET", "https://bizfileonline.sos.ca.gov/api/report/GetImageByNum/123", "xhr", "application/pdf"),
            "document_image_endpoint",
        )
        self.assertEqual(
            worker.classify_protocol_endpoint("GET", "https://bizfileonline.sos.ca.gov/_Incapsula_Resource?SW=1", "script", "text/html"),
            "access_challenge",
        )
        self.assertEqual(
            worker.classify_protocol_endpoint("POST", "https://bizfileonline.sos.ca.gov/api/business/search", "fetch", "application/json"),
            "portal_api_candidate",
        )

    def test_build_protocol_manifest_deduplicates_endpoints(self):
        observations = [
            {
                "method": "GET",
                "url": "https://bizfileonline.sos.ca.gov/api/report/GetImageByNum/123",
                "status": 200,
                "resource_type": "xhr",
                "content_type": "application/pdf",
                "kind": "document_image_endpoint",
            },
            {
                "method": "GET",
                "url": "https://bizfileonline.sos.ca.gov/api/report/GetImageByNum/123",
                "status": 304,
                "resource_type": "xhr",
                "content_type": "application/pdf",
                "kind": "document_image_endpoint",
            },
        ]

        manifest = worker.build_protocol_manifest(observations, business_name="California Autopilot LLC")

        self.assertEqual(manifest["state"], "CA")
        self.assertEqual(manifest["endpoint_count"], 1)
        self.assertEqual(manifest["endpoints"][0]["count"], 2)
        self.assertEqual(set(manifest["endpoints"][0]["statuses"]), {200, 304})

    def test_discover_dry_run_does_not_open_browser(self):
        payload = asyncio.run(worker.run_worker(operation="discover", dry_run=True, business_name="California Autopilot LLC"))

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["results"][0]["status"], "would_discover")
        self.assertIn("ca_bizfile_protocol_manifest.json", payload["results"][0]["manifest_path"])

    def test_protocol_discovery_returns_manifest_when_queue_is_blocked(self):
        class FakePage:
            async def goto(self, *args, **kwargs):
                return None

            async def wait_for_timeout(self, *args, **kwargs):
                return None

        async def fake_observe_protocol_requests(page, observations):
            observations.append({
                "method": "GET",
                "url": "https://bizfileonline.sos.ca.gov/api/report/GetImageByNum/123",
                "status": 200,
                "resource_type": "xhr",
                "content_type": "application/pdf",
                "kind": "document_image_endpoint",
            })

        async def fake_noop(*args, **kwargs):
            return None

        async def fake_public_search(*args, **kwargs):
            return "", ""

        async def fake_checkpoint_snapshot(*args, **kwargs):
            return str(Path(self.tmp.name) / "final.png")

        async def fake_open_work_queue(*args, **kwargs):
            raise worker.BizFileBlocked("work_queue_link_not_found", "queue not visible", "/tmp/queue.png")

        originals = {
            "observe_protocol_requests": worker.observe_protocol_requests,
            "login_if_needed": worker.login_if_needed,
            "assert_not_blocked": worker.assert_not_blocked,
            "public_business_search": worker.public_business_search,
            "checkpoint_snapshot": worker.checkpoint_snapshot,
            "open_work_queue": worker.open_work_queue,
        }
        try:
            worker.observe_protocol_requests = fake_observe_protocol_requests
            worker.login_if_needed = fake_noop
            worker.assert_not_blocked = fake_noop
            worker.public_business_search = fake_public_search
            worker.checkpoint_snapshot = fake_checkpoint_snapshot
            worker.open_work_queue = fake_open_work_queue

            payload = asyncio.run(worker.run_protocol_discovery_probe(FakePage(), business_name="California Autopilot LLC"))
        finally:
            for name, original in originals.items():
                setattr(worker, name, original)

        self.assertEqual(payload["status"], "protocol_manifest_captured_with_blockers")
        self.assertEqual(payload["endpoint_count"], 1)
        self.assertEqual(payload["blockers"][0]["code"], "work_queue_link_not_found")
        self.assertTrue(Path(payload["manifest_path"]).exists())


if __name__ == "__main__":
    unittest.main()
