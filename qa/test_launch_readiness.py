#!/usr/bin/env python3
"""Tests for national launch-readiness classification."""

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import launch_readiness  # noqa: E402
import server  # noqa: E402


class LaunchReadinessTests(unittest.TestCase):
    def test_report_classifies_all_jurisdictions_and_sellability(self):
        report = launch_readiness.build_launch_readiness_report()

        self.assertEqual(report["summary"]["total_jurisdictions"], 51)
        self.assertEqual(len(report["states"]), 51)
        self.assertEqual(
            report["summary"]["sellable_now"] + report["summary"]["not_available"],
            51,
        )
        categories = report["summary"]["by_launch_category"]
        self.assertIn("fully_automated_sellable", categories)
        self.assertGreater(report["summary"]["sellable_now"], 0)

    def test_texas_is_fully_automated_sellable_with_transparent_pricing(self):
        report = launch_readiness.build_launch_readiness_report()
        tx = next(item for item in report["states"] if item["state"] == "TX")

        self.assertEqual(tx["launch_category"], "fully_automated_sellable")
        self.assertEqual(tx["customer_offer_status"], "available_automated")
        self.assertTrue(tx["sellable_now"])
        self.assertEqual(tx["pricing"]["sosfiler_fee_cents"], 2900)
        self.assertGreater(tx["pricing"]["estimated_total_cents"], tx["pricing"]["official_government_fee_cents"])

    def test_restricted_states_are_sellable_only_with_operator_or_partner_handoff(self):
        report = launch_readiness.build_launch_readiness_report()
        states = {item["state"]: item for item in report["states"]}

        self.assertEqual(states["CA"]["launch_category"], "trusted_access_operator_sellable")
        self.assertTrue(states["CA"]["sellable_now"])
        self.assertIn("prefill forms", " ".join(states["CA"]["application_handled_steps"]))
        self.assertIn("verified operator profile", " ".join(states["CA"]["operator_exception_steps"]))
        self.assertIn("CAPTCHA solving services", " ".join(states["CA"]["operator_exception_steps"]))
        self.assertEqual(states["DE"]["launch_category"], "partner_intermediary_sellable")
        self.assertIn("partner-ready filing package", " ".join(states["DE"]["application_handled_steps"]))
        self.assertIn("partner availability", " ".join(states["DE"]["operator_exception_steps"]))

    def test_operator_assisted_states_are_application_first(self):
        report = launch_readiness.build_launch_readiness_report()

        self.assertEqual(report["automation_first_policy"]["operator_touch_model"], "operator_by_exception")
        self.assertIn("click services", report["automation_first_policy"]["captcha_policy"])
        for state in report["states"]:
            self.assertGreater(len(state["application_handled_steps"]), 5)
            self.assertGreaterEqual(len(state["operator_exception_steps"]), 1)
            self.assertIn("operator_touch_target_minutes", state)

    def test_lifecycle_support_is_present_for_every_state(self):
        report = launch_readiness.build_launch_readiness_report()
        expected = set(launch_readiness.LIFECYCLE_WORKFLOWS)
        for state in report["states"]:
            self.assertEqual(set(state["lifecycle_support"]), expected)

    def test_public_launch_readiness_redacts_internal_operations(self):
        public_report = launch_readiness.build_public_launch_readiness_report()
        self.assertEqual(public_report["summary"]["total_jurisdictions"], 51)
        self.assertEqual(len(public_report["states"]), 51)

        forbidden = {
            "automation_lane",
            "production_readiness",
            "portal_urls",
            "evidence_urls",
            "certification_gate_statuses",
            "next_actions",
            "application_handled_steps",
            "operator_exception_steps",
            "automation_first_policy",
            "blockers",
        }
        for state in public_report["states"]:
            self.assertFalse(forbidden.intersection(state), state)
            self.assertIn("automation_scope", state)

        ca = next(item for item in public_report["states"] if item["state"] == "CA")
        self.assertIn("Operator-assisted", ca["headline"])
        serialized_ca = json.dumps(ca).lower()
        self.assertNotIn("captcha", serialized_ca)
        self.assertNotIn("bypass", serialized_ca)
        self.assertNotIn("login", serialized_ca)

    def test_markdown_render_includes_rollout_table_and_controls(self):
        report = launch_readiness.build_launch_readiness_report()
        markdown = launch_readiness.render_markdown_report(report)

        self.assertIn("# SOSFiler Launch Readiness", markdown)
        self.assertIn("| State | Category | Offer | SLA | Price Status | Next Action |", markdown)
        self.assertIn("No bypassing access controls", markdown)
        self.assertIn("Automation-First Fulfillment", markdown)
        self.assertIn("No CAPTCHA solving or click-service outsourcing", markdown)
        self.assertIn("Customer-Facing Fulfillment", markdown)

    def test_admin_launch_readiness_endpoint(self):
        old_admin = os.environ.get("ADMIN_TOKEN")
        old_db_path = server.DB_PATH
        old_email_verification_path = server.EMAIL_LIVE_VERIFICATION_PATH
        tmp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_TOKEN"] = "test-admin"
        server.DB_PATH = Path(tmp.name) / "launch.db"
        server.EMAIL_LIVE_VERIFICATION_PATH = Path(tmp.name) / "email_live_verification.json"
        server.init_db()
        client = TestClient(server.app)

        try:
            res = client.get("/api/admin/launch-readiness", headers={"x-admin-token": "test-admin"})
            self.assertEqual(res.status_code, 200, res.text)
            payload = res.json()
            self.assertEqual(payload["summary"]["total_jurisdictions"], 51)
            self.assertEqual(len(payload["states"]), 51)
            self.assertIn("platform_launch_gates", payload)
            self.assertFalse(payload["summary"]["ready_for_paid_traffic"])
            self.assertFalse(payload["platform_launch_gates"]["email_delivery"]["launch_ready"])
        finally:
            server.DB_PATH = old_db_path
            server.EMAIL_LIVE_VERIFICATION_PATH = old_email_verification_path
            if old_admin is None:
                os.environ.pop("ADMIN_TOKEN", None)
            else:
                os.environ["ADMIN_TOKEN"] = old_admin
            tmp.cleanup()

    def test_public_formation_availability_endpoint_is_unauthenticated_and_sanitized(self):
        client = TestClient(server.app)

        tx_res = client.get("/api/formation-availability/TX")
        self.assertEqual(tx_res.status_code, 200, tx_res.text)
        tx = tx_res.json()
        self.assertEqual(tx["state"], "TX")
        self.assertEqual(tx["customer_offer_status"], "available_automated")
        self.assertGreater(tx["pricing"]["estimated_total_cents"], 0)

        ca_res = client.get("/api/formation-availability/CA")
        self.assertEqual(ca_res.status_code, 200, ca_res.text)
        ca = ca_res.json()
        self.assertTrue(ca["sellable_now"])
        self.assertIn("operator", ca["fulfillment_lane"].lower())
        forbidden_blob = json.dumps(ca).lower()
        self.assertNotIn("captcha", forbidden_blob)
        self.assertNotIn("bypass", forbidden_blob)
        self.assertNotIn("portal_urls", forbidden_blob)

    def test_public_formation_availability_rejects_invalid_state(self):
        client = TestClient(server.app)
        res = client.get("/api/formation-availability/california")
        self.assertEqual(res.status_code, 400)

    def test_operator_cockpit_exposes_launch_readiness_panel(self):
        html = (Path(__file__).resolve().parents[1] / "frontend" / "operator.html").read_text()

        self.assertIn("Launch Readiness", html)
        self.assertIn("launchReadinessList", html)
        self.assertIn("/api/admin/launch-readiness", html)
        self.assertIn("launchCategoryFilter", html)
        self.assertIn("App handles:", html)
        self.assertIn("Operator only:", html)

    def test_customer_wizard_exposes_formation_availability(self):
        html = (Path(__file__).resolve().parents[1] / "frontend" / "app.html").read_text()

        self.assertIn("sidebarFulfillmentLane", html)
        self.assertIn("formationAvailabilityByState", html)
        self.assertIn("/api/formation-availability", html)
        self.assertIn("setupSidebarListeners", html)


if __name__ == "__main__":
    unittest.main()
