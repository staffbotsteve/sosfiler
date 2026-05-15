#!/usr/bin/env python3
"""Tests for the Firecrawl-powered regulatory research pipeline."""

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from backend.regulatory.extractor import records_from_firecrawl_result
from backend.regulatory.firecrawl_client import FirecrawlExtractResult
from backend.regulatory.html_local_extractor import records_from_local_page_payloads
from backend.regulatory.authority_manifest import (
    seed_authority_manifest,
    select_authorities,
    sync_authority_manifest_from_records,
)
from backend.regulatory.local_source_discovery import (
    LocalSourceCandidate,
    discovery_urls,
    local_extraction_urls,
    seeded_local_candidates,
    select_extraction_candidates,
)
from backend.regulatory.research_providers import FirecrawlResearchProvider, provider_statuses
from backend.regulatory.research_runner import run_batch
from backend.regulatory.schemas import VerificationStatus


FIXTURE = BASE_DIR / "qa" / "fixtures" / "firecrawl_tx_state_extract.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def tx_batch() -> dict:
    return {
        "batch_id": "tx_state_filings",
        "jurisdiction_id": "tx_state",
        "title": "Texas state filing map",
        "phase": "top_ten",
        "scope": "state",
        "status": "active",
        "official_domains": ["sos.state.tx.us", "direct.sos.state.tx.us"],
        "required_filings": ["LLC formation"],
        "discovery_queries": ["Texas LLC formation fee official SOSDirect"],
    }


def tx_local_batch() -> dict:
    return {
        "batch_id": "tx_local_filings",
        "jurisdiction_id": "tx_state",
        "title": "Texas county and city local filing map",
        "phase": "top_ten",
        "scope": "local",
        "status": "active",
        "official_domains": ["sos.state.tx.us", "direct.sos.state.tx.us"],
        "required_filings": ["county DBA/FBN/assumed-name filings", "city and county general business licenses"],
        "discovery_queries": ["Texas county city DBA business license official"],
    }


def tx_authority_local_batch() -> dict:
    batch = tx_local_batch()
    batch["authority_context"] = {
        "authority_id": "tx_county_harris_clerk",
        "jurisdiction_name": "Harris County",
        "jurisdiction_level": "county",
        "authority_name": "Harris County Clerk",
    }
    return batch


def test_firecrawl_response_parsing() -> None:
    result = FirecrawlExtractResult.from_payload(load_fixture())
    assert result.success is True
    assert result.status == "completed"
    assert len(result.sources) == 1
    assert result.sources[0].url == "https://www.sos.state.tx.us/corp/forms_boc.shtml"


def test_extraction_gates_verified_record() -> None:
    result = FirecrawlExtractResult.from_payload(load_fixture())
    records = records_from_firecrawl_result(tx_batch(), result, auto_verify=True)
    assert len(records) == 1
    record = records[0]
    assert record.filing_id == "tx_state_filings_certificate_of_formation_limited_liability_company_205"
    assert record.price_confidence.value == "formula_verified"
    assert record.verification_status == VerificationStatus.VERIFIED_OFFICIAL
    assert record.automation.method.value == "playwright"


def test_extraction_gates_incomplete_record_for_review() -> None:
    payload = load_fixture()
    payload["data"]["filing_records"][0]["process_steps"] = []
    result = FirecrawlExtractResult.from_payload(payload)
    records = records_from_firecrawl_result(tx_batch(), result, auto_verify=True)
    assert records[0].verification_status == VerificationStatus.NEEDS_OPERATOR_REVIEW


def test_recurring_record_gets_default_reminders() -> None:
    payload = load_fixture()
    record = payload["data"]["filing_records"][0]
    record["filing_id"] = "annual_report"
    record["filing_category"] = "annual_obligation"
    record["filing_name"] = "Annual Report"
    record["annual_or_renewal_rule"] = "Annual report due each year"
    record["deadline_rule"] = "Due annually"
    record["reminder_rules"] = []
    result = FirecrawlExtractResult.from_payload(payload)
    records = records_from_firecrawl_result(tx_batch(), result, auto_verify=True)
    assert [rule.offset_days for rule in records[0].reminder_rules] == [90, 60, 30, 7]


