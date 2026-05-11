"""Convert Firecrawl extraction output into SOSFiler filing records."""

from __future__ import annotations

import re
from typing import Any

from .automation_classifier import classify_automation
from .firecrawl_client import FirecrawlExtractResult, FirecrawlSource
from .schemas import (
    EntityType,
    FeeComponent,
    FilingCategory,
    FilingRecord,
    JurisdictionLevel,
    PriceConfidence,
    ProcessStep,
    ReminderRule,
    SourceCitation,
    VerificationStatus,
    utc_now,
)


FILING_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "filing_records": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "filing_id": {"type": "string"},
                    "filing_category": {"type": "string"},
                    "entity_types": {"type": "array", "items": {"type": "string"}},
                    "filing_name": {"type": "string"},
                    "authority": {"type": "string"},
                    "domestic_or_foreign": {"type": "string"},
                    "form_number": {"type": "string"},
                    "form_name": {"type": "string"},
                    "portal_name": {"type": "string"},
                    "portal_url": {"type": "string"},
                    "submission_channels": {"type": "array", "items": {"type": "string"}},
                    "required_customer_inputs": {"type": "array", "items": {"type": "string"}},
                    "required_documents": {"type": "array", "items": {"type": "string"}},
                    "output_documents": {"type": "array", "items": {"type": "string"}},
                    "annual_or_renewal_rule": {"type": "string"},
                    "deadline_rule": {"type": "string"},
                    "reminder_rules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "trigger": {"type": "string"},
                                "offset_days": {"type": "integer"},
                                "channel": {"type": "string"},
                                "audience": {"type": "string"},
                                "message_key": {"type": "string"},
                            },
                        },
                    },
                    "expected_turnaround": {"type": "string"},
                    "fee_components": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "label": {"type": "string"},
                                "amount_cents": {"type": ["integer", "null"]},
                                "fee_type": {"type": "string"},
                                "formula": {"type": "string"},
                                "required": {"type": "boolean"},
                                "source_url": {"type": "string"},
                            },
                        },
                    },
                    "processing_fee": {
                        "type": ["object", "null"],
                        "properties": {
                            "code": {"type": "string"},
                            "label": {"type": "string"},
                            "amount_cents": {"type": ["integer", "null"]},
                            "fee_type": {"type": "string"},
                            "formula": {"type": "string"},
                            "required": {"type": "boolean"},
                            "source_url": {"type": "string"},
                        },
                    },
                    "process_steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "integer"},
                                "actor": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "portal_page": {"type": "string"},
                                "required_inputs": {"type": "array", "items": {"type": "string"}},
                                "evidence_output": {"type": "array", "items": {"type": "string"}},
                                "status_after_step": {"type": "string"},
                            },
                        },
                    },
                    "source_citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "title": {"type": "string"},
                                "authority": {"type": "string"},
                                "source_type": {"type": "string"},
                            },
                        },
                    },
                    "notes": {"type": "string"},
                },
                "required": ["filing_id", "filing_category", "filing_name", "authority", "source_citations"],
            },
        }
    },
    "required": ["filing_records"],
}


