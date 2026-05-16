#!/usr/bin/env python3
"""Smoke tests for the regulatory filing intelligence scaffold."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
TEMP_DIR = tempfile.TemporaryDirectory(prefix="sosfiler-regulatory-test-")
TEST_DATA_DIR = Path(TEMP_DIR.name)


def run_module(*args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "backend.regulatory.research_orchestrator", *args],
        cwd=BASE_DIR,
        check=True,
        text=True,
        capture_output=True,
        env={**os.environ, "REGULATORY_DATA_DIR": str(TEST_DATA_DIR)},
    )
    return json.loads(result.stdout)


def test_init_generates_expected_coverage() -> None:
    payload = run_module("init")
    assert payload["jurisdictions"] == 51
    assert payload["research_batches"] == 102
    assert payload["sample_filings"] >= 1
    assert payload["paths"]["phase_plan"].endswith("research_phase_plan.json")


def test_phase_gate_activates_top_ten_first() -> None:
    payload = run_module("activate-phase", "top_ten")
    assert payload["activated"] is True
    assert payload["counts"]["top_ten"]["active"] == 20
    assert payload["counts"]["remaining_40_plus_dc"]["queued"] == 82

    blocked = run_module("activate-phase", "remaining_40_plus_dc")
    assert blocked["activated"] is False
    assert len(blocked["blocked_by"]) == 20


def test_price_check_reports_sample_filing() -> None:
    payload = run_module("price-check")
    assert payload[0]["filing_id"] == "tx_llc_certificate_of_formation"
    assert "service_fee_cents" in payload[0]


if __name__ == "__main__":
    test_init_generates_expected_coverage()
    test_phase_gate_activates_top_ten_first()
    test_price_check_reports_sample_filing()
    print("regulatory scaffold smoke tests passed")