def test_expedite_options_are_structured_from_extraction() -> None:
    payload = load_fixture()
    record = payload["data"]["filing_records"][0]
    record["expedite_options"] = [
        {
            "label": "24-Hour Filing Service",
            "fee_cents": 35000,
            "processing_time": "Filing response guaranteed within 24 hours.",
            "channel": "online",
            "customer_selectable": True,
            "source_url": "https://www.sos.ca.gov/business-programs/business-entities/service-options/",
        }
    ]
    result = FirecrawlExtractResult.from_payload(payload)
    records = records_from_firecrawl_result(tx_batch(), result, auto_verify=True)
    assert len(records[0].expedite_options) == 1
    assert records[0].expedite_options[0].fee_cents == 35000
    assert records[0].expedite_options[0].customer_selectable is True


def test_mocked_runner_integration_for_texas_batch() -> None:
    from backend.regulatory import research_runner

    original_snapshot = research_runner.write_source_snapshot
    original_replace = research_runner.replace_batch_filing_records
    original_status = research_runner._update_batch_status
    original_load = research_runner.load_filing_records
    calls = {"status": None, "records": []}
    try:
        research_runner.write_source_snapshot = lambda batch_id, payload: BASE_DIR / "data/regulatory/source_snapshots/mock.json"
        research_runner.replace_batch_filing_records = lambda batch_id, records: calls.update(records=[r.filing_id for r in records]) or {
            "upserted": [r.filing_id for r in records],
            "record_count": len(records),
        }
        research_runner._update_batch_status = lambda batch_id, status, note="": calls.update(status=status)
        research_runner.load_filing_records = lambda: []
        output = run_batch(
            tx_batch(),
            provider=FirecrawlResearchProvider(),
            dry_run=False,
            auto_verify=True,
            fixture_payload=load_fixture(),
        )
    finally:
        research_runner.write_source_snapshot = original_snapshot
        research_runner.replace_batch_filing_records = original_replace
        research_runner._update_batch_status = original_status
        research_runner.load_filing_records = original_load

    assert output["records"] == ["tx_state_filings_certificate_of_formation_limited_liability_company_205"]
    assert output["status_after_run"] == "complete"
    assert calls["status"] == "complete"


def test_local_seed_discovery_expands_texas_sources() -> None:
    batch = tx_local_batch()
    candidates = seeded_local_candidates(batch)
    urls = discovery_urls(batch)
    assert len(candidates) >= 10
    assert any(candidate.jurisdiction_level == "county" for candidate in candidates)
    assert any(candidate.jurisdiction_level == "city" for candidate in candidates)
    assert any("hctx" in url or "harris" in url.lower() for url in urls)


def test_local_extraction_prefers_actionable_pages_over_landing_pages() -> None:
    batch = tx_local_batch()
    broad = LocalSourceCandidate(
        candidate_id="broad",
        batch_id=batch["batch_id"],
        jurisdiction_name="Travis County",
        jurisdiction_level="county",
        state="TX",
        authority="County Clerk",
        official_url="https://countyclerk.traviscountytx.gov/",
        domain="countyclerk.traviscountytx.gov",
        filing_topics=["assumed_name", "dba"],
        source_kind="curated_seed",
    )
    actionable = LocalSourceCandidate(
        candidate_id="actionable",
        batch_id=batch["batch_id"],
        jurisdiction_name="Travis County",
        jurisdiction_level="county",
        state="TX",
        authority="County Clerk",
        official_url="https://countyclerk.traviscountytx.gov/departments/recording/dbas/",
        domain="countyclerk.traviscountytx.gov",
        filing_topics=["assumed_name", "dba", "fee"],
        source_kind="firecrawl_web_discovery",
    )
    selected = select_extraction_candidates([broad, actionable])
    urls = local_extraction_urls(batch, selected)
    assert selected == [actionable]
    assert urls == ["https://countyclerk.traviscountytx.gov/departments/recording/dbas"]


