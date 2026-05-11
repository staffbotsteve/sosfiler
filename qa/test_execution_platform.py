#!/usr/bin/env python3
"""Unit tests for execution-platform safety primitives."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from execution_platform import (  # noqa: E402
    build_quote,
    decrypt_pii,
    encrypt_pii,
    redact_sensitive,
    should_escalate_chat,
    validate_transition,
)


class ExecutionPlatformTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("PII_ENCRYPTION_KEY", "test-key-for-local-unit-tests")

    def test_evidence_required_for_submitted(self):
        decision = validate_transition("ready_to_file", "submitted")
        self.assertFalse(decision.ok)
        self.assertIn("requires official evidence", decision.reason)

    def test_evidence_allows_submitted_transition(self):
        decision = validate_transition("ready_to_file", "submitted", "/tmp/receipt.pdf")
        self.assertTrue(decision.ok)

    def test_invalid_transition_rejected(self):
        decision = validate_transition("intake_complete", "complete", "/tmp/final.pdf")
        self.assertFalse(decision.ok)

    def test_quote_total_and_strategy(self):
        quote = build_quote(
            product_type="formation",
            entity_type="LLC",
            state="tx",
            platform_fee_cents=4900,
            government_fee_cents=30000,
            processing_fee_cents=820,
            registered_agent_fee_cents=4900,
        )
        self.assertEqual(quote["state"], "TX")
        self.assertEqual(quote["capture_strategy"], "authorize_then_capture")
        self.assertEqual(quote["estimated_total_cents"], 40620)

    def test_redacts_nested_sensitive_values(self):
        payload = {
            "responsible_party_ssn": "123-45-6789",
            "nested": {"ein": "12-3456789", "safe": "Formation question"},
        }
        redacted = redact_sensitive(payload)
        self.assertEqual(redacted["responsible_party_ssn"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["ein"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["safe"], "Formation question")

    def test_encrypt_decrypt_round_trip(self):
        token = encrypt_pii("123456789")
        self.assertNotIn("123456789", token)
        self.assertEqual(decrypt_pii(token), "123456789")

    def test_chat_without_verified_context_escalates(self):
        escalate, reason = should_escalate_chat("Can you form my company?", None)
        self.assertTrue(escalate)
        self.assertIn("No verified", reason)

    def test_chat_with_verified_context_can_answer(self):
        escalate, reason = should_escalate_chat(
            "What is the LLC filing fee?",
            {"state": "TX", "state_fee_cents": 30000},
        )
        self.assertFalse(escalate)
        self.assertEqual(reason, "")


if __name__ == "__main__":
    unittest.main()
