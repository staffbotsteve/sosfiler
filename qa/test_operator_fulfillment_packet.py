#!/usr/bin/env python3
"""Tests for operator fulfillment packets used by national filing ops."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


class OperatorFulfillmentPacketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = server.DB_PATH
        self.old_admin = os.environ.get("ADMIN_TOKEN")
        os.environ["ADMIN_TOKEN"] = "test-admin"
        server.DB_PATH = Path(self.tmp.name) / "operator-packet.db"
        server.init_db()
        self.client = TestClient(server.app)
        self.headers = {"x-admin-token": "test-admin"}

    def tearDown(self):
        server.DB_PATH = self.old_db_path
        if self.old_admin is None:
            os.environ.pop("ADMIN_TOKEN", None)
        else:
            os.environ["ADMIN_TOKEN"] = self.old_admin
        self.tmp.cleanup()

    def formation_payload(self, state="CA"):
        return {
            "email": "customer@example.com",
            "entity_type": "LLC",
            "state": state,
            "business_name": f"{state} Customer Packet LLC",
            "purpose": "Any lawful purpose",
            "principal_address": "123 Market Street",
            "principal_city": "Sacramento" if state == "CA" else "Capital City",
            "principal_state": state,
            "principal_zip": "95814",
            "mailing_address": "123 Market Street, Sacramento, CA 95814",
            "management_type": "member-managed",
            "ra_choice": "self",
            "ra_name": "Customer Agent",
            "ra_address": "123 Market Street",
            "ra_city": "Sacramento" if state == "CA" else "Capital City",
            "ra_state": state,
            "ra_zip": "95814",
            "members": [{
                "name": "Customer Owner",
                "address": "123 Market Street",
                "city": "Sacramento" if state == "CA" else "Capital City",
                "state": state,
                "zip_code": "95814",
                "ssn_itin": "123456789",
                "ssn_last4": "6789",
                "ownership_pct": 100,
                "is_responsible_party": True,
            }],
            "responsible_party_ssn": "",
            "responsible_party_ssn_vault_id": "PII-secret-vault-id",
            "responsible_party_ssn_last4": "6789",
            "fiscal_year_end": "December",
        }

    def create_order_and_job(self, state="CA"):
        formation = self.formation_payload(state)
        order_id = f"ORD-{state}-PACKET"
        route = server.get_state_filing_route(state, "LLC", "formation")
        conn = server.get_db()
        conn.execute("""
            INSERT INTO orders (
                id, email, token, status, entity_type, state, business_name,
                formation_data, state_fee_cents, gov_processing_fee_cents,
                platform_fee_cents, total_cents, paid_at
            )
            VALUES (?, ?, 'tok-test', 'paid', 'LLC', ?, ?, ?, ?, ?, 4900, ?, datetime('now'))
        """, (
            order_id,
            formation["email"],
            state,
            formation["business_name"],
            json.dumps(formation),
            int(route.get("state_fee_cents") or 10000),
            0,
            int(route.get("state_fee_cents") or 10000) + 4900,
        ))
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.commit()
        conn.close()
        job = server.create_or_update_filing_job(dict(row), "formation", "ready_to_file")
        return dict(row), job

    def test_operator_detail_includes_specific_fulfillment_packet_and_customer_data(self):
        _, job = self.create_order_and_job("CA")

        res = self.client.get(f"/api/admin/filing-jobs/{job['id']}", headers=self.headers)

        self.assertEqual(res.status_code, 200, res.text)
        packet = res.json()["job"]["operator_fulfillment_packet"]
        self.assertEqual(packet["version"], "operator_fulfillment_packet_v1")
        self.assertEqual(packet["summary"]["state"], "CA")
        self.assertIn("Customer Packet LLC", packet["customer_filing_data"]["business"]["legal_name"])
        self.assertEqual(packet["customer_filing_data"]["registered_agent"]["name"], "Customer Agent")
        self.assertEqual(packet["customer_filing_data"]["people"][0]["name"], "Customer Owner")
        self.assertGreaterEqual(len(packet["operator_actions"]), 6)
        self.assertGreaterEqual(len(packet["portal_steps"]), 1)
        self.assertTrue(any("CAPTCHA" in item for item in packet["safe_stop_conditions"]))
        self.assertIn("Official", " ".join(packet["required_evidence"]["submitted"]))

        serialized = json.dumps(packet)
        self.assertNotIn("123456789", serialized)
        self.assertNotIn("PII-secret-vault-id", serialized)
        self.assertIn("6789", serialized)

    def test_every_jurisdiction_has_operator_steps_safe_stops_and_customer_snapshot(self):
        formation = self.formation_payload("CA")
        for state in sorted(server.STATE_FEES["state_names"]):
            formation_for_state = {**formation, "state": state, "business_name": f"{state} Packet LLC"}
            route = server.get_state_filing_route(state, "LLC", "formation")
            job = {
                "id": f"FIL-{state}-packet",
                "order_id": f"ORD-{state}-packet",
                "action_type": "formation",
                "state": state,
                "entity_type": "LLC",
                "business_name": formation_for_state["business_name"],
                "email": "customer@example.com",
                "status": "ready_to_file",
                "formation_data": formation_for_state,
                "state_fee_cents": int(route.get("state_fee_cents") or 0),
                "processing_fee_cents": 0,
                "total_government_cents": int(route.get("state_fee_cents") or 0),
                "automation_lane": route.get("automation_lane"),
                "portal_name": route.get("portal_name"),
                "portal_url": route.get("portal_url"),
                "required_evidence": route.get("required_evidence"),
                "portal_blockers": route.get("blockers"),
                "route_metadata": route,
            }
            packet = server.build_operator_fulfillment_packet(job, {"platform_fee_cents": 4900, "total_cents": 14900})
            self.assertGreaterEqual(len(packet["operator_actions"]), 6, state)
            self.assertGreaterEqual(len(packet["portal_steps"]), 1, state)
            self.assertGreaterEqual(len(packet["safe_stop_conditions"]), 5, state)
            self.assertEqual(packet["customer_filing_data"]["business"]["formation_state"], state)
            self.assertIn("registered_agent", packet["customer_filing_data"])
            self.assertIn("submitted", packet["required_evidence"])

    def test_operator_cockpit_renders_fulfillment_packet_panel(self):
        html = (Path(__file__).resolve().parents[1] / "frontend" / "operator.html").read_text()

        self.assertIn("Operator Fulfillment Packet", html)
        self.assertIn("renderOperatorFulfillmentPacket", html)
        self.assertIn("Customer Filing Data", html)
        self.assertIn("Safe-stop conditions", html)


if __name__ == "__main__":
    unittest.main()
