#!/usr/bin/env python3
"""Tests for the partner-facing formation API."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.old_email_mode = os.environ.get("EMAIL_DELIVERY_MODE")
        self.old_email_live = os.environ.get("SOSFILER_EMAIL_LIVE")
        self.old_stripe_secret = server.STRIPE_SECRET_KEY
        os.environ["ADMIN_TOKEN"] = "test-admin"
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ["PII_ENCRYPTION_KEY"] = "test-key-for-local-unit-tests"
        os.environ["EMAIL_DELIVERY_MODE"] = "noop"
        os.environ.pop("SOSFILER_EMAIL_LIVE", None)
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
            "EMAIL_DELIVERY_MODE": self.old_email_mode,
            "SOSFILER_EMAIL_LIVE": self.old_email_live,
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

    def test_public_formation_quote_rejects_registered_agent_add_on_until_june(self):
        res = self.client.post(
            "/api/quote",
            json={
                "product_type": "formation",
                "entity_type": "LLC",
                "state": "CA",
                "include_registered_agent": True,
            },
        )

        self.assertEqual(res.status_code, 400)
        self.assertIn("coming in June", res.json()["detail"])

    def test_public_formation_order_rejects_sosfiler_registered_agent_until_june(self):
        payload = formation_payload("ca-founder@example.com", "California RA Block Test LLC")
        payload.update({
            "state": "CA",
            "principal_state": "CA",
            "ra_choice": "sosfiler",
            "ra_name": "SOSFiler Registered Agent",
            "ra_address": "",
            "ra_city": "",
            "ra_state": "",
            "ra_zip": "",
        })

        res = self.client.post("/api/formation", json=payload)

        self.assertEqual(res.status_code, 400)
        self.assertIn("coming in June", res.json()["detail"])

    def test_returning_customer_login_links_checkout_email_orders(self):
        email = "returning-founder@example.com"
        signup = self.client.post(
            "/api/auth/signup",
            json={"email": email, "password": "TestPass123!", "name": "Returning Founder"},
        )
        self.assertEqual(signup.status_code, 200, signup.text)

        created = self.client.post(
            "/api/formation",
            json=formation_payload(email, "Returning Customer LLC"),
        )
        self.assertEqual(created.status_code, 200, created.text)
        order_id = created.json()["order_id"]

        login = self.client.post(
            "/api/auth/login",
            json={"email": email, "password": "TestPass123!"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        login_payload = login.json()
        user_id = login_payload["user"]["id"]

        conn = server.get_db()
        linked_user_id = conn.execute(
            "SELECT user_id FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()["user_id"]
        conn.close()
        self.assertEqual(linked_user_id, user_id)

        orders = self.client.get(
            "/api/user/orders",
            headers={"Authorization": f"Bearer {login_payload['token']}"},
        )
        self.assertEqual(orders.status_code, 200, orders.text)
        self.assertIn(order_id, {order["id"] for order in orders.json()["orders"]})

    def test_public_multi_formation_checkout_creates_traceable_cart_capture(self):
        server.STRIPE_SECRET_KEY = "sk_test_unit"
        first = formation_payload("multi-founder@example.com", "First Cart Test LLC")
        second = formation_payload("multi-founder@example.com", "Second Cart Test LLC")
        second.update({
            "state": "CA",
            "principal_state": "CA",
            "ra_state": "CA",
            "principal_city": "San Francisco",
            "principal_zip": "94105",
            "ra_city": "San Francisco",
            "ra_zip": "94105",
        })

        class FakeSession:
            id = "cs_test_cart"
            url = "https://checkout.stripe.test/cart"

        with patch("server.stripe.checkout.Session.create", return_value=FakeSession()) as create_session:
            res = self.client.post(
                "/api/formations/checkout",
                json={
                    "items": [first, second],
                    "success_url": "https://example.com/dashboard.html",
                    "cancel_url": "https://example.com/app.html",
                },
            )

        self.assertEqual(res.status_code, 200, res.text)
        payload = res.json()
        self.assertEqual(payload["checkout_url"], "https://checkout.stripe.test/cart")
        self.assertEqual(len(payload["orders"]), 2)
        stripe_kwargs = create_session.call_args.kwargs
        self.assertIn(f"cart_id={payload['cart_id']}", stripe_kwargs["success_url"])
        self.assertIn(f"order_id={payload['orders'][0]['order_id']}", stripe_kwargs["success_url"])
        self.assertIn("token=", stripe_kwargs["success_url"])
        self.assertEqual(stripe_kwargs["metadata"]["type"], "formation_cart")
        self.assertEqual(stripe_kwargs["metadata"]["capture_strategy"], "capture_immediate")
        self.assertNotIn("payment_intent_data", stripe_kwargs)
        self.assertEqual(len(stripe_kwargs["line_items"]), 2)

        result = server.update_formation_cart_capture_from_session({
            "id": "cs_test_cart",
            "amount_total": payload["total_cents"],
            "payment_intent": "pi_test_cart",
            "metadata": stripe_kwargs["metadata"],
        })
        self.assertTrue(result["updated"])
        self.assertEqual(len(result["order_ids"]), 2)

        conn = server.get_db()
        rows = conn.execute(
            "SELECT id, status, paid_at, stripe_session_id, stripe_payment_intent FROM orders ORDER BY created_at"
        ).fetchall()
        captured_events = conn.execute(
            "SELECT count(*) FROM payment_ledger WHERE event_type = 'captured'"
        ).fetchone()[0]
        quote_statuses = [
            row["reconciliation_status"]
            for row in conn.execute("SELECT reconciliation_status FROM execution_quotes ORDER BY created_at").fetchall()
        ]
        conn.close()

        self.assertEqual([row["status"] for row in rows], ["payment_captured", "payment_captured"])
        self.assertTrue(all(row["paid_at"] for row in rows))
        self.assertEqual({row["stripe_session_id"] for row in rows}, {"cs_test_cart"})
        self.assertEqual({row["stripe_payment_intent"] for row in rows}, {"pi_test_cart"})
        self.assertEqual(captured_events, 2)
        self.assertEqual(quote_statuses, ["captured", "captured"])

    def test_status_update_schedules_customer_dashboard_email(self):
        created = self.client.post(
            "/api/formation",
            json=formation_payload("status-email@example.com", "Status Email LLC"),
        )
        self.assertEqual(created.status_code, 200, created.text)
        order_id = created.json()["order_id"]

        with patch("server.schedule_customer_status_email") as schedule_email:
            server.add_status_update(order_id, "payment_captured", "Payment received. Open your dashboard for next steps.")

        schedule_email.assert_called_once_with(
            order_id,
            "payment_captured",
            "Payment received. Open your dashboard for next steps.",
        )

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
