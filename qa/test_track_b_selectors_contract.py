#!/usr/bin/env python3
"""Track B tests: FilingAdapter.selectors_contract surface.

Plan v2.6 §4.3 + §4.5. The drift canary (future workstream) reads each
adapter's selectors_contract to validate the live portal still matches.
These tests pin the dataclass shape, ABC default, and a basic adapter
override pattern.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("SOSFILER_SKIP_INIT_DB", "1")

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))


class SelectorContractDataclassTests(unittest.TestCase):
    def test_dataclass_is_frozen_with_expected_defaults(self):
        from execution_platform import SelectorContractEntry  # type: ignore

        entry = SelectorContractEntry(
            page="login",
            selector="input[name='credentials']",
            purpose="fill credentials",
        )
        self.assertEqual(entry.page, "login")
        self.assertEqual(entry.interaction, "click")
        self.assertTrue(entry.required)
        self.assertEqual(entry.fail_behavior, "raise")
        # frozen — mutation raises
        with self.assertRaises(Exception):
            entry.purpose = "tamper"  # type: ignore[misc]

    def test_dataclass_accepts_all_interactions(self):
        from execution_platform import SelectorContractEntry  # type: ignore

        for interaction in ("click", "fill", "assert", "extract", "check"):
            entry = SelectorContractEntry(
                page="receipt",
                selector="body",
                purpose="extract body text",
                interaction=interaction,
            )
            self.assertEqual(entry.interaction, interaction)


class FilingAdapterDefaultTests(unittest.TestCase):
    def test_base_adapter_default_contract_is_empty_tuple(self):
        from execution_platform import FilingAdapter  # type: ignore

        # Class-level default; subclasses inherit.
        self.assertEqual(FilingAdapter.selectors_contract, ())

    def test_subclass_can_override_contract(self):
        from execution_platform import FilingAdapter, FilingActionResult, SelectorContractEntry  # type: ignore

        class FakeLiveAdapter(FilingAdapter):
            selectors_contract = (
                SelectorContractEntry(
                    page="login",
                    selector="input[name='user']",
                    purpose="fill username",
                    interaction="fill",
                ),
                SelectorContractEntry(
                    page="payment",
                    selector="button[name='submit-payment']",
                    purpose="submit payment",
                    interaction="click",
                ),
            )

            async def preflight(self, filing_job):
                return FilingActionResult(status="ready_to_file")

            async def submit(self, filing_job):
                return FilingActionResult(status="submitted")

            async def check_status(self, filing_job):
                return FilingActionResult(status="approved")

            async def collect_documents(self, filing_job):
                return FilingActionResult(status="documents_collected")

            def requires_operator(self, filing_job):
                return False

        adapter = FakeLiveAdapter()
        self.assertEqual(len(adapter.selectors_contract), 2)
        self.assertEqual(adapter.selectors_contract[0].page, "login")
        self.assertEqual(adapter.selectors_contract[1].interaction, "click")

    def test_audit_doc_exists_for_track_b(self):
        # Sanity check: the audit doc that produced this contract is on disk.
        doc = Path(__file__).resolve().parents[1] / "docs" / "track_b_adapter_audit_2026-05-16.md"
        self.assertTrue(doc.exists(), f"track B audit missing at {doc}")
        text = doc.read_text(encoding="utf-8")
        for state in ("California", "Nevada", "Texas"):
            self.assertIn(state, text)


if __name__ == "__main__":
    unittest.main()
