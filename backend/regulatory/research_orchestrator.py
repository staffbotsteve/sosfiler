"""CLI for generating SOSFiler regulatory research batches and sample records."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import sys
from pathlib import Path
from typing import Any

from .automation_classifier import classify_automation
from .jurisdiction_registry import JURISDICTIONS_PATH, load_registry, write_state_registry
from .paths import BASE_DIR, display_path, regulatory_data_dir
from .pricing_engine import price_readiness
from .schemas import (
    EntityType,
    FeeComponent,
    FilingCategory,
    FilingRecord,
    JurisdictionLevel,
    PriceConfidence,
    ProcessStep,
    ResearchBatch,
    SourceCitation,
    VerificationStatus,
)
from .source_discovery_agent import build_discovery_queries


DATA_DIR = regulatory_data_dir()
BATCHES_PATH = DATA_DIR / "research_batches.json"
FILINGS_PATH = DATA_DIR / "filings.json"
PHASE_PLAN_PATH = DATA_DIR / "research_phase_plan.json"
BATCHES_LOCK_PATH = DATA_DIR / ".research_batches.lock"

TOP_TEN_STATES = ["TX", "CA", "FL", "DE", "NV", "AZ", "NY", "WY", "GA", "IL"]
TOP_TEN_PHASE = "top_ten"
REMAINING_PHASE = "remaining_40_plus_dc"
BATCH_STATUS_QUEUED = "queued"
BATCH_STATUS_ACTIVE = "active"
BATCH_STATUS_COMPLETE = "complete"
BATCH_STATUS_BLOCKED = "blocked"


@contextmanager
def batches_lock():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with BATCHES_LOCK_PATH.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

REQUIRED_STATE_FILINGS = [
    "LLC formation",
    "corporation formation",
    "nonprofit corporation formation",
    "LP/LLP/professional entity filings where offered",
    "foreign LLC authority",
    "foreign corporation authority",
    "foreign nonprofit authority",
    "name reservation or assumed-name prerequisite",
    "amendment/change filings",
    "registered agent and address changes",
    "annual or biennial statements/reports",
    "renewal filings and recurring compliance filings",
    "beneficial ownership information reports and update/correction filings where applicable",
    "USPTO trademark filing, maintenance, renewal, and assignment workflows",
    "USPTO patent filing, maintenance fee, and assignment workflows",
    "dissolution/withdrawal",
    "reinstatement",
    "copies and certificates",
]

REQUIRED_LOCAL_FILINGS = [
    "county DBA/FBN/assumed-name filings",
    "city and county general business licenses",
    "business tax certificates or local business tax receipts",
    "home occupation permits and zoning clearance dependencies",
    "renewal filings and recurring local compliance filings",
    "license renewals, amendments, closures, and late fees",
]


def build_batches() -> list[ResearchBatch]:
    batches: list[ResearchBatch] = []
    for jurisdiction in load_registry():
        state = jurisdiction["state"]
        jid = jurisdiction["jurisdiction_id"]
        domains = jurisdiction.get("official_domains", [])
        base_priority = int(jurisdiction.get("priority", 999)) * 10
        phase = TOP_TEN_PHASE if state in TOP_TEN_STATES else REMAINING_PHASE
        state_batch = ResearchBatch(
            batch_id=f"{state.lower()}_state_filings",
            jurisdiction_id=jid,
            title=f"{jurisdiction['name']} state filing map",
            priority=base_priority,
            scope="state",
            phase=phase,
            required_filings=REQUIRED_STATE_FILINGS,
            discovery_queries=[],
            official_domains=domains,
        )
        state_batch.discovery_queries = build_discovery_queries(state_batch)
        local_batch = ResearchBatch(
            batch_id=f"{state.lower()}_local_filings",
            jurisdiction_id=jid,
            title=f"{jurisdiction['name']} county and city local filing map",
            priority=base_priority + 1,
            scope="local",
            phase=phase,
            required_filings=REQUIRED_LOCAL_FILINGS,
            discovery_queries=[],
            official_domains=domains,
        )
        local_batch.discovery_queries = build_discovery_queries(local_batch)
        batches.extend([state_batch, local_batch])
    return sorted(batches, key=lambda item: (item.priority, item.batch_id))


def write_batches(path: Path = BATCHES_PATH) -> list[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "coverage_goal": "State filing maps for all states plus DC, then county/city local license and DBA maps.",
        "phase_order": [TOP_TEN_PHASE, REMAINING_PHASE],
        "phase_gate": "remaining_40_plus_dc starts after top_ten batches are complete or explicitly overridden.",
        "batches": [item.to_dict() for item in build_batches()],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload["batches"]


def load_batches() -> dict[str, Any]:
    if not BATCHES_PATH.exists():
        write_batches()
    return json.loads(BATCHES_PATH.read_text())


def save_batches(payload: dict[str, Any]) -> None:
    BATCHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    BATCHES_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def phase_counts(batches: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for batch in batches:
        phase = batch.get("phase", "unknown")
        status = batch.get("status", BATCH_STATUS_QUEUED)
        counts.setdefault(phase, {})
        counts[phase][status] = counts[phase].get(status, 0) + 1
    return counts


def next_batches(phase: str | None = None, status: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    batches = load_batches().get("batches", [])
    selected = [
        batch for batch in batches
        if (phase is None or batch.get("phase") == phase)
        and (status is None or batch.get("status") == status)
    ]
    return sorted(selected, key=lambda item: (item.get("priority", 9999), item["batch_id"]))[:limit]


def can_activate_phase(phase: str, batches: list[dict[str, Any]], force: bool = False) -> tuple[bool, list[str]]:
    if phase == TOP_TEN_PHASE or force:
        return True, []
    blockers = [
        batch["batch_id"]
        for batch in batches
        if batch.get("phase") == TOP_TEN_PHASE and batch.get("status") != BATCH_STATUS_COMPLETE
    ]
    return (not blockers), blockers


def activate_phase(phase: str, force: bool = False) -> dict[str, Any]:
    payload = load_batches()
    batches = payload.get("batches", [])
    allowed, blockers = can_activate_phase(phase, batches, force=force)
    if not allowed:
        return {
            "activated": False,
            "phase": phase,
            "blocked_by": blockers,
            "message": "Top-ten phase must complete before activating remaining_40_plus_dc.",
        }
    activated: list[str] = []
    for batch in batches:
        if batch.get("phase") == phase and batch.get("status") == BATCH_STATUS_QUEUED:
            batch["status"] = BATCH_STATUS_ACTIVE
            activated.append(batch["batch_id"])
    save_batches(payload)
    write_phase_plan(payload)
    return {
        "activated": True,
        "phase": phase,
        "activated_batches": activated,
        "counts": phase_counts(batches),
    }


def set_batch_status(batch_id: str, status: str, note: str = "") -> dict[str, Any]:
    payload = load_batches()
    updated = False
    for batch in payload.get("batches", []):
        if batch["batch_id"] == batch_id:
            batch["status"] = status
            if note:
                batch.setdefault("notes", []).append(note)
            updated = True
            break
    save_batches(payload)
    write_phase_plan(payload)
    return {"batch_id": batch_id, "updated": updated, "status": status}


def refresh_batches_preserving_status() -> dict[str, Any]:
    previous = load_batches().get("batches", []) if BATCHES_PATH.exists() else []
    previous_by_id = {batch["batch_id"]: batch for batch in previous}
    refreshed = []
    for batch in build_batches():
        payload = batch.to_dict()
        existing = previous_by_id.get(batch.batch_id)
        if existing:
            payload["status"] = existing.get("status", payload["status"])
            if existing.get("notes"):
                payload["notes"] = existing["notes"]
        refreshed.append(payload)
    output = {
        "version": 1,
        "coverage_goal": "State filing maps for all states plus DC, then county/city local license and DBA maps.",
        "phase_order": [TOP_TEN_PHASE, REMAINING_PHASE],
        "phase_gate": "remaining_40_plus_dc starts after top_ten batches are complete or explicitly overridden.",
        "batches": refreshed,
    }
    save_batches(output)
    write_phase_plan(output)
    return {
        "refreshed": len(refreshed),
        "preserved_statuses": len(previous_by_id),
        "counts": phase_counts(refreshed),
    }


def write_phase_plan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or load_batches()
    batches = payload.get("batches", [])
    plan = {
        "version": 1,
        "phase_order": [TOP_TEN_PHASE, REMAINING_PHASE],
        "rule": "Run and verify top_ten before remaining_40_plus_dc. Override only with explicit operator decision.",
        "counts": phase_counts(batches),
        "top_ten_states": TOP_TEN_STATES,
        "remaining_states": sorted({
            batch["jurisdiction_id"].split("_")[0].upper()
            for batch in batches
            if batch.get("phase") == REMAINING_PHASE
        }),
        "next_active_batches": next_batches(status=BATCH_STATUS_ACTIVE, limit=20),
        "next_queued_batches": next_batches(status=BATCH_STATUS_QUEUED, limit=20),
    }
    PHASE_PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PHASE_PLAN_PATH.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    return plan


def sample_texas_llc_record() -> FilingRecord:
    record = FilingRecord(
        filing_id="tx_llc_certificate_of_formation",
        jurisdiction_id="tx_state",
        jurisdiction_name="Texas",
        jurisdiction_level=JurisdictionLevel.STATE,
        filing_category=FilingCategory.FORMATION,
        entity_types=[EntityType.LLC],
        filing_name="Certificate of Formation - Limited Liability Company",
        authority="Texas Secretary of State",
        domestic_or_foreign="domestic",
        form_number="205",
        form_name="Certificate of Formation - Limited Liability Company",
        portal_name="SOSDirect",
        portal_url="https://direct.sos.state.tx.us/acct/acct-login.asp",
        submission_channels=["web_portal"],
        required_customer_inputs=[
            "entity_name",
            "registered_agent_name",
            "registered_office_address",
            "governing_authority",
            "organizer_name",
            "authorized_signature",
        ],
        output_documents=["certificate_of_filing", "filed_certificate_of_formation", "receipt"],
        expected_turnaround="Portal status must be checked; standard timing varies by SOSDirect review queue.",
        fee_components=[
            FeeComponent(
                code="state_filing_fee",
                label="Texas LLC certificate of formation state filing fee",
                amount_cents=30000,
                source_url="https://www.sos.state.tx.us/corp/forms_boc.shtml",
            )
        ],
        processing_fee=FeeComponent(
            code="credit_card_convenience_fee",
            label="SOSDirect credit card convenience fee",
            amount_cents=None,
            fee_type="percent",
            formula="2.7% of total fees when paid by credit card",
            source_url="https://direct.sos.state.tx.us/acct/acct-login.asp",
        ),
        process_steps=[
            ProcessStep(1, "sosfiler", "Perform entity name search", evidence_output=["name_search_result"]),
            ProcessStep(2, "sosfiler", "Enter Form 205 formation data in SOSDirect", status_after_step="drafted"),
            ProcessStep(3, "sosfiler", "Review filing and submit payment", evidence_output=["submission_receipt"], status_after_step="submitted"),
            ProcessStep(4, "sosfiler", "Poll SOSDirect/entity search for approval status", status_after_step="pending_review"),
            ProcessStep(5, "sosfiler", "Download approved filing package", evidence_output=["certificate_of_filing", "filed_certificate_of_formation"], status_after_step="approved"),
        ],
        source_citations=[
            SourceCitation(
                url="https://www.sos.state.tx.us/corp/forms_boc.shtml",
                title="Texas Business Organizations Code Forms",
                authority="Texas Secretary of State",
            )
        ],
        price_confidence=PriceConfidence.NEEDS_OPERATOR_QUOTE,
        verification_status=VerificationStatus.PORTAL_OBSERVED,
        notes="Processing fee is formula-based and must be computed during checkout/payment.",
    )
    record.automation = classify_automation(record)
    return record


def write_sample_filings(path: Path = FILINGS_PATH) -> list[dict[str, Any]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [sample_texas_llc_record()]
    payload = {
        "version": 1,
        "records": [record.to_dict() for record in records],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload["records"]


def report() -> dict[str, Any]:
    jurisdictions = load_registry()
    batches = json.loads(BATCHES_PATH.read_text()).get("batches", []) if BATCHES_PATH.exists() else []
    filings = json.loads(FILINGS_PATH.read_text()).get("records", []) if FILINGS_PATH.exists() else []
    return {
        "jurisdiction_count": len(jurisdictions),
        "batch_count": len(batches),
        "filing_record_count": len(filings),
        "phase_counts": phase_counts(batches),
        "next_active_batches": next_batches(status=BATCH_STATUS_ACTIVE, limit=10),
        "next_queued_batches": next_batches(status=BATCH_STATUS_QUEUED, limit=10),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build SOSFiler regulatory filing intelligence scaffold.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Write jurisdiction, batch, and sample filing data.")
    subparsers.add_parser("refresh-batches", help="Refresh research batches while preserving statuses and extracted filings.")
    subparsers.add_parser("report", help="Print scaffold coverage report.")
    subparsers.add_parser("price-check", help="Check pricing readiness for sample filings.")
    phase_parser = subparsers.add_parser("activate-phase", help="Activate a research phase.")
    phase_parser.add_argument("phase", choices=[TOP_TEN_PHASE, REMAINING_PHASE])
    phase_parser.add_argument("--force", action="store_true")
    next_parser = subparsers.add_parser("next-batches", help="List the next batches by phase/status.")
    next_parser.add_argument("--phase", choices=[TOP_TEN_PHASE, REMAINING_PHASE])
    next_parser.add_argument("--status", choices=[BATCH_STATUS_QUEUED, BATCH_STATUS_ACTIVE, BATCH_STATUS_COMPLETE, BATCH_STATUS_BLOCKED])
    next_parser.add_argument("--limit", type=int, default=10)
    status_parser = subparsers.add_parser("set-batch-status", help="Update one research batch status.")
    status_parser.add_argument("batch_id")
    status_parser.add_argument("status", choices=[BATCH_STATUS_QUEUED, BATCH_STATUS_ACTIVE, BATCH_STATUS_COMPLETE, BATCH_STATUS_BLOCKED])
    status_parser.add_argument("--note", default="")

    args = parser.parse_args(argv)
    if args.command == "init":
        jurisdictions = write_state_registry()
        batches = write_batches()
        records = write_sample_filings()
        write_phase_plan()
        print(json.dumps({
            "jurisdictions": len(jurisdictions),
            "research_batches": len(batches),
            "sample_filings": len(records),
            "paths": {
                "jurisdictions": display_path(JURISDICTIONS_PATH),
                "batches": display_path(BATCHES_PATH),
                "filings": display_path(FILINGS_PATH),
                "phase_plan": display_path(PHASE_PLAN_PATH),
            },
        }, indent=2, sort_keys=True))
        return 0
    if args.command == "report":
        print(json.dumps(report(), indent=2, sort_keys=True))
        return 0
    if args.command == "refresh-batches":
        print(json.dumps(refresh_batches_preserving_status(), indent=2, sort_keys=True))
        return 0
    if args.command == "price-check":
        records = [sample_texas_llc_record()]
        print(json.dumps([price_readiness(record) for record in records], indent=2, sort_keys=True))
        return 0
    if args.command == "activate-phase":
        print(json.dumps(activate_phase(args.phase, force=args.force), indent=2, sort_keys=True))
        return 0
    if args.command == "next-batches":
        print(json.dumps(next_batches(phase=args.phase, status=args.status, limit=args.limit), indent=2, sort_keys=True))
        return 0
    if args.command == "set-batch-status":
        print(json.dumps(set_batch_status(args.batch_id, args.status, note=args.note), indent=2, sort_keys=True))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
