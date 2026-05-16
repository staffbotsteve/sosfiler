#!/usr/bin/env python3
"""Tests for plan v2.6 §4.2.5 filing_confirmation JSON shape.

The trigger predicate documented in §4.2.4 requires
`{"value":"<non-empty string>","issued_at":"...","source":"regex|operator|adapter"}`.
These tests pin the builder + reader so the contract is enforced in app code
before SQL triggers ever fire.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

# Plan v2.6 / codex P2 #4: prevent server import from mutating the working
# SQLite file. Must be set BEFORE the first `import server` happens anywhere.
os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


class FilingConfirmationPayloadTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("PII_ENCRYPTION_KEY", "test-key-for-local-unit-tests")

    def test_builder_emits_canonical_json_shape(self):
        from server import build_filing_confirmation_payload  # type: ignore

        raw = build_filing_confirmation_payload("L25000012345", "regex")
        parsed = json.loads(raw)
        self.assertEqual(parsed["value"], "L25000012345")
        self.assertEqual(parsed["source"], "regex")
        self.assertIn("issued_at", parsed)
        self.assertRegex(parsed["issued_at"], r"^\d{4}-\d{2}-\d{2}T")

    def test_builder_strips_whitespace_on_value(self):
        from server import build_filing_confirmation_payload  # type: ignore

        raw = build_filing_confirmation_payload("  ABC-123  ", "operator")
        self.assertEqual(json.loads(raw)["value"], "ABC-123")

    def test_builder_rejects_empty_value(self):
        from server import build_filing_confirmation_payload  # type: ignore

        for bad in ("", "   ", None):
            with self.assertRaises((ValueError, TypeError)):
                build_filing_confirmation_payload(bad, "operator")  # type: ignore[arg-type]

    def test_builder_rejects_non_string_value(self):
        from server import build_filing_confirmation_payload  # type: ignore

        with self.assertRaises(ValueError):
            build_filing_confirmation_payload(12345, "operator")  # type: ignore[arg-type]

    def test_builder_rejects_unknown_source(self):
        from server import build_filing_confirmation_payload  # type: ignore

        with self.assertRaises(ValueError):
            build_filing_confirmation_payload("X-1", "guess")

    def test_builder_accepts_each_known_source(self):
        from server import build_filing_confirmation_payload  # type: ignore

        for source in ("regex", "operator", "adapter"):
            raw = build_filing_confirmation_payload("X-1", source)
            self.assertEqual(json.loads(raw)["source"], source)

    def test_reader_extracts_value_from_canonical_shape(self):
        from server import build_filing_confirmation_payload, read_filing_confirmation_value  # type: ignore

        raw = build_filing_confirmation_payload("FL12345", "adapter")
        self.assertEqual(read_filing_confirmation_value(raw), "FL12345")

    def test_reader_returns_none_for_legacy_null(self):
        from server import read_filing_confirmation_value  # type: ignore

        self.assertIsNone(read_filing_confirmation_value(None))
        self.assertIsNone(read_filing_confirmation_value(""))

    def test_reader_returns_none_for_malformed_json(self):
        from server import read_filing_confirmation_value  # type: ignore

        self.assertIsNone(read_filing_confirmation_value("not json"))

    def test_reader_returns_none_for_empty_value_field(self):
        from server import read_filing_confirmation_value  # type: ignore

        self.assertIsNone(read_filing_confirmation_value(json.dumps({"value": ""})))
        self.assertIsNone(read_filing_confirmation_value(json.dumps({"value": "   "})))

    def test_reader_rejects_non_string_value_field(self):
        """Plan v2.6 round-7: {"value": 123} must fail the predicate.

        The Postgres trigger uses jsonb_typeof for strict string typing; the
        Python reader mirrors that by rejecting non-string $.value values.
        """
        from server import read_filing_confirmation_value  # type: ignore

        self.assertIsNone(read_filing_confirmation_value(json.dumps({"value": 123})))
        self.assertIsNone(read_filing_confirmation_value(json.dumps({"value": True})))
        self.assertIsNone(read_filing_confirmation_value(json.dumps({"value": None})))

    def test_filing_transition_request_accepts_confirmation(self):
        from server import FilingTransitionRequest  # type: ignore

        req = FilingTransitionRequest(
            target_status="submitted",
            evidence_path="/tmp/x.pdf",
            filing_confirmation="L25000012345",
            filing_confirmation_source="regex",
        )
        self.assertEqual(req.filing_confirmation, "L25000012345")
        self.assertEqual(req.filing_confirmation_source, "regex")

    def test_filing_transition_request_defaults_source_to_operator(self):
        from server import FilingTransitionRequest  # type: ignore

        req = FilingTransitionRequest(target_status="submitted", evidence_path="/tmp/x.pdf")
        self.assertEqual(req.filing_confirmation_source, "operator")
        self.assertIsNone(req.filing_confirmation)


if __name__ == "__main__":
    unittest.main()