def build_extraction_prompt(batch: dict[str, Any]) -> str:
    required = ", ".join(batch.get("required_filings", []))
    queries = "\n".join(f"- {query}" for query in batch.get("discovery_queries", []))
    authority_context = batch.get("authority_context") or {}
    authority_instruction = ""
    if authority_context:
        authority_instruction = (
            " This is an authority-manifest run. Attribute extracted records to "
            f"{authority_context.get('authority_name')} for {authority_context.get('jurisdiction_name')} "
            f"({authority_context.get('jurisdiction_level')}). Do not emit generic authority names "
            "such as County Clerk or Development Services when the manifest provides the exact authority."
        )
    local_instruction = ""
    if batch.get("scope") == "local":
        local_instruction = (
            " For local batches, create separate records per county or city authority when the "
            "source shows jurisdiction-specific fees, turnaround, forms, or portals. Do not "
            "collapse a state into one generic local filing unless the source is only a statewide "
            "directory or summary. Capture the specific county/city name in authority and notes."
        )
    return (
        "Extract SOSFiler regulatory filing records from official government sources only. "
        "Do not use competitor sites as authority. For each filing, capture official fees, "
        "processing or card fees, expected turnaround, ordered process steps, submission channel, "
        "status-check method, document outputs, and source citations. If a fee is variable, "
        "store the official formula and set amount_cents to null. Store fixed amount_cents in cents, "
        "not dollars: $300 must be 30000, $35 must be 3500, and $5 must be 500. "
        "When automation requires a verified government portal account, trusted-access enrollment, "
        "or operator identity verification, mention that only in automation blockers or notes. "
        "If a portal supports an authorized filer, professional, or Trusted Access for Cyber-style "
        "verified account, capture that as the account strategy and explain whether SOSFiler can "
        "submit as an authorized agent for the customer. "
        "For annual reports, renewals, BOI reports, USPTO trademark filings, USPTO patent filings, "
        "and local license renewals, capture deadline_rule and reminder_rules suitable for customer "
        "alerts. Reminder offsets should normally include 90, 60, 30, and 7 days before due date "
        "when the deadline is recurring. "
        f"Batch: {batch.get('title')}. Required filing coverage: {required}.\n"
        f"Discovery queries:\n{queries}{authority_instruction}{local_instruction}"
    )


def batch_urls(batch: dict[str, Any]) -> list[str]:
    if batch.get("extraction_urls"):
        return sorted(set(str(url) for url in batch["extraction_urls"] if url))
    urls: list[str] = []
    for domain in batch.get("official_domains", []):
        if domain.startswith("http://") or domain.startswith("https://"):
            root = domain.rstrip("/")
        else:
            root = f"https://{domain}"
        urls.append(f"{root}/*")
    return urls


def _enum_value(enum_cls: Any, value: str, default: Any) -> Any:
    normalized = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    for item in enum_cls:
        if item.value.lower() == normalized or item.name.lower() == normalized:
            return item
    return default


def _entity_type(value: str) -> EntityType:
    cleaned = (value or "").strip().lower()
    aliases = {
        "llc": EntityType.LLC,
        "limited liability company": EntityType.LLC,
        "inc": EntityType.CORPORATION,
        "corporation": EntityType.CORPORATION,
        "corp": EntityType.CORPORATION,
        "nonprofit": EntityType.NONPROFIT_CORPORATION,
        "nonprofit corporation": EntityType.NONPROFIT_CORPORATION,
        "llp": EntityType.LLP,
        "lp": EntityType.LP,
        "pllc": EntityType.PLLC,
    }
    return aliases.get(cleaned, EntityType.OTHER)


