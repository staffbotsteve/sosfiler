#!/usr/bin/env python3
"""Promote verified regulatory filing records into runtime filing actions.

The regulatory research corpus is broad and intentionally append-only. This
script selects the best verified state-level record per state/entity/action and
normalizes it into the smaller `data/filing_actions.generated.json` shape used
by the application.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
INPUT_PATH = DATA_DIR / "regulatory" / "filings.json"
OUTPUT_PATH = DATA_DIR / "filing_actions.generated.json"

ENTITY_ALIASES = {
    "LLC": {"LLC", "Limited Liability Company"},
    "Corp": {"Corp", "Corporation", "Business Corporation", "C-Corp", "S-Corp"},
    "Nonprofit": {"Nonprofit", "Nonprofit Corporation", "Non Profit Corporation"},
}

FORMATION_PATTERNS = {
    "LLC": re.compile(r"(articles of organization|certificate of organization|certificate of formation|llc formation)", re.I),
    "Corp": re.compile(r"(articles of incorporation|certificate of incorporation|business corporation.*incorporation|corporation formation)", re.I),
    "Nonprofit": re.compile(r"(articles of incorporation|certificate of incorporation|non.?profit.*formation|non.?profit.*incorporation)", re.I),
}

INSTANCE_DOCUMENT_URL_PATTERNS = (
    "converttifftopdf",
    "/get/filing/confirm/",
    "documentnumber=",
    "/inquiry/corporationsearch/",
)

ENTITY_INSTANCE_PATTERN = re.compile(
    r"\bfor\s+(?!a\s+limited\s+liability\s+company\b|limited\s+liability\s+company\b|"
    r"a\s+business\s+corporation\b|business\s+corporation\b|"
    r"a\s+nonprofit\s+corporation\b|nonprofit\s+corporation\b)"
    r"[a-z0-9][a-z0-9&.,' -]{2,}\s+"
    r"(llc|l\.l\.c\.|inc\.?|corp\.?|corporation|company)\b",
    re.I,
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def state_code(record: dict) -> str:
    raw = record.get("jurisdiction_id") or ""
    match = re.match(r"([a-z]{2})_", raw.lower())
    return match.group(1).upper() if match else raw[:2].upper()


def entity_matches(record: dict, entity_key: str) -> bool:
    entities = set(record.get("entity_types") or [])
    aliases = ENTITY_ALIASES[entity_key]
    return bool(entities & aliases)


def required_fee_total(record: dict) -> int | None:
    total = 0
    for fee in record.get("fee_components") or []:
        if not fee.get("required"):
            continue
        amount = fee.get("amount_cents")
        if amount is None:
            return None
        total += int(amount)
    processing = record.get("processing_fee")
    if processing and processing.get("required"):
        amount = processing.get("amount_cents")
        if amount is None:
            return None
        total += int(amount)
    return total


def fee_confidence(record: dict) -> str:
    if required_fee_total(record) is None:
        return "needs_operator_quote"
    confidence = (record.get("price_confidence") or "").lower()
    if confidence in {"verified", "formula_verified"}:
        return confidence
    if record.get("verification_status") == "verified_official":
        return "verified"
    return confidence or "unknown"


def action_type_for(record: dict) -> str:
    category = record.get("filing_category") or ""
    if category == "formation":
        return "formation"
    mapping = {
        "foreign_authority": "foreign_registration",
        "registered_agent_change": "registered_agent_change",
        "annual_obligation": "annual_report",
        "dba_fbn": "dba",
        "business_license": "business_license",
        "other_license": "license",
        "dissolution": "dissolution",
        "reinstatement": "reinstatement",
        "amendment": "amendment",
    }
    return mapping.get(category, category or "other")


def automation_level(record: dict) -> str:
    method = ((record.get("automation") or {}).get("method") or "").lower()
    blockers = (record.get("automation") or {}).get("blockers") or []
    channels = {str(channel).lower() for channel in (record.get("submission_channels") or [])}
    if method in {"api", "official_api"}:
        return "api_ready"
    if method in {"partner_api"}:
        return "partner_api_ready"
    if method in {"playwright", "browser", "browser_automation"} and not blockers:
        return "browser_automation_ready"
    if "mail" in channels or "pdf" in channels:
        return "operator_assisted"
    return "manual_review"


def processing_fee(record: dict) -> dict:
    fee = record.get("processing_fee")
    if not fee:
        return {"type": "none"}
    amount = fee.get("amount_cents")
    if amount is not None:
        return {
            "type": "fixed",
            "amount_cents": int(amount),
            "label": fee.get("label", "Processing fee"),
            "source_note": fee.get("source_url", ""),
        }
    formula = fee.get("formula") or ""
    return {
        "type": "formula" if formula else "unknown",
        "formula": formula,
        "label": fee.get("label", "Processing fee"),
        "source_note": fee.get("source_url", ""),
    }


def source_urls(record: dict) -> list[str]:
    urls = []
    for citation in record.get("source_citations") or []:
        url = citation.get("url")
        if url and url not in urls:
            urls.append(url)
    for fee in record.get("fee_components") or []:
        url = fee.get("source_url")
        if url and url not in urls:
            urls.append(url)
    if record.get("portal_url") and record["portal_url"] not in urls:
        urls.append(record["portal_url"])
    return urls


def is_instance_specific_record(record: dict) -> bool:
    name = record.get("filing_name") or ""
    if ENTITY_INSTANCE_PATTERN.search(name):
        return True
    for url in source_urls(record):
        lowered = url.lower()
        if any(pattern in lowered for pattern in INSTANCE_DOCUMENT_URL_PATTERNS):
            return True
    return False


def is_runtime_candidate(record: dict, entity_key: str) -> bool:
    if action_type_for(record) != "formation":
        return False
    if is_instance_specific_record(record):
        return False
    name = record.get("filing_name") or record.get("form_name") or ""
    if not FORMATION_PATTERNS[entity_key].search(name):
        return False
    return True


def source_quality_score(record: dict) -> int:
    score = 0
    urls = [url.lower() for url in source_urls(record)]
    if any("/forms" in url or "forms/" in url for url in urls):
        score += 12
    if any("/fees" in url or "fee-" in url or "fee_" in url for url in urls):
        score += 8
    if any(url.endswith(".pdf") for url in urls):
        score += 4
    if is_instance_specific_record(record):
        score -= 200
    return score


def required_evidence(record: dict, stage: str) -> list[str]:
    outputs = record.get("output_documents") or []
    if stage == "submitted":
        return ["Official portal receipt, confirmation, or submission evidence"]
    if outputs:
        return outputs
    return ["Official approval, file-stamped document, certificate, or government correspondence"]


def score_record(record: dict, entity_key: str) -> int:
    score = 0
    name = record.get("filing_name") or ""
    if record.get("verification_status") == "verified_official":
        score += 50
    if record.get("filing_category") == "formation":
        score += 40
    if record.get("domestic_or_foreign", "").lower() == "domestic":
        score += 15
    if FORMATION_PATTERNS[entity_key].search(name):
        score += 30
    if required_fee_total(record) is not None:
        score += 20
    if record.get("process_steps"):
        score += 10
    if record.get("portal_url"):
        score += 5
    score += source_quality_score(record)
    if "foreign" in name.lower():
        score -= 50
    if record.get("filing_category") in {"other_license", "business_license"}:
        score -= 30
    return score


def normalize_action(record: dict, entity_key: str) -> dict:
    total = required_fee_total(record)
    return {
        "state": state_code(record),
        "entity_type": entity_key,
        "action_type": action_type_for(record),
        "customer_product_name": record.get("filing_name") or record.get("form_name") or "Formation filing",
        "office": record.get("authority") or record.get("jurisdiction_name") or "",
        "form_name": record.get("form_name") or record.get("filing_name") or "",
        "form_number": record.get("form_number") or "",
        "portal_name": record.get("portal_name") or "",
        "portal_url": record.get("portal_url") or "",
        "automation_level": automation_level(record),
        "filing_method": "web_portal" if automation_level(record) == "browser_automation_ready" else "manual_review",
        "state_fee_cents": int(total or 0),
        "processing_fee": processing_fee(record),
        "expected_processing_time": record.get("expected_turnaround") or "Varies by state and portal workload.",
        "required_consents": [
            "Customer authorization for SOSFiler to prepare and submit filing documents"
        ],
        "pre_submission_blockers": [
            {"code": f"automation_blocker_{i + 1}", "message": str(blocker)}
            for i, blocker in enumerate((record.get("automation") or {}).get("blockers") or [])
        ],
        "portal_field_sequence": record.get("process_steps") or [],
        "required_evidence_for_submitted": required_evidence(record, "submitted"),
        "required_evidence_for_approved": required_evidence(record, "approved"),
        "source_urls": source_urls(record),
        "last_verified": record.get("last_verified_at") or "",
        "price_confidence": fee_confidence(record),
        "source_filing_id": record.get("filing_id") or "",
        "verification_status": record.get("verification_status") or "",
        "generated_from_regulatory": True,
        "promotion_score": score_record(record, entity_key),
    }


def promote(records: list[dict]) -> dict:
    candidates: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for record in records:
        if record.get("jurisdiction_level") != "state":
            continue
        if record.get("verification_status") != "verified_official":
            continue
        state = state_code(record)
        if not state or len(state) != 2:
            continue
        for entity_key in ENTITY_ALIASES:
            if not entity_matches(record, entity_key):
                continue
            if not is_runtime_candidate(record, entity_key):
                continue
            action_type = action_type_for(record)
            candidates[(state, entity_key, action_type)].append(record)

    output: dict[str, Any] = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "source": str(INPUT_PATH.relative_to(APP_DIR)),
            "selection": "highest scoring verified_official state-level record per state/entity/action",
        }
    }
    for (state, entity_key, action_type), records_for_key in sorted(candidates.items()):
        best = max(records_for_key, key=lambda record: score_record(record, entity_key))
        output.setdefault(state, {}).setdefault(entity_key, {})[action_type] = normalize_action(best, entity_key)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote regulatory filings into runtime filing actions.")
    parser.add_argument("--input", type=Path, default=INPUT_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true", help="Do not write, only print coverage summary.")
    args = parser.parse_args()

    payload = load_json(args.input)
    records = payload.get("records") or []
    generated = promote(records)
    coverage = {
        state: sorted(entity_map.keys())
        for state, entity_map in generated.items()
        if not state.startswith("_")
    }
    summary = {
        "states": len(coverage),
        "actions": sum(len(actions) for entity_map in generated.values() if isinstance(entity_map, dict) for actions in entity_map.values() if isinstance(actions, dict)),
        "coverage": coverage,
    }
    print(json.dumps(summary, indent=2))
    if not args.check:
        args.output.write_text(json.dumps(generated, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