def test_local_filing_ids_include_authority() -> None:
    payload = {
        "success": True,
        "status": "completed",
        "data": {
            "filing_records": [
                {
                    "filing_id": "assumed_name",
                    "filing_category": "dba_fbn",
                    "filing_name": "Assumed Name Application",
                    "authority": "Dallas County",
                    "fee_components": [{"code": "fee", "label": "Fee", "amount_cents": 2300, "source_url": "https://example.gov"}],
                    "process_steps": [{"step": 1, "actor": "customer", "title": "File form"}],
                    "output_documents": ["file-stamped certificate"],
                    "source_citations": [{"url": "https://example.gov", "title": "Official", "authority": "Dallas County"}],
                    "expected_turnaround": "Varies",
                },
                {
                    "filing_id": "assumed_name",
                    "filing_category": "dba_fbn",
                    "filing_name": "Assumed Name Application",
                    "authority": "Bexar County",
                    "fee_components": [{"code": "fee", "label": "Fee", "amount_cents": 2300, "source_url": "https://example.gov"}],
                    "process_steps": [{"step": 1, "actor": "customer", "title": "File form"}],
                    "output_documents": ["file-stamped certificate"],
                    "source_citations": [{"url": "https://example.gov", "title": "Official", "authority": "Bexar County"}],
                    "expected_turnaround": "Varies",
                },
            ]
        },
    }
    records = records_from_firecrawl_result(tx_local_batch(), FirecrawlExtractResult.from_payload(payload), auto_verify=True)
    assert records[0].filing_id.startswith("tx_local_filings_dallas_county_assumed_name_application")
    assert records[1].filing_id.startswith("tx_local_filings_bexar_county_assumed_name_application")


def test_authority_context_replaces_generic_local_authority() -> None:
    payload = {
        "success": True,
        "status": "completed",
        "data": {
            "filing_records": [
                {
                    "filing_id": "assumed_name",
                    "filing_category": "dba_fbn",
                    "filing_name": "Assumed Name DBA Filing",
                    "authority": "County Clerk",
                    "fee_components": [{"code": "fee", "label": "Fee", "amount_cents": 2300, "source_url": "https://www.cclerk.hctx.net/PersonalRecords.aspx"}],
                    "process_steps": [{"step": 1, "actor": "sosfiler", "title": "Submit"}],
                    "output_documents": ["file-stamped certificate"],
                    "source_citations": [{"url": "https://www.cclerk.hctx.net/PersonalRecords.aspx", "title": "Assumed Names"}],
                    "expected_turnaround": "same day",
                }
            ]
        },
    }
    records = records_from_firecrawl_result(tx_authority_local_batch(), FirecrawlExtractResult.from_payload(payload), auto_verify=True)
    assert records[0].authority == "Harris County Clerk"
    assert records[0].jurisdiction_name == "Harris County"
    assert records[0].jurisdiction_level.value == "county"
    assert records[0].filing_id.startswith("tx_local_filings_harris_county_clerk_assumed_name_dba_filing")


def test_authority_context_replaces_prefixed_raw_id() -> None:
    payload = {
        "success": True,
        "status": "completed",
        "data": {
            "filing_records": [
                {
                    "filing_id": "tx_local_filings_county_clerk_assumed_name_dba_filing",
                    "filing_category": "dba_fbn",
                    "filing_name": "Assumed Name DBA Filing",
                    "authority": "County Clerk",
                    "fee_components": [{"code": "fee", "label": "Fee", "amount_cents": 2300, "source_url": "https://www.cclerk.hctx.net/PersonalRecords.aspx"}],
                    "process_steps": [{"step": 1, "actor": "sosfiler", "title": "Submit"}],
                    "output_documents": ["file-stamped certificate"],
                    "source_citations": [{"url": "https://www.cclerk.hctx.net/PersonalRecords.aspx", "title": "Assumed Names"}],
                    "expected_turnaround": "same day",
                }
            ]
        },
    }
    records = records_from_firecrawl_result(tx_authority_local_batch(), FirecrawlExtractResult.from_payload(payload), auto_verify=True)
    assert records[0].filing_id == "tx_local_filings_harris_county_clerk_assumed_name_dba_filing"


