#!/usr/bin/env python3
"""Unit tests for data-driven state filing route selection."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from state_routing import build_state_route, merge_filing_actions  # noqa: E402
from check_state_routes import check_routes  # noqa: E402


class StateRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data_dir = ROOT / "data"
        generated_actions = json.loads((data_dir / "filing_actions.generated.json").read_text())
        manual_actions = json.loads((data_dir / "filing_actions.json").read_text())
        cls.filing_actions = merge_filing_actions(generated_actions, manual_actions)
        cls.portal_research = json.loads((data_dir / "portal_maps" / "state_portals_research.json").read_text())
        cls.state_fees = json.loads((data_dir / "state_fees.json").read_text())

    def route(self, state: str):
        return build_state_route(
            state=state,
            entity_type="LLC",
            action_type="formation",
            filing_actions=self.filing_actions,
            portal_research=self.portal_research,
            state_fees=self.state_fees,
        )

    def test_deep_action_overrides_texas_fee_and_evidence(self):
        route = self.route("TX")
        self.assertTrue(route["has_deep_action"])
        self.assertEqual(route["state_fee_cents"], 30000)
        self.assertEqual(route["automation_difficulty"], "medium")
        self.assertEqual(route["automation_lane"], "browser_profile_automation")
        self.assertEqual(route["customer_status"], "production_ready_operator_supervised")
        self.assertIn("Texas Certificate of Filing", route["required_evidence"]["approved"])

    def test_certified_state_gets_profile_automation_lane(self):
        route = self.route("DE")
        self.assertTrue(route["has_deep_action"])
        self.assertEqual(route["automation_difficulty"], "medium")
        self.assertEqual(route["automation_lane"], "browser_profile_automation")
        self.assertEqual(route["customer_status"], "payment_screen_certification_required")

    def test_certification_gate_can_promote_state_to_trusted_access_lane(self):
        route = self.route("CA")
        self.assertEqual(route["automation_difficulty"], "hard")
        self.assertEqual(route["automation_lane"], "operator_assisted_browser_provider")
        self.assertEqual(route["customer_status"], "trusted_access_operator_checkpoint_required")
        codes = {blocker["code"] for blocker in route["blockers"]}
        self.assertIn("trusted_access_operator_checkpoint", codes)

    def test_medium_state_gets_profile_lane(self):
        route = self.route("AR")
        self.assertEqual(route["automation_difficulty"], "medium")
        self.assertEqual(route["automation_lane"], "browser_profile_automation")

    def test_hard_state_gets_operator_browser_lane_and_blocker(self):
        route = self.route("FL")
        self.assertEqual(route["automation_difficulty"], "hard")
        self.assertEqual(route["automation_lane"], "operator_assisted_browser_provider")
        self.assertEqual(route["customer_status"], "trusted_access_operator_checkpoint_required")
        self.assertNotIn("Signature 7 Consulting", route["form_name"])
        codes = {blocker["code"] for blocker in route["blockers"]}
        self.assertIn("captcha_turnstile", codes)
        self.assertIn("trusted_access_operator_checkpoint", codes)

    def test_generated_state_uses_canonical_fee_for_quote(self):
        route = self.route("AZ")
        self.assertTrue(route["has_deep_action"])
        self.assertTrue(route["deep_action_state_fee_cents"])
        self.assertEqual(route["state_fee_cents"], 5000)

    def test_every_llc_state_has_route_metadata(self):
        summary = check_routes("LLC", "formation")
        self.assertEqual(summary["states_checked"], 51)
        self.assertEqual(summary["issues"], [])
        self.assertEqual(sum(summary["by_difficulty"].values()), 51)
        self.assertGreaterEqual(summary["deep_action_states"], 30)

    def test_every_corp_state_has_route_metadata(self):
        summary = check_routes("Corp", "formation")
        self.assertEqual(summary["states_checked"], 51)
        self.assertEqual(summary["issues"], [])
        self.assertEqual(sum(summary["by_difficulty"].values()), 51)
        self.assertGreaterEqual(summary["deep_action_states"], 20)


if __name__ == "__main__":
    unittest.main()
