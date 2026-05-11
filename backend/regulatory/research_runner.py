"""Run Firecrawl-powered regulatory research batches."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from .extractor import FILING_EXTRACTION_SCHEMA, batch_urls, build_extraction_prompt, records_from_firecrawl_result
from .firecrawl_client import FirecrawlError, FirecrawlExtractResult
from .html_local_extractor import records_from_local_html, records_from_local_page_payloads
from .authority_manifest import (
    AUTHORITY_STATUS_NOT_STARTED,
    authority_review_report,
    authority_summary,
    seed_authority_manifest,
    select_authorities,
    sync_authority_manifest_from_records,
    update_authority_run_result,
)
from .local_source_discovery import (
    discover_local_sources,
    local_extraction_urls,
    select_extraction_candidates,
    seeded_local_candidates,
    write_local_source_candidates,
)
from .paths import display_path
from .record_store import (
    backfill_reminder_rules,
    backfill_missing_output_documents,
    load_filing_records,
    prune_unsupported_reminder_rules,
    prune_superseded_local_review_records,
    replace_batch_filing_records,
    upsert_filing_records,
    write_run_artifact,
    write_source_snapshot,
)
from .research_orchestrator import (
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_BLOCKED,
    BATCH_STATUS_COMPLETE,
    BATCH_STATUS_QUEUED,
    REMAINING_PHASE,
    TOP_TEN_PHASE,
    activate_phase,
    batches_lock,
    load_batches,
    next_batches,
    phase_counts,
    save_batches,
    write_phase_plan,
)
from .research_providers import ResearchProvider, active_discovery_providers, active_extraction_provider, provider_statuses
from .schemas import FilingRecord, VerificationStatus, utc_now


AUTO_VERIFY_ENV = "REGULATORY_RESEARCH_AUTO_VERIFY"
LOCAL_SOURCE_LIMIT_ENV = "REGULATORY_LOCAL_SOURCE_LIMIT"


def auto_verify_enabled() -> bool:
    return os.getenv(AUTO_VERIFY_ENV, "true").strip().lower() in {"1", "true", "yes", "on"}


def local_source_limit() -> int:
    value = os.getenv(LOCAL_SOURCE_LIMIT_ENV, "8").strip()
    try:
        return int(value)
    except ValueError:
        return 8


def _selected_batches(
    phase: str | None,
    limit: int | None,
    all_batches: bool,
    batch_id: str | None = None,
) -> list[dict[str, Any]]:
    if batch_id:
        batches = load_batches().get("batches", [])
        return [batch for batch in batches if batch["batch_id"] == batch_id and batch.get("status") == BATCH_STATUS_ACTIVE]
    if all_batches:
        limit = 1000
    return next_batches(phase=phase, status=BATCH_STATUS_ACTIVE, limit=limit or 1)


def _update_batch_status(batch_id: str, status: str, note: str = "") -> None:
    with batches_lock():
        payload = load_batches()
        for batch in payload.get("batches", []):
            if batch["batch_id"] == batch_id:
                batch["status"] = status
                if note:
                    batch.setdefault("notes", []).append(f"{utc_now()}: {note}")
                break
        save_batches(payload)
        write_phase_plan(payload)


def _batch_completion_status(
    records: list[FilingRecord],
    batch: dict[str, Any] | None = None,
    local_candidate_count: int = 0,
) -> tuple[str, str]:
    if not records:
        if batch and batch.get("scope") == "local" and local_candidate_count:
            return BATCH_STATUS_ACTIVE, "No filing records extracted from this local slice; continue with narrower candidates."
        return BATCH_STATUS_BLOCKED, "No filing records extracted."
    if batch and batch.get("scope") == "local":
        return BATCH_STATUS_ACTIVE, "Local slice extracted records; keep local coverage active until explicitly closed."
    if all(record.verification_status == VerificationStatus.VERIFIED_OFFICIAL for record in records):
        return BATCH_STATUS_COMPLETE, "All extracted records passed automatic verification gates."
    return BATCH_STATUS_ACTIVE, "Records extracted but at least one requires operator review."


def run_batch(
    batch: dict[str, Any],
    provider: ResearchProvider,
    dry_run: bool = False,
    auto_verify: bool = True,
    fixture_payload: dict[str, Any] | None = None,
    discover_local: bool = True,
    max_local_sources: int | None = None,
    local_source_offset: int = 0,
    local_source_urls: list[str] | None = None,
) -> dict[str, Any]:
    max_local_sources = local_source_limit() if max_local_sources is None else max_local_sources
    local_discovery_artifact: dict[str, Any] | None = None
    local_candidate_count = 0
    if batch.get("scope") == "local" and local_source_urls:
        urls = [url.strip().rstrip("/") for url in local_source_urls if url.strip()]
        local_candidate_count = len(urls)
        batch = {**batch, "extraction_urls": urls}
        local_discovery_artifact = {
            "batch_id": batch["batch_id"],
            "captured_at": utc_now(),
            "enable_web_discovery": False,
            "candidate_count": local_candidate_count,
            "source": "targeted_cli_urls",
            "candidates": [{"official_url": url, "source_kind": "targeted_cli_url"} for url in urls],
        }
    elif batch.get("scope") == "local" and discover_local and dry_run:
        candidates = select_extraction_candidates(seeded_local_candidates(batch))
        if local_source_offset > 0:
            candidates = candidates[local_source_offset:]
        if max_local_sources and max_local_sources > 0:
            candidates = candidates[:max_local_sources]
        local_candidate_count = len(candidates)
        if candidates:
            batch = {**batch, "extraction_urls": local_extraction_urls(batch, candidates)}
    elif batch.get("scope") == "local" and discover_local and fixture_payload is None:
        candidates, local_discovery_artifact = discover_local_sources(
            batch,
            providers=active_discovery_providers(),
            max_candidates=max_local_sources,
            candidate_offset=local_source_offset,
        )
        local_candidate_count = len(candidates)
        write_local_source_candidates(batch["batch_id"], local_discovery_artifact)
        if candidates:
            batch = {**batch, "extraction_urls": local_extraction_urls(batch, candidates)}
    prompt = build_extraction_prompt(batch)
    urls = batch_urls(batch)
    result_payload: dict[str, Any] | None = None
    error = ""
    records: list[FilingRecord] = []

    if dry_run:
        return {
            "batch_id": batch["batch_id"],
            "phase": batch.get("phase"),
            "dry_run": True,
            "urls": urls,
            "prompt_preview": prompt[:1000],
            "would_write": [],
            "status_after_run": batch.get("status"),
            "local_candidate_count": local_candidate_count,
            "local_source_offset": local_source_offset,
            "local_source_urls": local_source_urls or [],
        }

    try:
        if fixture_payload is not None:
            result = FirecrawlExtractResult.from_payload(fixture_payload)
        else:
            result = provider.extract(
                urls=urls,
                prompt=prompt,
                schema=FILING_EXTRACTION_SCHEMA,
                enable_web_search=False,
                show_sources=True,
            )
        result_payload = result.to_dict()
        snapshot_path = write_source_snapshot(batch["batch_id"], result_payload)
        records = records_from_firecrawl_result(batch, result, auto_verify=auto_verify)
        if not records and batch.get("scope") == "local" and urls:
            records = records_from_local_html(batch, urls)
        if not records and batch.get("scope") == "local" and urls and hasattr(provider, "client"):
            pages: list[dict[str, str]] = []
            for url in urls:
                try:
                    scrape_payload = provider.client.scrape(url)  # type: ignore[attr-defined]
                except Exception:
                    continue
                data = scrape_payload.get("data") if isinstance(scrape_payload, dict) else {}
                if not isinstance(data, dict):
                    data = scrape_payload if isinstance(scrape_payload, dict) else {}
                metadata = data.get("metadata") if isinstance(data, dict) else {}
                pages.append({
                    "url": url,
                    "title": (metadata or {}).get("title", "") if isinstance(metadata, dict) else "",
                    "markdown": data.get("markdown", "") if isinstance(data, dict) else "",
                    "html": data.get("html", "") if isinstance(data, dict) else "",
                    "content": data.get("content", "") if isinstance(data, dict) else "",
                })
            records = records_from_local_page_payloads(batch, pages)
        if records and batch.get("scope") == "local":
            store_result = upsert_filing_records(records)
        elif records:
            store_result = replace_batch_filing_records(batch["batch_id"], records)
        else:
            store_result = {"upserted": [], "record_count": len(load_filing_records())}
        status, note = _batch_completion_status(records, batch=batch, local_candidate_count=local_candidate_count)
        _update_batch_status(batch["batch_id"], status, note)
        return {
            "batch_id": batch["batch_id"],
            "phase": batch.get("phase"),
            "dry_run": False,
            "snapshot_path": display_path(snapshot_path),
            "local_candidate_count": local_candidate_count,
            "local_source_offset": local_source_offset,
            "local_source_urls": local_source_urls or [],
            "local_discovery": local_discovery_artifact,
            "records": [record.filing_id for record in records],
            "verification_statuses": {record.filing_id: record.verification_status.value for record in records},
            "store": store_result,
            "status_after_run": status,
        }
    except FirecrawlError as exc:
        error = str(exc)
        _update_batch_status(batch["batch_id"], BATCH_STATUS_BLOCKED, error)
        return {
            "batch_id": batch["batch_id"],
            "phase": batch.get("phase"),
            "dry_run": False,
            "error": error,
            "status_after_run": BATCH_STATUS_BLOCKED,
            "raw": result_payload,
        }


def run_research(
    phase: str | None = TOP_TEN_PHASE,
    limit: int | None = 1,
    all_batches: bool = False,
    dry_run: bool = False,
    batch_id: str | None = None,
    fixture_payload: dict[str, Any] | None = None,
    discover_local: bool = True,
    max_local_sources: int | None = None,
    local_source_offset: int = 0,
    local_source_urls: list[str] | None = None,
) -> dict[str, Any]:
    batches = _selected_batches(phase=phase, limit=limit, all_batches=all_batches, batch_id=batch_id)
    provider = active_extraction_provider()
    run = {
        "run_id": str(uuid.uuid4()),
        "started_at": utc_now(),
        "phase": phase,
        "dry_run": dry_run,
        "auto_verify": auto_verify_enabled(),
        "batch_count": len(batches),
        "results": [],
    }
    if not dry_run and fixture_payload is None and not provider.configured:
        run["error"] = f"{provider.name} provider is not configured."
        run["finished_at"] = utc_now()
        return run

    for batch in batches:
        run["results"].append(
            run_batch(
                batch,
                provider=provider,
                dry_run=dry_run,
                auto_verify=auto_verify_enabled(),
                fixture_payload=fixture_payload,
                discover_local=discover_local,
                max_local_sources=max_local_sources,
                local_source_offset=local_source_offset,
                local_source_urls=local_source_urls,
            )
        )
    run["finished_at"] = utc_now()
    if not dry_run:
        path = write_run_artifact(run)
        run["run_artifact"] = display_path(path)
    return run


def status_report() -> dict[str, Any]:
    batches = load_batches().get("batches", [])
    records = load_filing_records()
    by_verification: dict[str, int] = {}
    for record in records:
        status = record.get("verification_status", "unknown")
        by_verification[status] = by_verification.get(status, 0) + 1
    return {
        "batch_counts": phase_counts(batches),
        "active_batches": [batch["batch_id"] for batch in next_batches(status=BATCH_STATUS_ACTIVE, limit=50)],
        "queued_remaining_count": phase_counts(batches).get(REMAINING_PHASE, {}).get(BATCH_STATUS_QUEUED, 0),
        "filing_record_count": len(records),
        "filing_records_by_verification": by_verification,
        "providers": provider_statuses(),
        "firecrawl_configured": active_extraction_provider().configured,
    }


def _batch_by_id(batch_id: str) -> dict[str, Any] | None:
    for batch in load_batches().get("batches", []):
        if batch.get("batch_id") == batch_id:
            return batch
    return None


def run_authorities(
    state: str | None = "TX",
    level: str | None = None,
    status: str | None = AUTHORITY_STATUS_NOT_STARTED,
    limit: int = 1,
    dry_run: bool = False,
    authority_id: str | None = None,
) -> dict[str, Any]:
    """Run research from official authority manifest entries only."""

    authorities = select_authorities(state=state, level=level, status=status, limit=limit, authority_id=authority_id)
    provider = active_extraction_provider()
    run = {
        "run_id": str(uuid.uuid4()),
        "started_at": utc_now(),
        "mode": "authority_manifest",
        "state": state,
        "level": level,
        "status": status,
        "authority_id": authority_id,
        "dry_run": dry_run,
        "authority_count": len(authorities),
        "results": [],
    }
    if not dry_run and not provider.configured:
        run["error"] = f"{provider.name} provider is not configured."
        run["finished_at"] = utc_now()
        return run

    for authority in authorities:
        batch = _batch_by_id(authority["batch_id"])
        if not batch:
            result = {
                "authority_id": authority["authority_id"],
                "error": f"Batch {authority['batch_id']} was not found.",
            }
            run["results"].append(result)
            if not dry_run:
                update_authority_run_result(authority["authority_id"], [], error=result["error"])
            continue
        if authority.get("jurisdiction_level") == "state" and not dry_run:
            result = {
                "authority_id": authority["authority_id"],
                "batch_id": authority["batch_id"],
                "status_after_run": "blocked",
                "error": "State authority manifests are inventoried, but live state runs must use the state batch path until append-safe state extraction is enabled.",
                "source_urls": authority.get("source_urls", []),
            }
            update_authority_run_result(authority["authority_id"], [], error=result["error"])
            run["results"].append(result)
            continue

        batch = {**batch, "authority_context": authority}
        result = run_batch(
            batch,
            provider=provider,
            dry_run=dry_run,
            auto_verify=auto_verify_enabled(),
            discover_local=False,
            local_source_urls=authority.get("source_urls", []),
        )
        result["authority_id"] = authority["authority_id"]
        result["jurisdiction_name"] = authority.get("jurisdiction_name")
        result["jurisdiction_level"] = authority.get("jurisdiction_level")
        if not dry_run:
            update_authority_run_result(
                authority["authority_id"],
                result.get("records", []),
                verification_statuses=result.get("verification_statuses", {}),
                error=result.get("error", ""),
            )
        run["results"].append(result)

    sync_authority_manifest_from_records()
    run["finished_at"] = utc_now()
    if not dry_run:
        path = write_run_artifact(run)
        run["run_artifact"] = display_path(path)
    return run


def validate_records() -> dict[str, Any]:
    records = load_filing_records()
    invalid: list[dict[str, Any]] = []
    for record in records:
        issues: list[str] = []
        if not record.get("source_citations"):
            issues.append("missing_source_citations")
        if not record.get("process_steps"):
            issues.append("missing_process_steps")
        if not record.get("output_documents"):
            issues.append("missing_output_documents")
        if not record.get("automation"):
            issues.append("missing_automation")
        if record.get("filing_category") in {
            "annual_obligation",
            "renewal",
            "beneficial_ownership_report",
            "trademark",
            "patent",
        } and not record.get("reminder_rules"):
            issues.append("missing_reminder_rules")
        if record.get("verification_status") == VerificationStatus.VERIFIED_OFFICIAL.value:
            if record.get("price_confidence") not in {"verified", "formula_verified"}:
                issues.append("verified_record_without_price_confidence")
        if issues:
            invalid.append({"filing_id": record.get("filing_id"), "issues": issues})
    return {
        "record_count": len(records),
        "invalid_count": len(invalid),
        "invalid": invalid,
    }


def unlock_next_phase() -> dict[str, Any]:
    return activate_phase(REMAINING_PHASE)


def _load_fixture(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text())
    return payload.get("payload", payload)


def _split_urls(values: list[str] | None) -> list[str]:
    urls: list[str] = []
    for value in values or []:
        for item in value.split(","):
            item = item.strip()
            if item:
                urls.append(item)
    return urls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Firecrawl-powered SOSFiler regulatory research.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run active research batches.")
    run_parser.add_argument("--phase", choices=[TOP_TEN_PHASE, REMAINING_PHASE], default=TOP_TEN_PHASE)
    run_parser.add_argument("--limit", type=int, default=1)
    run_parser.add_argument("--all", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--batch-id")
    run_parser.add_argument("--fixture")
    run_parser.add_argument("--skip-local-discovery", action="store_true")
    run_parser.add_argument("--local-source-limit", type=int)
    run_parser.add_argument("--local-source-offset", type=int, default=0)
    run_parser.add_argument("--local-source-url", action="append", default=[])

    subparsers.add_parser("status", help="Show research runner status.")
    subparsers.add_parser("unlock-next-phase", help="Activate remaining_40_plus_dc after top-ten completion.")
    subparsers.add_parser("validate-records", help="Validate extracted filing records.")
    subparsers.add_parser("backfill-reminders", help="Add default reminder rules to existing recurring records.")
    subparsers.add_parser("backfill-output-documents", help="Add generic output document labels where extraction omitted them.")
    subparsers.add_parser("prune-reminders", help="Remove default reminder rules from non-recurring records.")
    subparsers.add_parser("prune-superseded-local-records", help="Remove stale local review records superseded by verified authority records.")
    seed_parser = subparsers.add_parser("seed-authority-manifest", help="Seed the official authority manifest.")
    seed_parser.add_argument("--state", default="TX")
    seed_parser.add_argument("--force", action="store_true")

    authority_status_parser = subparsers.add_parser("authority-status", help="Show official authority manifest status.")
    authority_status_parser.add_argument("--state", default="TX")

    authority_review_parser = subparsers.add_parser("authority-review", help="Show authorities that need narrower URLs or operator review.")
    authority_review_parser.add_argument("--state", default="TX")

    authority_run_parser = subparsers.add_parser("run-authorities", help="Run official-authority manifest research.")
    authority_run_parser.add_argument("--state", default="TX")
    authority_run_parser.add_argument("--level", choices=["state", "county", "city"])
    authority_run_parser.add_argument("--status", default=AUTHORITY_STATUS_NOT_STARTED)
    authority_run_parser.add_argument("--limit", type=int, default=1)
    authority_run_parser.add_argument("--dry-run", action="store_true")
    authority_run_parser.add_argument("--authority-id")

    args = parser.parse_args(argv)
    if args.command == "run":
        print(json.dumps(
            run_research(
                phase=args.phase,
                limit=args.limit,
                all_batches=args.all,
                dry_run=args.dry_run,
                batch_id=args.batch_id,
                fixture_payload=_load_fixture(args.fixture),
                discover_local=not args.skip_local_discovery,
                max_local_sources=args.local_source_limit,
                local_source_offset=args.local_source_offset,
                local_source_urls=_split_urls(args.local_source_url),
            ),
            indent=2,
            sort_keys=True,
        ))
        return 0
    if args.command == "status":
        print(json.dumps(status_report(), indent=2, sort_keys=True))
        return 0
    if args.command == "unlock-next-phase":
        print(json.dumps(unlock_next_phase(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate-records":
        print(json.dumps(validate_records(), indent=2, sort_keys=True))
        return 0
    if args.command == "backfill-reminders":
        print(json.dumps(backfill_reminder_rules(), indent=2, sort_keys=True))
        return 0
    if args.command == "backfill-output-documents":
        print(json.dumps(backfill_missing_output_documents(), indent=2, sort_keys=True))
        return 0
    if args.command == "prune-reminders":
        print(json.dumps(prune_unsupported_reminder_rules(), indent=2, sort_keys=True))
        return 0
    if args.command == "prune-superseded-local-records":
        print(json.dumps(prune_superseded_local_review_records(), indent=2, sort_keys=True))
        return 0
    if args.command == "seed-authority-manifest":
        print(json.dumps(seed_authority_manifest(state=args.state, force=args.force), indent=2, sort_keys=True))
        return 0
    if args.command == "authority-status":
        print(json.dumps(authority_summary(state=args.state), indent=2, sort_keys=True))
        return 0
    if args.command == "authority-review":
        print(json.dumps(authority_review_report(state=args.state), indent=2, sort_keys=True))
        return 0
    if args.command == "run-authorities":
        print(json.dumps(
            run_authorities(
                state=args.state,
                level=args.level,
                status=args.status,
                limit=args.limit,
                dry_run=args.dry_run,
                authority_id=args.authority_id,
            ),
            indent=2,
            sort_keys=True,
        ))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