def test_authority_context_applies_to_html_fallback() -> None:
    records = records_from_local_page_payloads(
        tx_authority_local_batch(),
        [
            {
                "url": "https://www.cclerk.hctx.net/PersonalRecords.aspx",
                "title": "Assumed Names",
                "markdown": "County Clerk Assumed Name / DBA filing fee is $23.00.",
            }
        ],
    )
    assert records[0].authority == "Harris County Clerk"
    assert records[0].jurisdiction_name == "Harris County"
    assert records[0].filing_id == "tx_local_filings_harris_county_clerk_assumed_name_dba_filing"


def test_fee_source_defaults_to_official_citation() -> None:
    payload = {
        "success": True,
        "status": "completed",
        "data": {
            "filing_records": [
                {
                    "filing_id": "assumed_name",
                    "filing_category": "dba_fbn",
                    "filing_name": "Assumed Name Filing",
                    "authority": "Dallas County Clerk",
                    "fee_components": [{"code": "additional_owner", "label": "Additional owner", "amount_cents": 50}],
                    "process_steps": [{"step": 1, "actor": "sosfiler", "title": "Submit"}],
                    "output_documents": ["file-stamped certificate"],
                    "source_citations": [{"url": "https://www.dallascounty.org/government/county-clerk/recording/assumed-names/assumed-names-procedures.php", "title": "Assumed Names"}],
                    "expected_turnaround": "same day",
                }
            ]
        },
    }
    records = records_from_firecrawl_result(tx_local_batch(), FirecrawlExtractResult.from_payload(payload), auto_verify=True)
    assert records[0].fee_components[0].source_url.startswith("https://www.dallascounty.org")
    assert "suspicious_fractional_fee" not in records[0].notes


def test_local_html_fallback_extracts_dallas_license_fee() -> None:
    records = records_from_local_page_payloads(
        tx_local_batch(),
        [
            {
                "url": "https://dallascityhall.com/departments/procurement/Pages/special_collections_licenses.aspx",
                "title": "Special Collections Licenses",
                "markdown": (
                    "Billiard Hall License\n"
                    "An annual license fee of $30 per regulated billiard table is charged to businesses operated for profit.\n"
                    "Precious Metal Vendor\n"
                    "An annual fee of $245 is charged per precious metal vending location."
                ),
            }
        ],
    )
    names = {record.filing_name: record for record in records}
    assert names["Billiard Hall License"].fee_components[0].amount_cents == 3000
    assert names["Precious Metal Vendor"].fee_components[0].amount_cents == 24500


def test_local_html_fallback_extracts_authority_city_permit() -> None:
    batch = tx_local_batch()
    batch["authority_context"] = {
        "authority_id": "tx_city_austin_dsd",
        "jurisdiction_name": "Austin",
        "jurisdiction_level": "city",
        "authority_name": "Austin Development Services",
    }
    records = records_from_local_page_payloads(
        batch,
        [
            {
                "url": "https://www.austintexas.gov/services/apply-operating-authority-license",
                "title": "Apply for an operating authority license | City of Austin Services | AustinTexas.gov",
                "markdown": (
                    "# Apply for an operating authority license\n"
                    "Companies that offer vehicle-for-hire services must obtain an operating authority license. "
                    "The application costs $159.00. How to apply: submit application and pay the fee."
                ),
            }
        ],
    )
    assert records[0].authority == "Austin Development Services"
    assert records[0].jurisdiction_name == "Austin"
    assert records[0].filing_name == "operating authority license"
    assert records[0].fee_components[0].amount_cents == 15900


def test_local_html_fallback_does_not_misread_directory_as_ector() -> None:
    records = records_from_local_page_payloads(
        tx_local_batch(),
        [
            {
                "url": "https://www.sa.gov/Directory/Departments/DSD",
                "title": "Development Services - City of San Antonio",
                "markdown": "Business licenses and permits are available through Development Services.",
            }
        ],
    )
    assert not any("ector" in record.authority.lower() for record in records)


