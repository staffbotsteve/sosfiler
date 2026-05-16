#!/usr/bin/env python3
"""Tests for safe email delivery configuration and diagnostics."""

import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from notifier import Notifier  # noqa: E402


class EmailDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.old_env = {
            key: os.environ.get(key)
            for key in (
                "ADMIN_TOKEN",
                "EMAIL_DELIVERY_MODE",
                "SENDGRID_API_KEY",
                "SENDGRID_FROM_EMAIL",
                "SENDGRID_TEST_RECIPIENT",
                "SOSFILER_EMAIL_LIVE",
            )
        }
        os.environ["ADMIN_TOKEN"] = "test-admin"
        os.environ.pop("EMAIL_DELIVERY_MODE", None)
        os.environ.pop("SOSFILER_EMAIL_LIVE", None)
        os.environ["SENDGRID_API_KEY"] = "SG.test-key-present-but-not-used"
        os.environ["SENDGRID_FROM_EMAIL"] = "verified@example.com"
        os.environ["SENDGRID_TEST_RECIPIENT"] = "admin@example.com"
        self.old_email_verification_path = server.EMAIL_LIVE_VERIFICATION_PATH
        self.tmp = tempfile.TemporaryDirectory()
        server.EMAIL_LIVE_VERIFICATION_PATH = Path(self.tmp.name) / "email_live_verification.json"
        self.client = TestClient(server.app)

    def tearDown(self):
        server.EMAIL_LIVE_VERIFICATION_PATH = self.old_email_verification_path
        self.tmp.cleanup()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_default_email_mode_is_noop_even_when_key_exists(self):
        notifier = Notifier()

        status = notifier.config_status()
        result = asyncio.run(notifier.send_test_email("customer@example.com"))

        self.assertTrue(status["ok"])
        self.assertEqual(status["mode"], "noop")
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "noop")
        self.assertFalse(result.get("live_send", True))

    def test_sendgrid_mode_requires_api_key(self):
        os.environ["EMAIL_DELIVERY_MODE"] = "sendgrid"
        os.environ.pop("SENDGRID_API_KEY", None)

        notifier = Notifier()
        status = notifier.config_status()
        with self.assertLogs("notifier", level="WARNING"):
            result = asyncio.run(notifier.send_test_email("customer@example.com"))

        self.assertFalse(status["ok"])
        self.assertEqual(status["mode"], "sendgrid")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "sendgrid_not_configured")

    def test_admin_email_status_endpoint_is_non_live(self):
        response = self.client.get(
            "/api/admin/email/config-status",
            headers={"x-admin-token": "test-admin"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "noop")
        self.assertFalse(payload["live_send_performed"])
        self.assertFalse(payload["launch_gate"]["launch_ready"])
        self.assertEqual(payload["launch_gate"]["status"], "not_verified")

    def test_admin_email_test_records_redacted_live_verification_status(self):
        response = self.client.post(
            "/api/admin/email/test",
            headers={"x-admin-token": "test-admin"},
            json={"to_email": "admin@example.com", "subject": "Launch gate test"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(server.EMAIL_LIVE_VERIFICATION_PATH.exists())
        recorded = server.read_email_live_verification()
        self.assertEqual(recorded["to_email"], "a***@example.com")
        self.assertTrue(recorded["verification_recorded"])
        self.assertFalse(recorded["verified"])
        self.assertTrue(payload["recorded_live_verification"]["verification_recorded"])
        self.assertFalse(payload["launch_gate"]["launch_ready"])

    def test_email_live_verification_write_failure_does_not_500(self):
        with mock.patch("pathlib.Path.mkdir", side_effect=PermissionError("blocked")):
            recorded = server.record_email_live_verification({
                "ok": True,
                "status": "sent",
                "message": "Email accepted by SendGrid.",
                "provider": "sendgrid",
                "status_code": 202,
                "to_email": "admin@example.com",
                "subject": "Write failure",
                "live_send": True,
            })

        self.assertFalse(recorded["verified"])
        self.assertFalse(recorded["verification_recorded"])
        self.assertEqual(recorded["status"], "verification_record_failed")


if __name__ == "__main__":
    unittest.main()
