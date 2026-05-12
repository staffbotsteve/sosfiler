#!/usr/bin/env python3
"""Tests for the partner-facing formation API."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def formation_payload(email: str = "founder@example.com", business_name: str = "Partner API Test LLC"):
    return {
        "email": email,
        "entity_type": "LLC",
        "state": "WY",
        "business_name": business_name,
        "purpose": "Any lawful purpose",
        "principal_address": "123 Main St",
        "principal_city": "Cheyenne",
        "principal_state": "WY",
        "principal_zip": "82001",
        "management_type": "member-managed",
        "ra_choice": "self",
        "ra_name": "Jane Founder",
        "ra_address": "123 Main St",
        "ra_city": "Cheyenne",
        "ra_state": "WY",
        "ra_zip": "82001",
        "members": [
            {
                "name": "Jane Founder",
                "address": "123 Main St",
                "city": "Cheyenne",
                "state": "WY",
                "zip_code": "82001",
                "ownership_pct": 100,
                "is_responsible_party": True,
                "ssn_itin": "123456789",
            }
        ],
        "oa_type": "single",
        "responsible_party_ssn": "123456789",
    }


class PartnerApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        self.old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        self.old_pii_key = os.environ.get("PII_ENCRYPTION_KEY")
        self.old_stripe_secret = server.STRIPE_SECRET_KEY
        os.environ["ADMIN_TOKEN"] = "test-admin"
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["PII_ENCRYPTION_KEY"] = "test-key-for-local-unit-tests"
        server.STRIPE_SECRET_KEY = None
        server.DB_PATH = Path(self.tmp.name) / "partner-api.db"
        server.ADMIN_RATE_LIMITS.clear()
        server.init_db()
        self.client = TestClient(server.app)

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        server.ADMIN_RATE_LIMITS.clear()
        for key, old_value in {
            "ADMIN_TOKEN": self.old_admin,
            "EXECUTION_PERSISTENCE_MODE": self.old_mode,
            "PII_ENCRYPTION_KEY": self.old_pii_key,
        }.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value
        server.STRIPE_SECRET_KEY = self.old_stripe_secret
        self.tmp.cleanup()

    def create_partner(self, slug: str):
        res = self.client.post(
            "/api/admin/partners",
            json={
                "name": f"{slug} Partner",
                "slug": slug,
                "support_email": f"{slug}@example.com",
            },
            headers={"x-admin-token": "test-admin"},
        )
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_partner_api_key_required_for_quote(self):
        res = self.client.post(
            "/api/v1/partners/quotes",
            json={"product_type": "formation", "entity_type": "LLC", "state": "WY"},
        )

        self.assertEqual(res.status_code, 401)

    def test_partner_can_create_and_read_own_formation(self):
        partner = self.create_partner("alpha")
        headers = {"Authorization": f"Bearer {partner['api_key']}"}

        created = self.client.post(
            "/api/v1/partners/formations",
            headers=headers,
            json={
                "external_order_id": "alpha-order-1",
                "external_customer_id": "alpha-customer-1",
                "formation": formation_payload(),
                "metadata": {"campaign": "launch"},
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        formation = created.json()["formation"]
        self.assertEqual(formation["external_order_id"], "alpha-order-1")
        self.assertEqual(formation["status"], "pending_payment")
        self.assertNotIn("token", formation)

        fetched = self.client.get(f"/api/v1/partners/formations/{formation['id']}", headers=headers)
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["formation"]["external_customer_id"], "alpha-customer-1")

    def test_partner_cannot_read_another_partner_order(self):
        alpha = self.create_partner("alpha")
        beta = self.create_partner("beta")
        alpha_headers = {"Authorization": f"Bearer {alpha['api_key']}"}
        beta_headers = {"Authorization": f"Bearer {beta['api_key']}"}

        created = self.client.post(
            "/api/v1/partners/formations",
            headers=alpha_headers,
            json={
                "external_order_id": "alpha-order-2",
                "formation": formation_payload("alpha@example.com", "Alpha Secret LLC"),
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        order_id = created.json()["formation"]["id"]

        forbidden = self.client.get(f"/api/v1/partners/formations/{order_id}", headers=beta_headers)
        self.assertEqual(forbidden.status_code, 404)

    def test_partner_external_order_id_is_idempotent(self):
        partner = self.create_partner("idempotent")
        headers = {"Authorization": f"Bearer {partner['api_key']}"}
        body = {
            "external_order_id": "same-order",
            "formation": formation_payload("same@example.com", "Same Order LLC"),
        }

        first = self.client.post("/api/v1/partners/formations", headers=headers, json=body)
        second = self.client.post("/api/v1/partners/formations", headers=headers, json=body)

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(first.json()["formation"]["id"], second.json()["formation"]["id"])

    def test_partner_application_requires_payment_before_approval(self):
        application = self.client.post(
            "/api/partners/applications",
            json={
                "company_name": "API Customer Co",
                "contact_name": "Pat Partner",
                "contact_email": "pat@example.com",
                "website": "https://example.com",
                "use_case": "We want to embed formations inside our founder platform.",
                "expected_monthly_volume": "11-50",
                "plan": "starter",
            },
        )
        self.assertEqual(application.status_code, 200, application.text)
        application_id = application.json()["application_id"]

        blocked = self.client.post(
            f"/api/admin/partner-applications/{application_id}/approve",
            json={"notes": "looks fine"},
            headers={"x-admin-token": "test-admin"},
        )

        self.assertEqual(blocked.status_code, 402)

    def test_paid_partner_application_approval_returns_one_time_key(self):
        application = self.client.post(
            "/api/partners/applications",
            json={
                "company_name": "Approved API Co",
                "contact_name": "Alex Approved",
                "contact_email": "alex@example.com",
                "use_case": "We need API access to create LLC filings for portfolio founders.",
                "plan": "growth",
            },
        )
        self.assertEqual(application.status_code, 200, application.text)
        application_id = application.json()["application_id"]
        conn = server.get_db()
        conn.execute(
            "UPDATE partner_applications SET payment_status = 'paid' WHERE id = ?",
            (application_id,),
        )
        conn.commit()
        conn.close()

        approved = self.client.post(
            f"/api/admin/partner-applications/{application_id}/approve",
            json={"notes": "approved"},
            headers={"x-admin-token": "test-admin"},
        )

        self.assertEqual(approved.status_code, 200, approved.text)
        payload = approved.json()
        self.assertTrue(payload["api_key"].startswith("sos_live_"))
        self.assertEqual(payload["partner"]["name"], "Approved API Co")

    def test_partner_application_checkout_without_stripe_returns_manual_payment(self):
        application = self.client.post(
            "/api/partners/applications",
            json={
                "company_name": "Manual Pay Co",
                "contact_name": "Morgan Manual",
                "contact_email": "morgan@example.com",
                "use_case": "We will send formation customers from a CPA workflow.",
            },
        )
        self.assertEqual(application.status_code, 200, application.text)
        application_id = application.json()["application_id"]

        checkout = self.client.post(
            f"/api/partners/applications/{application_id}/checkout",
            json={"success_url": "https://example.com/success", "cancel_url": "https://example.com/cancel"},
        )

        self.assertEqual(checkout.status_code, 200, checkout.text)
        self.assertEqual(checkout.json()["status"], "manual_payment_required")


if __name__ == "__main__":
    unittest.main()