def test_local_upsert_does_not_downgrade_verified_record() -> None:
    from tempfile import TemporaryDirectory

    from backend.regulatory.record_store import upsert_filing_records

    verified_payload = {
        "success": True,
        "status": "completed",
        "data": {
            "filing_records": [
                {
                    "filing_id": "assumed_name",
                    "filing_category": "dba_fbn",
                    "filing_name": "Assumed Name Filing",
                    "authority": "Travis County Clerk",
                    "fee_components": [{"code": "fee", "label": "Fee", "amount_cents": 1000, "source_url": "https://example.gov/dbas"}],
                    "process_steps": [{"step": 1, "actor": "sosfiler", "title": "Submit"}],
                    "output_documents": ["file-stamped certificate"],
                    "source_citations": [{"url": "https://example.gov/dbas", "title": "DBAs", "authority": "Travis County Clerk"}],
                    "expected_turnaround": "same day",
                }
            ]
        },
    }
    weaker_payload = json.loads(json.dumps(verified_payload))
    weaker = weaker_payload["data"]["filing_records"][0]
    weaker["fee_components"] = []
    weaker["source_citations"] = [{"url": "https://example.gov/forms", "title": "Forms", "authority": "Travis County Clerk"}]
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "filings.json"
        verified = records_from_firecrawl_result(tx_local_batch(), FirecrawlExtractResult.from_payload(verified_payload), auto_verify=True)
        weaker_records = records_from_firecrawl_result(tx_local_batch(), FirecrawlExtractResult.from_payload(weaker_payload), auto_verify=True)
        upsert_filing_records(verified, path=path)
        result = upsert_filing_records(weaker_records, path=path)
        stored = json.loads(path.read_text())["records"][0]
        assert result["skipped"] == ["tx_local_filings_travis_county_clerk_assumed_name_filing"]
        assert stored["verification_status"] == "verified_official"


def test_prune_superseded_local_review_records() -> None:
    from tempfile import TemporaryDirectory

    from backend.regulatory.record_store import prune_superseded_local_review_records

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "filings.json"
        path.write_text(json.dumps({
            "version": 1,
            "records": [
                {
                    "filing_id": "old",
                    "jurisdiction_level": "city",
                    "filing_name": "Assumed Name Certificate",
                    "verification_status": "needs_operator_review",
                    "source_citations": [{"url": "https://example.gov/dba"}],
                },
                {
                    "filing_id": "new",
                    "jurisdiction_level": "county",
                    "filing_name": "Assumed Name Certificate (DBA)",
                    "verification_status": "verified_official",
                    "source_citations": [{"url": "https://example.gov/dba"}],
                },
                {
                    "filing_id": "old_place",
                    "jurisdiction_level": "city",
                    "authority": "City of Dallas",
                    "filing_name": "Sexually Oriented Business License",
                    "verification_status": "needs_operator_review",
                    "source_citations": [{"url": "https://example.gov/old"}],
                },
                {
                    "filing_id": "new_place",
                    "jurisdiction_level": "city",
                    "authority": "City of Dallas Special Collections",
                    "filing_name": "Sexually Oriented Business License",
                    "verification_status": "verified_official",
                    "source_citations": [{"url": "https://example.gov/new"}],
                },
                {
                    "filing_id": "state_review",
                    "jurisdiction_level": "state",
                    "filing_name": "Certificate of Formation",
                    "verification_status": "needs_operator_review",
                    "source_citations": [{"url": "https://example.gov/dba"}],
                },
                {
                    "filing_id": "old_alcohol",
                    "jurisdiction_level": "city",
                    "authority": "Fort Worth Environmental Services",
                    "filing_name": "City of Fort Worth Alcohol Permit",
                    "verification_status": "needs_operator_review",
                    "source_citations": [{"url": "https://example.gov/alcohol"}],
                },
                {
                    "filing_id": "new_alcohol",
                    "jurisdiction_level": "city",
                    "authority": "Fort Worth Environmental Services Department Consumer Health Division",
                    "filing_name": "Alcohol Permit",
                    "verification_status": "verified_official",
                    "source_citations": [{"url": "https://example.gov/alcohol-fees"}],
                },
            ],
        }))
        result = prune_superseded_local_review_records(path=path)
        remaining = {record["filing_id"] for record in json.loads(path.read_text())["records"]}
        assert result["removed"] == ["old", "old_alcohol", "old_place"]
        assert remaining == {"new", "new_alcohol", "new_place", "state_review"}


