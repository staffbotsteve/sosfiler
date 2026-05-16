"""Official-authority manifest for targeted regulatory research.

The manifest is the queue for authority-led research. Broad discovery may help
find candidate official URLs, but filing records should be extracted from the
official authorities listed here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import regulatory_data_dir
from .record_store import load_filing_records
from .schemas import utc_now


MANIFEST_PATH = regulatory_data_dir() / "authority_manifest.json"

AUTHORITY_STATUS_NOT_STARTED = "not_started"
AUTHORITY_STATUS_RUNNING = "running"
AUTHORITY_STATUS_VERIFIED = "verified"
AUTHORITY_STATUS_NEEDS_REVIEW = "needs_review"
AUTHORITY_STATUS_BLOCKED = "blocked"

LOCAL_LEVELS = {"county", "city"}


def _authority(
    authority_id: str,
    state: str,
    jurisdiction_name: str,
    jurisdiction_level: str,
    authority_name: str,
    authority_type: str,
    source_urls: list[str],
    filing_focus: list[str],
    batch_id: str,
    priority: int,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "authority_id": authority_id,
        "state": state,
        "jurisdiction_name": jurisdiction_name,
        "jurisdiction_level": jurisdiction_level,
        "authority_name": authority_name,
        "authority_type": authority_type,
        "batch_id": batch_id,
        "priority": priority,
        "status": AUTHORITY_STATUS_NOT_STARTED,
        "source_urls": source_urls,
        "filing_focus": filing_focus,
        "notes": notes,
        "last_run_at": "",
        "record_ids": [],
        "blockers": [],
    }


def texas_authorities() -> list[dict[str, Any]]:
    local_focus = [
        "dba_fbn",
        "business_license",
        "business_tax",
        "home_occupation",
        "renewal",
        "amendment",
        "closure",
    ]
    return [
        _authority(
            "tx_state_sos",
            "TX",
            "Texas",
            "state",
            "Texas Secretary of State",
            "state_filing_office",
            [
                "https://www.sos.state.tx.us/corp/forms_boc.shtml",
                "https://www.sos.state.tx.us/corp/filingandothergeneralfaqs.shtml",
                "https://direct.sos.state.tx.us/acct/acct-login.asp",
                "https://www.sos.state.tx.us/corp/sosda/index.shtml",
                "https://webservices.sos.state.tx.us/filing-status/status.aspx",
                "https://comptroller.texas.gov/taxes/franchise/",
                "https://comptroller.texas.gov/taxes/franchise/pir-oir-filing-req.php",
            ],
            [
                "formation",
                "foreign_authority",
                "amendment",
                "registered_agent_change",
                "address_change",
                "annual_obligation",
                "dissolution",
                "reinstatement",
                "certificate_copy",
            ],
            "tx_state_filings",
            10,
            "State source set is official-only. State batch replacement should be used carefully.",
        ),
        _authority(
            "tx_county_harris_clerk",
            "TX",
            "Harris County",
            "county",
            "Harris County Clerk",
            "county_clerk",
            ["https://www.cclerk.hctx.net/PersonalRecords.aspx"],
            local_focus,
            "tx_local_filings",
            20,
        ),
        _authority(
            "tx_county_dallas_clerk",
            "TX",
            "Dallas County",
            "county",
            "Dallas County Clerk",
            "county_clerk",
            ["https://www.dallascounty.org/government/county-clerk/recording/assumed-names/assumed-names-procedures.php"],
            local_focus,
            "tx_local_filings",
            30,
        ),
        _authority(
            "tx_county_tarrant_clerk",
            "TX",
            "Tarrant County",
            "county",
            "Tarrant County Clerk",
            "county_clerk",
            ["https://www.tarrantcountytx.gov/en/county-clerk/vital-records/assumed-names.html"],
            local_focus,
            "tx_local_filings",
            40,
        ),
        _authority(
            "tx_county_bexar_clerk",
            "TX",
            "Bexar County",
            "county",
            "Bexar County Clerk",
            "county_clerk",
            ["https://www.bexar.org/2955/Assumed-Business-NamesDBAs"],
            local_focus,
            "tx_local_filings",
            50,
        ),
        _authority(
            "tx_county_travis_clerk",
            "TX",
            "Travis County",
            "county",
            "Travis County Clerk",
            "county_clerk",
            ["https://countyclerk.traviscountytx.gov/departments/recording/dbas/"],
            local_focus,
            "tx_local_filings",
            60,
        ),
        _authority(
            "tx_county_collin_clerk",
            "TX",
            "Collin County",
            "county",
            "Collin County Clerk",
            "county_clerk",
            ["https://www.collincountytx.gov/County-Clerk/Pages/Assumed-Names.aspx"],
            local_focus,
            "tx_local_filings",
            70,
        ),
        _authority(
            "tx_county_denton_clerk",
            "TX",
            "Denton County",
            "county",
            "Denton County Clerk",
            "county_clerk",
            ["https://www.dentoncounty.gov/277/Assumed-Names-DBAs"],
            local_focus,
            "tx_local_filings",
            80,
        ),
        _authority(
            "tx_county_fort_bend_clerk",
            "TX",
            "Fort Bend County",
            "county",
            "Fort Bend County Clerk",
            "county_clerk",
            ["https://www.fortbendcountytx.gov/government/departments/county-clerk/dba-assumed-name"],
            local_focus,
            "tx_local_filings",
            90,
        ),
        _authority(
            "tx_county_williamson_clerk",
            "TX",
            "Williamson County",
            "county",
            "Williamson County Clerk",
            "county_clerk",
            ["https://www.wilcotx.gov/311/Assumed-Name-Certificates"],
            local_focus,
            "tx_local_filings",
            100,
        ),
        _authority(
            "tx_county_ector_clerk",
            "TX",
            "Ector County",
            "county",
            "Ector County Clerk",
            "county_clerk",
            ["https://newtools.cira.state.tx.us/page/ector.AssumedName"],
            local_focus,
            "tx_local_filings",
            110,
        ),
        _authority(
            "tx_county_rockwall_clerk",
            "TX",
            "Rockwall County",
            "county",
            "Rockwall County Clerk",
            "county_clerk",
            ["https://www.rockwallcountytexas.com/658/Assumed-Name-DBA"],
            local_focus,
            "tx_local_filings",
            120,
        ),
        _authority(
            "tx_county_ellis_clerk",
            "TX",
            "Ellis County",
            "county",
            "Ellis County Clerk",
            "county_clerk",
            ["https://elliscountytx.gov/819/Assumed-Names"],
            local_focus,
            "tx_local_filings",
            130,
        ),
        _authority(
            "tx_city_houston_permitting",
            "TX",
            "Houston",
            "city",
            "Houston Permitting Center",
            "city_permitting",
            ["https://www.houstonpermittingcenter.org/"],
            local_focus,
            "tx_local_filings",
            200,
        ),
        _authority(
            "tx_city_san_antonio_dsd",
            "TX",
            "San Antonio",
            "city",
            "City of San Antonio Development Services",
            "city_permitting",
            ["https://www.sa.gov/Directory/Departments/DSD"],
            local_focus,
            "tx_local_filings",
            210,
        ),
        _authority(
            "tx_city_dallas_licenses",
            "TX",
            "Dallas",
            "city",
            "City of Dallas Special Collections",
            "city_license_office",
            ["https://dallascityhall.com/departments/procurement/Pages/special_collections_licenses.aspx"],
            local_focus,
            "tx_local_filings",
            220,
        ),
        _authority(
            "tx_city_austin_dsd",
            "TX",
            "Austin",
            "city",
            "Austin Development Services",
            "city_permitting",
            ["https://www.austintexas.gov/department/development-services"],
            local_focus,
            "tx_local_filings",
            230,
        ),
        _authority(
            "tx_city_fort_worth_dsd",
            "TX",
            "Fort Worth",
            "city",
            "Fort Worth Development Services",
            "city_permitting",
            ["https://www.fortworthtexas.gov/departments/development-services"],
            local_focus,
            "tx_local_filings",
            240,
        ),
    ]


def _manifest_path(path: Path | None = None) -> Path:
    return path or MANIFEST_PATH


def load_authority_manifest(path: Path | None = None) -> dict[str, Any]:
    path = _manifest_path(path)
    if not path.exists():
        seed_authority_manifest()
    return json.loads(path.read_text())


def save_authority_manifest(payload: dict[str, Any], path: Path | None = None) -> None:
    path = _manifest_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = utc_now()
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def seed_authority_manifest(state: str = "TX", force: bool = False, path: Path | None = None) -> dict[str, Any]:
    path = _manifest_path(path)
    existing = {"version": 1, "authorities": []}
    if path.exists() and not force:
        existing = json.loads(path.read_text())
    by_id = {item["authority_id"]: item for item in existing.get("authorities", [])}
    seeded = texas_authorities() if state.upper() == "TX" else []
    added: list[str] = []
    for authority in seeded:
        if authority["authority_id"] in by_id and not force:
            continue
        by_id[authority["authority_id"]] = authority
        added.append(authority["authority_id"])
    payload = {
        "version": 1,
        "method": "authority_led_official_sources_only",
        "coverage_goal": "Research state filing offices first, then each county/city official authority for DBA, license, renewal, amendment, and closure filings.",
        "updated_at": utc_now(),
        "authorities": sorted(by_id.values(), key=lambda item: (item.get("state", ""), item.get("priority", 9999), item["authority_id"])),
    }
    save_authority_manifest(payload, path=path)
    sync_authority_manifest_from_records(path=path)
    return {"state": state.upper(), "added": added, "authority_count": len(payload["authorities"]), "path": str(path)}


def matching_record_ids(authority: dict[str, Any], records: list[dict[str, Any]]) -> list[str]:
    names = {
        str(authority.get("jurisdiction_name", "")).lower(),
        str(authority.get("authority_name", "")).lower(),
    }
    authority_level = str(authority.get("jurisdiction_level", "")).lower()
    urls = [url.rstrip("/") for url in authority.get("source_urls", [])]
    matched: list[str] = []
    for record in records:
        record_level = str(record.get("jurisdiction_level", "")).lower()
        haystack = " ".join([
            str(record.get("jurisdiction_name", "")),
            str(record.get("authority", "")),
            str(record.get("filing_name", "")),
        ]).lower()
        citation_urls = [
            str(citation.get("url", "")).rstrip("/")
            for citation in record.get("source_citations", [])
            if isinstance(citation, dict)
        ]
        level_match = not authority_level or record_level == authority_level
        name_match = any(name and name in haystack for name in names)
        url_match = any(url and any(citation_url.startswith(url) or url.startswith(citation_url) for citation_url in citation_urls) for url in urls)
        if url_match or (level_match and name_match):
            matched.append(str(record.get("filing_id", "")))
    return sorted(item for item in matched if item)


def status_for_record_ids(record_ids: list[str], records: list[dict[str, Any]]) -> str:
    if not record_ids:
        return AUTHORITY_STATUS_NOT_STARTED
    by_id = {str(record.get("filing_id", "")): record for record in records}
    statuses = [str(by_id.get(record_id, {}).get("verification_status", "")) for record_id in record_ids]
    if statuses and all(status == "verified_official" for status in statuses):
        return AUTHORITY_STATUS_VERIFIED
    return AUTHORITY_STATUS_NEEDS_REVIEW


def sync_authority_manifest_from_records(path: Path | None = None) -> dict[str, Any]:
    path = _manifest_path(path)
    payload = load_authority_manifest(path) if path.exists() else {"version": 1, "authorities": []}
    records = load_filing_records()
    updated: list[str] = []
    for authority in payload.get("authorities", []):
        if authority.get("status") in {AUTHORITY_STATUS_RUNNING, AUTHORITY_STATUS_BLOCKED}:
            continue
        record_ids = matching_record_ids(authority, records)
        if record_ids != authority.get("record_ids", []):
            authority["record_ids"] = record_ids
            updated.append(authority["authority_id"])
        if record_ids and authority.get("blockers"):
            authority["blockers"] = []
            updated.append(authority["authority_id"])
        derived_status = status_for_record_ids(record_ids, records)
        if not record_ids and authority.get("status") == AUTHORITY_STATUS_VERIFIED:
            derived_status = AUTHORITY_STATUS_NEEDS_REVIEW
        if record_ids and authority.get("status") != derived_status:
            authority["status"] = derived_status
            updated.append(authority["authority_id"])
        elif not record_ids and authority.get("status") == AUTHORITY_STATUS_VERIFIED:
            authority["status"] = derived_status
            authority.setdefault("blockers", []).append("No current filing records are linked to this authority.")
            updated.append(authority["authority_id"])
    save_authority_manifest(payload, path=path)
    return {"updated": sorted(set(updated)), "authority_count": len(payload.get("authorities", []))}


def authority_summary(state: str | None = None) -> dict[str, Any]:
    sync_authority_manifest_from_records()
    payload = load_authority_manifest()
    authorities = [
        authority for authority in payload.get("authorities", [])
        if state is None or authority.get("state", "").upper() == state.upper()
    ]
    by_status: dict[str, int] = {}
    by_level: dict[str, int] = {}
    for authority in authorities:
        by_status[authority.get("status", "unknown")] = by_status.get(authority.get("status", "unknown"), 0) + 1
        by_level[authority.get("jurisdiction_level", "unknown")] = by_level.get(authority.get("jurisdiction_level", "unknown"), 0) + 1
    return {
        "authority_count": len(authorities),
        "by_status": by_status,
        "by_level": by_level,
        "next_not_started": [
            {
                "authority_id": authority["authority_id"],
                "jurisdiction_name": authority["jurisdiction_name"],
                "jurisdiction_level": authority["jurisdiction_level"],
                "source_urls": authority.get("source_urls", []),
            }
            for authority in sorted(authorities, key=lambda item: (item.get("priority", 9999), item["authority_id"]))
            if authority.get("status") == AUTHORITY_STATUS_NOT_STARTED
        ][:10],
    }


def authority_review_report(state: str | None = None) -> dict[str, Any]:
    sync_authority_manifest_from_records()
    payload = load_authority_manifest()
    records = {str(record.get("filing_id", "")): record for record in load_filing_records()}
    authorities = [
        authority for authority in payload.get("authorities", [])
        if state is None or authority.get("state", "").upper() == state.upper()
    ]
    review_items: list[dict[str, Any]] = []
    for authority in sorted(authorities, key=lambda item: (item.get("priority", 9999), item["authority_id"])):
        if authority.get("status") not in {AUTHORITY_STATUS_NEEDS_REVIEW, AUTHORITY_STATUS_BLOCKED}:
            continue
        record_ids = authority.get("record_ids", [])
        record_summaries = []
        for record_id in record_ids:
            record = records.get(record_id, {})
            record_summaries.append({
                "filing_id": record_id,
                "filing_name": record.get("filing_name", ""),
                "verification_status": record.get("verification_status", ""),
                "price_confidence": record.get("price_confidence", ""),
                "notes": record.get("notes", ""),
            })
        if record_ids:
            reason = "Extracted records need fee/process/automation review."
        else:
            reason = "No filing records extracted from the current official URL set; refine to narrower official filing pages."
        review_items.append({
            "authority_id": authority["authority_id"],
            "jurisdiction_name": authority.get("jurisdiction_name", ""),
            "jurisdiction_level": authority.get("jurisdiction_level", ""),
            "authority_name": authority.get("authority_name", ""),
            "status": authority.get("status", ""),
            "reason": reason,
            "source_urls": authority.get("source_urls", []),
            "blockers": authority.get("blockers", []),
            "records": record_summaries,
        })
    return {
        "review_count": len(review_items),
        "items": review_items,
    }


def select_authorities(
    state: str | None = None,
    level: str | None = None,
    status: str | None = AUTHORITY_STATUS_NOT_STARTED,
    limit: int = 1,
    authority_id: str | None = None,
) -> list[dict[str, Any]]:
    sync_authority_manifest_from_records()
    payload = load_authority_manifest()
    selected = []
    for authority in payload.get("authorities", []):
        if authority_id and authority.get("authority_id") != authority_id:
            continue
        if state and authority.get("state", "").upper() != state.upper():
            continue
        if level and authority.get("jurisdiction_level") != level:
            continue
        if status and not authority_id and authority.get("status") != status:
            continue
        selected.append(authority)
    return sorted(selected, key=lambda item: (item.get("priority", 9999), item["authority_id"]))[:limit]


def update_authority_run_result(
    authority_id: str,
    record_ids: list[str],
    verification_statuses: dict[str, str] | None = None,
    error: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    path = _manifest_path(path)
    payload = load_authority_manifest(path)
    updated = False
    for authority in payload.get("authorities", []):
        if authority["authority_id"] != authority_id:
            continue
        authority["last_run_at"] = utc_now()
        authority["record_ids"] = sorted(set([*authority.get("record_ids", []), *record_ids]))
        if error:
            authority["status"] = AUTHORITY_STATUS_BLOCKED
            authority.setdefault("blockers", []).append(error)
        elif not record_ids:
            authority["status"] = AUTHORITY_STATUS_NEEDS_REVIEW
            authority.setdefault("blockers", []).append("No filing records extracted from official authority URLs.")
        else:
            authority["blockers"] = []
            statuses = list((verification_statuses or {}).values())
            if statuses and all(status == "verified_official" for status in statuses):
                authority["status"] = AUTHORITY_STATUS_VERIFIED
            else:
                authority["status"] = AUTHORITY_STATUS_NEEDS_REVIEW
        updated = True
        break
    save_authority_manifest(payload, path=path)
    return {"authority_id": authority_id, "updated": updated}
