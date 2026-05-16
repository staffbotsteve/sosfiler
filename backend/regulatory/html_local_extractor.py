"""Fallback local filing extraction from official HTML pages.

Firecrawl remains the primary provider. This module is a conservative fallback
for city/county pages that are plainly official and human-readable but return
an empty structured extraction response.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.request import Request, urlopen

from .automation_classifier import classify_automation
from .extractor import classify_price_confidence, classify_verification_status, normalized_filing_id
from .schemas import (
    EntityType,
    FeeComponent,
    FilingCategory,
    FilingRecord,
    JurisdictionLevel,
    PriceConfidence,
    ProcessStep,
    SourceCitation,
    VerificationStatus,
    utc_now,
)


def fetch_html(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": "SOSFilerRegulatoryResearch/0.1"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def html_to_text(payload: str) -> str:
    payload = re.sub(r"(?is)<(script|style).*?</\1>", " ", payload)
    payload = re.sub(r"(?i)<br\s*/?>", "\n", payload)
    payload = re.sub(r"(?i)</p|</div|</li|</h[1-6]", "\n<", payload)
    payload = re.sub(r"(?s)<[^>]+>", " ", payload)
    payload = html.unescape(payload)
    lines = [re.sub(r"\s+", " ", line).strip() for line in payload.splitlines()]
    return "\n".join(line for line in lines if line)


def _title(payload: str) -> str:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", payload)
    return html.unescape(re.sub(r"\s+", " ", match.group(1)).strip()) if match else ""


def _amount_to_cents(value: str) -> int | None:
    match = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{2})?)", value)
    if not match:
        return None
    return int(round(float(match.group(1).replace(",", "")) * 100))


def _authority_from_url(url: str, text: str) -> tuple[str, JurisdictionLevel]:
    lower = url.lower()
    path = re.sub(r"https?://[^/]+", "", lower)
    if "traviscountytx.gov" in lower:
        return "Travis County Clerk", JurisdictionLevel.COUNTY
    if "bexar.org" in lower:
        return "Bexar County Clerk", JurisdictionLevel.COUNTY
    if "elliscountytx.gov" in lower:
        return "Ellis County Clerk", JurisdictionLevel.COUNTY
    if "dentoncounty.gov" in lower:
        return "Denton County Clerk", JurisdictionLevel.COUNTY
    if "collincountytx.gov" in lower:
        return "Collin County Clerk", JurisdictionLevel.COUNTY
    if "tarrantcountytx.gov" in lower:
        return "Tarrant County Clerk", JurisdictionLevel.COUNTY
    if "fortbendcountytx.gov" in lower:
        return "Fort Bend County Clerk", JurisdictionLevel.COUNTY
    if "ector" in lower and ("page/ector" in path or "ectorcounty" in lower or "co.ector" in lower):
        return "Ector County Clerk", JurisdictionLevel.COUNTY
    if "rockwallcountytexas.com" in lower:
        return "Rockwall County Clerk", JurisdictionLevel.COUNTY
    if "wilcotx.gov" in lower or "wilco.org" in lower:
        return "Williamson County Clerk", JurisdictionLevel.COUNTY
    if "dallas" in lower:
        return "City of Dallas", JurisdictionLevel.CITY
    if "county" in text.lower():
        return "County Clerk", JurisdictionLevel.COUNTY
    return "City licensing authority", JurisdictionLevel.CITY


def _record(
    batch: dict[str, Any],
    url: str,
    title: str,
    authority: str,
    level: JurisdictionLevel,
    filing_name: str,
    category: FilingCategory,
    fee: FeeComponent | None,
    output_documents: list[str],
    notes: str,
) -> FilingRecord:
    authority_context = batch.get("authority_context") or {}
    if authority_context:
        authority = authority_context.get("authority_name") or authority
        level = JurisdictionLevel(authority_context.get("jurisdiction_level")) if authority_context.get("jurisdiction_level") else level
        jurisdiction_name = authority_context.get("jurisdiction_name") or authority
    else:
        jurisdiction_name = authority
    payload = {"filing_id": filing_name, "filing_name": filing_name, "authority": authority}
    record = FilingRecord(
        filing_id=normalized_filing_id(batch, payload),
        jurisdiction_id=batch["jurisdiction_id"],
        jurisdiction_name=jurisdiction_name,
        jurisdiction_level=level,
        filing_category=category,
        entity_types=[EntityType.OTHER],
        filing_name=filing_name,
        authority=authority,
        portal_url=url,
        submission_channels=["official_page", "operator_assisted"],
        required_customer_inputs=["business name", "owner/applicant information", "business address"],
        output_documents=output_documents,
        expected_turnaround="Varies by local authority; verify during operator review.",
        fee_components=[fee] if fee else [],
        process_steps=[
            ProcessStep(
                1,
                "sosfiler",
                "Prepare local filing from official requirements",
                portal_page=url,
                evidence_output=output_documents,
            ),
            ProcessStep(
                2,
                "sosfiler",
                "Submit through listed local channel or portal",
                portal_page=url,
                status_after_step="submitted_to_local_authority",
            ),
            ProcessStep(
                3,
                "sosfiler",
                "Collect issued local document or receipt",
                evidence_output=output_documents,
                status_after_step="document_received",
            ),
        ],
        source_citations=[SourceCitation(url=url, title=title, authority=authority)],
        notes=notes,
        last_verified_at=utc_now(),
    )
    record.automation = classify_automation(record)
    record.price_confidence = classify_price_confidence(record)
    record.verification_status = classify_verification_status(record)
    if record.verification_status == VerificationStatus.VERIFIED_OFFICIAL and not fee:
        record.verification_status = VerificationStatus.NEEDS_OPERATOR_REVIEW
        record.price_confidence = PriceConfidence.NEEDS_OPERATOR_QUOTE
    return record


def _dallas_license_sections(payload: str, text: str) -> list[tuple[str, str]]:
    sections = [
        (html_to_text(raw_name), html_to_text(raw_body))
        for raw_name, raw_body in re.findall(r"(?is)<p>\s*<strong>\s*(.*?)\s*</strong>\s*<br\s*/?>(.*?)</p>", payload)
    ]
    if sections:
        return sections
    names = [
        "Amusement Center License",
        "Alcoholic Beverage Local Fee",
        "Beer License",
        "Billiard Hall License",
        "Coin Operated Machines",
        "Dance Hall License",
        "Liquor License",
        "Precious Metal Vendor",
        "Sexually Oriented Business License",
    ]
    for index, name in enumerate(names):
        start = text.lower().find(name.lower())
        if start < 0:
            continue
        next_positions = [
            text.lower().find(other.lower(), start + len(name))
            for other in names[index + 1:]
        ]
        next_positions = [position for position in next_positions if position >= 0]
        end = min(next_positions) if next_positions else min(len(text), start + 900)
        sections.append((name, text[start + len(name):end].strip()))
    return sections


def _dallas_license_records(batch: dict[str, Any], url: str, payload: str, text: str, text_title: str) -> list[FilingRecord]:
    records: list[FilingRecord] = []
    for name, body in _dallas_license_sections(payload, text):
        name = re.sub(r"\s+", " ", name).strip()
        if len(name) > 80 or " fee is charged " in name.lower():
            continue
        if not any(term in name.lower() for term in ["license", "machines", "vendor"]):
            continue
        amount = _amount_to_cents(body)
        fee = FeeComponent(
            code="annual_license_fee",
            label="Annual license fee",
            amount_cents=amount,
            fee_type="fixed" if amount is not None else "variable",
            formula="" if amount is not None else body[:240],
            source_url=url,
        )
        records.append(
            _record(
                batch=batch,
                url=url,
                title=text_title,
                authority="City of Dallas",
                level=JurisdictionLevel.CITY,
                filing_name=name,
                category=FilingCategory.BUSINESS_LICENSE,
                fee=fee,
                output_documents=["city business license or fee receipt"],
                notes="Fallback HTML extraction from Dallas Special Collections license page.",
            )
        )
    return records


def _clean_page_title(title: str, text: str) -> str:
    value = title or ""
    if not value:
        for line in text.splitlines():
            line = line.strip("# ").strip()
            if line:
                value = line
                break
    value = re.sub(r"\s*\|\s*AustinTexas\.gov\s*$", "", value, flags=re.I)
    value = re.sub(r"\s*\|\s*.*$", "", value)
    value = re.sub(r"^(apply for an?|apply for)\s+", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:90] or "Local Business Permit"


def _generic_city_license_record(batch: dict[str, Any], url: str, text: str, text_title: str) -> FilingRecord | None:
    authority_context = batch.get("authority_context") or {}
    if authority_context.get("jurisdiction_level") != "city":
        return None
    lower = " ".join([url, text]).lower()
    if not any(term in lower for term in ["permit", "license", "certificate of occupancy"]):
        return None
    if not any(term in lower for term in ["how to apply", "application", "fee", "ab+c portal", "portal", "email"]):
        return None
    name = _clean_page_title(text_title, text)
    if name.lower() in {"types of permits", "land use assistance"} or "fee schedule" in name.lower():
        return None
    if not any(term in name.lower() for term in ["permit", "license", "certificate", "establishment", "authority"]):
        name = f"{name} Permit"
    amounts = [
        amount for amount in (
            _amount_to_cents(match.group(0))
            for match in re.finditer(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{2})?", text)
        )
        if amount is not None and amount <= 100000
    ]
    if amounts:
        unique_amounts = sorted(set(amounts))
        fee = FeeComponent(
            code="permit_fee",
            label="Permit or license fee",
            amount_cents=unique_amounts[0] if len(unique_amounts) == 1 else None,
            fee_type="fixed" if len(unique_amounts) == 1 else "formula",
            formula="" if len(unique_amounts) == 1 else "Official page lists multiple fee amounts: " + ", ".join(f"${amount / 100:.2f}" for amount in unique_amounts[:8]),
            source_url=url,
        )
    else:
        fee = FeeComponent(
            code="permit_fee",
            label="Permit or license fee",
            amount_cents=None,
            fee_type="needs_review",
            source_url=url,
        )
    return _record(
        batch=batch,
        url=url,
        title=text_title,
        authority=authority_context.get("authority_name", "City licensing authority"),
        level=JurisdictionLevel.CITY,
        filing_name=name,
        category=FilingCategory.BUSINESS_LICENSE,
        fee=fee,
        output_documents=["issued city permit, license, registration, or receipt"],
        notes="Generic official city permit/license fallback extraction from authority-manifest page.",
    )


def _dba_record(batch: dict[str, Any], url: str, text: str, text_title: str) -> FilingRecord | None:
    lower = " ".join([url, text]).lower()
    if not any(term in lower for term in ["dba", "assumed name", "fictitious business name"]):
        return None
    authority, level = _authority_from_url(url, text)
    authority_context = batch.get("authority_context") or {}
    if authority_context:
        authority = authority_context.get("authority_name") or authority
        level = JurisdictionLevel(authority_context.get("jurisdiction_level")) if authority_context.get("jurisdiction_level") else level
    if level != JurisdictionLevel.COUNTY:
        return None
    amounts = [_amount_to_cents(match.group(0)) for match in re.finditer(r"\$\s*[0-9][0-9,]*(?:\.[0-9]{2})?", text)]
    amounts = [amount for amount in amounts if amount is not None and amount <= 10000]
    fee = FeeComponent(
        code="filing_fee",
        label="DBA/assumed name filing fee",
        amount_cents=amounts[0] if amounts else None,
        fee_type="fixed" if amounts else "needs_review",
        source_url=url,
    )
    return _record(
        batch=batch,
        url=url,
        title=text_title,
        authority=authority,
        level=level,
        filing_name="Assumed Name / DBA Filing",
        category=FilingCategory.DBA_FBN,
        fee=fee,
        output_documents=["file-stamped assumed name certificate"],
        notes="Fallback HTML extraction from official county DBA/assumed-name page.",
    )


def records_from_local_html(batch: dict[str, Any], urls: list[str]) -> list[FilingRecord]:
    pages: list[dict[str, str]] = []
    for url in urls:
        try:
            payload = fetch_html(url)
        except Exception:
            continue
        pages.append({"url": url, "html": payload, "title": _title(payload)})
    return records_from_local_page_payloads(batch, pages)


def records_from_local_page_payloads(batch: dict[str, Any], pages: list[dict[str, str]]) -> list[FilingRecord]:
    records: list[FilingRecord] = []
    for page in pages:
        url = page.get("url", "")
        payload = page.get("html") or page.get("markdown") or page.get("content") or ""
        text = page.get("text") or (html_to_text(payload) if "<" in payload and ">" in payload else payload)
        title = page.get("title") or _title(payload)
        if "dallascityhall.com" in url.lower() and "license" in text.lower():
            records.extend(_dallas_license_records(batch, url, payload, text, title))
        generic_city = _generic_city_license_record(batch, url, text, title)
        if generic_city:
            records.append(generic_city)
        dba = _dba_record(batch, url, text, title)
        if dba:
            records.append(dba)
    deduped: dict[str, FilingRecord] = {}
    for record in records:
        deduped[record.filing_id] = record
    return list(deduped.values())
