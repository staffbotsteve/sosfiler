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
from pathlib import Path
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


@dataclass(frozen=True)
class SelectorContractEntry:
    """A single declared selector the adapter relies on at runtime.

    Plan v2.6 §4.3 + §4.5. The drift canary reads each adapter's
    selectors_contract and asserts at runtime that the live portal still
    matches. Track B (docs/track_b_adapter_audit_2026-05-16.md) carries
    the inventory; this dataclass is the on-disk shape adapters use to
    declare it.
    """

    page: str          # logical page label, e.g. "login", "payment", "receipt"
    selector: str      # CSS / role / XPath — matches the production call
    purpose: str       # short imperative: "fill credentials", "click submit"
    interaction: str = "click"   # one of: click, fill, assert, extract, check
    required: bool = True        # canary fails the adapter when missing if True
    fail_behavior: str = "raise" # one of: raise, graceful_skip, soft_fail


class FilingAdapter(ABC):
    """Contract every state/product automation lane must implement."""

    lane = "operator_assisted"

    # Plan v2.6 §4.3 / Track B: each adapter declares its selector contract
    # so the drift canary can assert the live portal still matches. Empty
    # tuple by default (e.g. for operator-assisted and dry-run adapters
    # that drive no portal). Live adapters override.
    selectors_contract: tuple[SelectorContractEntry, ...] = ()

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


# Plan v2.6 §4.2.2 / PR4 — canonical filing_artifacts write helpers shared by
# the server module and any worker. Workers pass absolute file paths (they own
# their own receipts directories); the server passes paths that get resolved
# via resolve_document_path() before hashing. Keeping the helpers here avoids
# a circular import between server.py and the workers.


_CONFIRMATION_ALLOWED_SOURCES = {"regex", "operator", "adapter"}


