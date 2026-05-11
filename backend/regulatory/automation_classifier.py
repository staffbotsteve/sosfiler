"""Classify filing records into automation lanes."""

from __future__ import annotations

from urllib.parse import urlparse

from .schemas import AutomationMethod, AutomationProfile, FilingRecord


API_PORTAL_HINTS = {
    "accela": AutomationMethod.PARTNER_API,
    "opengov": AutomationMethod.PARTNER_API,
    "energov": AutomationMethod.PARTNER_API,
    "tylertech": AutomationMethod.PARTNER_API,
    "civicplus": AutomationMethod.PARTNER_API,
    "municode": AutomationMethod.PARTNER_API,
    "govos": AutomationMethod.PARTNER_API,
}


def classify_automation(record: FilingRecord) -> AutomationProfile:
    channels = {item.lower() for item in record.submission_channels}
    portal = f"{record.portal_name} {record.portal_url}".lower()
    blockers: list[str] = []

    if "api" in channels:
        method = AutomationMethod.DIRECT_API
    elif any(hint in portal for hint in API_PORTAL_HINTS):
        method = AutomationMethod.PARTNER_API
    elif "online" in channels or "web_portal" in channels or record.portal_url:
        method = AutomationMethod.PLAYWRIGHT
    elif "email" in channels or "upload" in channels:
        method = AutomationMethod.EMAIL_OR_UPLOAD
    elif channels == {"mail"} or "mail" in channels:
        method = AutomationMethod.MAIL_ONLY
    else:
        method = AutomationMethod.OPERATOR_ASSISTED
        blockers.append("no_verified_submission_channel")

    if not record.portal_url and method in {AutomationMethod.PLAYWRIGHT, AutomationMethod.PARTNER_API}:
        blockers.append("missing_portal_url")
    if not record.process_steps:
        blockers.append("missing_observed_process_steps")
    if not record.output_documents:
        blockers.append("missing_document_collection_map")

    host = urlparse(record.portal_url).netloc if record.portal_url else ""
    return AutomationProfile(
        method=method,
        account_strategy="sosfiler_master_account_or_customer_delegated_account",
        submission_strategy="submit structured intake through verified portal steps",
        status_query_strategy="poll portal/order/entity search every 15 minutes while pending",
        document_collection_strategy="download portal/email-produced documents into customer vault",
        blockers=blockers,
        selectors_or_api_notes=[f"portal_host={host}"] if host else [],
    )

