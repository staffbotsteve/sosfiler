#!/usr/bin/env python3
"""Tests for SOSFiler deep health monitoring and alerts."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class HealthMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_docs_dir = server.DOCS_DIR
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        self.old_webhook = os.environ.get("SLACK_TICKETS_WEBHOOK_URL")
        self.old_secret = os.environ.get("SLACK_SIGNING_SECRET")
        self.old_interactive = os.environ.get("SLACK_INTERACTIVE_TICKETS")
        os.environ["ADMIN_TOKEN"] = "test-admin"
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["SLACK_TICKETS_WEBHOOK_URL"] = "https://hooks.slack.test/health"
        os.environ["SLACK_SIGNING_SECRET"] = "test-secret"
        os.environ["SLACK_INTERACTIVE_TICKETS"] = "true"
        server.DB_PATH = Path(self.tmp.name) / "health.db"
        server.DOCS_DIR = Path(self.tmp.name) / "generated_docs"
        server.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        server.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        server.DOCS_DIR = self.old_docs_dir
        for key, value in {
            "ADMIN_TOKEN": self.old_admin,
            "EXECUTION_PERSISTENCE_MODE": self.old_mode,
            "SLACK_TICKETS_WEBHOOK_URL": self.old_webhook,
            "SLACK_SIGNING_SECRET": self.old_secret,
            "SLACK_INTERACTIVE_TICKETS": self.old_interactive,
        }.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_public_deep_health_returns_component_statuses(self):
        with patch.object(server, "fetch_deploy_check_url", return_value=(200, "SOSFiler Operator Cockpit")):
            res = self.client.get("/api/health/deep")

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertTrue(payload["ok"])
        names = {component["name"] for component in payload["components"]}
        self.assertIn("database_write_read", names)
        self.assertIn("slack_ticket_loop", names)
        self.assertIn("public_operator_page", names)
        self.assertIn("worker_dependencies", names)
        self.assertIn("email_delivery", names)

    def test_admin_deep_health_can_alert_on_failure(self):
        alerts = []

        def fake_urlopen(request, timeout=0):
            alerts.append(request.full_url)

            class Response:
                status = 200

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def read(self):
                    return b"ok"

            return Response()

        with patch.object(server, "docs_directory_writable", return_value=(False, "disk full")):
            with patch.object(server, "fetch_deploy_check_url", return_value=(200, "SOSFiler Operator Cockpit")):
                with patch.object(server.urllib.request, "urlopen", side_effect=fake_urlopen):
                    res = self.client.get(
                        "/api/admin/health/deep?alert=true",
                        headers={"x-admin-token": "test-admin"},
                    )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["alert_sent"])
        self.assertIn("document_directory", payload["failing_components"])
        self.assertEqual(alerts, ["https://hooks.slack.test/health"])


if __name__ == "__main__":
    unittest.main()
