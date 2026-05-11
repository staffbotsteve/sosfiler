"""Shared schemas for regulatory filing intelligence.

These dataclasses intentionally stay dependency-light so the research pipeline
can run from cron, operator laptops, or production workers before the app moves
to a larger database/workflow stack.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JurisdictionLevel(StrEnum):
    STATE = "state"
    COUNTY = "county"
    CITY = "city"
    SPECIAL_DISTRICT = "special_district"


class FilingCategory(StrEnum):
    FORMATION = "formation"
    FOREIGN_AUTHORITY = "foreign_authority"
    AMENDMENT = "amendment"
    REGISTERED_AGENT_CHANGE = "registered_agent_change"
    ADDRESS_CHANGE = "address_change"
    GOVERNING_PERSON_CHANGE = "governing_person_change"
    ANNUAL_OBLIGATION = "annual_obligation"
    RENEWAL = "renewal"
    DISSOLUTION = "dissolution"
    REINSTATEMENT = "reinstatement"
    DBA_FBN = "dba_fbn"
    BUSINESS_LICENSE = "business_license"
    TAX_REGISTRATION = "tax_registration"
    BENEFICIAL_OWNERSHIP_REPORT = "beneficial_ownership_report"
    TRADEMARK = "trademark"
    PATENT = "patent"
    CERTIFICATE_COPY = "certificate_copy"
    OTHER_LICENSE = "other_license"


class EntityType(StrEnum):
    LLC = "LLC"
    CORPORATION = "Corporation"
    NONPROFIT_CORPORATION = "Nonprofit Corporation"
    LLP = "LLP"
    LP = "LP"
    PLLC = "PLLC"
    PROFESSIONAL_CORPORATION = "Professional Corporation"
    PARTNERSHIP = "Partnership"
    SOLE_PROPRIETOR = "Sole Proprietor"
    OTHER = "Other"


class AutomationMethod(StrEnum):
    DIRECT_API = "direct_api"
    PARTNER_API = "partner_api"
    PLAYWRIGHT = "playwright"
    EMAIL_OR_UPLOAD = "email_or_upload"
    MAIL_ONLY = "mail_only"
    OPERATOR_ASSISTED = "operator_assisted"
    UNSUPPORTED = "unsupported"


class VerificationStatus(StrEnum):
    DISCOVERED = "discovered"
    EXTRACTED = "extracted"
    VERIFIED_OFFICIAL = "verified_official"
    PORTAL_OBSERVED = "portal_observed"
    NEEDS_OPERATOR_REVIEW = "needs_operator_review"
    STALE = "stale"


class PriceConfidence(StrEnum):
    VERIFIED = "verified"
    FORMULA_VERIFIED = "formula_verified"
    RANGE_ONLY = "range_only"
    NEEDS_OPERATOR_QUOTE = "needs_operator_quote"
    UNKNOWN = "unknown"


@dataclass
class ReminderRule:
    trigger: str
    offset_days: int
    channel: str = "email"
    audience: str = "customer"
    message_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceCitation:
    url: str
    title: str = ""
    authority: str = ""
    source_type: str = "official"
    retrieved_at: str = field(default_factory=utc_now)
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FeeComponent:
    code: str
    label: str
    amount_cents: int | None = None
    fee_type: str = "fixed"
    formula: str = ""
    required: bool = True
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExpediteOption:
    label: str
    fee_cents: int | None
    processing_time: str
    channel: str = "any"
    customer_selectable: bool = True
    source_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessStep:
    step: int
    actor: str
    title: str
    description: str = ""
    portal_page: str = ""
    required_inputs: list[str] = field(default_factory=list)
    evidence_output: list[str] = field(default_factory=list)
    status_after_step: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AutomationProfile:
    method: AutomationMethod
    account_strategy: str = ""
    submission_strategy: str = ""
    status_query_strategy: str = ""
    document_collection_strategy: str = ""
    blockers: list[str] = field(default_factory=list)
    selectors_or_api_notes: list[str] = field(default_factory=list)

    @property
    def automatable(self) -> bool:
        return self.method in {
            AutomationMethod.DIRECT_API,
            AutomationMethod.PARTNER_API,
            AutomationMethod.PLAYWRIGHT,
        } and not self.blockers

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["method"] = self.method.value
        payload["automatable"] = self.automatable
        return payload


@dataclass
class FilingRecord:
    filing_id: str
    jurisdiction_id: str
    jurisdiction_name: str
    jurisdiction_level: JurisdictionLevel
    filing_category: FilingCategory
    entity_types: list[EntityType]
    filing_name: str
    authority: str
    domestic_or_foreign: str = "unknown"
    form_number: str = ""
    form_name: str = ""
    portal_name: str = ""
    portal_url: str = ""
    submission_channels: list[str] = field(default_factory=list)
    required_customer_inputs: list[str] = field(default_factory=list)
    required_documents: list[str] = field(default_factory=list)
    output_documents: list[str] = field(default_factory=list)
    annual_or_renewal_rule: str = ""
    deadline_rule: str = ""
    reminder_rules: list[ReminderRule] = field(default_factory=list)
    expected_turnaround: str = ""
    fee_components: list[FeeComponent] = field(default_factory=list)
    processing_fee: FeeComponent | None = None
    expedite_options: list[ExpediteOption] = field(default_factory=list)
    process_steps: list[ProcessStep] = field(default_factory=list)
    automation: AutomationProfile = field(
        default_factory=lambda: AutomationProfile(method=AutomationMethod.OPERATOR_ASSISTED)
    )
    source_citations: list[SourceCitation] = field(default_factory=list)
    price_confidence: PriceConfidence = PriceConfidence.UNKNOWN
    verification_status: VerificationStatus = VerificationStatus.DISCOVERED
    last_verified_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "jurisdiction_level": self.jurisdiction_level.value,
            "filing_category": self.filing_category.value,
            "entity_types": [item.value for item in self.entity_types],
            "fee_components": [item.to_dict() for item in self.fee_components],
            "processing_fee": self.processing_fee.to_dict() if self.processing_fee else None,
            "expedite_options": [item.to_dict() for item in self.expedite_options],
            "process_steps": [item.to_dict() for item in self.process_steps],
            "reminder_rules": [item.to_dict() for item in self.reminder_rules],
            "automation": self.automation.to_dict(),
            "source_citations": [item.to_dict() for item in self.source_citations],
            "price_confidence": self.price_confidence.value,
            "verification_status": self.verification_status.value,
        }

    def validate_for_customer_pricing(self) -> list[str]:
        issues: list[str] = []
        if not self.source_citations:
            issues.append("missing_source_citation")
        if self.price_confidence not in {PriceConfidence.VERIFIED, PriceConfidence.FORMULA_VERIFIED}:
            issues.append("price_not_verified")
        if not self.expected_turnaround:
            issues.append("missing_expected_turnaround")
        if not self.process_steps:
            issues.append("missing_process_steps")
        if not self.output_documents:
            issues.append("missing_output_documents")
        return issues


@dataclass
class ResearchBatch:
    batch_id: str
    jurisdiction_id: str
    title: str
    priority: int
    scope: str
    phase: str
    required_filings: list[str]
    discovery_queries: list[str]
    official_domains: list[str]
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