def _filing_category(value: str, payload: dict[str, Any]) -> FilingCategory:
    cleaned = " ".join(
        [
            value or "",
            payload.get("filing_name") or "",
            payload.get("form_name") or "",
        ]
    ).strip().lower()
    aliases = [
        (("foreign", "registration"), FilingCategory.FOREIGN_AUTHORITY),
        (("foreign", "authority"), FilingCategory.FOREIGN_AUTHORITY),
        (("foreign", "qualification"), FilingCategory.FOREIGN_AUTHORITY),
        (("certificate of formation",), FilingCategory.FORMATION),
        (("articles of incorporation",), FilingCategory.FORMATION),
        (("articles of organization",), FilingCategory.FORMATION),
        (("formation",), FilingCategory.FORMATION),
        (("annual",), FilingCategory.ANNUAL_OBLIGATION),
        (("periodic report",), FilingCategory.ANNUAL_OBLIGATION),
        (("franchise tax",), FilingCategory.ANNUAL_OBLIGATION),
        (("public information report",), FilingCategory.ANNUAL_OBLIGATION),
        (("renewal",), FilingCategory.RENEWAL),
        (("beneficial ownership",), FilingCategory.BENEFICIAL_OWNERSHIP_REPORT),
        (("boi",), FilingCategory.BENEFICIAL_OWNERSHIP_REPORT),
        (("trademark",), FilingCategory.TRADEMARK),
        (("patent",), FilingCategory.PATENT),
        (("amendment",), FilingCategory.AMENDMENT),
        (("registered agent",), FilingCategory.REGISTERED_AGENT_CHANGE),
        (("address change",), FilingCategory.ADDRESS_CHANGE),
        (("dissolution",), FilingCategory.DISSOLUTION),
        (("withdrawal",), FilingCategory.DISSOLUTION),
        (("reinstatement",), FilingCategory.REINSTATEMENT),
        (("dba",), FilingCategory.DBA_FBN),
        (("assumed name",), FilingCategory.DBA_FBN),
        (("fictitious business",), FilingCategory.DBA_FBN),
        (("business license",), FilingCategory.BUSINESS_LICENSE),
        (("tax registration",), FilingCategory.TAX_REGISTRATION),
        (("certificate of status",), FilingCategory.CERTIFICATE_COPY),
        (("certified copy",), FilingCategory.CERTIFICATE_COPY),
    ]
    for needles, category in aliases:
        if all(needle in cleaned for needle in needles):
            return category
    return _enum_value(FilingCategory, value, FilingCategory.OTHER_LICENSE)


def _fee_component(payload: dict[str, Any], default_code: str) -> FeeComponent:
    return FeeComponent(
        code=payload.get("code") or default_code,
        label=payload.get("label") or default_code.replace("_", " ").title(),
        amount_cents=payload.get("amount_cents"),
        fee_type=payload.get("fee_type") or ("formula" if payload.get("formula") else "fixed"),
        formula=payload.get("formula") or "",
        required=payload.get("required", True),
        source_url=payload.get("source_url") or "",
    )


def _source_citation(payload: dict[str, Any], fallback_authority: str) -> SourceCitation:
    return SourceCitation(
        url=payload.get("url") or "",
        title=payload.get("title") or "",
        authority=payload.get("authority") or fallback_authority,
        source_type=payload.get("source_type") or "official",
    )


def _process_step(payload: dict[str, Any], index: int) -> ProcessStep:
    return ProcessStep(
        step=int(payload.get("step") or index),
        actor=payload.get("actor") or "sosfiler",
        title=payload.get("title") or f"Step {index}",
        description=payload.get("description") or "",
        portal_page=payload.get("portal_page") or "",
        required_inputs=payload.get("required_inputs") or [],
        evidence_output=payload.get("evidence_output") or [],
        status_after_step=payload.get("status_after_step") or "",
    )


def _reminder_rule(payload: dict[str, Any]) -> ReminderRule:
    return ReminderRule(
        trigger=payload.get("trigger") or "before_due_date",
        offset_days=int(payload.get("offset_days") or 0),
        channel=payload.get("channel") or "email",
        audience=payload.get("audience") or "customer",
        message_key=payload.get("message_key") or "",
    )


def _money_to_cents(value: str) -> int | None:
    match = re.search(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)", value)
    if not match:
        return None
    return int(round(float(match.group(1).replace(",", "")) * 100))