def test_provider_registry_exposes_planned_slots() -> None:
    statuses = {item["name"]: item for item in provider_statuses()}
    assert "firecrawl" in statuses
    assert "tavily" in statuses
    assert "exa" in statuses
    assert "apify" in statuses
    assert "accela" in statuses
    assert "browserbase" in statuses
    assert "source_discovery" in statuses["tavily"]["capabilities"]


def test_authority_manifest_seeds_official_texas_sources() -> None:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "authority_manifest.json"
        result = seed_authority_manifest(path=path)
        payload = json.loads(path.read_text())
        authorities = {item["authority_id"]: item for item in payload["authorities"]}
        assert result["authority_count"] >= 10
        assert authorities["tx_state_sos"]["jurisdiction_level"] == "state"
        assert authorities["tx_county_harris_clerk"]["source_urls"][0].startswith("https://www.cclerk.hctx.net")
        assert authorities["tx_city_houston_permitting"]["authority_type"] == "city_permitting"


def test_authority_manifest_can_select_county_work() -> None:
    from tempfile import TemporaryDirectory

    import backend.regulatory.authority_manifest as manifest

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "authority_manifest.json"
        original_path = manifest.MANIFEST_PATH
        original_load_records = manifest.load_filing_records
        try:
            manifest.MANIFEST_PATH = path
            manifest.load_filing_records = lambda: []
            seed_authority_manifest(path=path)
            selected = select_authorities(state="TX", level="county", status="not_started", limit=2)
            one = select_authorities(authority_id="tx_city_houston_permitting", limit=1)
        finally:
            manifest.MANIFEST_PATH = original_path
            manifest.load_filing_records = original_load_records
        assert [item["authority_id"] for item in selected] == [
            "tx_county_harris_clerk",
            "tx_county_dallas_clerk",
        ]
        assert one[0]["authority_id"] == "tx_city_houston_permitting"


def test_authority_manifest_syncs_existing_verified_record() -> None:
    from tempfile import TemporaryDirectory

    import backend.regulatory.authority_manifest as manifest

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "authority_manifest.json"
        original_load_records = manifest.load_filing_records
        try:
            manifest.load_filing_records = lambda: [
                {
                    "filing_id": "tx_local_filings_travis_county_clerk_assumed_name",
                    "jurisdiction_name": "Texas",
                    "authority": "Travis County Clerk",
                    "filing_name": "Assumed Name Certificate",
                    "verification_status": "verified_official",
                    "source_citations": [
                        {"url": "https://countyclerk.traviscountytx.gov/departments/recording/dbas/"}
                    ],
                }
            ]
            seed_authority_manifest(path=path)
            sync_authority_manifest_from_records(path=path)
        finally:
            manifest.load_filing_records = original_load_records
        payload = json.loads(path.read_text())
        travis = next(item for item in payload["authorities"] if item["authority_id"] == "tx_county_travis_clerk")
        assert travis["status"] == "verified"
        assert travis["record_ids"] == ["tx_local_filings_travis_county_clerk_assumed_name"]


if __name__ == "__main__":
    test_firecrawl_response_parsing()
    test_extraction_gates_verified_record()
    test_extraction_gates_incomplete_record_for_review()
    test_recurring_record_gets_default_reminders()
    test_mocked_runner_integration_for_texas_batch()
    test_local_seed_discovery_expands_texas_sources()
    test_local_extraction_prefers_actionable_pages_over_landing_pages()
    test_local_filing_ids_include_authority()
    test_authority_context_replaces_generic_local_authority()
    test_authority_context_replaces_prefixed_raw_id()
    test_authority_context_applies_to_html_fallback()
    test_fee_source_defaults_to_official_citation()
    test_local_html_fallback_extracts_dallas_license_fee()
    test_local_html_fallback_extracts_authority_city_permit()
    test_local_html_fallback_does_not_misread_directory_as_ector()
    test_local_upsert_does_not_downgrade_verified_record()
    test_prune_superseded_local_review_records()
    test_provider_registry_exposes_planned_slots()
    test_authority_manifest_seeds_official_texas_sources()
    test_authority_manifest_can_select_county_work()
    test_authority_manifest_syncs_existing_verified_record()
    print("regulatory Firecrawl tests passed")
