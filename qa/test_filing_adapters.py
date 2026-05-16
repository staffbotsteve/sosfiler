#!/usr/bin/env python3
"""Unit tests for filing adapter routing and dry-run safety."""

import asyncio
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from filing_adapters import BrowserPreflightProbeAdapter, DryRunLaneAdapter, build_adapter_contract, run_adapter_operation  # noqa: E402


class FilingAdapterTests(unittest.TestCase):
    def complete_job(self, **overrides):
        job = {
            "automation_lane": "browser_automation",
            "automation_difficulty": "easy",
            "adapter_key": "ny_formation_browser_automation",
            "portal_blockers": [],
            "required_evidence": {"submitted": ["Official receipt"], "approved": ["Filed articles"]},
            "state": "NY",
            "entity_type": "LLC",
            "business_name": "Example LLC",
            "status": "ready_to_file",
            "order_status": "payment_authorized",
            "state_fee_cents": 20000,
            "total_government_cents": 20000,
            "document_types": ["filing_authorization"],
            "artifact_types": [],
            "formation_data": {
                "business_name": "Example LLC",
                "state": "NY",
                "entity_type": "LLC",
                "principal_address": "1 Main St",
                "principal_city": "Albany",
                "principal_state": "NY",
                "principal_zip": "12207",
                "ra_choice": "self",
                "ra_name": "Jane Agent",
                "ra_address": "1 Main St",
                "ra_city": "Albany",
                "ra_state": "NY",
                "ra_zip": "12207",
                "members": [{
                    "name": "Jane Owner",
                    "address": "1 Main St",
                    "city": "Albany",
                    "state": "NY",
                    "zip_code": "12207",
                }],
            },
        }
        job.update(overrides)
        return job

    def test_hard_browser_provider_requires_operator(self):
        job = self.complete_job(
            automation_lane="operator_assisted_browser_provider",
            automation_difficulty="hard",
            adapter_key="fl_formation_operator_assisted_browser_provider",
            portal_blockers=[{"code": "captcha_turnstile", "message": "Turnstile challenge"}],
            state="FL",
        )
        adapter = DryRunLaneAdapter(job["automation_lane"])
        self.assertTrue(adapter.requires_operator(job))

        result = asyncio.run(run_adapter_operation(job, "preflight"))
        self.assertEqual(result.status, "operator_required")
        self.assertTrue(result.metadata["dry_run"])
        self.assertTrue(result.metadata["requires_operator"])
        self.assertEqual(result.metadata["blockers"][0]["code"], "captcha_turnstile")

    def test_easy_browser_dry_run_can_start_automation(self):
        job = self.complete_job(
            state="DE",
            automation_lane="browser_profile_automation",
            adapter_key="de_formation_browser_profile_automation",
            formation_data={
                **self.complete_job()["formation_data"],
                "state": "DE",
                "principal_state": "DE",
                "ra_state": "DE",
            },
        )

        result = asyncio.run(run_adapter_operation(job, "preflight"))
        self.assertEqual(result.status, "automation_started")
        self.assertFalse(result.metadata["requires_operator"])
        self.assertEqual(result.metadata["lane"], "browser_profile_automation")
        self.assertTrue(result.metadata["preflight"]["passed"])

    def test_submit_is_blocked_in_dry_run(self):
        job = self.complete_job()

        result = asyncio.run(run_adapter_operation(job, "submit"))
        self.assertEqual(result.status, "operator_required")
        self.assertFalse(result.metadata["submit_allowed"])
        self.assertIn("Live submission is disabled", result.message)

    def test_missing_payment_blocks_preflight(self):
        job = self.complete_job(order_status="pending_payment")

        result = asyncio.run(run_adapter_operation(job, "preflight"))
        self.assertEqual(result.status, "operator_required")
        self.assertFalse(result.metadata["preflight"]["passed"])
        codes = {issue["code"] for issue in result.metadata["preflight"]["blocking_issues"]}
        self.assertIn("payment_ready", codes)

    def test_browser_probe_reaches_portal_without_submit(self):
        class FakeResponse:
            status = 200
            url = "https://apps.dos.ny.gov/publicInquiry/"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        job = self.complete_job(portal_url="https://apps.dos.ny.gov/publicInquiry/")
        adapter = BrowserPreflightProbeAdapter("browser_automation")
        with patch("filing_adapters.urllib.request.urlopen", return_value=FakeResponse()):
            result = asyncio.run(adapter.preflight(job))

        self.assertEqual(result.status, "automation_started")
        self.assertTrue(result.metadata["portal_probe"]["ok"])
        self.assertEqual(result.metadata["portal_probe"]["status_code"], 200)

    def test_colorado_formation_adapter_exposes_state_contract(self):
        job = self.complete_job(
            state="CO",
            automation_lane="browser_automation",
            adapter_key="co_formation_browser_automation",
            formation_data={
                **self.complete_job()["formation_data"],
                "state": "CO",
                "principal_state": "CO",
                "ra_state": "CO",
            },
        )

        result = asyncio.run(run_adapter_operation(job, "preflight"))

        self.assertEqual(result.status, "automation_started")
        self.assertEqual(result.metadata["state_adapter"], "colorado_formation_v1")
        self.assertIn("business_name", result.metadata["field_map"])
        self.assertIn("state_confirmation_receipt", result.metadata["evidence_plan"])

    def test_matrix_certified_state_exposes_payment_certification_gate(self):
        job = self.complete_job(
            state="DE",
            automation_lane="browser_profile_automation",
            adapter_key="de_formation_browser_profile_automation",
            formation_data={
                **self.complete_job()["formation_data"],
                "state": "DE",
                "principal_state": "DE",
                "ra_state": "DE",
            },
        )

        result = asyncio.run(run_adapter_operation(job, "preflight"))

        self.assertEqual(result.status, "automation_started")
        self.assertEqual(result.metadata["state_adapter"], "de_matrix_portal_v1")
        self.assertTrue(result.metadata["payment_screen_certification_required"])
        gate_codes = {gate["code"] for gate in result.metadata["certification_gates"]}
        self.assertIn("payment_screen", gate_codes)

    def test_california_routes_to_browser_profile_worker_after_live_certification(self):
        job = self.complete_job(
            state="CA",
            automation_lane="browser_profile_automation",
            adapter_key="ca_formation_browser_profile_automation",
            formation_data={
                **self.complete_job()["formation_data"],
                "state": "CA",
                "principal_state": "CA",
                "ra_state": "CA",
            },
        )

        result = asyncio.run(run_adapter_operation(job, "preflight"))

        self.assertEqual(result.status, "automation_started")
        self.assertEqual(result.metadata["state_adapter"], "ca_matrix_portal_v1")
        self.assertFalse(result.metadata.get("trusted_access_checkpoint_required", False))
        self.assertEqual(
            result.metadata["state_automation_profile"]["status_check_method"],
            "ca_bizfile_protocol_manifest_then_worker_my_work_queue",
        )

    def test_trusted_access_state_pauses_for_operator_checkpoint(self):
        job = self.complete_job(
            state="NY",
            automation_lane="operator_assisted_browser_provider",
            adapter_key="ny_formation_operator_assisted_browser_provider",
            portal_blockers=[{"code": "trusted_access_operator_checkpoint", "message": "Checkpoint required"}],
        )

        result = asyncio.run(run_adapter_operation(job, "preflight"))

        self.assertEqual(result.status, "operator_required")
        self.assertTrue(result.metadata["trusted_access_checkpoint_required"])
        self.assertEqual(result.metadata["state_adapter"], "ny_trusted_access_checkpoint_v1")

    def test_texas_adapter_uses_sosdirect_contract(self):
        job = self.complete_job(
            state="TX",
            automation_lane="browser_profile_automation",
            adapter_key="tx_formation_browser_profile_automation",
            formation_data={
                **self.complete_job()["formation_data"],
                "state": "TX",
                "principal_state": "TX",
                "ra_state": "TX",
            },
        )

        result = asyncio.run(run_adapter_operation(job, "preflight"))

        self.assertIn(result.status, {"automation_started", "operator_required"})
        self.assertEqual(result.metadata["state_adapter"], "tx_sosdirect_v1")
        self.assertIn("document_zip_download", result.metadata["sosdirect_flow"])
        self.assertFalse(result.metadata["payment_screen_certification_required"])

    def test_generic_adapter_contract_exposes_deep_action_steps(self):
        job = self.complete_job(
            state="TX",
            adapter_key="tx_formation_operator_assisted_browser_provider",
            route_metadata={
                "adapter_key": "tx_formation_operator_assisted_browser_provider",
                "source_urls": ["https://www.sos.state.tx.us/corp/options.shtml"],
                "last_verified": "2026-05-03",
                "portal_field_sequence": [
                    {
                        "page": "Article 2 - Registered Agent and Office",
                        "fields": [
                            "registered_agent_business_name_or_individual_name",
                            "registered_office_street_address",
                        ],
                    }
                ],
            },
            portal_blockers=[{"code": "portal_account_required", "message": "Account required"}],
        )

        contract = build_adapter_contract(job)

        self.assertEqual(contract["contract_version"], "state_action_v1")
        self.assertEqual(contract["state_adapter"], "tx_formation_operator_assisted_browser_provider")
        self.assertIn("registered_office_street_address", contract["required_customer_inputs"])
        self.assertTrue(contract["operator_fallback"])


if __name__ == "__main__":
    unittest.main()
