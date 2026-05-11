"""Import regulatory JSON research into Supabase Postgres.

This is the bridge between the phase-one JSON store and the durable database
schema in supabase/migrations.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .paths import BASE_DIR, regulatory_data_dir


DATA_DIR = regulatory_data_dir()
FILINGS_PATH = DATA_DIR / "filings.json"
BATCHES_PATH = DATA_DIR / "research_batches.json"
JURISDICTIONS_PATH = DATA_DIR / "jurisdictions.json"
RUNS_DIR = DATA_DIR / "research_runs"
COMPETITOR_TARGETS_PATH = DATA_DIR / "competitor_research_targets.json"
SERVICE_CATALOG_PATH = DATA_DIR / "sosfiler_service_catalog.json"
STATE_AUTOMATION_MATRIX_PATH = DATA_DIR / "state_automation_matrix.json"

ENTITY_TYPE_DEFINITIONS = {
    "llc": {
        "display_name": "Limited Liability Company",
        "entity_family": "limited_liability_company",
        "profit_type": "for_profit",
        "aliases": ["LLC", "Limited Liability Company"],
        "description": "Domestic or foreign limited liability company.",
    },
    "pllc": {
        "display_name": "Professional Limited Liability Company",
        "entity_family": "limited_liability_company",
        "profit_type": "for_profit",
        "aliases": ["PLLC", "Professional LLC", "Professional Limited Liability Company"],
        "description": "Professional limited liability company where available.",
    },
    "corporation": {
        "display_name": "For-Profit Corporation",
        "entity_family": "corporation",
        "profit_type": "for_profit",
        "aliases": ["Corporation", "INC", "Inc.", "C Corporation", "For-Profit Corporation"],
        "description": "Domestic or foreign for-profit corporation.",
    },
    "nonprofit_corporation": {
        "display_name": "Nonprofit Corporation",
        "entity_family": "corporation",
        "profit_type": "nonprofit",
        "aliases": ["Nonprofit Corporation", "Non-Profit Corporation", "Nonprofit", "Non-Profit"],
        "description": "Domestic or foreign nonprofit corporation.",
    },
    "lp": {
        "display_name": "Limited Partnership",
        "entity_family": "partnership",
        "profit_type": "for_profit",
        "aliases": ["LP", "Limited Partnership"],
        "description": "Domestic or foreign limited partnership.",
    },
    "llp": {
        "display_name": "Limited Liability Partnership",
        "entity_family": "partnership",
        "profit_type": "for_profit",
        "aliases": ["LLP", "Limited Liability Partnership"],
        "description": "Domestic or foreign limited liability partnership.",
    },
    "other": {
        "display_name": "Other Entity or Filing",
        "entity_family": "other",
        "profit_type": "unknown",
        "aliases": ["Other"],
        "description": "Entity type is not yet normalized or the filing applies across entity types.",
    },
}

ENTITY_TYPE_ALIASES = {
    alias.lower(): code
    for code, definition in ENTITY_TYPE_DEFINITIONS.items()
    for alias in [definition["display_name"], *definition["aliases"]]
}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def as_text_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    return [str(value)]


def clean_ts(value: Any) -> str | None:
    return str(value) if value else None


def normalize_entity_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return ENTITY_TYPE_ALIASES.get(normalized, "other")


def normalized_entity_types(record: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label in as_text_list(record.get("entity_types")):
        code = normalize_entity_type(label)
        if code in seen:
            continue
        pairs.append((code, label))
        seen.add(code)
    if not pairs:
        pairs.append(("other", ""))
    return pairs


def batch_id_for_filing(filing_id: str) -> str:
    for marker in ["_state_filings_", "_local_filings_"]:
        if marker in filing_id:
            return filing_id.split(marker, 1)[0] + marker.rstrip("_")
    parts = filing_id.split("_")
    return "_".join(parts[:3]) if len(parts) >= 3 else filing_id


def load_payload() -> dict[str, Any]:
    filings = load_json(FILINGS_PATH, {"records": []}).get("records", [])
    batches = load_json(BATCHES_PATH, {"batches": []}).get("batches", [])
    jurisdiction_payload = load_json(JURISDICTIONS_PATH, {"jurisdictions": []})
    jurisdictions = (
        jurisdiction_payload.get("jurisdictions", [])
        if isinstance(jurisdiction_payload, dict)
        else jurisdiction_payload
    )
    runs = []
    if RUNS_DIR.exists():
        for path in sorted(RUNS_DIR.glob("*.json")):
            runs.append({**load_json(path, {}), "artifact_path": str(path.relative_to(BASE_DIR))})
    competitor_payload = load_json(COMPETITOR_TARGETS_PATH, {"targets": []})
    competitors = (
        competitor_payload.get("targets", [])
        if isinstance(competitor_payload, dict)
        else competitor_payload
    )
    service_payload = load_json(SERVICE_CATALOG_PATH, {"services": []})
    services = service_payload.get("services", []) if isinstance(service_payload, dict) else service_payload
    automation_payload = load_json(STATE_AUTOMATION_MATRIX_PATH, {"profiles": []})
    state_automation_profiles = (
        automation_payload.get("profiles", [])
        if isinstance(automation_payload, dict)
        else automation_payload
    )
    return {
        "filings": filings,
        "batches": batches,
        "jurisdictions": jurisdictions,
        "runs": runs,
        "competitors": competitors,
        "services": services,
        "state_automation_profiles": state_automation_profiles,
    }


def import_payload(database_url: str, payload: dict[str, Any]) -> dict[str, int]:
    import psycopg

    counts = {
        "jurisdictions": 0,
        "batches": 0,
        "filings": 0,
        "entity_types": 0,
        "filing_entity_types": 0,
        "fees": 0,
        "expedite_options": 0,
        "process_steps": 0,
        "source_citations": 0,
        "automation_recipes": 0,
        "reminder_rules": 0,
        "runs": 0,
        "competitors": 0,
        "services": 0,
        "service_prices": 0,
        "service_competitor_benchmarks": 0,
        "state_automation_profiles": 0,
        "review_tasks": 0,
    }
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for code, definition in ENTITY_TYPE_DEFINITIONS.items():
                cur.execute(
                    """
                    insert into public.regulatory_entity_types
                    (entity_type_code, display_name, entity_family, profit_type, aliases, description, raw, updated_at)
                    values (%s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (entity_type_code) do update set
                      display_name = excluded.display_name,
                      entity_family = excluded.entity_family,
                      profit_type = excluded.profit_type,
                      aliases = excluded.aliases,
                      description = excluded.description,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        code,
                        definition["display_name"],
                        definition["entity_family"],
                        definition["profit_type"],
                        definition["aliases"],
                        definition["description"],
                        json.dumps(definition),
                    ),
                )
                counts["entity_types"] += 1

            for item in payload["jurisdictions"]:
                cur.execute(
                    """
                    insert into public.regulatory_jurisdictions
                    (jurisdiction_id, name, state_code, jurisdiction_level, official_domains, raw, updated_at)
                    values (%s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (jurisdiction_id) do update set
                      name = excluded.name,
                      state_code = excluded.state_code,
                      jurisdiction_level = excluded.jurisdiction_level,
                      official_domains = excluded.official_domains,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        item.get("jurisdiction_id"),
                        item.get("name", ""),
                        item.get("state", ""),
                        item.get("level", "state"),
                        as_text_list(item.get("official_domains")),
                        json.dumps(item),
                    ),
                )
                counts["jurisdictions"] += 1

            for batch in payload["batches"]:
                cur.execute(
                    """
                    insert into public.regulatory_research_batches
                    (batch_id, jurisdiction_id, title, phase, scope, status, priority,
                     required_filings, discovery_queries, official_domains, notes, raw, updated_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (batch_id) do update set
                      jurisdiction_id = excluded.jurisdiction_id,
                      title = excluded.title,
                      phase = excluded.phase,
                      scope = excluded.scope,
                      status = excluded.status,
                      priority = excluded.priority,
                      required_filings = excluded.required_filings,
                      discovery_queries = excluded.discovery_queries,
                      official_domains = excluded.official_domains,
                      notes = excluded.notes,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        batch.get("batch_id"),
                        batch.get("jurisdiction_id"),
                        batch.get("title", ""),
                        batch.get("phase", ""),
                        batch.get("scope", ""),
                        batch.get("status", ""),
                        int(batch.get("priority", 9999)),
                        as_text_list(batch.get("required_filings")),
                        as_text_list(batch.get("discovery_queries")),
                        as_text_list(batch.get("official_domains")),
                        as_text_list(batch.get("notes")),
                        json.dumps(batch),
                    ),
                )
                counts["batches"] += 1

            for competitor in payload["competitors"]:
                cur.execute(
                    """
                    insert into public.regulatory_competitors
                    (competitor_code, name, website_url, notes, raw, updated_at)
                    values (%s, %s, %s, %s, %s::jsonb, now())
                    on conflict (competitor_code) do update set
                      name = excluded.name,
                      website_url = excluded.website_url,
                      notes = excluded.notes,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        competitor.get("competitor_code"),
                        competitor.get("name", ""),
                        competitor.get("website_url", ""),
                        ", ".join(as_text_list(competitor.get("focus"))),
                        json.dumps(competitor),
                    ),
                )
                counts["competitors"] += 1

            for service in payload["services"]:
                service_code = service.get("service_code")
                if not service_code:
                    continue
                cur.execute(
                    """
                    insert into public.sosfiler_services
                    (service_code, service_name, service_category, filing_category, entity_type_code,
                     lifecycle_stage, description, included, active, requires_official_filing_record,
                     renewal_or_recurring, raw, updated_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (service_code) do update set
                      service_name = excluded.service_name,
                      service_category = excluded.service_category,
                      filing_category = excluded.filing_category,
                      entity_type_code = excluded.entity_type_code,
                      lifecycle_stage = excluded.lifecycle_stage,
                      description = excluded.description,
                      included = excluded.included,
                      active = excluded.active,
                      requires_official_filing_record = excluded.requires_official_filing_record,
                      renewal_or_recurring = excluded.renewal_or_recurring,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        service_code,
                        service.get("service_name", ""),
                        service.get("service_category", ""),
                        service.get("filing_category"),
                        service.get("entity_type_code"),
                        service.get("lifecycle_stage", "any"),
                        service.get("description", ""),
                        bool(service.get("included", False)),
                        bool(service.get("active", True)),
                        bool(service.get("requires_official_filing_record", True)),
                        bool(service.get("renewal_or_recurring", False)),
                        json.dumps(service),
                    ),
                )
                counts["services"] += 1

                cur.execute("delete from public.sosfiler_service_prices where service_code = %s", (service_code,))
                cur.execute(
                    "delete from public.sosfiler_service_competitor_benchmarks where service_code = %s",
                    (service_code,),
                )

                price = service.get("price") or {}
                cur.execute(
                    """
                    insert into public.sosfiler_service_prices
                    (service_code, price_type, amount_cents, minimum_amount_cents, maximum_amount_cents,
                     currency, billing_interval, price_formula, excludes_government_fees, raw)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        service_code,
                        price.get("price_type", "flat"),
                        price.get("amount_cents"),
                        price.get("minimum_amount_cents"),
                        price.get("maximum_amount_cents"),
                        price.get("currency", "USD"),
                        price.get("billing_interval", "one_time"),
                        price.get("price_formula", ""),
                        bool(price.get("excludes_government_fees", True)),
                        json.dumps(price),
                    ),
                )
                counts["service_prices"] += 1

                for benchmark in service.get("competitor_benchmarks") or []:
                    cur.execute(
                        """
                        insert into public.sosfiler_service_competitor_benchmarks
                        (service_code, competitor_code, competitor_price_cents, competitor_price_text,
                         source_url, notes, raw)
                        values (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            service_code,
                            benchmark.get("competitor_code"),
                            benchmark.get("competitor_price_cents"),
                            benchmark.get("competitor_price_text", ""),
                            benchmark.get("source_url", ""),
                            benchmark.get("notes", ""),
                            json.dumps(benchmark),
                        ),
                    )
                    counts["service_competitor_benchmarks"] += 1

            for profile in payload["state_automation_profiles"]:
                cur.execute(
                    """
                    insert into public.state_automation_profiles
                    (state_code, jurisdiction_id, state_name, portal_urls, account_required, master_account_possible,
                     human_challenge_status, online_llc, online_corporation, online_nonprofit,
                     online_foreign_qualification, online_amendments, online_annual_report, payment_method,
                     status_check_method, document_retrieval_method, automation_lane, production_readiness,
                     blocker_summary, next_action, evidence_urls, last_certified_at, raw, updated_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (state_code) do update set
                      jurisdiction_id = excluded.jurisdiction_id,
                      state_name = excluded.state_name,
                      portal_urls = excluded.portal_urls,
                      account_required = excluded.account_required,
                      master_account_possible = excluded.master_account_possible,
                      human_challenge_status = excluded.human_challenge_status,
                      online_llc = excluded.online_llc,
                      online_corporation = excluded.online_corporation,
                      online_nonprofit = excluded.online_nonprofit,
                      online_foreign_qualification = excluded.online_foreign_qualification,
                      online_amendments = excluded.online_amendments,
                      online_annual_report = excluded.online_annual_report,
                      payment_method = excluded.payment_method,
                      status_check_method = excluded.status_check_method,
                      document_retrieval_method = excluded.document_retrieval_method,
                      automation_lane = excluded.automation_lane,
                      production_readiness = excluded.production_readiness,
                      blocker_summary = excluded.blocker_summary,
                      next_action = excluded.next_action,
                      evidence_urls = excluded.evidence_urls,
                      last_certified_at = excluded.last_certified_at,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        profile.get("state_code"),
                        profile.get("jurisdiction_id"),
                        profile.get("state_name", ""),
                        as_text_list(profile.get("portal_urls")),
                        profile.get("account_required", "unknown"),
                        profile.get("master_account_possible", "unknown"),
                        profile.get("human_challenge_status", "unknown"),
                        bool(profile.get("online_llc", False)),
                        bool(profile.get("online_corporation", False)),
                        bool(profile.get("online_nonprofit", False)),
                        bool(profile.get("online_foreign_qualification", False)),
                        bool(profile.get("online_amendments", False)),
                        bool(profile.get("online_annual_report", False)),
                        profile.get("payment_method", "unknown"),
                        profile.get("status_check_method", "unknown"),
                        profile.get("document_retrieval_method", "unknown"),
                        profile.get("automation_lane", "needs_certification"),
                        profile.get("production_readiness", "not_ready"),
                        profile.get("blocker_summary", ""),
                        profile.get("next_action", ""),
                        as_text_list(profile.get("evidence_urls")),
                        clean_ts(profile.get("last_certified_at")),
                        json.dumps(profile),
                    ),
                )
                counts["state_automation_profiles"] += 1

            for record in payload["filings"]:
                filing_id = record.get("filing_id", "")
                if not filing_id:
                    continue
                entity_type_pairs = normalized_entity_types(record)
                primary_entity_type_code = entity_type_pairs[0][0]
                cur.execute(
                    """
                    insert into public.regulatory_filing_records
                    (filing_id, batch_id, jurisdiction_id, jurisdiction_name, jurisdiction_level, authority,
                     filing_name, filing_category, domestic_or_foreign, primary_entity_type_code, entity_types, form_number, form_name,
                     portal_name, portal_url, submission_channels, required_customer_inputs, required_documents,
                     output_documents, annual_or_renewal_rule, deadline_rule, expected_turnaround,
                     price_confidence, verification_status, last_verified_at, notes, raw, updated_at)
                    values
                    (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                     %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (filing_id) do update set
                      batch_id = excluded.batch_id,
                      jurisdiction_id = excluded.jurisdiction_id,
                      jurisdiction_name = excluded.jurisdiction_name,
                      jurisdiction_level = excluded.jurisdiction_level,
                      authority = excluded.authority,
                      filing_name = excluded.filing_name,
                      filing_category = excluded.filing_category,
                      domestic_or_foreign = excluded.domestic_or_foreign,
                      primary_entity_type_code = excluded.primary_entity_type_code,
                      entity_types = excluded.entity_types,
                      form_number = excluded.form_number,
                      form_name = excluded.form_name,
                      portal_name = excluded.portal_name,
                      portal_url = excluded.portal_url,
                      submission_channels = excluded.submission_channels,
                      required_customer_inputs = excluded.required_customer_inputs,
                      required_documents = excluded.required_documents,
                      output_documents = excluded.output_documents,
                      annual_or_renewal_rule = excluded.annual_or_renewal_rule,
                      deadline_rule = excluded.deadline_rule,
                      expected_turnaround = excluded.expected_turnaround,
                      price_confidence = excluded.price_confidence,
                      verification_status = excluded.verification_status,
                      last_verified_at = excluded.last_verified_at,
                      notes = excluded.notes,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        filing_id,
                        batch_id_for_filing(filing_id),
                        record.get("jurisdiction_id"),
                        record.get("jurisdiction_name", ""),
                        record.get("jurisdiction_level", ""),
                        record.get("authority", ""),
                        record.get("filing_name", ""),
                        record.get("filing_category", ""),
                        record.get("domestic_or_foreign", "unknown"),
                        primary_entity_type_code,
                        as_text_list(record.get("entity_types")),
                        record.get("form_number", ""),
                        record.get("form_name", ""),
                        record.get("portal_name", ""),
                        record.get("portal_url", ""),
                        as_text_list(record.get("submission_channels")),
                        as_text_list(record.get("required_customer_inputs")),
                        as_text_list(record.get("required_documents")),
                        as_text_list(record.get("output_documents")),
                        record.get("annual_or_renewal_rule", ""),
                        record.get("deadline_rule", ""),
                        record.get("expected_turnaround", ""),
                        record.get("price_confidence", "unknown"),
                        record.get("verification_status", "discovered"),
                        clean_ts(record.get("last_verified_at")),
                        record.get("notes", ""),
                        json.dumps(record),
                    ),
                )
                cur.execute("delete from public.regulatory_filing_entity_types where filing_id = %s", (filing_id,))
                cur.execute("delete from public.regulatory_filing_fees where filing_id = %s", (filing_id,))
                cur.execute("delete from public.regulatory_expedite_options where filing_id = %s", (filing_id,))
                cur.execute("delete from public.regulatory_process_steps where filing_id = %s", (filing_id,))
                cur.execute("delete from public.regulatory_source_citations where filing_id = %s", (filing_id,))
                cur.execute("delete from public.regulatory_automation_recipes where filing_id = %s", (filing_id,))
                cur.execute("delete from public.regulatory_reminder_rules where filing_id = %s", (filing_id,))
                cur.execute("delete from public.regulatory_review_tasks where filing_id = %s", (filing_id,))

                for idx, (code, source_label) in enumerate(entity_type_pairs):
                    cur.execute(
                        """
                        insert into public.regulatory_filing_entity_types
                        (filing_id, entity_type_code, source_label, is_primary, raw)
                        values (%s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            filing_id,
                            code,
                            source_label,
                            idx == 0,
                            json.dumps({"source_label": source_label}),
                        ),
                    )
                    counts["filing_entity_types"] += 1

                fees = list(record.get("fee_components") or [])
                if record.get("processing_fee"):
                    fees.append({**record["processing_fee"], "fee_role": "processing"})
                for fee in fees:
                    cur.execute(
                        """
                        insert into public.regulatory_filing_fees
                        (filing_id, code, label, amount_cents, fee_type, formula, required, source_url, fee_role, raw)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            filing_id,
                            fee.get("code", ""),
                            fee.get("label", ""),
                            fee.get("amount_cents"),
                            fee.get("fee_type", "fixed"),
                            fee.get("formula", ""),
                            bool(fee.get("required", True)),
                            fee.get("source_url", ""),
                            fee.get("fee_role", "government"),
                            json.dumps(fee),
                        ),
                    )
                    counts["fees"] += 1

                for option in record.get("expedite_options") or []:
                    cur.execute(
                        """
                        insert into public.regulatory_expedite_options
                        (filing_id, label, fee_cents, processing_time, channel, customer_selectable, source_url, raw)
                        values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            filing_id,
                            option.get("label", ""),
                            option.get("fee_cents"),
                            option.get("processing_time", ""),
                            option.get("channel", "any"),
                            bool(option.get("customer_selectable", True)),
                            option.get("source_url", ""),
                            json.dumps(option),
                        ),
                    )
                    counts["expedite_options"] += 1

                for idx, step in enumerate(record.get("process_steps") or [], start=1):
                    step_number = step.get("step") or step.get("step_number") or idx
                    cur.execute(
                        """
                        insert into public.regulatory_process_steps
                        (filing_id, step_number, actor, title, description, portal_page,
                         required_inputs, evidence_output, status_after_step, raw)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            filing_id,
                            int(step_number),
                            step.get("actor", ""),
                            step.get("title") or step.get("action", ""),
                            step.get("description", ""),
                            step.get("portal_page") or step.get("portal_url", ""),
                            as_text_list(step.get("required_inputs")),
                            as_text_list(step.get("evidence_output")),
                            step.get("status_after_step", ""),
                            json.dumps(step),
                        ),
                    )
                    counts["process_steps"] += 1

                for citation in record.get("source_citations") or []:
                    cur.execute(
                        """
                        insert into public.regulatory_source_citations
                        (filing_id, url, title, authority, source_type, retrieved_at, content_hash, raw)
                        values (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            filing_id,
                            citation.get("url", ""),
                            citation.get("title", ""),
                            citation.get("authority", ""),
                            citation.get("source_type", "official"),
                            clean_ts(citation.get("retrieved_at")),
                            citation.get("content_hash", ""),
                            json.dumps(citation),
                        ),
                    )
                    counts["source_citations"] += 1

                automation = record.get("automation") or {}
                cur.execute(
                    """
                    insert into public.regulatory_automation_recipes
                    (filing_id, method, automatable, account_strategy, submission_strategy,
                     status_query_strategy, document_collection_strategy, blockers, selectors_or_api_notes, raw, updated_at)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now())
                    on conflict (filing_id) do update set
                      method = excluded.method,
                      automatable = excluded.automatable,
                      account_strategy = excluded.account_strategy,
                      submission_strategy = excluded.submission_strategy,
                      status_query_strategy = excluded.status_query_strategy,
                      document_collection_strategy = excluded.document_collection_strategy,
                      blockers = excluded.blockers,
                      selectors_or_api_notes = excluded.selectors_or_api_notes,
                      raw = excluded.raw,
                      updated_at = now()
                    """,
                    (
                        filing_id,
                        automation.get("method", "operator_assisted"),
                        bool(automation.get("automatable", False)),
                        automation.get("account_strategy", ""),
                        automation.get("submission_strategy", ""),
                        automation.get("status_query_strategy", ""),
                        automation.get("document_collection_strategy", ""),
                        as_text_list(automation.get("blockers")),
                        as_text_list(automation.get("selectors_or_api_notes")),
                        json.dumps(automation),
                    ),
                )
                counts["automation_recipes"] += 1

                for rule in record.get("reminder_rules") or []:
                    cur.execute(
                        """
                        insert into public.regulatory_reminder_rules
                        (filing_id, trigger, offset_days, channel, audience, message_key, raw)
                        values (%s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            filing_id,
                            rule.get("trigger", ""),
                            int(rule.get("offset_days", 0)),
                            rule.get("channel", "email"),
                            rule.get("audience", "customer"),
                            rule.get("message_key", ""),
                            json.dumps(rule),
                        ),
                    )
                    counts["reminder_rules"] += 1

                if record.get("verification_status") == "needs_operator_review":
                    cur.execute(
                        """
                        insert into public.regulatory_review_tasks
                        (filing_id, batch_id, status, reason)
                        values (%s, %s, 'open', 'needs_operator_review')
                        """,
                        (filing_id, batch_id_for_filing(filing_id)),
                    )
                    counts["review_tasks"] += 1

                counts["filings"] += 1

            for run in payload["runs"]:
                run_id = run.get("run_id")
                if not run_id:
                    continue
                cur.execute(
                    """
                    insert into public.regulatory_research_runs
                    (run_id, phase, batch_count, auto_verify, dry_run, started_at, finished_at, artifact_path, raw)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    on conflict (run_id) do update set
                      phase = excluded.phase,
                      batch_count = excluded.batch_count,
                      auto_verify = excluded.auto_verify,
                      dry_run = excluded.dry_run,
                      started_at = excluded.started_at,
                      finished_at = excluded.finished_at,
                      artifact_path = excluded.artifact_path,
                      raw = excluded.raw
                    """,
                    (
                        run_id,
                        run.get("phase", ""),
                        int(run.get("batch_count", 0) or 0),
                        bool(run.get("auto_verify", True)),
                        bool(run.get("dry_run", False)),
                        clean_ts(run.get("started_at")),
                        clean_ts(run.get("finished_at")),
                        run.get("artifact_path", ""),
                        json.dumps(run),
                    ),
                )
                counts["runs"] += 1
        conn.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Import regulatory JSON into Supabase Postgres.")
    parser.add_argument("--database-url", default=os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = load_payload()
    summary = {
        "jurisdictions": len(payload["jurisdictions"]),
        "batches": len(payload["batches"]),
        "filings": len(payload["filings"]),
        "runs": len(payload["runs"]),
        "competitors": len(payload["competitors"]),
        "services": len(payload["services"]),
        "state_automation_profiles": len(payload["state_automation_profiles"]),
    }
    if args.dry_run:
        print(json.dumps({"dry_run": True, **summary}, indent=2, sort_keys=True))
        return 0
    if not args.database_url:
        raise SystemExit("SUPABASE_DB_URL or DATABASE_URL is required")
    print(json.dumps(import_payload(args.database_url, payload), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
