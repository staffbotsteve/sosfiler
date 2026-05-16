"""Pricing helpers for government fees, processing fees, and SOSFiler margins."""

from __future__ import annotations

from .schemas import FeeComponent, FilingRecord, PriceConfidence


DEFAULT_SERVICE_FEE_CENTS = 2900
LOCAL_OPERATOR_ASSISTED_FEE_CENTS = 7900
CHANGE_FILING_SERVICE_FEE_CENTS = 4900
ANNUAL_REPORT_SERVICE_FEE_CENTS = 2900


def required_government_total_cents(record: FilingRecord) -> int | None:
    total = 0
    for component in record.fee_components:
        if not component.required:
            continue
        if component.amount_cents is None:
            return None
        total += component.amount_cents
    if record.processing_fee and record.processing_fee.required:
        if record.processing_fee.amount_cents is None:
            return None
        total += record.processing_fee.amount_cents
    return total


def recommended_service_fee_cents(record: FilingRecord) -> int:
    if record.filing_category.value in {"business_license", "dba_fbn", "other_license"}:
        return LOCAL_OPERATOR_ASSISTED_FEE_CENTS
    if record.filing_category.value in {
        "amendment",
        "registered_agent_change",
        "address_change",
        "governing_person_change",
        "dissolution",
        "reinstatement",
    }:
        return CHANGE_FILING_SERVICE_FEE_CENTS
    if record.filing_category.value == "annual_obligation":
        return ANNUAL_REPORT_SERVICE_FEE_CENTS
    return DEFAULT_SERVICE_FEE_CENTS


def customer_price_cents(record: FilingRecord) -> int | None:
    government_total = required_government_total_cents(record)
    if government_total is None:
        return None
    return government_total + recommended_service_fee_cents(record)


def price_readiness(record: FilingRecord) -> dict[str, object]:
    fixed_price = customer_price_cents(record)
    issues = record.validate_for_customer_pricing()
    if fixed_price is None:
        issues.append("variable_or_unknown_required_fee")
    return {
        "filing_id": record.filing_id,
        "government_total_cents": required_government_total_cents(record),
        "service_fee_cents": recommended_service_fee_cents(record),
        "customer_price_cents": fixed_price,
        "price_confidence": record.price_confidence.value,
        "ready_for_self_service_pricing": not issues
        and record.price_confidence in {PriceConfidence.VERIFIED, PriceConfidence.FORMULA_VERIFIED},
        "issues": sorted(set(issues)),
    }
