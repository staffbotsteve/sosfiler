#!/usr/bin/env python3
"""Tests for operator admin sessions and audit events."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class AdminSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        self.old_admin_emails = os.environ.get("ADMIN_EMAILS")
        self.old_email_mode = os.environ.get("EMAIL_DELIVERY_MODE")
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        os.environ["ADMIN_TOKEN"] = "test-admin"
        os.environ["ADMIN_EMAILS"] = "sactoswan@gmail.com"
        os.environ["EMAIL_DELIVERY_MODE"] = "noop"
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        server.DB_PATH = Path(self.tmp.name) / "admin-security.db"
        server.ADMIN_RATE_LIMITS.clear()
        server.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        server.ADMIN_RATE_LIMITS.clear()
        if self.old_admin is None:
            os.environ.pop("ADMIN_TOKEN", None)
        else:
            os.environ["ADMIN_TOKEN"] = self.old_admin
        if self.old_admin_emails is None:
            os.environ.pop("ADMIN_EMAILS", None)
        else:
            os.environ["ADMIN_EMAILS"] = self.old_admin_emails
        if self.old_email_mode is None:
            os.environ.pop("EMAIL_DELIVERY_MODE", None)
        else:
            os.environ["EMAIL_DELIVERY_MODE"] = self.old_email_mode
        if self.old_mode is None:
            os.environ.pop("EXECUTION_PERSISTENCE_MODE", None)
        else:
            os.environ["EXECUTION_PERSISTENCE_MODE"] = self.old_mode
        self.tmp.cleanup()

    def start_session(self):
        res = self.client.post(
            "/api/admin/session",
            json={"admin_token": "test-admin", "operator": "sactoswan@gmail.com"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def create_admin_user(self, email="sactoswan@gmail.com", role="admin"):
        conn = server.get_db()
        conn.execute(
            """
            INSERT INTO users (id, email, name, auth_provider, password_hash, role)
            VALUES (?, ?, ?, 'email', ?, ?)
            """,
            ("USR-ADMINTEST", email, "Ops User", server.hash_password("AdminPass123!"), role),
        )
        conn.commit()
        conn.close()

    def start_user_session_with_mfa(self):
        self.create_admin_user()
        start = self.client.post(
            "/api/admin/session/login",
            json={"email": "sactoswan@gmail.com", "password": "AdminPass123!", "operator": "sactoswan@gmail.com"},
        )
        self.assertEqual(start.status_code, 200, start.text)
        challenge_id = start.json()["challenge_id"]
        conn = server.get_db()
        conn.execute(
            "UPDATE auth_mfa_challenges SET code_hash = ? WHERE id = ?",
            (server.hash_mfa_code(challenge_id, "123456"), challenge_id),
        )
        conn.commit()
        conn.close()
        verify = self.client.post(
            "/api/admin/session/verify",
            json={"challenge_id": challenge_id, "code": "123456", "operator": "sactoswan@gmail.com"},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        return verify.json()

    def test_admin_session_can_access_admin_api_without_raw_token(self):
        session = self.start_session()

        res = self.client.get(
            "/api/admin/session",
            headers={"Authorization": f"Bearer {session['session_token']}"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["operator"], "sactoswan@gmail.com")
        self.assertEqual(res.json()["auth_mode"], "session")

    def test_admin_user_login_requires_mfa_before_session(self):
        session = self.start_user_session_with_mfa()

        res = self.client.get(
            "/api/admin/session",
            headers={"Authorization": f"Bearer {session['session_token']}"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["operator"], "sactoswan@gmail.com")
        self.assertEqual(res.json()["auth_mode"], "session")

    def test_admin_user_login_rejects_non_admin_account(self):
        self.create_admin_user(email="customer@example.com", role="customer")

        res = self.client.post(
            "/api/admin/session/login",
            json={"email": "customer@example.com", "password": "AdminPass123!", "operator": "customer@example.com"},
        )

        self.assertEqual(res.status_code, 403, res.text)

    def test_signed_in_admin_account_can_request_mfa(self):
        self.create_admin_user()
        token = server.create_jwt_token("USR-ADMINTEST")

        res = self.client.post(
            "/api/admin/session/account",
            json={"operator": "sactoswan@gmail.com"},
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["mfa_required"])

    def test_signed_in_customer_account_cannot_request_admin_mfa(self):
        self.create_admin_user(email="customer@example.com", role="customer")
        token = server.create_jwt_token("USR-ADMINTEST")

        res = self.client.post(
            "/api/admin/session/account",
            json={"operator": "customer@example.com"},
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(res.status_code, 403, res.text)

    def test_admin_mfa_rejects_wrong_code(self):
        self.create_admin_user()
        start = self.client.post(
            "/api/admin/session/login",
            json={"email": "sactoswan@gmail.com", "password": "AdminPass123!", "operator": "sactoswan@gmail.com"},
        )
        self.assertEqual(start.status_code, 200, start.text)

        verify = self.client.post(
            "/api/admin/session/verify",
            json={"challenge_id": start.json()["challenge_id"], "code": "111111", "operator": "sactoswan@gmail.com"},
        )

        self.assertEqual(verify.status_code, 401, verify.text)

    def test_admin_session_revoke_blocks_reuse(self):
        session = self.start_session()
        headers = {"Authorization": f"Bearer {session['session_token']}"}

        revoke = self.client.delete("/api/admin/session", headers=headers)
        self.assertEqual(revoke.status_code, 200, revoke.text)
        after = self.client.get("/api/admin/session", headers=headers)

        self.assertEqual(after.status_code, 401)

    def test_invalid_admin_session_attempt_is_audited(self):
        res = self.client.post(
            "/api/admin/session",
            json={"admin_token": "wrong", "operator": "bad actor"},
        )

        self.assertEqual(res.status_code, 403)
        conn = server.get_db()
        row = conn.execute("SELECT * FROM admin_audit_events WHERE outcome = 'denied'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["path"], "/api/admin/session")

    def test_legacy_admin_token_still_works_for_existing_scripts(self):
        res = self.client.get("/api/admin/slack/tickets", headers={"x-admin-token": "test-admin"})

        self.assertEqual(res.status_code, 200, res.text)
        conn = server.get_db()
        row = conn.execute("SELECT * FROM admin_audit_events WHERE operator = 'legacy_admin_token'").fetchone()
        conn.close()
        self.assertIsNotNone(row)


if __name__ == "__main__":
    unittest.main()
