"""Launch-readiness reporting for SOSFiler state automation.

This module converts certification evidence into a market-facing operating
plan: what can be sold now, what needs operator assistance, and what is blocked
until official access or partner coverage exists.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from state_automation_profiles import all_state_adapter_manifests


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FEES_PATH = BASE_DIR / "data" / "state_fees.json"
REPORT_JSON_PATH = BASE_DIR / "data" / "regulatory" / "launch_readiness_report.json"
REPORT_MD_PATH = BASE_DIR / "docs" / "launch_readiness.md"
SOSFILER_PLATFORM_FEE_CENTS = 2900

LIFECYCLE_WORKFLOWS = [
    "annual_report",
    "amendment",
    "registered_agent_change",
    "certificate_status",
    "certified_copy",
    "dissolution",
    "withdrawal",
    "reinstatement",
    "compliance_reminder",
]

LIFECYCLE_PUBLIC_LABELS = {
    "annual_report": "Annual reports",
    "amendment": "Amendments",
    "registered_agent_change": "Registered agent changes",
    "certificate_status": "Certificates of status",
    "certified_copy": "Certified copies",
    "dissolution": "Dissolution",
    "withdrawal": "Withdrawal",
    "reinstatement": "Reinstatement",
    "compliance_reminder": "Compliance reminders",
}

SELLABLE_CATEGORIES = {
    "fully_automated_sellable",
    "operator_assisted_sellable",
    "customer_data_intake_sellable",
    "trusted_access_operator_sellable",
    "partner_intermediary_sellable",
}

PUBLIC_CATEGORY_COPY = {
    "fully_automated_sellable": {
        "fulfillment_lane": "Automated state filing",
        "headline": "Automated filing available",
        "message": (
            "SOSFiler validates your order, prepares the formation package, submits through the approved "
            "state channel, and posts receipts and filed documents in your dashboard."
        ),
    },
    "operator_assisted_sellable": {
        "fulfillment_lane": "Operator-assisted filing",
        "headline": "Operator-assisted filing available",
        "message": (
            "SOSFiler prepares the state package and an operator completes the permitted filing steps "
            "through the official state channel."
        ),
    },
    "customer_data_intake_sellable": {
        "fulfillment_lane": "Guided intake plus operator filing",
        "headline": "Available after state-specific details are complete",
        "message": (
            "SOSFiler collects the required filer and entity details, prepares the package, and routes it "
            "to the fastest permitted filing lane."
        ),
    },
    "trusted_access_operator_sellable": {
        "fulfillment_lane": "Secure operator-assisted filing",
        "headline": "Operator-assisted filing available",
        "message": (
            "SOSFiler prepares the filing package, completes the permitted online steps with a verified "
            "operator, and keeps your dashboard current with each filing event."
        ),
    },
    "partner_intermediary_sellable": {
        "fulfillment_lane": "Partner-assisted filing",
        "headline": "Partner-assisted filing available",
        "message": (
            "SOSFiler prepares the package and coordinates the approved partner handoff required for this "
            "jurisdiction, then stores official evidence in your dashboard."
        ),
    },
    "unavailable_pending_official_access": {
        "fulfillment_lane": "Not available",
        "headline": "Not yet available for checkout",
        "message": (
            "This jurisdiction needs additional official access before SOSFiler can accept formation orders."
        ),
    },
}

AUTOMATION_FIRST_POLICY = {
    "operator_touch_model": "operator_by_exception",
    "target": "Application performs review, preparation, permitted execution, evidence capture, payment reconciliation, status updates, and lifecycle routing.",
    "captcha_policy": "Do not use CAPTCHA solving or click services. Stop at CAPTCHA and route to an authorized human or official access path.",
    "payment_policy": "Application may prepare, authorize, and reconcile payments; final portal payment submission is automated only where the official channel permits it.",
    "prohibited_automation": [
        "CAPTCHA solving or click-service outsourcing",
        "Credential sharing outside approved operator profiles",
        "Bypassing login, identity, terms, attestation, rate-limit, or payment controls",
        "Submitting payment or attestation where the official portal requires a human authorized action",
    ],
}

BASE_APPLICATION_HANDLED_STEPS = [
    "Validate customer intake, entity data, organizer/filer details, registered-agent data, and state-specific requirements before work is queued.",
    "Calculate official government fees, SOSFiler fee, optional expedite or partner costs, and payment-readiness status.",
    "Prepare the formation package, filing payload, operator-safe checklist, source links, and idempotent execution job.",
    "Run permitted browser/API automation for portal navigation, data entry, review-screen comparison, and filing-package upload.",
    "Prepare and reconcile state payment using approved payment channels; submit payment automatically only where the official channel permits it.",
    "Capture portal responses, receipts, confirmation numbers, downloaded documents, screenshots, timestamps, and audit events.",
    "Poll or check status where allowed and keep the customer dashboard, document vault, timeline, and next-action messaging current.",
    "Create post-formation lifecycle tasks for annual reports, amendments, registered-agent changes, certificates, certified copies, dissolution, withdrawal, reinstatement, and reminders.",
]

CATEGORY_APPLICATION_STEPS = {
    "fully_automated_sellable": [
        "Submit the filing through the certified production adapter after payment-authorized preflight passes.",
        "Complete document and receipt ingestion without operator touch unless the state portal returns an unexpected gate or failure.",
    ],
    "operator_assisted_sellable": [
        "Execute all known filing steps through the browser assistant and stop only on uncategorized state-specific exceptions.",
        "Turn first-live-filing evidence into a stricter adapter contract for the next filing.",
    ],
    "customer_data_intake_sellable": [
        "Detect missing applicant/filer fields, generate a customer-facing data request, and hold execution until required data is complete.",
        "Resume validation and browser/API execution automatically after the customer completes the missing fields.",
    ],
    "trusted_access_operator_sellable": [
        "Drive portal navigation, prefill forms, upload package data, and compare review screens up to the protected checkpoint.",
        "Prepare a minimal exception task with exact checkpoint context, redacted evidence, and the next permitted action.",
        "Resume automation after the authorized checkpoint is completed.",
    ],
    "partner_intermediary_sellable": [
        "Generate the partner-ready filing package, quote request, customer price reconciliation, and evidence checklist.",
        "Track partner status, ingest partner receipts, and reconcile official state evidence before customer-visible completion.",
    ],
}

CATEGORY_OPERATOR_EXCEPTION_STEPS = {
    "fully_automated_sellable": [
        "Intervene only if the certified adapter hits an unexpected official portal gate, payment mismatch, or state rejection.",
    ],
    "operator_assisted_sellable": [
        "Resolve any uncategorized portal exception and certify the resulting adapter evidence for future automation.",
    ],
    "customer_data_intake_sellable": [
        "Review only escalated customer-data ambiguity or state-specific data that cannot be validated automatically.",
    ],
    "trusted_access_operator_sellable": [
        "Use the verified operator profile only to complete authorized login, CAPTCHA, identity, payment, terms, or attestation checkpoints.",
        "Do not use CAPTCHA solving services or bypass protected portal controls.",
        "Confirm the checkpoint result, then hand control back to automation for downloads, receipts, status updates, and dashboard messaging.",
    ],
    "partner_intermediary_sellable": [
        "Confirm partner availability, quote, and authority to file before payment capture or partner submission.",
        "Resolve partner exceptions and verify official evidence when it arrives.",
    ],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def _gate_statuses(manifest: dict[str, Any]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for gate in manifest.get("certification_gates") or []:
        statuses[str(gate.get("code", ""))] = str(gate.get("status", ""))
    return statuses


def _has_gate_status(manifest: dict[str, Any], *statuses: str) -> bool:
    wanted = set(statuses)
    return any(str(gate.get("status", "")) in wanted for gate in manifest.get("certification_gates") or [])


def launch_category(manifest: dict[str, Any]) -> str:
    readiness = manifest.get("production_readiness", "")
    lane = manifest.get("automation_lane", "")
    if readiness == "production_ready_operator_supervised":
        return "fully_automated_sellable"
    if _has_gate_status(manifest, "requires_registered_agent_intermediary"):
        return "partner_intermediary_sellable"
    if lane == "operator_assisted_browser_provider" or _has_gate_status(manifest, "trusted_access_required", "login_required", "required"):
        return "trusted_access_operator_sellable"
    if _has_gate_status(manifest, "applicant_data_required"):
        return "customer_data_intake_sellable"
    if readiness == "certified_portal_entry_observed":
        return "operator_assisted_sellable"
    return "unavailable_pending_official_access"


def customer_offer_status(category: str) -> str:
    if category == "fully_automated_sellable":
        return "available_automated"
    if category in SELLABLE_CATEGORIES:
        return "available_operator_assisted"
    return "not_available"


def operator_sla_hours(category: str) -> int | None:
    if category == "fully_automated_sellable":
        return 1
    if category == "customer_data_intake_sellable":
        return 4
    if category == "operator_assisted_sellable":
        return 8
    if category in {"trusted_access_operator_sellable", "partner_intermediary_sellable"}:
        return 24
    return None


def application_handled_steps(category: str) -> list[str]:
    return BASE_APPLICATION_HANDLED_STEPS + CATEGORY_APPLICATION_STEPS.get(category, [])


def operator_exception_steps(category: str) -> list[str]:
    return CATEGORY_OPERATOR_EXCEPTION_STEPS.get(category, [
        "Review missing certification evidence and convert the exception into an application-handled workflow where permitted.",
    ])


def operator_touch_target_minutes(category: str) -> int:
    if category == "fully_automated_sellable":
        return 0
    if category == "customer_data_intake_sellable":
        return 5
    if category in {"trusted_access_operator_sellable", "partner_intermediary_sellable"}:
        return 10
    if category == "operator_assisted_sellable":
        return 15
    return 30


def customer_timeline(category: str, sla_hours: int | None) -> str:
    if category == "fully_automated_sellable":
        return "Automated preflight target: within 1 hour after payment and validation."
    if sla_hours is None:
        return "Timeline will be confirmed before checkout opens for this jurisdiction."
    if sla_hours <= 4:
        return f"Operator handoff target: within {sla_hours} business hours after complete intake."
    if sla_hours <= 8:
        return "Operator handoff target: same business day after complete intake."
    return "Operator handoff target: within 1 business day after complete intake."


def next_actions(manifest: dict[str, Any], category: str) -> list[str]:
    gate = next(
        (
            item
            for item in manifest.get("certification_gates") or []
            if item.get("status") not in {"complete"}
        ),
        None,
    )
    if category == "fully_automated_sellable":
        return [
            "Run payment-authorized production preflight.",
            "Submit from the certified operator-supervised adapter lane.",
            "Capture receipt, confirmation number, and filed document before customer-visible completion.",
        ]
    if category == "customer_data_intake_sellable":
        return [
            "Let the application collect and validate missing applicant/filer fields before portal execution.",
            "Review only customer-data ambiguity that cannot be resolved automatically.",
            "Resume automated execution after applicant data is complete.",
        ]
    if category == "trusted_access_operator_sellable":
        return [
            "Let the application execute portal navigation, prefilling, package upload, and review-screen comparison up to the protected gate.",
            "Operator resolves only authorized login, CAPTCHA, identity, payment, terms, or attestation checkpoints.",
            "Return control to automation for receipts, downloads, status updates, and dashboard messaging.",
        ]
    if category == "partner_intermediary_sellable":
        return [
            "Let the application generate the partner-ready filing package, quote request, and evidence checklist.",
            "Operator confirms partner availability, quote, and authority to file before payment capture or partner submission.",
            "Application tracks partner receipt and official state evidence before marking submitted or approved.",
        ]
    if category == "operator_assisted_sellable":
        return [
            "Let the application prepare the official filing package and execute known browser-assist steps.",
            "Operator resolves only uncategorized state-specific exceptions.",
            "Convert first-live-filing evidence into a stricter adapter contract for future automation.",
        ]
    message = "Complete missing state automation profile and official-source evidence."
    if gate:
        message = f"Complete certification gate: {gate.get('label', gate.get('code', 'unknown gate'))}."
    return [message]


def lifecycle_support(manifest: dict[str, Any]) -> dict[str, str]:
    counts = manifest.get("record_counts") or {}
    support: dict[str, str] = {}
    for workflow in LIFECYCLE_WORKFLOWS:
        if workflow == "compliance_reminder":
            support[workflow] = "supported" if counts.get("annual_obligation") else "operator_research_required"
        elif workflow == "certificate_status":
            support[workflow] = "operator_assisted" if counts.get("other_license") or counts.get("formation") else "operator_research_required"
        elif workflow == "certified_copy":
            support[workflow] = "operator_assisted" if counts.get("formation") else "operator_research_required"
        elif workflow == "withdrawal":
            support[workflow] = "operator_assisted" if counts.get("foreign_authority") or counts.get("dissolution") else "operator_research_required"
        else:
            support[workflow] = "operator_assisted" if counts.get(workflow) else "operator_research_required"
    return support


def public_lifecycle_summary(lifecycle: dict[str, str]) -> list[dict[str, str]]:
    visible: list[dict[str, str]] = []
    for workflow in LIFECYCLE_WORKFLOWS:
        status = lifecycle.get(workflow, "operator_research_required")
        if status == "operator_research_required":
            continue
        visible.append({
            "code": workflow,
            "label": LIFECYCLE_PUBLIC_LABELS.get(workflow, workflow.replace("_", " ").title()),
            "status": "operator_assisted" if status == "operator_assisted" else "supported",
        })
    return visible


def pricing_for_state(state: str, state_fees: dict[str, Any]) -> dict[str, Any]:
    fee_record = ((state_fees.get("LLC") or {}).get(state) or {})
    government_fee = int(round(float(fee_record.get("filing_fee", 0)) * 100))
    total = government_fee + SOSFILER_PLATFORM_FEE_CENTS
    return {
        "currency": "usd",
        "official_government_fee_cents": government_fee,
        "sosfiler_fee_cents": SOSFILER_PLATFORM_FEE_CENTS,
        "estimated_total_cents": total,
        "pricing_status": "published" if government_fee > 0 else "operator_quote_required",
        "notes": "Final payment capture must reconcile official fee, expedite, partner, and portal processing costs before live submission.",
    }


def public_launch_readiness_state(item: dict[str, Any]) -> dict[str, Any]:
    category = item.get("launch_category", "unavailable_pending_official_access")
    copy = PUBLIC_CATEGORY_COPY.get(category, PUBLIC_CATEGORY_COPY["unavailable_pending_official_access"])
    sla_hours = item.get("operator_sla_hours")
    return {
        "state": item.get("state", ""),
        "state_name": item.get("state_name", item.get("state", "")),
        "sellable_now": bool(item.get("sellable_now")),
        "customer_offer_status": item.get("customer_offer_status", "not_available"),
        "fulfillment_lane": copy["fulfillment_lane"],
        "headline": copy["headline"],
        "message": copy["message"],
        "timeline": customer_timeline(str(category), sla_hours if isinstance(sla_hours, int) else None),
        "operator_sla_hours": sla_hours if isinstance(sla_hours, int) else None,
        "automation_scope": {
            "application_handles": [
                "formation package preparation",
                "permitted filing execution",
                "official fee calculation",
                "receipt and document ingestion",
                "status timeline updates",
            ],
            "operator_only_for": "Protected official checkpoints or partner exceptions when required.",
        },
        "pricing": {
            "currency": item.get("pricing", {}).get("currency", "usd"),
            "official_government_fee_cents": item.get("pricing", {}).get("official_government_fee_cents", 0),
            "sosfiler_fee_cents": item.get("pricing", {}).get("sosfiler_fee_cents", SOSFILER_PLATFORM_FEE_CENTS),
            "estimated_total_cents": item.get("pricing", {}).get("estimated_total_cents", SOSFILER_PLATFORM_FEE_CENTS),
            "pricing_status": item.get("pricing", {}).get("pricing_status", "operator_quote_required"),
        },
        "post_formation_workflows": public_lifecycle_summary(item.get("lifecycle_support", {})),
    }


def build_public_launch_readiness_report(report: dict[str, Any] | None = None) -> dict[str, Any]:
    report = report or build_launch_readiness_report()
    states = [public_launch_readiness_state(item) for item in report.get("states", [])]
    automated = sum(1 for item in states if item["customer_offer_status"] == "available_automated")
    operator_assisted = sum(1 for item in states if item["customer_offer_status"] == "available_operator_assisted")
    return {
        "version": report.get("version", 1),
        "generated_at": report.get("generated_at", utc_now()),
        "summary": {
            "total_jurisdictions": len(states),
            "sellable_now": sum(1 for item in states if item["sellable_now"]),
            "available_automated": automated,
            "available_operator_assisted": operator_assisted,
            "not_available": sum(1 for item in states if not item["sellable_now"]),
        },
        "states": states,
    }


def public_launch_readiness_for_state(state: str, report: dict[str, Any] | None = None) -> dict[str, Any] | None:
    state = (state or "").upper()
    public_report = build_public_launch_readiness_report(report)
    for item in public_report["states"]:
        if item["state"] == state:
            return item
    return None


def build_launch_readiness_report(
    manifests: list[dict[str, Any]] | None = None,
    state_fees: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifests = manifests or all_state_adapter_manifests()
    state_fees = state_fees or load_json(STATE_FEES_PATH, {})
    states: list[dict[str, Any]] = []
    for manifest in sorted(manifests, key=lambda item: item.get("state", "")):
        state = str(manifest.get("state", "")).upper()
        category = launch_category(manifest)
        gates = _gate_statuses(manifest)
        states.append({
            "state": state,
            "state_name": manifest.get("state_name", state),
            "launch_category": category,
            "customer_offer_status": customer_offer_status(category),
            "sellable_now": category in SELLABLE_CATEGORIES,
            "operator_sla_hours": operator_sla_hours(category),
            "automation_lane": manifest.get("automation_lane", ""),
            "automation_difficulty": manifest.get("automation_difficulty", ""),
            "production_readiness": manifest.get("production_readiness", ""),
            "portal_urls": manifest.get("portal_urls", []),
            "evidence_urls": manifest.get("evidence_urls", []),
            "certification_gate_statuses": gates,
            "next_actions": next_actions(manifest, category),
            "automation_first_policy": AUTOMATION_FIRST_POLICY["operator_touch_model"],
            "operator_touch_target_minutes": operator_touch_target_minutes(category),
            "application_handled_steps": application_handled_steps(category),
            "operator_exception_steps": operator_exception_steps(category),
            "blockers": manifest.get("blockers", []),
            "pricing": pricing_for_state(state, state_fees),
            "lifecycle_support": lifecycle_support(manifest),
        })
    by_category = Counter(item["launch_category"] for item in states)
    by_offer = Counter(item["customer_offer_status"] for item in states)
    sellable_states = [item for item in states if item["sellable_now"]]
    return {
        "version": 1,
        "generated_at": utc_now(),
        "objective": "Market-ready SOSFiler formation and post-formation automation coverage for all U.S. states and D.C.",
        "summary": {
            "total_jurisdictions": len(states),
            "sellable_now": len(sellable_states),
            "not_available": len(states) - len(sellable_states),
            "by_launch_category": dict(sorted(by_category.items())),
            "by_customer_offer_status": dict(sorted(by_offer.items())),
        },
        "enterprise_controls": [
            "No bypassing access controls, CAPTCHAs, rate limits, payment systems, identity checks, attestations, or terms restrictions.",
            "No CAPTCHA solving or click-service outsourcing; protected challenges route only to authorized human or official access paths.",
            "Every filing has dry-run preflight, evidence requirements, safe-stop gates, and operator handoff metadata.",
            "Receipts, confirmation numbers, documents, payments, and operator actions must be persisted before customer-visible completion.",
            "PII and secrets must be redacted from logs, tests, screenshots, and reports.",
        ],
        "automation_first_policy": AUTOMATION_FIRST_POLICY,
        "states": states,
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# SOSFiler Launch Readiness",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Total jurisdictions: {summary['total_jurisdictions']}",
        f"- Sellable now: {summary['sellable_now']}",
        f"- Not available: {summary['not_available']}",
        "",
        "## Launch Categories",
        "",
    ]
    for category, count in summary["by_launch_category"].items():
        lines.append(f"- {category}: {count}")
    lines.extend([
        "",
        "## State Rollout",
        "",
        "| State | Category | Offer | SLA | Price Status | Next Action |",
        "|---|---|---|---:|---|---|",
    ])
    for item in report["states"]:
        sla = "" if item["operator_sla_hours"] is None else str(item["operator_sla_hours"])
        next_action = item["next_actions"][0].replace("|", "\\|") if item["next_actions"] else ""
        lines.append(
            f"| {item['state']} | {item['launch_category']} | {item['customer_offer_status']} | "
            f"{sla} | {item['pricing']['pricing_status']} | {next_action} |"
        )
    lines.extend([
        "",
        "## Automation-First Fulfillment",
        "",
        "The application is the filing engine. Operators handle exceptions only.",
        "",
        "### Application Handles",
        "",
    ])
    for step in BASE_APPLICATION_HANDLED_STEPS:
        lines.append(f"- {step}")
    lines.extend([
        "",
        "### Operator Exceptions",
        "",
        "- Customer-data-intake states: operator reviews only escalated ambiguity; the application collects missing customer fields and resumes execution automatically.",
        "- Trusted-access states: operator uses a verified profile only for authorized login, CAPTCHA, identity, payment, terms, or attestation checkpoints, then returns control to automation.",
        "- Partner/intermediary states: operator confirms partner authority, quote, and exceptions; the application handles package generation, tracking, receipt ingestion, and evidence reconciliation.",
        "- No CAPTCHA solving or click-service outsourcing. CAPTCHA is a protected gate, not a task for third-party circumvention.",
        "",
        "### Automation Target",
        "",
        "- Review: application validates data, fees, filing readiness, documents, and state-specific rules before queueing.",
        "- Execution: application performs permitted browser/API steps, prefilling, package upload, review-screen comparison, safe stops, and resume.",
        "- Downloads: application captures receipts, confirmations, screenshots, filed documents, and document-vault records.",
        "- Payment: application captures SOSFiler payment, prepares state payment, reconciles official fees, and submits state payment only where the official channel permits automation.",
        "",
        "## Market Positioning",
        "",
        "- Sell all jurisdictions with clear fulfillment expectations: automated where certified, operator-assisted where protected, and partner-assisted where required.",
        "- Compete on transparent pricing, reliable evidence capture, fast operator handoffs, and lifecycle management after formation.",
        "- Do not claim unsupported full automation for protected portals; use accurate customer-facing language and fast fulfillment instead.",
        "",
        "## Customer-Facing Fulfillment",
        "",
        "- `/api/formation-availability` exposes sanitized availability, pricing, fulfillment lane, SLA, timeline, and post-formation support by state.",
        "- The formation wizard shows official fee, SOSFiler fee, estimated total, fulfillment lane, handoff target, and lifecycle support before checkout.",
        "- Internal portal URLs, blockers, certification gates, protected-gate details, and operator-only next actions remain admin-only.",
    ])
    lines.extend(["", "## Enterprise Controls", ""])
    for control in report["enterprise_controls"]:
        lines.append(f"- {control}")
    return "\n".join(lines) + "\n"


def write_launch_readiness_report(
    json_path: Path = REPORT_JSON_PATH,
    markdown_path: Path = REPORT_MD_PATH,
) -> dict[str, Any]:
    report = build_launch_readiness_report()
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(render_markdown_report(report))
    return report


def main() -> int:
    report = write_launch_readiness_report()
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
