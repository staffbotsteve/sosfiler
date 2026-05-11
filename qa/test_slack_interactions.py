#!/usr/bin/env python3
"""Tests for signed Slack ticket interactions."""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from execution_platform import format_slack_ticket  # noqa: E402
import server  # noqa: E402


class SlackInteractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        self.old_secret = os.environ.get("SLACK_SIGNING_SECRET")
        self.old_webhook = os.environ.get("SLACK_TICKETS_WEBHOOK_URL")
        self.old_interactive = os.environ.get("SLACK_INTERACTIVE_TICKETS")
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["SLACK_SIGNING_SECRET"] = "test-slack-secret"
        os.environ["ADMIN_TOKEN"] = "test-admin"
        server.DB_PATH = Path(self.tmp.name) / "slack.db"
        server.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        if self.old_mode is None:
            os.environ.pop("EXECUTION_PERSISTENCE_MODE", None)
        else:
            os.environ["EXECUTION_PERSISTENCE_MODE"] = self.old_mode
        if self.old_secret is None:
            os.environ.pop("SLACK_SIGNING_SECRET", None)
        else:
            os.environ["SLACK_SIGNING_SECRET"] = self.old_secret
        if self.old_webhook is None:
            os.environ.pop("SLACK_TICKETS_WEBHOOK_URL", None)
        else:
            os.environ["SLACK_TICKETS_WEBHOOK_URL"] = self.old_webhook
        if self.old_interactive is None:
            os.environ.pop("SLACK_INTERACTIVE_TICKETS", None)
        else:
            os.environ["SLACK_INTERACTIVE_TICKETS"] = self.old_interactive
        if self.old_admin is None:
            os.environ.pop("ADMIN_TOKEN", None)
        else:
            os.environ["ADMIN_TOKEN"] = self.old_admin
        self.tmp.cleanup()

    def insert_ticket(self, ticket_id: str, status: str = "open"):
        conn = server.get_db()
        conn.execute("""
            INSERT INTO support_tickets (
                id, ticket_type, status, priority, order_id, question,
                confidence_reason, suggested_answer, slack_sent
            ) VALUES (?, 'enhancement', ?, 'high', 'ORDER-SLACK', ?, ?, ?, 1)
        """, (
            ticket_id,
            status,
            "Can this be automated?",
            "Verified source coverage is missing.",
            "Review as an enhancement.",
        ))
        conn.commit()
        conn.close()

    def signed_body(self, payload: dict) -> tuple[bytes, dict]:
        body = urllib.parse.urlencode({"payload": json.dumps(payload, separators=(",", ":"))}).encode("utf-8")
        timestamp = str(int(time.time()))
        base = b"v0:" + timestamp.encode("utf-8") + b":" + body
        signature = "v0=" + hmac.new(
            os.environ["SLACK_SIGNING_SECRET"].encode("utf-8"),
            base,
            hashlib.sha256,
        ).hexdigest()
        return body, {
            "content-type": "application/x-www-form-urlencoded",
            "x-slack-request-timestamp": timestamp,
            "x-slack-signature": signature,
        }

    def test_slack_approve_button_marks_ticket_and_creates_run(self):
        self.insert_ticket("TKT-SLACK-APPROVE")
        body, headers = self.signed_body({
            "type": "block_actions",
            "user": {"id": "U123", "username": "steve"},
            "actions": [{"action_id": "approve_enhancement", "value": "TKT-SLACK-APPROVE"}],
        })

        res = self.client.post("/api/slack/interactions", content=body, headers=headers)

        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("Approved SOSFiler ticket", res.json()["text"])
        conn = server.get_db()
        ticket = conn.execute("SELECT status, approved_by FROM support_tickets WHERE id = ?", ("TKT-SLACK-APPROVE",)).fetchone()
        run_count = conn.execute("SELECT count(*) FROM automation_runs WHERE adapter_key = 'approved_ticket_engineering'").fetchone()[0]
        conn.close()
        self.assertEqual(ticket["status"], "approved")
        self.assertEqual(ticket["approved_by"], "steve")
        self.assertEqual(run_count, 1)

    def test_slack_close_button_marks_ticket_closed(self):
        self.insert_ticket("TKT-SLACK-CLOSE")
        body, headers = self.signed_body({
            "type": "block_actions",
            "user": {"id": "U123"},
            "actions": [{"action_id": "close_ticket", "value": "TKT-SLACK-CLOSE"}],
        })

        res = self.client.post("/api/slack/interactions", content=body, headers=headers)

        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn("Closed SOSFiler ticket", res.json()["text"])
        conn = server.get_db()
        ticket = conn.execute("SELECT status, approved_by FROM support_tickets WHERE id = ?", ("TKT-SLACK-CLOSE",)).fetchone()
        run_count = conn.execute("SELECT count(*) FROM automation_runs WHERE adapter_key = 'closed_ticket_review'").fetchone()[0]
        conn.close()
        self.assertEqual(ticket["status"], "closed")
        self.assertEqual(ticket["approved_by"], "U123")
        self.assertEqual(run_count, 1)

    def test_invalid_slack_signature_is_rejected(self):
        self.insert_ticket("TKT-SLACK-BAD")
        body, headers = self.signed_body({
            "type": "block_actions",
            "actions": [{"action_id": "approve_enhancement", "value": "TKT-SLACK-BAD"}],
        })
        headers["x-slack-signature"] = "v0=bad"

        res = self.client.post("/api/slack/interactions", content=body, headers=headers)

        self.assertEqual(res.status_code, 401)

    def test_admin_slack_config_status_does_not_expose_secrets(self):
        os.environ["SLACK_TICKETS_WEBHOOK_URL"] = "https://hooks.slack.test/secret"
        os.environ["SLACK_INTERACTIVE_TICKETS"] = "true"

        res = self.client.get("/api/admin/slack/config-status", headers={"x-admin-token": "test-admin"})

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertTrue(payload["webhook_configured"])
        self.assertTrue(payload["signing_secret_configured"])
        self.assertTrue(payload["interactive_buttons_enabled"])
        self.assertNotIn("hooks.slack.test", json.dumps(payload).lower())

    def test_slack_ticket_blocks_use_real_newlines(self):
        os.environ["SLACK_INTERACTIVE_TICKETS"] = "true"
        payload = format_slack_ticket({
            "id": "TKT-FORMAT",
            "ticket_type": "support",
            "status": "open",
            "priority": "normal",
            "question": "Please test Slack formatting.",
            "confidence_reason": "Formatting smoke.",
            "suggested_answer": "Operator review needed.",
        })

        serialized = json.dumps(payload)
        self.assertNotIn("\\\\n", serialized)
        self.assertIn("\\n", serialized)
        self.assertIn("blocks", payload)


if __name__ == "__main__":
    unittest.main()
