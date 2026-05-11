#!/usr/bin/env python3
"""Unit tests for execution persistence repository mapping."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from execution_repository import ExecutionPersistence, SupabaseExecutionRepository  # noqa: E402


class ExecutionRepositoryTests(unittest.TestCase):
    def test_quote_payload_maps_external_id(self):
        quote = {
            "quote_id": "Q-123",
            "product_type": "formation",
            "entity_type": "LLC",
            "state": "ny",
            "line_items": [{"label": "Fee", "amount_cents": 4900}],
            "estimated_total_cents": 24900,
            "idempotency_key": "idem",
        }

        payload = SupabaseExecutionRepository.quote_payload(quote, "ORDER-1")
        self.assertEqual(payload["external_quote_id"], "Q-123")
        self.assertEqual(payload["order_id"], "ORDER-1")
        self.assertEqual(payload["state"], "ny")
        self.assertEqual(payload["estimated_total_cents"], 24900)

    def test_filing_job_payload_maps_runtime_route(self):
        job = {
            "id": "FIL-1",
            "order_id": "ORDER-1",
            "action_type": "formation",
            "state": "NY",
            "entity_type": "LLC",
            "status": "ready_to_file",
            "automation_lane": "browser_automation",
            "automation_difficulty": "easy",
            "adapter_key": "ny_formation_browser_automation",
            "portal_url": "https://example.test",
            "portal_blockers": [],
            "required_evidence": {"submitted": ["receipt"], "approved": ["approval"]},
            "route_metadata": {"source_urls": ["https://example.test"]},
        }

        payload = SupabaseExecutionRepository.filing_job_payload(job)
        self.assertEqual(payload["legacy_job_id"], "FIL-1")
        self.assertEqual(payload["product_type"], "formation")
        self.assertEqual(payload["automation_lane"], "browser_automation")
        self.assertEqual(payload["evidence_required"]["submitted"], ["receipt"])
        self.assertEqual(payload["metadata"]["source_urls"], ["https://example.test"])

    def test_sqlite_mode_dual_write_is_noop(self):
        old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        old_url = os.environ.get("SUPABASE_DB_URL")
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "sqlite"
        os.environ.pop("SUPABASE_DB_URL", None)
        try:
            persistence = ExecutionPersistence()
            result = persistence.dual_write("upsert_quote", {"quote_id": "Q", "product_type": "formation"})
            self.assertTrue(result.ok)
            self.assertEqual(result.mode, "sqlite")
            self.assertIn("disabled", result.message)
        finally:
            if old_mode is None:
                os.environ.pop("EXECUTION_PERSISTENCE_MODE", None)
            else:
                os.environ["EXECUTION_PERSISTENCE_MODE"] = old_mode
            if old_url is None:
                os.environ.pop("SUPABASE_DB_URL", None)
            else:
                os.environ["SUPABASE_DB_URL"] = old_url

    def test_table_counts_result_when_repository_unconfigured(self):
        old_mode = os.environ.get("EXECUTION_PERSISTENCE_MODE")
        old_url = os.environ.get("SUPABASE_DB_URL")
        os.environ["EXECUTION_PERSISTENCE_MODE"] = "dual"
        os.environ.pop("SUPABASE_DB_URL", None)
        try:
            result, counts = ExecutionPersistence().table_counts()
            self.assertFalse(result.ok)
            self.assertEqual(counts, {})
            self.assertIn("not configured", result.message)
        finally:
            if old_mode is None:
                os.environ.pop("EXECUTION_PERSISTENCE_MODE", None)
            else:
                os.environ["EXECUTION_PERSISTENCE_MODE"] = old_mode
            if old_url is None:
                os.environ.pop("SUPABASE_DB_URL", None)
            else:
                os.environ["SUPABASE_DB_URL"] = old_url


if __name__ == "__main__":
    unittest.main()
