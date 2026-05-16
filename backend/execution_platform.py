"""
Shared execution-platform primitives for SOSFiler.

This module is deliberately framework-light so workers, tests, and the FastAPI
server can share the same state machine and safety rules.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


UNIVERSAL_FILING_STATES = (
    "intake_complete",
    "ready_to_file",
    "automation_started",
    "operator_required",
    "payment_required",
    "submitted",
    "pending_government_review",
    "rejected_or_needs_correction",
    "approved",
    "documents_collected",
    "customer_notified",
    "complete",
    "needs_evidence_reverification",
    "closed_unfiled",
)

LEGACY_STATUS_ALIASES = {
    "pending_payment": "payment_required",
    "paid": "intake_complete",
    "preparing": "intake_complete",
    "generating_documents": "intake_complete",
    "ready_to_file": "ready_to_file",
    "submitted_to_state": "submitted",
    "state_approved": "approved",
    "ein_pending": "pending_government_review",
    "ein_queued": "pending_government_review",
    "ein_received": "documents_collected",
    "human_review": "operator_required",
    "manual_review": "operator_required",
    "error": "operator_required",
}

ALLOWED_TRANSITIONS = {
    "intake_complete": {"payment_required", "ready_to_file", "operator_required"},
    "payment_required": {"intake_complete", "operator_required"},
    "ready_to_file": {"automation_started", "operator_required", "submitted"},
    "automation_started": {"payment_required", "submitted", "operator_required", "rejected_or_needs_correction"},
    "operator_required": {"ready_to_file", "automation_started", "submitted", "pending_government_review", "rejected_or_needs_correction", "needs_evidence_reverification", "closed_unfiled"},
    "submitted": {"pending_government_review", "rejected_or_needs_correction", "approved", "operator_required", "needs_evidence_reverification"},
    "pending_government_review": {"approved", "rejected_or_needs_correction", "operator_required", "needs_evidence_reverification"},
    "rejected_or_needs_correction": {"ready_to_file", "operator_required", "closed_unfiled"},
    "approved": {"documents_collected", "operator_required", "needs_evidence_reverification"},
    "documents_collected": {"customer_notified", "complete", "needs_evidence_reverification"},
    "customer_notified": {"complete", "needs_evidence_reverification"},
    "complete": set(),
    "needs_evidence_reverification": {"submitted", "approved", "complete", "closed_unfiled", "operator_required"},
    "closed_unfiled": set(),
}

EVIDENCE_REQUIRED_STATES = {"submitted", "approved", "documents_collected", "complete"}
SENSITIVE_KEYS = {
    "ssn",
    "ssn_itin",
    "responsible_party_ssn",
    "ein",
    "tax_id",
    "password",
    "secret",
    "token",
}


@dataclass(frozen=True)
class TransitionDecision:
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class FilingActionResult:
    status: str
    message: str = ""
    evidence_path: str = ""
    raw_status: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class FilingAdapter(ABC):
    """Contract every state/product automation lane must implement."""

    lane = "operator_assisted"

    @abstractmethod
    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        raise NotImplementedError

    @abstractmethod
    async def submit(self, filing_job: dict[str, Any]) -> FilingActionResult:
        raise NotImplementedError

    @abstractmethod
    async def check_status(self, filing_job: dict[str, Any]) -> FilingActionResult:
        raise NotImplementedError

    @abstractmethod
    async def collect_documents(self, filing_job: dict[str, Any]) -> FilingActionResult:
        raise NotImplementedError

    @abstractmethod
    def requires_operator(self, filing_job: dict[str, Any]) -> bool:
        raise NotImplementedError


class OperatorAssistedAdapter(FilingAdapter):
    lane = "operator_assisted"

    async def preflight(self, filing_job: dict[str, Any]) -> FilingActionResult:
        return FilingActionResult(
            status="operator_required",
            message="Automation lane requires operator verification before submission.",
        )

    async def submit(self, filing_job: dict[str, Any]) -> FilingActionResult:
        return FilingActionResult(
            status="operator_required",
            message="Operator must submit through the official filing channel.",
        )

    async def check_status(self, filing_job: dict[str, Any]) -> FilingActionResult:
        return FilingActionResult(
            status="operator_required",
            message="Operator must check the official portal/status source.",
        )

    async def collect_documents(self, filing_job: dict[str, Any]) -> FilingActionResult:
        return FilingActionResult(
            status="operator_required",
            message="Operator must attach official government evidence.",
        )

    def requires_operator(self, filing_job: dict[str, Any]) -> bool:
        return True


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_state(status: str) -> str:
    return LEGACY_STATUS_ALIASES.get(status, status)


def validate_transition(current_status: str, target_status: str, evidence_path: str = "") -> TransitionDecision:
    current = normalize_state(current_status)
    target = normalize_state(target_status)
    if target not in UNIVERSAL_FILING_STATES:
        return TransitionDecision(False, f"Unknown target state: {target_status}")
    if target in EVIDENCE_REQUIRED_STATES and not evidence_path:
        return TransitionDecision(False, f"{target} requires official evidence before it can be recorded")
    if current == target:
        return TransitionDecision(True)
    if current not in UNIVERSAL_FILING_STATES:
        return TransitionDecision(False, f"Unknown current state: {current_status}")
    if target not in ALLOWED_TRANSITIONS[current]:
        return TransitionDecision(False, f"Cannot transition from {current} to {target}")
    return TransitionDecision(True)


def build_idempotency_key(*parts: Any) -> str:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return digest[:32]


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in SENSITIVE_KEYS or any(token in lowered for token in ("ssn", "ein", "password", "secret")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\b\d{3}-?\d{2}-?\d{4}\b", "[REDACTED_SSN]", value)
        value = re.sub(r"\b\d{2}-?\d{7}\b", "[REDACTED_EIN]", value)
    return value


def pii_fingerprint(value: str) -> str:
    pepper = os.getenv("PII_FINGERPRINT_PEPPER") or os.getenv("JWT_SECRET") or "sosfiler-dev"
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def _encryption_key() -> bytes:
    raw = os.getenv("PII_ENCRYPTION_KEY") or os.getenv("JWT_SECRET")
    if not raw:
        raise RuntimeError("PII_ENCRYPTION_KEY or JWT_SECRET is required for encrypted PII storage")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_pii(plaintext: str) -> str:
    """
    Encrypt a sensitive value for local queue compatibility.

    Production should prefer KMS or pgcrypto; this keeps plaintext out of
    SQLite/files during the migration period and authenticates ciphertext with
    HMAC so tampering is detected before decryption.
    """
    if plaintext is None:
        plaintext = ""
    key = _encryption_key()
    nonce = secrets.token_bytes(16)
    stream = hashlib.blake2b(nonce, key=key, digest_size=64).digest()
    plain = plaintext.encode("utf-8")
    while len(stream) < len(plain):
        stream += hashlib.blake2b(stream[-16:], key=key, digest_size=64).digest()
    cipher = bytes(a ^ b for a, b in zip(plain, stream))
    mac = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + mac + cipher).decode("ascii")


def decrypt_pii(token: str) -> str:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    nonce, mac, cipher = raw[:16], raw[16:48], raw[48:]
    key = _encryption_key()
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("Encrypted PII failed integrity check")
    stream = hashlib.blake2b(nonce, key=key, digest_size=64).digest()
    while len(stream) < len(cipher):
        stream += hashlib.blake2b(stream[-16:], key=key, digest_size=64).digest()
    plain = bytes(a ^ b for a, b in zip(cipher, stream))
    return plain.decode("utf-8")


def build_quote(
    *,
    product_type: str,
    entity_type: str,
    state: str,
    platform_fee_cents: int,
    government_fee_cents: int,
    processing_fee_cents: int = 0,
    registered_agent_fee_cents: int = 0,
    expedite_fee_cents: int = 0,
) -> dict[str, Any]:
    line_items = [
        {"code": "platform_fee", "label": "SOSFiler service fee", "amount_cents": platform_fee_cents, "kind": "revenue"},
        {"code": "government_fee", "label": "Government filing fee estimate", "amount_cents": government_fee_cents, "kind": "passthrough"},
    ]
    if processing_fee_cents:
        line_items.append({"code": "processing_fee", "label": "Government portal processing fee estimate", "amount_cents": processing_fee_cents, "kind": "passthrough"})
    if registered_agent_fee_cents:
        line_items.append({"code": "registered_agent", "label": "Registered agent partner fee", "amount_cents": registered_agent_fee_cents, "kind": "partner"})
    if expedite_fee_cents:
        line_items.append({"code": "expedite", "label": "Expedited processing estimate", "amount_cents": expedite_fee_cents, "kind": "passthrough"})
    total = sum(item["amount_cents"] for item in line_items)
    return {
        "quote_id": f"Q-{secrets.token_hex(8).upper()}",
        "product_type": product_type,
        "entity_type": entity_type,
        "state": state.upper(),
        "currency": "usd",
        "line_items": line_items,
        "estimated_total_cents": total,
        "capture_strategy": "authorize_then_capture",
        "idempotency_key": build_idempotency_key(product_type, entity_type, state, total),
        "created_at": utc_now(),
    }


def should_escalate_chat(message: str, verified_context: dict[str, Any] | None) -> tuple[bool, str]:
    if not verified_context:
        return True, "No verified SOSFiler/state context was available for this question."
    lowered = message.lower()
    supported_terms = (
        "formation",
        "llc",
        "corp",
        "nonprofit",
        "ein",
        "registered agent",
        "state fee",
        "filing fee",
        "license",
        "dba",
        "annual report",
        "compliance",
    )
    if not any(term in lowered for term in supported_terms):
        return True, "Question is outside verified SOSFiler filing topics."
    return False, ""


def format_slack_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    admin_url = os.getenv("SOSFILER_OPERATOR_COCKPIT_URL") or os.getenv("DASHBOARD_URL", "").replace("dashboard.html", "operator.html")
    interactive_enabled = (os.getenv("SLACK_INTERACTIVE_TICKETS") or "").lower() in {"1", "true", "yes"}
    text = (
        f"*SOSFiler ticket {ticket.get('id')}*\n"
        f"*Type:* {ticket.get('ticket_type', 'support')}\n"
        f"*Status:* {ticket.get('status', 'open')}\n"
        f"*Priority:* {ticket.get('priority', 'normal')}\n"
        f"*Order/Session:* {ticket.get('order_id', '')} {ticket.get('session_id', '')}\n"
        f"*State/Product:* {ticket.get('state', '')} {ticket.get('product_type', '')}\n"
        f"*Question:* {ticket.get('question', '')}\n"
        f"*Reason:* {ticket.get('confidence_reason', '')}\n"
        f"*Suggested:* {ticket.get('suggested_answer', '')}"
    )
    if admin_url:
        text += f"\n*Review:* {admin_url}"
    payload: dict[str, Any] = {"text": text}
    if interactive_enabled:
        fields = [
            {"type": "mrkdwn", "text": f"*Type:*\n{ticket.get('ticket_type', 'support')}"},
            {"type": "mrkdwn", "text": f"*Priority:*\n{ticket.get('priority', 'normal')}"},
            {"type": "mrkdwn", "text": f"*Order:*\n{ticket.get('order_id') or 'None'}"},
            {"type": "mrkdwn", "text": f"*State/Product:*\n{ticket.get('state') or 'NA'} {ticket.get('product_type') or 'support'}"},
        ]
        payload["blocks"] = [
            {"type": "header", "text": {"type": "plain_text", "text": f"SOSFiler ticket {ticket.get('id')}", "emoji": False}},
            {"type": "section", "fields": fields},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Question:*\n{ticket.get('question', '')}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Reason:*\n{ticket.get('confidence_reason', '')}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Suggested:*\n{ticket.get('suggested_answer', '') or 'Operator review needed.'}"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve enhancement", "emoji": False},
                        "style": "primary",
                        "action_id": "approve_enhancement",
                        "value": str(ticket.get("id", "")),
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Close ticket", "emoji": False},
                        "action_id": "close_ticket",
                        "value": str(ticket.get("id", "")),
                    },
                ],
            },
        ]
        if admin_url:
            payload["blocks"].insert(
                -1,
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"<{admin_url}|Open operator cockpit>"},
                },
            )
    return payload


def send_slack_ticket(ticket: dict[str, Any]) -> bool:
    webhook = os.getenv("SLACK_TICKETS_WEBHOOK_URL")
    if not webhook:
        return False
    payload = json.dumps(format_slack_ticket(redact_sensitive(ticket))).encode("utf-8")
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return 200 <= resp.status < 300