def build_filing_confirmation_payload(value: str, source: str, issued_at: str | None = None) -> str:
    """Build the canonical JSON shape stored in orders.filing_confirmation.

    Plan v2.6 §4.2.5. Required shape:
    `{"value":"<state-issued #>","issued_at":"<ISO8601>","source":"regex|operator|adapter"}`.
    `value` must be a non-empty string. The DB trigger rejects malformed shapes.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("filing_confirmation value must be a non-empty string")
    if source not in _CONFIRMATION_ALLOWED_SOURCES:
        raise ValueError(f"filing_confirmation source must be one of {sorted(_CONFIRMATION_ALLOWED_SOURCES)}")
    return json.dumps({
        "value": value.strip(),
        "issued_at": issued_at or datetime.now(timezone.utc).isoformat(),
        "source": source,
    })


def read_filing_confirmation_value(raw: str | None) -> str | None:
    """Extract `$.value` from the canonical JSON shape; tolerate legacy null/empty."""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict):
        value = parsed.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_filing_confirmation(text: str | None, pattern: str | None) -> str | None:
    """Run a state-specific regex against captured portal text and return the
    first capture group (or full match) as the confirmation number.

    Plan v2.6 §4.5 — each adapter declares confirmation_number_regex and runs
    it against the receipt-page DOM or the captured receipt PDF text. Returns
    None when text or pattern is empty, or when the regex does not match.
    """
    if not text or not pattern:
        return None
    try:
        match = re.search(pattern, text)
    except re.error:
        return None
    if not match:
        return None
    groups = match.groups()
    return (groups[0] if groups else match.group(0)).strip() or None


def sha256_for_file_path(file_path: str | Path | None) -> str | None:
    """Stream a SHA-256 digest from an absolute path. None when missing/unreadable."""
    if not file_path:
        return None
    try:
        with open(file_path, "rb") as fh:
            digest = hashlib.sha256()
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except (OSError, ValueError):
        return None


def escalate_to_operator_required(
    conn,
    *,
    filing_job_id: str,
    order_id: str,
    source: str,
    error_message: str,
    evidence_path: str = "",
) -> None:
    """Track B follow-up #2: catch-all path for worker `except Exception`.

    Audit doc recommendation #2 — CA, NV, TX workers used to log an event
    and append `status='error'` to a results list without updating
    filing_jobs.status / orders.status. The cockpit never surfaced the
    failure for operator review.

    This helper:
      1. INSERTs a `<source>_worker_error` event with the optional
         evidence_path so operators can navigate straight to the
         captured screenshot/html.
      2. When evidence_path resolves to a real file, also INSERTs a
         state_correspondence `filing_artifacts` row (with sha256_hex)
         so the cockpit detail view surfaces the checkpoint.
      3. UPDATEs filing_jobs.status + orders.status to `operator_required`
         (a non-terminal status — the PR7 evidence trigger never fires on
         this UPDATE because the target is not in the evidence set).
      4. Writes a status_updates row so the customer dashboard timeline
         reflects the escalation.

    Best-effort: any sqlite3.Error inside the function is swallowed; the
    worker has already failed and we don't want this helper to mask the
    original exception in the caller's try/except.
    """
    import sqlite3 as _sqlite3
    if not (conn and filing_job_id and order_id):
        return
    safe_message = (error_message or f"{source} worker raised unhandled exception").strip()
    safe_event_type = f"{source}_worker_error" if source else "worker_error"
    safe_evidence_path = (evidence_path or "").strip()
    try:
        conn.execute(
            """
            INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (filing_job_id, order_id, safe_event_type, safe_message, source or "worker", safe_evidence_path),
        )
        # Track B follow-up #3 codex round-2 P2: persist the captured
        # checkpoint screenshot/html as a state_correspondence artifact
        # so the operator cockpit's detail view has something to inspect.
        if safe_evidence_path:
            try:
                resolved = Path(safe_evidence_path)
                if resolved.exists():
                    digest = sha256_for_file_path(safe_evidence_path)
                    existing = conn.execute(
                        "SELECT 1 FROM filing_artifacts WHERE filing_job_id = ? AND filename = ? AND artifact_type = ? LIMIT 1",
                        (filing_job_id, resolved.name, "state_correspondence"),
                    ).fetchone()
                    if not existing:
                        insert_filing_artifact_row(
                            conn,
                            filing_job_id=filing_job_id,
                            order_id=order_id,
                            artifact_type="state_correspondence",
                            filename=resolved.name,
                            file_path=safe_evidence_path,
                            is_evidence=True,
                            sha256_hex=digest,
                        )
            except (OSError, ValueError):
                pass
        conn.execute(
            "UPDATE filing_jobs SET status = 'operator_required', evidence_summary = ?, updated_at = datetime('now') WHERE id = ?",
            (safe_message, filing_job_id),
        )
        conn.execute(
            "UPDATE orders SET status = 'operator_required', updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )
        conn.execute(
            "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'operator_required', ?)",
            (order_id, safe_message),
        )
    except _sqlite3.Error:
        # Worker is already in a failed state; do not raise from the
        # escalation helper. Callers see the original exception traceback.
        pass


def insert_filing_artifact_row(
    conn,
    *,
    filing_job_id: str,
    order_id: str,
    artifact_type: str,
    filename: str,
    file_path: str,
    is_evidence: bool | int,
    sha256_hex: str | None = None,
) -> int:
    """Canonical SQLite INSERT for filing_artifacts.

    Callers that have the absolute path should pre-compute `sha256_hex` (the
    server module owns path resolution via resolve_document_path; workers
    already operate on absolute paths). Dual-write to the Supabase mirror is
    NOT performed here — see server.insert_filing_artifact() for the wrapper
    that fans out via execution_dual_write.
    """
    is_evidence_int = 1 if is_evidence else 0
    cursor = conn.execute(
        """
        INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence, sha256_hex)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence_int, sha256_hex),
    )
    return cursor.lastrowid or 0