def slug(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def normalized_filing_id(batch: dict[str, Any], payload: dict[str, Any]) -> str:
    raw_id = slug(payload.get("filing_id") or "")
    name = slug(payload.get("filing_name") or payload.get("form_name") or "")
    form = slug(payload.get("form_number") or ("" if batch.get("authority_context") else raw_id))
    base = name or form or "filing"
    if batch.get("scope") == "local":
        authority = slug(payload.get("authority") or payload.get("jurisdiction_name") or "")
        if authority and authority not in base:
            base = f"{authority}_{base}"
    if form and form not in base:
        base = f"{base}_{form}"
    if len(base) > 120:
        base = base[:120].rstrip("_")
    batch_prefix = slug(batch.get("batch_id") or batch.get("jurisdiction_id") or "regulatory")
    if raw_id and raw_id.startswith(batch_prefix) and not batch.get("authority_context"):
        return raw_id
    return f"{batch_prefix}_{base}"


def _contextualized_payload(batch: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    authority_context = batch.get("authority_context") or {}
    if batch.get("scope") != "local" or not authority_context:
        return payload
    updated = dict(payload)
    exact_authority = authority_context.get("authority_name") or ""
    exact_jurisdiction = authority_context.get("jurisdiction_name") or ""
    generic_authorities = {
        "",
        "county clerk",
        "city clerk",
        "development services",
        "permitting center",
        "official government source",
    }
    current_authority = str(updated.get("authority", "")).strip()
    if current_authority.lower() in generic_authorities and exact_authority:
        updated["authority"] = exact_authority
    elif exact_jurisdiction and exact_jurisdiction.lower() not in current_authority.lower():
        updated["authority"] = exact_authority or current_authority
    updated["jurisdiction_name"] = exact_jurisdiction or updated.get("jurisdiction_name") or ""
    updated["jurisdiction_level"] = authority_context.get("jurisdiction_level") or updated.get("jurisdiction_level") or ""
    citations = []
    for citation in updated.get("source_citations") or []:
        if isinstance(citation, dict):
            citations.append({**citation, "authority": citation.get("authority") or updated.get("authority") or exact_authority})
    updated["source_citations"] = citations
    return updated


def fallback_record_from_sources(batch: dict[str, Any], sources: list[FirecrawlSource]) -> FilingRecord:
    authority_context = batch.get("authority_context") or {}
    text = "\n".join(source.markdown for source in sources)
    first_source = next((source for source in sources if source.url), FirecrawlSource(url=""))
    amount = _money_to_cents(text)
    scope = batch.get("scope")
    category = FilingCategory.BUSINESS_LICENSE if scope == "local" else FilingCategory.FORMATION
    entity_types = [EntityType.OTHER] if scope == "local" else [EntityType.LLC, EntityType.CORPORATION]
    context_level = authority_context.get("jurisdiction_level")
    jurisdiction_level = _enum_value(JurisdictionLevel, context_level, JurisdictionLevel.CITY if scope == "local" else JurisdictionLevel.STATE)
    jurisdiction_name = (
        authority_context.get("jurisdiction_name")
        or batch.get("title", "").replace(" state filing map", "").replace(" county and city local filing map", "")
    )
    authority = authority_context.get("authority_name") or first_source.metadata.get("authority", "") or "Official government source"
    record = FilingRecord(
        filing_id=f"{batch['batch_id']}_source_packet",
        jurisdiction_id=batch["jurisdiction_id"],
        jurisdiction_name=jurisdiction_name,
        jurisdiction_level=jurisdiction_level,
        filing_category=category,
        entity_types=entity_types,
        filing_name=batch.get("title", "Regulatory filing source packet"),
        authority=authority,
        portal_url=first_source.url,
        submission_channels=["needs_review"],
        expected_turnaround="Needs operator review from extracted official source packet.",
        fee_components=[
            FeeComponent(
                code="detected_fee",
                label="Detected official fee mention",
                amount_cents=amount,
                source_url=first_source.url,
            )
        ] if amount is not None else [],
        process_steps=[
            ProcessStep(1, "sosfiler", "Review official source packet", evidence_output=["source_snapshot"])
        ],
        output_documents=["source_snapshot"],
        source_citations=[
            SourceCitation(url=source.url, title=source.title, authority=authority)
            for source in sources
            if source.url
        ],
        price_confidence=PriceConfidence.NEEDS_OPERATOR_QUOTE,
        verification_status=VerificationStatus.NEEDS_OPERATOR_REVIEW,
        notes="Fallback record created because Firecrawl did not return structured filing_records.",
    )
    record.automation = classify_automation(record)
    return record


def _record_from_payload(batch: dict[str, Any], payload: dict[str, Any]) -> FilingRecord:
    payload = _contextualized_payload(batch, payload)
    category = _filing_category(payload.get("filing_category", ""), payload)
    entity_types = [_entity_type(item) for item in payload.get("entity_types") or ["Other"]]
    citations = [
        _source_citation(item, payload.get("authority", ""))
        for item in payload.get("source_citations", [])
        if isinstance(item, dict) and item.get("url")
    ]
    fee_components = [
        _fee_component(item, f"fee_{index}")
        for index, item in enumerate(payload.get("fee_components") or [], start=1)
        if isinstance(item, dict)
    ]
    default_fee_source = citations[0].url if citations else ""
    for fee in fee_components:
        if not fee.source_url and default_fee_source:
            fee.source_url = default_fee_source
    processing_payload = payload.get("processing_fee")
    processing_fee = _fee_component(processing_payload, "processing_fee") if isinstance(processing_payload, dict) else None
    if processing_fee and not processing_fee.source_url and default_fee_source:
        processing_fee.source_url = default_fee_source
    process_steps = [
        _process_step(item, index)
        for index, item in enumerate(payload.get("process_steps") or [], start=1)
        if isinstance(item, dict)
    ]
    record = FilingRecord(
        filing_id=normalized_filing_id(batch, payload),
        jurisdiction_id=batch["jurisdiction_id"],
        jurisdiction_name=payload.get("jurisdiction_name") or batch.get("title", "").split(" state filing map")[0].split(" county and city")[0],
        jurisdiction_level=_enum_value(JurisdictionLevel, payload.get("jurisdiction_level", ""), JurisdictionLevel.CITY if batch.get("scope") == "local" else JurisdictionLevel.STATE),
        filing_category=category,
        entity_types=entity_types,
        filing_name=payload.get("filing_name") or payload["filing_id"],
        authority=payload.get("authority") or "",
        domestic_or_foreign=payload.get("domestic_or_foreign") or "unknown",
        form_number=payload.get("form_number") or "",
        form_name=payload.get("form_name") or "",
        portal_name=payload.get("portal_name") or "",
        portal_url=payload.get("portal_url") or "",
        submission_channels=payload.get("submission_channels") or [],
        required_customer_inputs=payload.get("required_customer_inputs") or [],
        required_documents=payload.get("required_documents") or [],
        output_documents=payload.get("output_documents") or [],
        annual_or_renewal_rule=payload.get("annual_or_renewal_rule") or "",
        deadline_rule=payload.get("deadline_rule") or "",
        reminder_rules=[
            _reminder_rule(item)
            for item in payload.get("reminder_rules") or []
            if isinstance(item, dict)
        ],
        expected_turnaround=payload.get("expected_turnaround") or "",
        fee_components=fee_components,
        processing_fee=processing_fee,
        process_steps=process_steps,
        source_citations=citations,
        notes=payload.get("notes") or "",
        last_verified_at=utc_now(),
    )
    record.automation = classify_automation(record)
    if not record.reminder_rules:
        record.reminder_rules = default_reminder_rules(record)
    record.price_confidence = classify_price_confidence(record)
    record.verification_status = classify_verification_status(record)
    quality_issues = record_quality_issues(record)
    if quality_issues:
        issue_text = "Auto-review issues: " + ", ".join(quality_issues)
        record.notes = f"{record.notes} {issue_text}".strip()
        record.verification_status = VerificationStatus.NEEDS_OPERATOR_REVIEW
    return record


def default_reminder_rules(record: FilingRecord) -> list[ReminderRule]:
    recurring_categories = {
        FilingCategory.ANNUAL_OBLIGATION,
        FilingCategory.RENEWAL,
        FilingCategory.BENEFICIAL_OWNERSHIP_REPORT,
        FilingCategory.TRADEMARK,
        FilingCategory.PATENT,
    }
    text = " ".join([record.filing_name, record.deadline_rule]).lower()
    is_recurring = record.filing_category in recurring_categories or any(
        term in text
        for term in ["annual", "biennial", "renewal", "renews", "maintenance fee", "maintenance filing"]
    )
    if not is_recurring:
        return []
    return [
        ReminderRule(trigger="before_due_date", offset_days=offset, message_key="regulatory_filing_due")
        for offset in [90, 60, 30, 7]
    ]


def classify_price_confidence(record: FilingRecord) -> PriceConfidence:
    required_fees = [fee for fee in record.fee_components if fee.required]
    if not required_fees:
        return PriceConfidence.UNKNOWN
    if all(fee.amount_cents is not None for fee in required_fees):
        if record.processing_fee and record.processing_fee.required and record.processing_fee.amount_cents is None:
            return PriceConfidence.FORMULA_VERIFIED if record.processing_fee.formula else PriceConfidence.NEEDS_OPERATOR_QUOTE
        return PriceConfidence.VERIFIED
    if all(fee.amount_cents is not None or fee.formula for fee in required_fees):
        return PriceConfidence.FORMULA_VERIFIED
    return PriceConfidence.NEEDS_OPERATOR_QUOTE


def classify_verification_status(record: FilingRecord, auto_verify: bool = True) -> VerificationStatus:
    if not auto_verify:
        return VerificationStatus.NEEDS_OPERATOR_REVIEW
    issues = record.validate_for_customer_pricing()
    issues.extend(record_quality_issues(record))
    if not record.automation:
        issues.append("missing_automation")
    if record.price_confidence in {PriceConfidence.VERIFIED, PriceConfidence.FORMULA_VERIFIED}:
        issues = [issue for issue in issues if issue != "price_not_verified"]
    if not issues:
        return VerificationStatus.VERIFIED_OFFICIAL
    return VerificationStatus.NEEDS_OPERATOR_REVIEW


def record_quality_issues(record: FilingRecord) -> list[str]:
    issues: list[str] = []
    for fee in record.fee_components:
        if fee.required and fee.amount_cents is not None and fee.amount_cents > 0:
            if record.jurisdiction_level == JurisdictionLevel.STATE and record.filing_category in {
                FilingCategory.FORMATION,
                FilingCategory.FOREIGN_AUTHORITY,
            } and fee.amount_cents < 5000:
                issues.append(f"suspicious_low_state_filing_fee:{fee.code}")
            if record.filing_category == FilingCategory.DBA_FBN and fee.amount_cents > 10000:
                issues.append(f"suspicious_high_dba_fee:{fee.code}")
        if fee.required and not fee.source_url and not fee.formula:
            issues.append(f"missing_fee_source:{fee.code}")
    if record.jurisdiction_id == "tx_state" and "articles of organization" in record.filing_name.lower():
        issues.append("texas_term_mismatch_articles_of_organization")
    return sorted(set(issues))


def records_from_firecrawl_result(
    batch: dict[str, Any],
    result: FirecrawlExtractResult,
    auto_verify: bool = True,
) -> list[FilingRecord]:
    data = result.data or {}
    if isinstance(data, list):
        candidate_records = data
    elif isinstance(data, dict):
        candidate_records = data.get("filing_records") or data.get("records") or []
    else:
        candidate_records = []

    records = [
        _record_from_payload(batch, item)
        for item in candidate_records
        if isinstance(item, dict) and item.get("filing_id")
    ]
    if not records and result.sources:
        records = [fallback_record_from_sources(batch, result.sources)]
    seen: dict[str, int] = {}
    for record in records:
        count = seen.get(record.filing_id, 0) + 1
        seen[record.filing_id] = count
        if count > 1:
            record.filing_id = f"{record.filing_id}_{count}"
    for record in records:
        if record.verification_status != VerificationStatus.NEEDS_OPERATOR_REVIEW:
            record.verification_status = classify_verification_status(record, auto_verify=auto_verify)
    return records
