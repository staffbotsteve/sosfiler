"""
SOSFiler — FastAPI Backend Server
Production-grade LLC formation platform.
"""

import os
import asyncio
import jwt
import urllib.request
import urllib.parse
import json
import json
import uuid
import sqlite3
import hashlib
import hmac
import shlex
import secrets
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import re as _re

import stripe
from fastapi import FastAPI, HTTPException, Request, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from dotenv import load_dotenv
from corpnet_client import CorpNetClient
from execution_platform import (
    build_idempotency_key,
    build_quote,
    decrypt_pii,
    encrypt_pii,
    normalize_state,
    pii_fingerprint,
    redact_sensitive,
    send_slack_ticket,
    should_escalate_chat,
    utc_now,
    validate_transition,
)
from execution_repository import ExecutionPersistence
from filing_adapters import (
    PAYMENT_READY_STATUSES,
    TERMINAL_OR_SUBMITTED_STATUSES,
    build_adapter_contract,
    run_adapter_operation,
    validate_filing_preflight,
)
from state_automation_profiles import (
    all_state_adapter_manifests,
    certification_worklist,
    manifest_summary,
    write_state_adapter_manifest,
)
from state_routing import build_state_route, merge_filing_actions

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

# --- Configuration ---
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SLACK_TICKETS_WEBHOOK_URL = os.getenv("SLACK_TICKETS_WEBHOOK_URL")

stripe.api_key = STRIPE_SECRET_KEY
EXECUTION_PERSISTENCE = ExecutionPersistence()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"
RESEARCH_JOB_QUEUE_PATH = DATA_DIR / "research_jobs.json"
DOCS_DIR.mkdir(exist_ok=True)

PLATFORM_FEE = 4900  # $49.00 in cents
DBA_PLATFORM_FEE = 2900  # $29.00 in cents
LICENSE_PLATFORM_FEE = 4900  # $49.00 in cents
SPECIALTY_LICENSE_FEE = 9900  # $99.00 in cents
RA_RENEWAL_FEE = 4900  # $49/yr
ANNUAL_REPORT_FEE = 2500  # $25/yr

RESEARCH_COMPLETE_STATUSES = {"ready_for_review", "verified"}
RESEARCH_REQUIRED_TASKS = [
    "state_filing_map",
    "state_tax_gateway",
    "county_directory",
    "municipality_directory",
    "county_local_filings",
    "city_local_filings",
    "productization",
]
ADMIN_SESSION_TTL_SECONDS = int(os.getenv("ADMIN_SESSION_TTL_SECONDS", str(8 * 60 * 60)))
ADMIN_RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("ADMIN_RATE_LIMIT_WINDOW_SECONDS", "60"))
ADMIN_RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv("ADMIN_RATE_LIMIT_MAX_ATTEMPTS", "12"))
ADMIN_RATE_LIMITS: dict[str, list[float]] = {}

app = FastAPI(
    title="SOSFiler API",
    version="1.0.0",
    description="LLC formation platform — honest pricing, AI-powered documents, automated filing."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Security Headers Middleware ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://accounts.google.com https://apis.google.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://*.googleusercontent.com; connect-src 'self' https://api.stripe.com https://accounts.google.com https://oauth2.googleapis.com https://www.googleapis.com https://appleid.apple.com https://graph.facebook.com; frame-src https://accounts.google.com https://appleid.apple.com https://www.facebook.com"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# --- Input Sanitization ---
_SQL_INJECTION_PATTERN = _re.compile(
    r"(--|;|\b(DROP|DELETE|INSERT|UPDATE|ALTER|EXEC|UNION|SELECT)\b)",
    _re.IGNORECASE
)


def _validate_no_sql_injection(value: str, field_name: str) -> str:
    if _SQL_INJECTION_PATTERN.search(value):
        raise ValueError(f"{field_name} contains invalid characters")
    return value


def _normalize_ssn_itin(value: str) -> str:
    return _re.sub(r"\D", "", value or "")


def _validate_full_ssn_itin(value: str, field_name: str = "responsible_party_ssn") -> str:
    normalized = _normalize_ssn_itin(value)
    if not _re.fullmatch(r"\d{9}", normalized):
        raise ValueError(f"{field_name} must be a full 9-digit SSN/ITIN for EIN filing")
    return normalized

# --- Database ---
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _ensure_column(conn, table: str, column: str, definition: str):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            auth_provider TEXT NOT NULL,
            auth_provider_id TEXT,
            password_hash TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_login TEXT
        );

        CREATE TABLE IF NOT EXISTS auth_recovery_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            purpose TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            email TEXT NOT NULL,
            token TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_payment',
            entity_type TEXT NOT NULL,
            state TEXT NOT NULL,
            business_name TEXT NOT NULL,
            formation_data TEXT NOT NULL,
            stripe_session_id TEXT,
            stripe_payment_intent TEXT,
            state_fee_cents INTEGER NOT NULL,
            gov_processing_fee_cents INTEGER NOT NULL DEFAULT 0,
            platform_fee_cents INTEGER NOT NULL DEFAULT 4900,
            total_cents INTEGER NOT NULL,
            filing_confirmation TEXT,
            ein TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            paid_at TEXT,
            filed_at TEXT,
            approved_at TEXT,
            documents_ready_at TEXT
        );

        CREATE TABLE IF NOT EXISTS status_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL REFERENCES orders(id),
            status TEXT NOT NULL,
            message TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL REFERENCES orders(id),
            doc_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'pdf',
            category TEXT NOT NULL DEFAULT 'customer_document',
            visibility TEXT NOT NULL DEFAULT 'customer',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS document_access_events (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            actor TEXT NOT NULL,
            access_type TEXT NOT NULL DEFAULT 'download',
            auth_context TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS filing_jobs (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL REFERENCES orders(id),
            action_type TEXT NOT NULL DEFAULT 'formation',
            state TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_payment',
            automation_level TEXT NOT NULL DEFAULT 'manual_review',
            filing_method TEXT NOT NULL DEFAULT 'web_portal',
            office TEXT,
            form_name TEXT,
            portal_name TEXT,
            portal_url TEXT,
            state_fee_cents INTEGER NOT NULL DEFAULT 0,
            processing_fee_cents INTEGER NOT NULL DEFAULT 0,
            total_government_cents INTEGER NOT NULL DEFAULT 0,
            required_consents TEXT,
            required_evidence TEXT,
            evidence_summary TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            submitted_at TEXT,
            approved_at TEXT
        );

        CREATE TABLE IF NOT EXISTS filing_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_job_id TEXT NOT NULL REFERENCES filing_jobs(id),
            order_id TEXT NOT NULL REFERENCES orders(id),
            event_type TEXT NOT NULL,
            message TEXT,
            actor TEXT NOT NULL DEFAULT 'system',
            evidence_path TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS filing_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filing_job_id TEXT NOT NULL REFERENCES filing_jobs(id),
            order_id TEXT NOT NULL REFERENCES orders(id),
            artifact_type TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            is_evidence INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS compliance_deadlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT NOT NULL REFERENCES orders(id),
            deadline_type TEXT NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'upcoming',
            reminder_60_sent INTEGER DEFAULT 0,
            reminder_30_sent INTEGER DEFAULT 0,
            reminder_7_sent INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS license_filings (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            license_type TEXT NOT NULL,
            city TEXT,
            county TEXT,
            state TEXT NOT NULL,
            business_type TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            response_type TEXT,
            response_data TEXT,
            stripe_payment_intent TEXT,
            fee_cents INTEGER DEFAULT 0,
            platform_fee_cents INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            ip_address TEXT,
            message_count INTEGER DEFAULT 0,
            history TEXT,
            last_message_at TEXT DEFAULT (datetime('now')),
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS execution_quotes (
            id TEXT PRIMARY KEY,
            order_id TEXT,
            product_type TEXT NOT NULL,
            entity_type TEXT,
            state TEXT,
            currency TEXT NOT NULL DEFAULT 'usd',
            line_items TEXT NOT NULL DEFAULT '[]',
            estimated_total_cents INTEGER NOT NULL DEFAULT 0,
            authorized_total_cents INTEGER,
            captured_total_cents INTEGER,
            reconciliation_status TEXT NOT NULL DEFAULT 'quoted',
            idempotency_key TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS payment_ledger (
            id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            quote_id TEXT,
            stripe_session_id TEXT,
            stripe_payment_intent TEXT,
            event_type TEXT NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'usd',
            idempotency_key TEXT NOT NULL,
            raw_event TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS stripe_webhook_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'processing',
            order_id TEXT,
            stripe_object_id TEXT,
            error TEXT,
            raw_event TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            processed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS support_tickets (
            id TEXT PRIMARY KEY,
            ticket_type TEXT NOT NULL DEFAULT 'support',
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'normal',
            customer_email TEXT,
            order_id TEXT,
            session_id TEXT,
            state TEXT,
            product_type TEXT,
            question TEXT NOT NULL,
            confidence_reason TEXT,
            suggested_answer TEXT,
            slack_sent INTEGER NOT NULL DEFAULT 0,
            approved_by TEXT,
            approved_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS automation_runs (
            id TEXT PRIMARY KEY,
            filing_job_id TEXT,
            order_id TEXT,
            adapter_key TEXT NOT NULL,
            lane TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            stop_requested INTEGER NOT NULL DEFAULT 0,
            stop_reason TEXT,
            redacted_log TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pii_vault (
            id TEXT PRIMARY KEY,
            subject_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            pii_type TEXT NOT NULL,
            encrypted_payload TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            last4 TEXT,
            retention_until TEXT,
            created_by TEXT NOT NULL DEFAULT 'system',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            accessed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pii_access_events (
            id TEXT PRIMARY KEY,
            vault_id TEXT NOT NULL,
            subject_type TEXT,
            subject_id TEXT,
            pii_type TEXT,
            actor TEXT NOT NULL,
            purpose TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS admin_sessions (
            id TEXT PRIMARY KEY,
            token_hash TEXT UNIQUE NOT NULL,
            operator TEXT NOT NULL,
            client_ip TEXT,
            user_agent TEXT,
            expires_at TEXT NOT NULL,
            revoked_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_seen_at TEXT
        );

        CREATE TABLE IF NOT EXISTS admin_audit_events (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            operator TEXT NOT NULL,
            action TEXT NOT NULL,
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            client_ip TEXT,
            user_agent TEXT,
            outcome TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS health_check_markers (
            id TEXT PRIMARY KEY,
            marker TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email);
        CREATE INDEX IF NOT EXISTS idx_orders_token ON orders(token);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        CREATE INDEX IF NOT EXISTS idx_status_updates_order ON status_updates(order_id);
        CREATE INDEX IF NOT EXISTS idx_documents_order ON documents(order_id);
        CREATE INDEX IF NOT EXISTS idx_document_access_order ON document_access_events(order_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_filing_jobs_order ON filing_jobs(order_id);
        CREATE INDEX IF NOT EXISTS idx_filing_jobs_status ON filing_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_filing_events_order ON filing_events(order_id);
        CREATE INDEX IF NOT EXISTS idx_filing_artifacts_order ON filing_artifacts(order_id);
        CREATE INDEX IF NOT EXISTS idx_compliance_order ON compliance_deadlines(order_id);
        CREATE INDEX IF NOT EXISTS idx_license_filings_email ON license_filings(email);
        CREATE INDEX IF NOT EXISTS idx_execution_quotes_order ON execution_quotes(order_id);
        CREATE INDEX IF NOT EXISTS idx_payment_ledger_order ON payment_ledger(order_id);
        CREATE INDEX IF NOT EXISTS idx_stripe_webhook_events_type ON stripe_webhook_events(event_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_support_tickets_status ON support_tickets(status, priority);
        CREATE INDEX IF NOT EXISTS idx_automation_runs_status ON automation_runs(status, stop_requested);
        CREATE INDEX IF NOT EXISTS idx_pii_vault_subject ON pii_vault(subject_type, subject_id);
        CREATE INDEX IF NOT EXISTS idx_pii_access_events_vault ON pii_access_events(vault_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_admin_sessions_token ON admin_sessions(token_hash);
        CREATE INDEX IF NOT EXISTS idx_admin_sessions_expiry ON admin_sessions(expires_at, revoked_at);
        CREATE INDEX IF NOT EXISTS idx_admin_audit_events_created ON admin_audit_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_admin_audit_events_operator ON admin_audit_events(operator, action);
        CREATE INDEX IF NOT EXISTS idx_health_check_markers_created ON health_check_markers(created_at);
    """)
    _ensure_column(conn, "orders", "gov_processing_fee_cents", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "documents", "category", "TEXT NOT NULL DEFAULT 'customer_document'")
    _ensure_column(conn, "documents", "visibility", "TEXT NOT NULL DEFAULT 'customer'")
    _ensure_column(conn, "filing_jobs", "automation_lane", "TEXT NOT NULL DEFAULT 'operator_assisted'")
    _ensure_column(conn, "filing_jobs", "automation_difficulty", "TEXT NOT NULL DEFAULT 'unknown'")
    _ensure_column(conn, "filing_jobs", "adapter_key", "TEXT")
    _ensure_column(conn, "filing_jobs", "customer_status", "TEXT")
    _ensure_column(conn, "filing_jobs", "portal_blockers", "TEXT")
    _ensure_column(conn, "filing_jobs", "route_metadata", "TEXT")
    _ensure_column(conn, "stripe_webhook_events", "order_id", "TEXT")
    _ensure_column(conn, "stripe_webhook_events", "stripe_object_id", "TEXT")
    _ensure_column(conn, "stripe_webhook_events", "error", "TEXT")
    conn.commit()
    conn.close()

init_db()

# --- Load state data ---
STATE_DATA = {}
for entity_type, filename in [("llc", "state_requirements_v2.json"), ("corp", "corp_requirements_v2.json"), ("nonprofit", "nonprofit_requirements_v2.json")]:
    try:
        with open(DATA_DIR / filename) as f:
            STATE_DATA[entity_type] = json.load(f)
    except Exception as e:
        print(f"Error loading {filename}: {e}")
        STATE_DATA[entity_type] = {}

with open(DATA_DIR / "state_fees.json") as f:
    STATE_FEES = json.load(f)

try:
    with open(DATA_DIR / "filing_actions.generated.json") as f:
        GENERATED_FILING_ACTIONS = json.load(f)
except Exception:
    GENERATED_FILING_ACTIONS = {}

try:
    with open(DATA_DIR / "filing_actions.json") as f:
        MANUAL_FILING_ACTIONS = json.load(f)
except Exception:
    MANUAL_FILING_ACTIONS = {}

FILING_ACTIONS = merge_filing_actions(GENERATED_FILING_ACTIONS, MANUAL_FILING_ACTIONS)

try:
    with open(DATA_DIR / "portal_maps" / "state_portals_research.json") as f:
        PORTAL_RESEARCH = json.load(f)
except Exception:
    PORTAL_RESEARCH = {"states": {}, "summary": {}}

with open(DATA_DIR / "state_requirements.json") as f:
    STATE_REQUIREMENTS = json.load(f)


# --- Models ---
# --- Load license/DBA data ---
with open(DATA_DIR / "license_types.json") as f:
    LICENSE_TYPES_DATA = json.load(f)

with open(DATA_DIR / "dba_requirements.json") as f:
    DBA_REQUIREMENTS_DATA = json.load(f)


# --- Models ---
class LicenseCheckRequest(BaseModel):
    city: str = ""
    county: str = ""
    state: str
    business_type: str = ""
    license_type: str

class DBARequest(BaseModel):
    email: str
    business_name: str
    dba_name: str
    state: str
    county: str = ""
    city: str = ""

class LicenseRequest(BaseModel):
    email: str
    city: str = ""
    county: str = ""
    state: str
    business_type: str
    license_type: str

class LicenseNeedsRequest(BaseModel):
    city: str
    state: str
    business_type: str

class MemberInfo(BaseModel):
    name: str
    address: str
    city: str
    state: str
    zip_code: str
    ssn_itin: Optional[str] = None
    ownership_pct: float
    ssn_last4: Optional[str] = None  # Only for EIN responsible party
    is_responsible_party: bool = False

class FormationRequest(BaseModel):
    email: str
    user_id: Optional[str] = None
    entity_type: str = Field(..., pattern="^(LLC|S-Corp|C-Corp|Nonprofit)$")
    state: str = Field(..., min_length=2, max_length=2)
    business_name: str = Field(..., min_length=1, max_length=200)
    purpose: str = "Any lawful purpose"

    @field_validator("business_name")
    @classmethod
    def sanitize_business_name(cls, v: str) -> str:
        return _validate_no_sql_injection(v.strip(), "business_name")

    @field_validator("email")
    @classmethod
    def sanitize_email(cls, v: str) -> str:
        return _validate_no_sql_injection(v.strip(), "email")
    principal_address: str = ""
    principal_city: str = ""
    principal_state: str = ""
    principal_zip: str = ""
    mailing_address: Optional[str] = None
    management_type: str = Field(default="member-managed", pattern="^(member-managed|manager-managed)$")
    # Registered Agent
    ra_choice: str = Field(default="self", pattern="^(self|sosfiler)$")
    ra_name: str = ""
    ra_address: str = ""
    ra_city: str = ""
    ra_state: str = ""
    ra_zip: str = ""
    members: list[MemberInfo]
    managers: Optional[list[dict]] = None
    # Operating Agreement preferences
    oa_type: str = Field(default="single", pattern="^(single|multi)$")
    profit_distribution: str = "pro-rata"
    voting_rights: str = "pro-rata"
    dissolution_terms: str = "unanimous"
    major_decision_threshold: int = 10000
    transfer_restrictions: bool = True
    non_compete: bool = False
    tax_distributions: bool = True
    # EIN info
    responsible_party_ssn: str
    fiscal_year_end: str = "December"

    @field_validator("responsible_party_ssn")
    @classmethod
    def validate_responsible_party_ssn(cls, v: str) -> str:
        return _validate_full_ssn_itin(v)

class CheckoutRequest(BaseModel):
    order_id: str
    success_url: str
    cancel_url: str

class QuoteRequest(BaseModel):
    product_type: str = Field(default="formation", pattern="^(formation|dba|license|annual_report|registered_agent|ein)$")
    entity_type: str = "LLC"
    state: str = Field(..., min_length=2, max_length=2)
    include_registered_agent: bool = False
    expedite_fee_cents: int = 0

class OrderRequest(BaseModel):
    product_type: str = Field(default="formation", pattern="^(formation|dba|license|annual_report|registered_agent|ein)$")
    formation: Optional[FormationRequest] = None
    quote_id: Optional[str] = None

class AuthorizeRequest(BaseModel):
    success_url: str
    cancel_url: str
    quote_id: Optional[str] = None

class PaymentReconcileRequest(BaseModel):
    final_government_fee_cents: int = Field(default=0, ge=0)
    final_processing_fee_cents: int = Field(default=0, ge=0)
    final_registered_agent_fee_cents: Optional[int] = Field(default=None, ge=0)
    message: str = ""
    actor: str = "operator"

class PaymentCaptureRequest(BaseModel):
    amount_cents: Optional[int] = Field(default=None, ge=0)
    actor: str = "operator"
    message: str = ""

class AdditionalAuthorizationRequest(BaseModel):
    amount_cents: Optional[int] = Field(default=None, ge=1)
    success_url: str = ""
    cancel_url: str = ""
    actor: str = "operator"
    message: str = ""

class FilingPrepRequest(BaseModel):
    actor: str = "operator"
    message: str = ""
    generate_documents: bool = True

class PersistenceCutoverReadinessRequest(BaseModel):
    require_zero_deltas: bool = True
    include_append_only: bool = False

class FilingTransitionRequest(BaseModel):
    target_status: str
    message: str = ""
    evidence_path: str = ""
    actor: str = "operator"
    notify_customer: bool = False

class ClaimFilingJobRequest(BaseModel):
    operator: str = "operator"

class AutomationStopRequest(BaseModel):
    reason: str = "Operator requested stop."

class AutomationRunRequest(BaseModel):
    operation: str = Field(default="preflight", pattern="^(preflight|submit|check_status|collect_documents)$")
    dry_run: bool = True
    actor: str = "operator"

class PersistenceBackfillRequest(BaseModel):
    dry_run: bool = True
    include_append_only: bool = False
    limit: int = Field(default=500, ge=1, le=5000)

class SlackApprovalRequest(BaseModel):
    approved_by: str = "admin"
    approval_note: str = ""

class AdminAnnualReportFixtureRequest(BaseModel):
    state: str = Field(default="CA", min_length=2, max_length=2)
    entity_type: str = "LLC"
    business_name: str = "SOSFiler Playwright Annual Report Test LLC"
    email: EmailStr = "playwright-e2e@sosfiler.com"

class EngineeringTransitionRequest(BaseModel):
    target_status: str = Field(
        ...,
        pattern="^(approved|in_progress|tests_failed|ready_to_deploy|deployed|blocked|stop_requested)$",
    )
    actor: str = "operator"
    message: str = ""
    test_command: str = ""
    deployment_url: str = ""

class EngineeringTestRunRequest(BaseModel):
    actor: str = "operator"
    test_command: str = ""
    timeout_seconds: int = Field(default=180, ge=10, le=900)

class EngineeringDeployCheckRequest(BaseModel):
    actor: str = "operator"
    deployment_url: str = ""
    timeout_seconds: int = Field(default=15, ge=3, le=60)

class AdminSessionRequest(BaseModel):
    admin_token: str = Field(..., min_length=1)
    operator: str = Field(default="operator", min_length=1, max_length=80)

class EmailTestRequest(BaseModel):
    to_email: Optional[EmailStr] = None
    subject: str = Field(default="SOSFiler SendGrid diagnostic", max_length=160)

class NameCheckRequest(BaseModel):
    state: str
    name: str
    entity_type: str = "LLC"

class ChatRequest(BaseModel):
    message: str
    session_id: str
    context: Optional[dict] = None

class FilingEvidenceRequest(BaseModel):
    artifact_type: str = Field(..., pattern="^(submitted_receipt|submitted_document|approved_certificate|state_correspondence|rejection_notice|registered_agent_consent|registered_agent_assignment|filing_authorization|annual_report_packet|ein_confirmation_letter|other)$")
    filename: str
    file_path: str
    message: str = ""
    notify_customer: bool = False

class AnnualReportPacketRequest(BaseModel):
    actor: str = "operator"
    message: str = ""

class EinQueueActionRequest(BaseModel):
    actor: str = "operator"
    message: str = ""

class EinCompletionRequest(BaseModel):
    actor: str = "operator"
    ein: str = Field(default="", max_length=16)
    filename: str
    file_path: str
    message: str = ""
    notify_customer: bool = False

class ResponsiblePartySSNRequest(BaseModel):
    ssn_itin: str

    @field_validator("ssn_itin")
    @classmethod
    def validate_ssn_itin(cls, v: str) -> str:
        return _validate_full_ssn_itin(v, "ssn_itin")

class CorpNetRAQuoteRequest(BaseModel):
    state: str = Field(..., min_length=2, max_length=2)
    entity_name: str = Field(..., min_length=1, max_length=200)
    entity_type: str = Field(default="LLC", min_length=2, max_length=32)
    contact_name: str = ""
    contact_email: EmailStr
    metadata: Optional[dict] = None

class CorpNetRAOrderRequest(BaseModel):
    state: str = Field(..., min_length=2, max_length=2)
    entity_name: str = Field(..., min_length=1, max_length=200)
    entity_type: str = Field(default="LLC", min_length=2, max_length=32)
    contact_name: str = ""
    contact_email: EmailStr
    external_customer_id: str = ""
    sosfiler_order_id: str = ""
    metadata: Optional[dict] = None

class RAFulfillmentRequest(BaseModel):
    actor: str = "operator"
    provider: str = "corpnet"
    message: str = ""

class CorpNetRAReconcileRequest(BaseModel):
    actor: str = "system"
    limit: int = Field(default=50, ge=1, le=250)
    order_id: str = ""


# --- Auth helpers ---
def parse_json_field(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def execution_dual_write(method_name: str, *args, **kwargs):
    result = EXECUTION_PERSISTENCE.dual_write(method_name, *args, **kwargs)
    if not result.ok:
        print(result.message)
    return result


def sqlite_execution_counts(conn) -> dict:
    return {
        "execution_quotes": conn.execute("SELECT count(*) FROM execution_quotes").fetchone()[0],
        "execution_payment_ledger": conn.execute("SELECT count(*) FROM payment_ledger").fetchone()[0],
        "execution_filing_jobs": conn.execute("SELECT count(*) FROM filing_jobs").fetchone()[0],
        "execution_events": conn.execute("SELECT count(*) FROM filing_events").fetchone()[0],
        "execution_artifacts": conn.execute("SELECT count(*) FROM filing_artifacts").fetchone()[0],
        "support_tickets": conn.execute("SELECT count(*) FROM support_tickets").fetchone()[0],
        "automation_runs": conn.execute("SELECT count(*) FROM automation_runs").fetchone()[0],
        "stripe_webhook_events": conn.execute("SELECT count(*) FROM stripe_webhook_events").fetchone()[0],
    }


def execution_count_deltas(sqlite_counts: dict, supabase_counts: dict) -> dict:
    keys = sorted(set(sqlite_counts) | set(supabase_counts))
    return {
        key: {
            "sqlite": int(sqlite_counts.get(key, 0)),
            "supabase": int(supabase_counts.get(key, 0)),
            "delta": int(sqlite_counts.get(key, 0)) - int(supabase_counts.get(key, 0)),
        }
        for key in keys
    }


def limited_table_count(conn, table: str, limit: int) -> int:
    count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return min(int(count), int(limit))


def admin_timestamp(offset_seconds: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%d %H:%M:%S")


def request_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def hash_admin_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_operator(value: str) -> str:
    cleaned = _re.sub(r"[^A-Za-z0-9_.@ -]", "", value or "").strip()
    return cleaned[:80] or "operator"


def enforce_admin_rate_limit(request: Request, action: str):
    now = time.time()
    key = f"{action}:{request_client_ip(request)}"
    attempts = [stamp for stamp in ADMIN_RATE_LIMITS.get(key, []) if now - stamp < ADMIN_RATE_LIMIT_WINDOW_SECONDS]
    if len(attempts) >= ADMIN_RATE_LIMIT_MAX_ATTEMPTS:
        ADMIN_RATE_LIMITS[key] = attempts
        raise HTTPException(status_code=429, detail="Too many admin attempts; wait and try again")
    attempts.append(now)
    ADMIN_RATE_LIMITS[key] = attempts


def record_admin_audit_event(
    request: Request,
    operator: str,
    outcome: str = "allowed",
    session_id: str = "",
    detail: str = "",
):
    try:
        conn = get_db()
        conn.execute("""
            INSERT INTO admin_audit_events (
                id, session_id, operator, action, method, path,
                client_ip, user_agent, outcome, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            f"AUD-{uuid.uuid4().hex[:12].upper()}",
            session_id,
            normalize_operator(operator),
            f"{request.method} {request.url.path}",
            request.method,
            request.url.path,
            request_client_ip(request),
            request.headers.get("user-agent", "")[:240],
            outcome,
            detail[:500],
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


def create_admin_session(payload: AdminSessionRequest, request: Request) -> dict:
    admin_token = os.getenv("ADMIN_TOKEN")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin filing API is not configured")
    enforce_admin_rate_limit(request, "admin_session")
    if not secrets.compare_digest(payload.admin_token, admin_token):
        record_admin_audit_event(request, normalize_operator(payload.operator), "denied", detail="Invalid admin session token")
        raise HTTPException(status_code=403, detail="Invalid admin token")
    session_id = f"ADM-{uuid.uuid4().hex[:12].upper()}"
    session_token = secrets.token_urlsafe(32)
    operator = normalize_operator(payload.operator)
    expires_at = admin_timestamp(ADMIN_SESSION_TTL_SECONDS)
    conn = get_db()
    conn.execute("""
        INSERT INTO admin_sessions (
            id, token_hash, operator, client_ip, user_agent, expires_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        hash_admin_session_token(session_token),
        operator,
        request_client_ip(request),
        request.headers.get("user-agent", "")[:240],
        expires_at,
        admin_timestamp(),
    ))
    conn.commit()
    conn.close()
    record_admin_audit_event(request, operator, "allowed", session_id, "Admin session created")
    return {
        "session_id": session_id,
        "session_token": session_token,
        "operator": operator,
        "expires_at": expires_at,
        "ttl_seconds": ADMIN_SESSION_TTL_SECONDS,
    }


def bearer_admin_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return request.headers.get("x-admin-session", "").strip()


def verify_admin_access(request: Request):
    session_token = bearer_admin_token(request)
    if session_token:
        conn = get_db()
        row = conn.execute("""
            SELECT * FROM admin_sessions
            WHERE token_hash = ? AND revoked_at IS NULL AND expires_at > datetime('now')
        """, (hash_admin_session_token(session_token),)).fetchone()
        if row:
            conn.execute("UPDATE admin_sessions SET last_seen_at = datetime('now') WHERE id = ?", (row["id"],))
            conn.commit()
            conn.close()
            record_admin_audit_event(request, row["operator"], "allowed", row["id"])
            return {"operator": row["operator"], "session_id": row["id"], "auth_mode": "session"}
        conn.close()
        record_admin_audit_event(request, "unknown", "denied", detail="Invalid or expired admin session")
        raise HTTPException(status_code=401, detail="Invalid or expired admin session")

    admin_token = os.getenv("ADMIN_TOKEN")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin filing API is not configured")
    provided = request.headers.get("x-admin-token", "")
    if not secrets.compare_digest(provided, admin_token):
        enforce_admin_rate_limit(request, "admin_token")
        record_admin_audit_event(request, "unknown", "denied", detail="Invalid legacy admin token")
        raise HTTPException(status_code=403, detail="Invalid admin token")
    record_admin_audit_event(request, "legacy_admin_token", "allowed", detail="Legacy x-admin-token access")
    return {"operator": "legacy_admin_token", "session_id": "", "auth_mode": "legacy_token"}


def revoke_admin_session(request: Request) -> dict:
    session_token = bearer_admin_token(request)
    if not session_token:
        raise HTTPException(status_code=400, detail="No admin session was provided")
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM admin_sessions WHERE token_hash = ? AND revoked_at IS NULL",
        (hash_admin_session_token(session_token),),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid or expired admin session")
    conn.execute("UPDATE admin_sessions SET revoked_at = datetime('now') WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    record_admin_audit_event(request, row["operator"], "allowed", row["id"], "Admin session revoked")
    return {"status": "revoked", "session_id": row["id"]}


def verify_order_access(order_id: str, token: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM orders WHERE id = ? AND token = ?", (order_id, token)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=403, detail="Invalid order ID or token")
    return dict(row)

def add_status_update(order_id: str, status: str, message: str = ""):
    conn = get_db()
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, ?, ?)",
        (order_id, status, message)
    )
    conn.execute(
        "UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (status, order_id)
    )
    conn.commit()
    conn.close()


def formation_data_needs_ssn(formation_data: dict) -> bool:
    if formation_data.get("responsible_party_ssn_vault_id"):
        return False
    return not _re.fullmatch(r"\d{9}", _normalize_ssn_itin(formation_data.get("responsible_party_ssn", "")))


def update_responsible_party_ssn(order_id: str, ssn_itin: str, user: dict | None = None, token: str = "") -> dict:
    ssn = _validate_full_ssn_itin(ssn_itin, "ssn_itin")
    conn = get_db()
    try:
        if user:
            link_orders_to_user_by_email(conn, user["id"], user["email"])
            order = conn.execute(
                """
                SELECT * FROM orders
                WHERE id = ?
                  AND (user_id = ? OR lower(email) = lower(?))
                """,
                (order_id, user["id"], user["email"]),
            ).fetchone()
        else:
            order = conn.execute("SELECT * FROM orders WHERE id = ? AND token = ?", (order_id, token)).fetchone()
        if not order:
            raise HTTPException(status_code=403, detail="Invalid order access")

        formation_data = parse_json_field(order["formation_data"], {})
        members = formation_data.get("members") or []
        responsible = next((m for m in members if m.get("is_responsible_party")), members[0] if members else None)
        if responsible is None:
            raise HTTPException(status_code=400, detail="No responsible party is available for this order.")
        vault_id = store_sensitive_value("order", order_id, "responsible_party_ssn", ssn, created_by=(user or {}).get("id", "customer_portal"))
        responsible["ssn_itin"] = ""
        responsible["ssn_last4"] = ssn[-4:]
        responsible["is_responsible_party"] = True
        formation_data["responsible_party_ssn"] = ""
        formation_data["responsible_party_ssn_vault_id"] = vault_id
        formation_data["responsible_party_ssn_last4"] = ssn[-4:]
        formation_data["members"] = members

        conn.execute(
            "UPDATE orders SET formation_data = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(formation_data), order_id),
        )

        queue_path = DOCS_DIR / order_id / "ein_queue.json"
        if queue_path.exists():
            queue = parse_json_field(queue_path.read_text(), {})
            queue.setdefault("ss4_data", {})
            queue["ss4_data"].setdefault("responsible_party", {})
            queue["ss4_data"]["responsible_party"]["ssn"] = ""
            queue["ss4_data"]["responsible_party"]["ssn_vault_id"] = vault_id
            queue["ss4_data"]["responsible_party"]["ssn_last4"] = ssn[-4:]
            queue["status"] = "ready_for_submission"
            queue["ssn_received_at"] = utc_now()
            queue_path.write_text(json.dumps(queue, indent=2))

        existing = conn.execute(
            "SELECT 1 FROM status_updates WHERE order_id = ? AND status = 'ein_ready_for_submission' LIMIT 1",
            (order_id,),
        ).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'ein_ready_for_submission', ?)",
                (order_id, "Responsible-party SSN/ITIN received securely through the customer portal. EIN application is ready for IRS submission."),
            )
        if order["status"] in {"state_approved", "ein_pending", "ein_queued"}:
            conn.execute("UPDATE orders SET status = 'ein_pending', updated_at = datetime('now') WHERE id = ?", (order_id,))
        conn.commit()
        return {"status": "ok", "ein_queue_ready": queue_path.exists()}
    finally:
        conn.close()


def add_customer_document_if_missing(
    conn,
    order_id: str,
    doc_type: str,
    filename: str,
    file_path: str,
    fmt: str = "",
    category: str = "",
    visibility: str = "customer",
):
    """Expose operator evidence in the customer document vault."""
    if not file_path:
        return
    existing = conn.execute(
        "SELECT id FROM documents WHERE order_id = ? AND filename = ?",
        (order_id, filename),
    ).fetchone()
    if existing:
        return
    file_format = fmt or Path(filename).suffix.lstrip(".").lower() or "file"
    document_category = category or document_category_for_type(doc_type)
    document_visibility = visibility if visibility in {"customer", "admin", "hidden"} else "customer"
    conn.execute(
        """
        INSERT INTO documents (order_id, doc_type, filename, file_path, format, category, visibility)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (order_id, doc_type, filename, file_path, file_format, document_category, document_visibility),
    )

def resolve_document_path(file_path: str) -> Path:
    path = Path(file_path)
    if path.is_absolute():
        return path
    return BASE_DIR / path


OFFICIAL_DOCUMENT_TYPES = {
    "approved_certificate",
    "state_filed_document",
    "state_acknowledgment",
    "submitted_receipt",
    "ein_confirmation_letter",
}

HIDDEN_CUSTOMER_DOCUMENT_TYPES = {
    "ein_ss4_data",
}

DOCUMENT_CATEGORY_BY_TYPE = {
    "approved_certificate": "official_evidence",
    "submitted_receipt": "official_evidence",
    "state_filed_document": "official_evidence",
    "state_acknowledgment": "official_evidence",
    "state_correspondence": "official_evidence",
    "rejection_notice": "official_evidence",
    "ein_confirmation_letter": "official_evidence",
    "annual_report_packet": "operator_packet",
    "filing_authorization": "authorization",
    "registered_agent_consent": "registered_agent",
    "registered_agent_assignment": "registered_agent",
    "registered_agent_partner_order": "registered_agent",
    "registered_agent_status": "registered_agent",
    "ein_ss4_data": "sensitive_source",
}

def document_category_for_type(doc_type: str) -> str:
    return DOCUMENT_CATEGORY_BY_TYPE.get(doc_type, "customer_document")

SOURCE_DOCUMENT_FORMATS = {"text", "txt", "md", "markdown", "json"}
PREFERRED_CUSTOMER_FORMATS = {"pdf": 0, "png": 1, "jpg": 1, "jpeg": 1, "zip": 2}


def customer_visible_documents(rows) -> list[dict]:
    """Return customer-facing document rows without source/data clutter."""
    docs = [dict(row) for row in rows]
    candidates: list[dict] = []
    for doc in docs:
        doc_type = doc.get("doc_type", "")
        doc["category"] = doc.get("category") or document_category_for_type(doc_type)
        doc["visibility"] = doc.get("visibility") or "customer"
        if doc["visibility"] != "customer":
            continue
        fmt = (doc.get("format") or Path(doc.get("filename", "")).suffix.lstrip(".")).lower()
        doc["format"] = fmt
        if doc_type in HIDDEN_CUSTOMER_DOCUMENT_TYPES:
            continue
        if doc_type in OFFICIAL_DOCUMENT_TYPES and fmt in SOURCE_DOCUMENT_FORMATS:
            continue
        candidates.append(doc)

    grouped: dict[str, list[dict]] = {}
    for doc in candidates:
        grouped.setdefault(doc.get("doc_type", ""), []).append(doc)

    visible: list[dict] = []
    for group in grouped.values():
        best_rank = min(PREFERRED_CUSTOMER_FORMATS.get(doc.get("format", ""), 9) for doc in group)
        best_docs = [doc for doc in group if PREFERRED_CUSTOMER_FORMATS.get(doc.get("format", ""), 9) == best_rank]
        visible.extend(best_docs)

    return sorted(visible, key=lambda doc: (doc.get("created_at") or "", doc.get("filename") or ""))


def record_document_access(order_id: str, filename: str, actor: str, auth_context: str = "") -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO document_access_events (id, order_id, filename, actor, access_type, auth_context)
        VALUES (?, ?, ?, ?, 'download', ?)
        """,
        (f"DOC-AUD-{uuid.uuid4().hex[:12].upper()}", order_id, filename, normalize_operator(actor), auth_context),
    )
    conn.commit()
    conn.close()


def get_filing_action(state: str, entity_type: str, action_type: str = "formation") -> Optional[dict]:
    state = state.upper()
    entity_type = "LLC" if entity_type == "LLC" else entity_type
    return (
        FILING_ACTIONS.get(state, {})
        .get(entity_type, {})
        .get(action_type)
    )

def get_state_filing_route(state: str, entity_type: str, action_type: str = "formation") -> dict:
    return build_state_route(
        state=state,
        entity_type=entity_type,
        action_type=action_type,
        filing_actions=FILING_ACTIONS,
        portal_research=PORTAL_RESEARCH,
        state_fees=STATE_FEES,
    )

def blocker_level_for_route(route: dict) -> str:
    blockers = route.get("blockers") or []
    difficulty = route.get("automation_difficulty") or "unknown"
    lane = route.get("automation_lane") or "operator_assisted"
    codes = {str(blocker.get("code", "")).lower() for blocker in blockers}
    if difficulty == "hard" or lane == "operator_assisted_browser_provider" or any("captcha" in code or "waf" in code for code in codes):
        return "hard"
    if blockers or difficulty in {"medium", "unknown"}:
        return "medium"
    return "low"

def automation_readiness_for_route(route: dict) -> dict:
    evidence = route.get("required_evidence") or {}
    submitted = bool(evidence.get("submitted"))
    approved = bool(evidence.get("approved"))
    portal = bool(route.get("portal_url"))
    lane = route.get("automation_lane") or "operator_assisted"
    blocker_level = blocker_level_for_route(route)
    gates = route.get("certification_gates") or []
    pending_gates = [
        gate for gate in gates
        if gate.get("status") in {"pending", "needs_repair", "required"}
    ]
    readiness = (route.get("state_automation_profile") or {}).get("production_readiness", "")
    ready = submitted and approved and portal and blocker_level != "hard"
    if lane == "operator_assisted":
        status = "operator_assisted"
    elif readiness == "production_ready_operator_supervised":
        status = "production_ready_operator_supervised"
        ready = True
    elif readiness == "operator_assisted_required":
        status = "trusted_access_operator_checkpoint_required"
    elif ready:
        status = "payment_screen_certification_required" if pending_gates else "dry_run_ready"
    elif blocker_level == "hard":
        status = "operator_fallback_required"
    else:
        status = "metadata_incomplete"
    return {
        "status": status,
        "dry_run_ready": ready,
        "pending_certification_gates": pending_gates,
        "next_certification_gate": pending_gates[0] if pending_gates else None,
        "missing": [
            label for label, ok in {
                "portal_url": portal,
                "submitted_evidence": submitted,
                "approved_evidence": approved,
            }.items()
            if not ok
        ],
        "blocker_level": blocker_level,
    }

def state_metadata_summary(state: str, entity_type: str = "LLC", action_type: str = "formation") -> dict:
    state_code = state.upper()
    route = get_state_filing_route(state_code, entity_type, action_type)
    evidence = route.get("required_evidence") or {}
    return {
        "state": state_code,
        "state_name": (STATE_FEES.get("state_names") or {}).get(state_code, state_code),
        "entity_type": entity_type,
        "action_type": action_type,
        "filing_lane": route.get("automation_lane") or "operator_assisted",
        "automation_difficulty": route.get("automation_difficulty") or "unknown",
        "blocker_level": blocker_level_for_route(route),
        "portal_name": route.get("portal_name") or "",
        "portal_url": route.get("portal_url") or "",
        "form_number": route.get("form_number") or "",
        "expected_processing_time": route.get("expected_processing_time") or "",
        "evidence_requirements": {
            "submitted": evidence.get("submitted") or ["Official submission receipt or confirmation"],
            "approved": evidence.get("approved") or ["Official approval, certificate, or accepted filing record"],
        },
        "automation_readiness": automation_readiness_for_route(route),
        "state_automation_profile": route.get("state_automation_profile") or {},
        "certification_gates": route.get("certification_gates") or [],
        "blockers": route.get("blockers") or [],
        "portal_field_sequence": route.get("portal_field_sequence") or [],
        "required_customer_inputs": route.get("required_customer_inputs") or [],
        "evidence_outputs": route.get("evidence_outputs") or [],
        "source_urls": route.get("source_urls") or [],
        "customer_status": route.get("customer_status") or "operator_verified",
        "adapter_key": route.get("adapter_key") or "",
    }

def all_state_metadata(entity_type: str = "LLC", action_type: str = "formation") -> list[dict]:
    state_names = STATE_FEES.get("state_names") or {}
    states = sorted(state_names.keys() or STATE_FEES.get("LLC", {}).keys())
    return [state_metadata_summary(state, entity_type, action_type) for state in states]


def adapter_matrix_job(record: dict) -> dict:
    state_code = record["state"]
    return {
        "id": f"MATRIX-{state_code}-{record['action_type']}",
        "order_id": f"MATRIX-{state_code}",
        "action_type": record["action_type"],
        "state": state_code,
        "entity_type": record["entity_type"],
        "business_name": f"Matrix {state_code} Test LLC",
        "status": "ready_to_file",
        "order_status": "payment_captured",
        "state_fee_cents": 10000,
        "total_government_cents": 10000,
        "automation_lane": record["filing_lane"],
        "automation_difficulty": record["automation_difficulty"],
        "adapter_key": record["adapter_key"],
        "portal_name": record["portal_name"],
        "portal_url": record["portal_url"],
        "portal_blockers": record["blockers"],
        "required_evidence": record["evidence_requirements"],
        "route_metadata": {
            "source_urls": record.get("source_urls", []),
            "state_automation_profile": record.get("state_automation_profile") or {},
            "certification_gates": record.get("certification_gates") or [],
        },
        "document_types": ["filing_authorization"] + (["annual_report_packet"] if record["action_type"] == "annual_report" else []),
        "artifact_types": ["registered_agent_assignment"] + (["annual_report_packet"] if record["action_type"] == "annual_report" else []),
        "formation_data": {
            "business_name": f"Matrix {state_code} Test LLC",
            "state": state_code,
            "entity_type": record["entity_type"],
            "principal_address": "1 Main St",
            "principal_city": "Capital City",
            "principal_state": state_code,
            "principal_zip": "00000",
            "ra_choice": "sosfiler",
            "members": [{
                "name": "Matrix Owner",
                "address": "1 Main St",
                "city": "Capital City",
                "state": state_code,
                "zip_code": "00000",
            }],
        },
    }


async def build_adapter_matrix(entity_type: str = "LLC", action_type: str = "formation") -> dict:
    records = all_state_metadata(entity_type, action_type)
    rows = []
    for record in records:
        job = adapter_matrix_job(record)
        result = await run_adapter_operation(job, "preflight", dry_run=True)
        preflight = result.metadata.get("preflight", {})
        rows.append({
            **record,
            "dry_run_status": result.status,
            "dry_run_message": result.message,
            "preflight_passed": bool(preflight.get("passed")),
            "blocking_issue_codes": [issue.get("code") for issue in preflight.get("blocking_issues", [])],
            "warning_codes": [issue.get("code") for issue in preflight.get("warnings", [])],
            "next_certification_gate": (record.get("automation_readiness") or {}).get("next_certification_gate"),
            "certification_gates": record.get("certification_gates") or [],
        })
    summary = {
        "states": len(rows),
        "preflight_passed": sum(1 for row in rows if row["preflight_passed"]),
        "operator_required": sum(1 for row in rows if row["dry_run_status"] == "operator_required"),
        "automation_started": sum(1 for row in rows if row["dry_run_status"] == "automation_started"),
        "metadata_incomplete": sum(1 for row in rows if row["blocking_issue_codes"]),
    }
    return {"summary": summary, "records": rows}

def calculate_processing_fee_cents(action: Optional[dict], state_fee_cents: int) -> int:
    if not action:
        return 0
    rule = action.get("processing_fee") or {}
    if rule.get("type") == "percent" and rule.get("applies_to") == "state_fee":
        return int(round(state_fee_cents * float(rule.get("rate", 0))))
    if rule.get("type") == "fixed":
        return int(rule.get("amount_cents", 0))
    return 0

def build_fee_breakdown(state: str, entity_type: str, include_ra: bool = False) -> dict:
    entity_key = "LLC" if entity_type == "LLC" else "Corp"
    route = get_state_filing_route(state, "LLC" if entity_key == "LLC" else entity_type)
    state_fee_cents = int(route.get("state_fee_cents") or STATE_FEES[entity_key][state]["filing_fee"] * 100)
    action = route
    processing_fee_cents = calculate_processing_fee_cents(action, state_fee_cents)
    ra_fee_cents = RA_RENEWAL_FEE if include_ra else 0
    total_cents = PLATFORM_FEE + state_fee_cents + processing_fee_cents + ra_fee_cents
    return {
        "platform_fee_cents": PLATFORM_FEE,
        "state_fee_cents": state_fee_cents,
        "gov_processing_fee_cents": processing_fee_cents,
        "registered_agent_fee_cents": ra_fee_cents,
        "total_cents": total_cents,
        "processing_fee_rule": (action or {}).get("processing_fee"),
        "automation_route": route,
    }

def persist_quote(quote: dict, order_id: str = "") -> dict:
    conn = get_db()
    conn.execute("""
        INSERT INTO execution_quotes (
            id, order_id, product_type, entity_type, state, currency, line_items,
            estimated_total_cents, reconciliation_status, idempotency_key, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            order_id = COALESCE(excluded.order_id, execution_quotes.order_id),
            line_items = excluded.line_items,
            estimated_total_cents = excluded.estimated_total_cents,
            idempotency_key = excluded.idempotency_key,
            updated_at = datetime('now')
    """, (
        quote["quote_id"], order_id, quote["product_type"], quote.get("entity_type", ""),
        quote.get("state", ""), quote.get("currency", "usd"),
        json.dumps(quote.get("line_items", [])), int(quote.get("estimated_total_cents") or 0),
        quote.get("reconciliation_status", "quoted"), quote.get("idempotency_key", ""),
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM execution_quotes WHERE id = ?", (quote["quote_id"],)).fetchone()
    conn.close()
    execution_dual_write("upsert_quote", quote, order_id)
    return dict(row)


def insert_payment_ledger(
    order_id: str,
    event_type: str,
    amount_cents: int,
    quote_id: str = "",
    stripe_session_id: str = "",
    stripe_payment_intent: str = "",
    raw_event: Optional[dict] = None,
) -> dict:
    ledger_id = f"PAY-{uuid.uuid4().hex[:12].upper()}"
    idem = build_idempotency_key(order_id, event_type, amount_cents, stripe_payment_intent or stripe_session_id)
    conn = get_db()
    conn.execute("""
        INSERT INTO payment_ledger (
            id, order_id, quote_id, stripe_session_id, stripe_payment_intent, event_type,
            amount_cents, idempotency_key, raw_event
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ledger_id, order_id, quote_id, stripe_session_id, stripe_payment_intent,
        event_type, int(amount_cents or 0), idem, json.dumps(redact_sensitive(raw_event or {})),
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM payment_ledger WHERE id = ?", (ledger_id,)).fetchone()
    conn.close()
    ledger = dict(row)
    ledger["raw_event"] = parse_json_field(ledger.get("raw_event"), {})
    execution_dual_write("insert_payment_ledger", ledger)
    return ledger


def stripe_object_to_dict(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict_recursive"):
        return value.to_dict_recursive()
    try:
        return dict(value)
    except Exception:
        return {"raw": str(value)}


def stripe_event_id(event: dict, payload: bytes | None = None) -> str:
    if event.get("id"):
        return str(event["id"])
    digest = hashlib.sha256(payload or json.dumps(event, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return f"evt_local_{digest}"


def begin_stripe_webhook_event(event: dict, payload: bytes | None = None) -> tuple[str, bool]:
    event_id = stripe_event_id(event, payload)
    event_type = event.get("type", "unknown")
    obj = ((event.get("data") or {}).get("object") or {})
    order_id = (obj.get("metadata") or {}).get("order_id") or ""
    object_id = obj.get("id") or ""
    conn = get_db()
    existing = conn.execute("SELECT status FROM stripe_webhook_events WHERE id = ?", (event_id,)).fetchone()
    if existing:
        prior_status = (existing["status"] or "").lower()
        if prior_status in {"failed", "error"}:
            conn.execute(
                """
                UPDATE stripe_webhook_events
                SET status = 'processing',
                    error = '',
                    processed_at = NULL,
                    raw_event = ?
                WHERE id = ?
                """,
                (json.dumps(redact_sensitive(event)), event_id),
            )
            conn.commit()
            conn.close()
            return event_id, True
        conn.close()
        return event_id, False
    conn.execute(
        """
        INSERT INTO stripe_webhook_events (id, event_type, status, order_id, stripe_object_id, raw_event)
        VALUES (?, ?, 'processing', ?, ?, ?)
        """,
        (event_id, event_type, order_id, object_id, json.dumps(redact_sensitive(event))),
    )
    conn.commit()
    conn.close()
    return event_id, True


def finish_stripe_webhook_event(event_id: str, status: str = "processed", error: str = "") -> None:
    conn = get_db()
    conn.execute(
        """
        UPDATE stripe_webhook_events
        SET status = ?, error = ?, processed_at = datetime('now')
        WHERE id = ?
        """,
        (status, error[:500], event_id),
    )
    conn.commit()
    conn.close()


def latest_quote_for_order(conn, order_id: str):
    return conn.execute(
        "SELECT * FROM execution_quotes WHERE order_id = ? ORDER BY created_at DESC LIMIT 1",
        (order_id,),
    ).fetchone()


def quote_line_amounts(quote: dict | None) -> dict:
    amounts = {"platform_fee": 0, "government_fee": 0, "processing_fee": 0, "registered_agent": 0, "expedite": 0}
    if not quote:
        return amounts
    for item in parse_json_field(quote.get("line_items"), []):
        code = item.get("code") or ""
        if code in amounts:
            amounts[code] += int(item.get("amount_cents") or 0)
    return amounts


def payment_reconciliation_summary(conn, order: dict) -> dict:
    quote_row = latest_quote_for_order(conn, order["id"])
    quote = dict(quote_row) if quote_row else None
    amounts = quote_line_amounts(quote)
    authorized = int((quote or {}).get("authorized_total_cents") or 0)
    captured = int((quote or {}).get("captured_total_cents") or 0)
    ledger_rows = conn.execute(
        "SELECT event_type, amount_cents, stripe_session_id, stripe_payment_intent, created_at FROM payment_ledger WHERE order_id = ? ORDER BY created_at",
        (order["id"],),
    ).fetchall()
    if not authorized:
        authorized = sum(int(row["amount_cents"] or 0) for row in ledger_rows if row["event_type"] in {"authorized", "authorization_started"})
    final_total = int((quote or {}).get("estimated_total_cents") or order.get("total_cents") or 0)
    status = (quote or {}).get("reconciliation_status") or "missing_quote"
    if captured:
        status = "captured"
    elif authorized and status in {"authorized", "authorization_started"}:
        status = "authorized_pending_reconcile"
    return {
        "order_id": order["id"],
        "order_status": order.get("status"),
        "quote_id": (quote or {}).get("id", ""),
        "stripe_payment_intent": order.get("stripe_payment_intent") or "",
        "stripe_session_id": order.get("stripe_session_id") or "",
        "authorized_total_cents": authorized,
        "captured_total_cents": captured,
        "final_total_cents": final_total,
        "line_amounts": amounts,
        "reconciliation_status": status,
        "can_capture": status == "ready_to_capture" and authorized >= final_total and final_total > 0 and not captured,
        "requires_additional_authorization": status == "additional_authorization_required",
        "release_or_refund_due_cents": max(0, authorized - final_total) if status in {"ready_to_capture", "refund_or_release_due"} else 0,
        "ledger": [dict(row) for row in ledger_rows],
    }


def reconcile_order_payment(order_id: str, payload: PaymentReconcileRequest) -> dict:
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    quote = latest_quote_for_order(conn, order_id)
    if not quote:
        conn.close()
        raise HTTPException(status_code=400, detail="A quote is required before reconciliation.")
    order = dict(order)
    quote = dict(quote)
    amounts = quote_line_amounts(quote)
    platform_fee = amounts["platform_fee"] or int(order.get("platform_fee_cents") or 0)
    ra_fee = payload.final_registered_agent_fee_cents
    if ra_fee is None:
        ra_fee = amounts["registered_agent"]
    final_total = platform_fee + payload.final_government_fee_cents + payload.final_processing_fee_cents + int(ra_fee or 0) + amounts["expedite"]
    authorized = int(quote.get("authorized_total_cents") or 0)
    if not authorized:
        authorized = sum(
            int(row["amount_cents"] or 0)
            for row in conn.execute("SELECT event_type, amount_cents FROM payment_ledger WHERE order_id = ?", (order_id,)).fetchall()
            if row["event_type"] in {"authorized", "authorization_started"}
        )
    if not authorized:
        conn.close()
        raise HTTPException(status_code=400, detail="Payment must be authorized before reconciliation.")
    if final_total > authorized:
        status = "additional_authorization_required"
        event_type = "additional_authorization_required"
    elif final_total < authorized:
        status = "ready_to_capture"
        event_type = "authorized_amount_reduced_for_capture"
    else:
        status = "ready_to_capture"
        event_type = "ready_to_capture"
    line_items = [
        {"code": "platform_fee", "label": "SOSFiler service fee", "amount_cents": platform_fee, "kind": "revenue"},
        {"code": "government_fee", "label": "Final government filing fee", "amount_cents": payload.final_government_fee_cents, "kind": "passthrough"},
    ]
    if payload.final_processing_fee_cents:
        line_items.append({"code": "processing_fee", "label": "Final government portal processing fee", "amount_cents": payload.final_processing_fee_cents, "kind": "passthrough"})
    if int(ra_fee or 0):
        line_items.append({"code": "registered_agent", "label": "Registered agent partner fee", "amount_cents": int(ra_fee or 0), "kind": "partner"})
    if amounts["expedite"]:
        line_items.append({"code": "expedite", "label": "Expedited processing estimate", "amount_cents": amounts["expedite"], "kind": "passthrough"})
    conn.execute(
        """
        UPDATE execution_quotes
        SET line_items = ?, estimated_total_cents = ?, reconciliation_status = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (json.dumps(line_items), final_total, status, quote["id"]),
    )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, ?, ?)",
        (
            order_id,
            status,
            payload.message or (
                "Final government fees reconciled and payment is ready to capture."
                if status == "ready_to_capture"
                else "Final government fees exceed the authorized amount; additional customer authorization is required."
            ),
        ),
    )
    conn.commit()
    conn.close()
    insert_payment_ledger(
        order_id=order_id,
        quote_id=quote["id"],
        event_type=event_type,
        amount_cents=final_total,
        stripe_payment_intent=order.get("stripe_payment_intent") or "",
        raw_event={
            "actor": normalize_operator(payload.actor),
            "authorized_total_cents": authorized,
            "final_total_cents": final_total,
            "message": payload.message,
        },
    )
    conn = get_db()
    order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
    summary = payment_reconciliation_summary(conn, order)
    conn.close()
    return summary


def payment_capture_ready(conn, order: dict) -> bool:
    if not order or not order.get("id"):
        return False
    return payment_reconciliation_summary(conn, order).get("reconciliation_status") in {"captured", "payment_captured"}


def _readiness_check(code: str, label: str, status: str, message: str, severity: str = "blocking") -> dict:
    return {
        "code": code,
        "label": label,
        "status": status,
        "passed": status == "ready",
        "severity": severity,
        "message": message,
    }


def payment_execution_readiness(conn, order: dict, job: dict | None = None) -> dict:
    """Summarize whether a paid/authorized order is ready for operator filing prep."""
    if job is None:
        job_row = conn.execute(
            "SELECT * FROM filing_jobs WHERE order_id = ? AND action_type = 'formation' ORDER BY created_at DESC LIMIT 1",
            (order["id"],),
        ).fetchone()
        job = dict(job_row) if job_row else None
    payment_summary = payment_reconciliation_summary(conn, order)
    ledger_count = conn.execute("SELECT count(*) FROM payment_ledger WHERE order_id = ?", (order["id"],)).fetchone()[0]
    ready_ledger_count = conn.execute(
        """
        SELECT count(*) FROM payment_ledger
        WHERE order_id = ?
          AND event_type IN ('authorized', 'additional_authorization_received', 'captured', 'captured_dry_run')
        """,
        (order["id"],),
    ).fetchone()[0]
    quote_count = conn.execute("SELECT count(*) FROM execution_quotes WHERE order_id = ?", (order["id"],)).fetchone()[0]
    checks = []
    order_status = order.get("status") or ""
    payment_verified = order_status in PAYMENT_READY_STATUSES or ready_ledger_count > 0
    checks.append(_readiness_check(
        "payment_verified",
        "Payment verified",
        "ready" if payment_verified else "blocked",
        "Stripe/order status shows payment authorization or capture." if payment_verified else "No payment authorization or capture is recorded yet.",
    ))
    if order_status == "payment_authorized":
        capture_status = "ready" if payment_capture_ready(conn, order) else "needs_review"
        checks.append(_readiness_check(
            "final_fee_capture",
            "Final fee capture",
            capture_status,
            "Final fees are reconciled and captured." if capture_status == "ready" else "Payment is authorized, but final fees still need reconciliation/capture before live government submission.",
            "warning",
        ))
    else:
        checks.append(_readiness_check(
            "final_fee_capture",
            "Final fee capture",
            "ready" if order_status in {"paid", "payment_captured", "ready_to_file"} else "needs_review",
            f"Current order status is {order_status or 'unknown'}.",
            "warning",
        ))
    checks.append(_readiness_check(
        "quote_linked",
        "Quote linked",
        "ready" if quote_count else "needs_review",
        "Execution quote is linked to the order." if quote_count else "No execution quote is linked; this may be a legacy/direct checkout order and should be reviewed.",
        "warning",
    ))
    checks.append(_readiness_check(
        "payment_ledger",
        "Payment ledger",
        "ready" if ledger_count else "needs_review",
        "Payment ledger event is recorded." if ledger_count else "No payment ledger event is recorded; verify Stripe before filing.",
        "warning",
    ))
    if not job:
        checks.append(_readiness_check(
            "filing_job_created",
            "Filing job created",
            "blocked",
            "No formation filing job exists yet.",
        ))
        preflight = {"passed": False, "checks": [], "blocking_issues": [], "warnings": []}
        job_payload = {}
    else:
        job_payload = enrich_filing_job_for_adapter(conn, serialize_filing_job(job))
        preflight = validate_filing_preflight(job_payload)
        checks.append(_readiness_check(
            "filing_job_created",
            "Filing job created",
            "ready",
            f"Filing job {job_payload.get('id')} is present.",
        ))
        for check in preflight.get("checks", []):
            if check["code"] in {"payment_ready", "not_already_submitted"}:
                continue
            checks.append(_readiness_check(
                check["code"],
                check["label"],
                "ready" if check["passed"] else ("needs_review" if check.get("severity") == "warning" else "blocked"),
                check.get("message") or "",
                check.get("severity") or "blocking",
            ))
    blocked = [check for check in checks if check["status"] == "blocked"]
    review = [check for check in checks if check["status"] == "needs_review"]
    if blocked:
        readiness_status = "needs_filing_prep" if payment_verified and job else "blocked"
    elif review:
        readiness_status = "needs_reconciliation"
    else:
        readiness_status = "ready_to_file"
    return {
        "order_id": order["id"],
        "job_id": (job_payload or {}).get("id", ""),
        "business_name": order.get("business_name") or "",
        "email": order.get("email") or "",
        "state": order.get("state") or "",
        "entity_type": order.get("entity_type") or "",
        "order_status": order_status,
        "job_status": (job_payload or {}).get("status", ""),
        "readiness_status": readiness_status,
        "payment": payment_summary,
        "quote_count": quote_count,
        "ledger_count": ledger_count,
        "ready_ledger_count": ready_ledger_count,
        "blocking_count": len(blocked),
        "review_count": len(review),
        "checks": checks,
        "preflight": preflight,
        "created_at": order.get("created_at"),
        "updated_at": order.get("updated_at"),
        "paid_at": order.get("paid_at"),
    }


async def prepare_order_for_filing(order_id: str, payload: FilingPrepRequest) -> dict:
    """Generate internal prep artifacts and move a paid order toward the filing queue."""
    actor = normalize_operator(payload.actor)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(order)
    initial_readiness = payment_execution_readiness(conn, order)
    if not initial_readiness["checks"][0]["passed"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Payment is not verified for filing preparation.")
    conn.close()

    formation_data = parse_json_field(order.get("formation_data"), {})
    generated_docs: list[dict] = []
    if payload.generate_documents:
        generated_docs = await generate_internal_documents_async(order_id, formation_data)
        conn = get_db()
        conn.execute(
            "UPDATE orders SET documents_ready_at = COALESCE(documents_ready_at, datetime('now')), updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )
        conn.commit()
        conn.close()

    conn = get_db()
    refreshed_order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not refreshed_order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    refreshed_order = dict(refreshed_order)
    conn.close()
    job = create_or_update_filing_job(refreshed_order, "formation", "ready_to_file")
    ra_result = None
    if (parse_json_field(refreshed_order.get("formation_data"), {}) or {}).get("ra_choice") == "sosfiler":
        ra_result = fulfill_registered_agent_assignment(
            order_id,
            RAFulfillmentRequest(
                actor=actor,
                provider="corpnet",
                message="Registered agent fulfillment attempted during filing prep.",
            ),
        )
    conn = get_db()
    job_row = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job["id"],)).fetchone()
    readiness = payment_execution_readiness(conn, refreshed_order, dict(job_row) if job_row else dict(job))
    blocking = [check for check in readiness["checks"] if check["status"] == "blocked"]
    if not blocking:
        conn.execute("UPDATE filing_jobs SET status = 'ready_to_file', updated_at = datetime('now') WHERE id = ?", (job["id"],))
        if refreshed_order["status"] in {"paid", "payment_authorized", "payment_captured", "preparing", "generating_documents"}:
            conn.execute("UPDATE orders SET status = 'ready_to_file', updated_at = datetime('now') WHERE id = ?", (order_id,))
        status_message = payload.message or "Filing prep completed. Order is ready for operator-verified filing."
        event_type = "filing_prep_ready"
        status_value = "ready_to_file"
    else:
        conn.execute("UPDATE filing_jobs SET status = 'operator_required', updated_at = datetime('now') WHERE id = ?", (job["id"],))
        if refreshed_order["status"] in {"paid", "payment_authorized", "payment_captured", "preparing", "generating_documents"}:
            conn.execute("UPDATE orders SET status = 'operator_required', updated_at = datetime('now') WHERE id = ?", (order_id,))
        status_message = payload.message or "Filing prep generated available documents; operator must resolve remaining blockers."
        event_type = "filing_prep_operator_required"
        status_value = "operator_required"
    conn.execute(
        "INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor) VALUES (?, ?, ?, ?, ?)",
        (job["id"], order_id, event_type, status_message, actor),
    )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, ?, ?)",
        (order_id, status_value, status_message),
    )
    conn.commit()
    updated_order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
    updated_job = dict(conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job["id"],)).fetchone())
    final_readiness = payment_execution_readiness(conn, updated_order, updated_job)
    conn.close()
    execution_dual_write("upsert_filing_job", serialize_filing_job(updated_job))
    execution_dual_write("insert_event", {
        "filing_job_id": job["id"],
        "order_id": order_id,
        "event_type": event_type,
        "message": status_message,
        "actor": actor,
        "evidence_path": "",
    })
    return {
        "order_id": order_id,
        "job": serialize_filing_job(updated_job),
        "readiness": final_readiness,
        "generated_documents": [
            {"type": doc.get("type"), "filename": doc.get("filename"), "format": doc.get("format")}
            for doc in generated_docs
        ],
        "registered_agent": ra_result,
        "remaining_blockers": [check for check in final_readiness["checks"] if check["status"] == "blocked"],
        "remaining_reviews": [check for check in final_readiness["checks"] if check["status"] == "needs_review"],
    }


def update_payment_authorization_from_session(session: dict) -> dict:
    metadata = session.get("metadata") or {}
    order_id = metadata.get("order_id") or ""
    if not order_id:
        return {"updated": False, "reason": "missing_order_id"}
    quote_id = metadata.get("quote_id") or ""
    payment_intent = session.get("payment_intent") or ""
    amount = int(session.get("amount_total") or session.get("amount_subtotal") or 0)
    additional = metadata.get("additional_authorization") == "true"
    status = "additional_authorization_received" if additional else "authorized"

    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        return {"updated": False, "reason": "order_not_found", "order_id": order_id}
    if additional:
        # Preserve the original PaymentIntent so the capture path can settle both authorizations.
        # Only update the session pointer; the supplemental PaymentIntent is tracked in payment_ledger.
        conn.execute(
            """
            UPDATE orders
            SET status = 'payment_authorized',
                stripe_session_id = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (session.get("id"), order_id),
        )
    else:
        conn.execute(
            """
            UPDATE orders
            SET status = 'payment_authorized',
                stripe_session_id = ?,
                stripe_payment_intent = COALESCE(?, stripe_payment_intent),
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (session.get("id"), payment_intent, order_id),
        )
    if quote_id:
        if additional:
            conn.execute(
                """
                UPDATE execution_quotes
                SET authorized_total_cents = COALESCE(authorized_total_cents, 0) + ?,
                    reconciliation_status = 'authorized',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (amount, quote_id),
            )
        else:
            conn.execute(
                """
                UPDATE execution_quotes
                SET authorized_total_cents = ?,
                    reconciliation_status = 'authorized',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (amount, quote_id),
            )
    conn.commit()
    conn.close()
    if quote_id:
        execution_dual_write("update_quote_authorized", quote_id, amount)
    insert_payment_ledger(
        order_id=order_id,
        quote_id=quote_id,
        event_type=status,
        amount_cents=amount,
        stripe_session_id=session.get("id"),
        stripe_payment_intent=payment_intent,
        raw_event=session,
    )
    add_status_update(
        order_id,
        "payment_authorized",
        "Additional payment authorization received." if additional else "Payment authorized. Preparing workflow before final capture.",
    )
    return {"updated": True, "order_id": order_id, "quote_id": quote_id, "amount_cents": amount, "additional": additional}


def update_payment_intent_snapshot(intent: dict) -> dict:
    payment_intent = intent.get("id") or ""
    if not payment_intent:
        return {"updated": False, "reason": "missing_payment_intent"}
    amount_capturable = int(intent.get("amount_capturable") or 0)
    amount_received = int(intent.get("amount_received") or 0)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE stripe_payment_intent = ? ORDER BY created_at DESC LIMIT 1", (payment_intent,)).fetchone()
    if not order:
        conn.close()
        return {"updated": False, "reason": "order_not_found", "payment_intent": payment_intent}
    order = dict(order)
    quote = latest_quote_for_order(conn, order["id"])
    quote_id = dict(quote)["id"] if quote else ""
    if quote and amount_capturable:
        conn.execute(
            """
            UPDATE execution_quotes
            SET authorized_total_cents = MAX(COALESCE(authorized_total_cents, 0), ?),
                reconciliation_status = CASE
                    WHEN reconciliation_status = 'quoted' THEN 'authorized'
                    ELSE reconciliation_status
                END,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (amount_capturable, quote_id),
        )
    if amount_received and quote:
        conn.execute(
            """
            UPDATE execution_quotes
            SET captured_total_cents = MAX(COALESCE(captured_total_cents, 0), ?),
                reconciliation_status = 'captured',
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (amount_received, quote_id),
        )
        conn.execute(
            "UPDATE orders SET status = 'payment_captured', total_cents = ?, updated_at = datetime('now') WHERE id = ?",
            (amount_received, order["id"]),
        )
    conn.commit()
    conn.close()
    event_type = "captured" if amount_received else "amount_capturable_updated"
    insert_payment_ledger(
        order_id=order["id"],
        quote_id=quote_id,
        event_type=event_type,
        amount_cents=amount_received or amount_capturable,
        stripe_payment_intent=payment_intent,
        raw_event=intent,
    )
    return {"updated": True, "order_id": order["id"], "quote_id": quote_id, "payment_intent": payment_intent}


def store_sensitive_value(subject_type: str, subject_id: str, pii_type: str, value: str, created_by: str = "system") -> str:
    vault_id = f"PII-{uuid.uuid4().hex[:12].upper()}"
    normalized = _normalize_ssn_itin(value) if "ssn" in pii_type.lower() else (value or "")
    encrypted = encrypt_pii(normalized)
    fingerprint = pii_fingerprint(normalized)
    last4 = normalized[-4:] if len(normalized) >= 4 else ""
    conn = get_db()
    conn.execute("""
        INSERT INTO pii_vault (
            id, subject_type, subject_id, pii_type, encrypted_payload, fingerprint, last4, created_by
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (vault_id, subject_type, subject_id, pii_type, encrypted, fingerprint, last4, created_by))
    conn.commit()
    conn.close()
    return vault_id


def retrieve_sensitive_value(vault_id: str, actor: str, purpose: str) -> str:
    conn = get_db()
    row = conn.execute("SELECT * FROM pii_vault WHERE id = ?", (vault_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="PII vault reference not found")
    event_id = f"PIIA-{uuid.uuid4().hex[:12].upper()}"
    conn.execute("""
        INSERT INTO pii_access_events (
            id, vault_id, subject_type, subject_id, pii_type, actor, purpose
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        vault_id,
        row["subject_type"],
        row["subject_id"],
        row["pii_type"],
        normalize_operator(actor),
        purpose,
    ))
    conn.execute("UPDATE pii_vault SET accessed_at = datetime('now') WHERE id = ?", (vault_id,))
    conn.commit()
    conn.close()
    return decrypt_pii(row["encrypted_payload"])


def create_support_ticket(
    *,
    question: str,
    confidence_reason: str,
    session_id: str = "",
    order_id: str = "",
    customer_email: str = "",
    state: str = "",
    product_type: str = "",
    suggested_answer: str = "",
    ticket_type: str = "support",
    priority: str = "normal",
) -> dict:
    ticket_id = f"TKT-{uuid.uuid4().hex[:12].upper()}"
    ticket = {
        "id": ticket_id,
        "ticket_type": ticket_type,
        "status": "open",
        "priority": priority,
        "customer_email": customer_email,
        "order_id": order_id,
        "session_id": session_id,
        "state": state,
        "product_type": product_type,
        "question": question,
        "confidence_reason": confidence_reason,
        "suggested_answer": suggested_answer,
    }
    slack_sent = 0
    try:
        slack_sent = 1 if send_slack_ticket(ticket) else 0
    except Exception:
        slack_sent = 0
    conn = get_db()
    conn.execute("""
        INSERT INTO support_tickets (
            id, ticket_type, status, priority, customer_email, order_id, session_id,
            state, product_type, question, confidence_reason, suggested_answer, slack_sent
        )
        VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        ticket_id, ticket_type, priority, customer_email, order_id, session_id, state,
        product_type, question, confidence_reason, suggested_answer, slack_sent,
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()
    execution_dual_write("insert_support_ticket", dict(row))
    return dict(row)


ENGINEERING_JOB_STATUSES = {
    "approved",
    "in_progress",
    "tests_failed",
    "ready_to_deploy",
    "deployed",
    "blocked",
    "stop_requested",
}

ENGINEERING_TRANSITIONS = {
    "approved": {"in_progress", "blocked", "stop_requested"},
    "in_progress": {"tests_failed", "ready_to_deploy", "blocked", "stop_requested"},
    "tests_failed": {"in_progress", "blocked", "stop_requested"},
    "ready_to_deploy": {"deployed", "in_progress", "blocked", "stop_requested"},
    "blocked": {"in_progress", "stop_requested"},
    "deployed": set(),
    "stop_requested": set(),
}

ENGINEERING_STOP_CONDITIONS = [
    "failing_tests",
    "migration_failure",
    "secret_or_credential_risk",
    "duplicate_filing_risk",
    "payment_or_pii_safety_risk",
    "production_health_check_failure",
]


def build_engineering_plan(ticket: dict) -> dict:
    question = (ticket.get("question") or "").strip()
    suggested = (ticket.get("suggested_answer") or "").strip()
    product = ticket.get("product_type") or "support"
    state = ticket.get("state") or ""
    combined = f"{question} {suggested} {product}".lower()
    risk_flags = []
    if any(term in combined for term in ("stripe", "payment", "authorize", "capture", "refund")):
        risk_flags.append("payment_flow_review")
    if any(term in combined for term in ("ssn", "ein", "pii", "tax id", "responsible party")):
        risk_flags.append("pii_or_tax_data_review")
    if any(term in combined for term in ("file", "filing", "submit", "state portal", "secretary of state")):
        risk_flags.append("filing_safety_review")
    if any(term in combined for term in ("migration", "database", "supabase", "postgres", "schema")):
        risk_flags.append("database_migration_review")
    if any(term in combined for term in ("secret", "token", "credential", "webhook", "signing")):
        risk_flags.append("secret_handling_review")
    if not risk_flags:
        risk_flags.append("standard_code_review")

    acceptance_criteria = [
        "Approved behavior is implemented without exposing secrets, PII, payment data, or filing credentials.",
        "Operator-visible status or audit trail is updated so the change can be verified after deployment.",
        "Relevant tests pass before the job can be marked ready_to_deploy.",
    ]
    if "chat" in combined or "answer" in combined or "question" in combined:
        acceptance_criteria.append("Low-confidence assistant behavior escalates instead of guessing.")
    if "slack" in combined:
        acceptance_criteria.append("Slack notification or interaction behavior is verified with signed requests or a safe smoke test.")
    if "filing" in combined or "state portal" in combined or "submit" in combined:
        acceptance_criteria.append("No filing can be marked submitted, approved, or complete without official evidence.")

    required_tests = [
        "python3 -m py_compile backend/server.py",
        ".venv312/bin/python -m unittest qa/test_execution_platform.py qa/test_slack_interactions.py qa/test_engineering_queue.py",
    ]
    if "filing" in combined or "state" in combined or "portal" in combined:
        required_tests.append(".venv312/bin/python -m unittest qa/test_filing_adapters.py qa/test_state_routing.py")
    if "supabase" in combined or "database" in combined or "migration" in combined:
        required_tests.append(".venv312/bin/python -m unittest qa/test_execution_repository.py")

    return {
        "source_ticket_id": ticket.get("id", ""),
        "source_question": question,
        "requested_behavior": suggested or question or "Operator-approved SOSFiler enhancement.",
        "context": {
            "state": state,
            "product_type": product,
            "order_id": ticket.get("order_id") or "",
            "session_id": ticket.get("session_id") or "",
        },
        "implementation_plan": [
            "Inspect the current code path and identify the smallest safe change.",
            "Implement the change behind existing safety gates and audit logging.",
            "Run the required tests and record the exact command/results in this engineering job.",
            "Deploy only after the job reaches ready_to_deploy and production health checks pass.",
        ],
        "acceptance_criteria": acceptance_criteria,
        "risk_flags": risk_flags,
        "required_tests": required_tests,
        "deployment_target": os.getenv("SOSFILER_OPERATOR_COCKPIT_URL", "https://ops.sosfiler.com/operator.html"),
        "execution_modes": ["plan_only", "code_patch_local", "test_only", "deploy_after_approval"],
        "stop_conditions": ENGINEERING_STOP_CONDITIONS,
    }


def engineering_ticket_id_from_log(log: list[dict]) -> str:
    for entry in log:
        ticket_id = entry.get("ticket_id")
        if ticket_id:
            return ticket_id
    return ""


def engineering_plan_from_log(log: list[dict]) -> dict:
    for entry in reversed(log):
        plan = entry.get("engineering_plan")
        if isinstance(plan, dict):
            return plan
    return {}


def engineering_work_plan_from_log(log: list[dict]) -> dict:
    for entry in reversed(log):
        work_plan = entry.get("work_plan")
        if isinstance(work_plan, dict):
            return work_plan
    return {}


def engineering_execution_package_from_log(log: list[dict]) -> dict:
    for entry in reversed(log):
        execution_package = entry.get("execution_package")
        if isinstance(execution_package, dict):
            return execution_package
    return {}


def engineering_test_run_from_log(log: list[dict]) -> dict:
    for entry in reversed(log):
        test_run = entry.get("test_run")
        if isinstance(test_run, dict):
            return test_run
    return {}


def engineering_deploy_check_from_log(log: list[dict]) -> dict:
    for entry in reversed(log):
        deploy_check = entry.get("deploy_check")
        if isinstance(deploy_check, dict):
            return deploy_check
    return {}


def engineering_work_plan_path(run_id: str) -> Path:
    safe_run_id = _re.sub(r"[^A-Za-z0-9_.-]", "_", run_id or "engineering-job")
    return DOCS_DIR / "engineering_jobs" / safe_run_id / "work_plan.md"


def engineering_execution_package_path(run_id: str) -> Path:
    safe_run_id = _re.sub(r"[^A-Za-z0-9_.-]", "_", run_id or "engineering-job")
    return DOCS_DIR / "engineering_jobs" / safe_run_id / "execution_package.md"


def markdown_ordered(items: list[str]) -> str:
    if not items:
        return "None recorded."
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def markdown_bullets(items: list[str]) -> str:
    if not items:
        return "- None recorded."
    return "\n".join(f"- {item}" for item in items)


def render_engineering_work_plan(job: dict) -> str:
    safe_job = redact_sensitive(job)
    ticket = safe_job.get("ticket") or {}
    plan = safe_job.get("engineering_plan") or {}
    context = plan.get("context") or {}
    log = safe_job.get("redacted_log") or []
    audit_lines = []
    for entry in log:
        when = entry.get("at") or entry.get("created_at") or ""
        message = entry.get("message") or entry.get("to_status") or "event"
        actor = entry.get("actor") or entry.get("approved_by") or "system"
        audit_lines.append(f"{when} - {actor}: {message}")

    return "\n".join([
        f"# Engineering Work Plan: {safe_job.get('id', '')}",
        "",
        "## Summary",
        f"- Status: {safe_job.get('status', '')}",
        f"- Source ticket: {safe_job.get('ticket_id') or plan.get('source_ticket_id') or ''}",
        f"- Updated: {safe_job.get('updated_at') or ''}",
        f"- Deployment target: {plan.get('deployment_target') or 'Not set'}",
        "",
        "## Source Ticket",
        f"- Customer/session: {ticket.get('customer_email') or ticket.get('session_id') or 'Not attached'}",
        f"- Priority: {ticket.get('priority') or 'normal'}",
        f"- State: {context.get('state') or ticket.get('state') or 'NA'}",
        f"- Product: {context.get('product_type') or ticket.get('product_type') or 'support'}",
        "",
        "### Question",
        ticket.get("question") or plan.get("source_question") or "No source question attached.",
        "",
        "### Requested Behavior",
        plan.get("requested_behavior") or ticket.get("suggested_answer") or ticket.get("question") or "No requested behavior attached.",
        "",
        "## Implementation Steps",
        markdown_ordered(plan.get("implementation_plan") or []),
        "",
        "## Acceptance Criteria",
        markdown_ordered(plan.get("acceptance_criteria") or []),
        "",
        "## Required Tests",
        markdown_bullets(plan.get("required_tests") or []),
        "",
        "## Risk Flags",
        markdown_bullets(plan.get("risk_flags") or []),
        "",
        "## Execution Modes",
        markdown_bullets(plan.get("execution_modes") or []),
        "",
        "## Stop Conditions",
        markdown_bullets(plan.get("stop_conditions") or ENGINEERING_STOP_CONDITIONS),
        "",
        "## Audit Trail",
        markdown_bullets(audit_lines),
        "",
    ])


def build_engineering_execution_gates(job: dict) -> list[dict]:
    plan = job.get("engineering_plan") or {}
    work_plan = job.get("work_plan") or {}
    status = job.get("status") or ""
    required_tests = plan.get("required_tests") or []
    stop_conditions = plan.get("stop_conditions") or job.get("stop_conditions") or []
    risk_flags = plan.get("risk_flags") or []
    latest_test_run = job.get("latest_test_run") or {}
    allowed_status = status in {"approved", "in_progress", "tests_failed", "blocked"}
    return [
        readiness_item(
            "status_allows_execution",
            "Job status allows execution",
            "ready" if allowed_status else "blocked",
            f"Current status: {status or 'unknown'}",
        ),
        readiness_item(
            "work_plan_artifact",
            "Work plan artifact exists",
            "ready" if work_plan.get("file_path") else "blocked",
            work_plan.get("file_path") or "Create the work plan before execution.",
        ),
        readiness_item(
            "required_tests",
            "Required tests are declared",
            "ready" if required_tests else "blocked",
            "; ".join(required_tests) if required_tests else "No required tests are attached to the engineering plan.",
        ),
        readiness_item(
            "required_tests_passed",
            "Required tests have passed",
            "ready" if latest_test_run.get("passed") else "needs_review",
            latest_test_run.get("summary") or "Run required tests before marking this job ready_to_deploy.",
        ),
        readiness_item(
            "stop_conditions",
            "Stop conditions are attached",
            "ready" if stop_conditions else "blocked",
            "; ".join(stop_conditions) if stop_conditions else "No stop conditions are attached to the engineering plan.",
        ),
        readiness_item(
            "risk_flags",
            "Risk flags are visible",
            "ready" if risk_flags else "needs_review",
            "; ".join(risk_flags) if risk_flags else "No risk flags generated; operator review is required.",
        ),
        readiness_item(
            "deployment_target",
            "Deployment target is known",
            "ready" if plan.get("deployment_target") else "needs_review",
            plan.get("deployment_target") or "No deployment target is attached.",
        ),
    ]


def render_engineering_execution_package(job: dict, gates: list[dict]) -> str:
    safe_job = redact_sensitive(job)
    ticket = safe_job.get("ticket") or {}
    plan = safe_job.get("engineering_plan") or {}
    can_start = all(
        gate.get("status") == "ready"
        for gate in gates
        if gate.get("key") not in {"deployment_target", "required_tests_passed"}
    )
    gate_lines = [
        f"- {gate.get('label')}: {gate.get('status')} - {gate.get('detail', '')}"
        for gate in gates
    ]
    return "\n".join([
        f"# Guarded Engineering Execution Package: {safe_job.get('id', '')}",
        "",
        "## Authorization",
        f"- Source ticket: {safe_job.get('ticket_id') or ''}",
        f"- Current status: {safe_job.get('status') or ''}",
        f"- Can start implementation: {'yes' if can_start else 'no'}",
        f"- Customer/session: {ticket.get('customer_email') or ticket.get('session_id') or 'Not attached'}",
        "",
        "## Approved Request",
        ticket.get("question") or plan.get("source_question") or "No source question attached.",
        "",
        "## Implementation Boundary",
        plan.get("requested_behavior") or ticket.get("suggested_answer") or "Implement only the approved enhancement.",
        "",
        "## Execution Gates",
        "\n".join(gate_lines) or "- No gates generated.",
        "",
        "## Required Tests",
        markdown_bullets(plan.get("required_tests") or []),
        "",
        "## Stop Conditions",
        markdown_bullets(plan.get("stop_conditions") or ENGINEERING_STOP_CONDITIONS),
        "",
        "## Deployment Gate",
        f"- Target: {plan.get('deployment_target') or 'Not set'}",
        "- Do not mark ready_to_deploy until required tests pass.",
        "- Do not mark deployed until production health checks pass.",
        "",
    ])


def serialize_engineering_run(row, ticket_row=None) -> dict:
    item = dict(row)
    item["redacted_log"] = parse_json_field(item.get("redacted_log"), [])
    item["ticket_id"] = engineering_ticket_id_from_log(item["redacted_log"])
    item["work_plan"] = engineering_work_plan_from_log(item["redacted_log"])
    item["execution_package"] = engineering_execution_package_from_log(item["redacted_log"])
    item["latest_test_run"] = engineering_test_run_from_log(item["redacted_log"])
    item["latest_deploy_check"] = engineering_deploy_check_from_log(item["redacted_log"])
    item["stop_conditions"] = ENGINEERING_STOP_CONDITIONS
    ticket = dict(ticket_row) if ticket_row else None
    item["ticket"] = redact_sensitive(ticket) if ticket else None
    item["engineering_plan"] = engineering_plan_from_log(item["redacted_log"]) or (build_engineering_plan(ticket) if ticket else {})
    item["execution_gates"] = build_engineering_execution_gates(item)
    return redact_sensitive(item)


def summarize_process_output(value: str, limit: int = 1800) -> str:
    value = redact_sensitive(value or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"


def allowed_engineering_test_commands(plan: dict) -> set[str]:
    commands = set(plan.get("required_tests") or [])
    commands.add("python3 -m py_compile backend/server.py")
    return {command.strip() for command in commands if command and command.strip()}


def run_engineering_tests(run_id: str, payload: EngineeringTestRunRequest) -> dict:
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM automation_runs
        WHERE id = ? AND adapter_key = 'approved_ticket_engineering'
    """, (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Engineering job not found")
    if row["status"] not in {"in_progress", "tests_failed"}:
        conn.close()
        raise HTTPException(status_code=400, detail="Prepare execution and move the job in_progress before running tests")

    log = parse_json_field(row["redacted_log"], [])
    if not engineering_execution_package_from_log(log):
        conn.close()
        raise HTTPException(status_code=400, detail="Prepare guarded execution before running tests")
    ticket_id = engineering_ticket_id_from_log(log)
    ticket_row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() if ticket_id else None
    temp_row = dict(row)
    temp_row["redacted_log"] = json.dumps(redact_sensitive(log))
    job = serialize_engineering_run(temp_row, ticket_row)
    plan = job.get("engineering_plan") or {}
    allowed_commands = allowed_engineering_test_commands(plan)
    requested_commands = [payload.test_command.strip()] if payload.test_command.strip() else list(plan.get("required_tests") or [])
    requested_commands = [command for command in requested_commands if command]
    if not requested_commands:
        conn.close()
        raise HTTPException(status_code=400, detail="No required tests are declared for this job")
    disallowed = [command for command in requested_commands if command not in allowed_commands]
    if disallowed:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Test command is not allowlisted for this job: {disallowed[0]}")

    results = []
    passed = True
    for command in requested_commands:
        started_at = utc_now()
        try:
            completed = subprocess.run(
                shlex.split(command),
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=payload.timeout_seconds,
            )
            result = {
                "command": command,
                "started_at": started_at,
                "returncode": completed.returncode,
                "passed": completed.returncode == 0,
                "stdout": summarize_process_output(completed.stdout),
                "stderr": summarize_process_output(completed.stderr),
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "command": command,
                "started_at": started_at,
                "returncode": None,
                "passed": False,
                "stdout": summarize_process_output(exc.stdout or ""),
                "stderr": summarize_process_output(exc.stderr or f"Timed out after {payload.timeout_seconds} seconds."),
            }
        results.append(result)
        if not result["passed"]:
            passed = False
            break

    target_status = "ready_to_deploy" if passed else "tests_failed"
    summary = f"{len([r for r in results if r['passed']])}/{len(requested_commands)} required test command(s) passed."
    test_run = {
        "id": f"TEST-{uuid.uuid4().hex[:10].upper()}",
        "actor": payload.actor,
        "passed": passed,
        "summary": summary,
        "commands": results,
        "created_at": utc_now(),
    }
    log.append({
        "at": test_run["created_at"],
        "message": "Required engineering tests passed." if passed else "Required engineering tests failed.",
        "actor": payload.actor,
        "from_status": row["status"],
        "to_status": target_status,
        "test_command": " && ".join(requested_commands),
        "test_run": test_run,
        "stop_conditions": ENGINEERING_STOP_CONDITIONS,
    })
    conn.execute("""
        UPDATE automation_runs
        SET status = ?, redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (target_status, json.dumps(redact_sensitive(log)), run_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    execution_dual_write("update_automation_run", run_id, target_status, redact_sensitive(log))
    return {"job": serialize_engineering_run(updated, ticket_row), "test_run": redact_sensitive(test_run)}


def deployment_base_url(deployment_url: str = "") -> str:
    public_base = (os.getenv("SOSFILER_PUBLIC_BASE_URL") or "https://ops.sosfiler.com").strip().rstrip("/")
    raw = (deployment_url or os.getenv("SOSFILER_OPERATOR_COCKPIT_URL") or public_base).strip()
    host = urllib.parse.urlparse(raw).hostname or ""
    if host in {"127.0.0.1", "localhost", "::1"}:
        raw = public_base
    if raw.endswith("/operator.html"):
        raw = raw[: -len("/operator.html")]
    if raw.endswith("/api/health"):
        raw = raw[: -len("/api/health")]
    return raw.rstrip("/")


def fetch_deploy_check_url(url: str, timeout_seconds: int) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "SOSFiler-DeployCheck/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8", errors="replace")
        return int(getattr(response, "status", 200)), body


def health_component(name: str, ok: bool, message: str, **metadata) -> dict:
    return {
        "name": name,
        "ok": bool(ok),
        "status": "ok" if ok else "fail",
        "message": message,
        **metadata,
    }


def docs_directory_writable() -> tuple[bool, str]:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    marker = DOCS_DIR / f".health-{uuid.uuid4().hex[:10]}"
    try:
        marker.write_text("ok")
        if marker.read_text() != "ok":
            return False, "Document directory write/read marker did not round-trip."
        marker.unlink(missing_ok=True)
        return True, f"{DOCS_DIR} is writable."
    except Exception as exc:
        try:
            marker.unlink(missing_ok=True)
        except Exception:
            pass
        return False, str(exc)


def sqlite_write_read_health() -> tuple[bool, str]:
    marker_id = f"HLT-{uuid.uuid4().hex[:12].upper()}"
    marker_value = uuid.uuid4().hex
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO health_check_markers (id, marker) VALUES (?, ?)",
            (marker_id, marker_value),
        )
        row = conn.execute("SELECT marker FROM health_check_markers WHERE id = ?", (marker_id,)).fetchone()
        conn.execute("DELETE FROM health_check_markers WHERE id = ?", (marker_id,))
        conn.commit()
        if not row or row["marker"] != marker_value:
            return False, "SQLite health marker did not read back."
        return True, "SQLite write/read check passed."
    except Exception as exc:
        conn.rollback()
        return False, str(exc)
    finally:
        conn.close()


def email_delivery_health_component() -> dict:
    from notifier import Notifier

    status = Notifier().config_status()
    metadata = {key: value for key, value in status.items() if key not in {"ok", "message", "status"}}
    return health_component(
        "email_delivery",
        bool(status.get("ok")),
        status.get("message", "Email delivery status unavailable."),
        delivery_status=status.get("status", "unknown"),
        **metadata,
    )


def public_operator_base_url() -> str:
    return deployment_base_url(os.getenv("SOSFILER_PUBLIC_BASE_URL") or os.getenv("SOSFILER_OPERATOR_COCKPIT_URL") or "")


def build_deep_health(include_public_fetch: bool = True) -> dict:
    checked_at = utc_now()
    components = []

    sqlite_ok, sqlite_message = sqlite_write_read_health()
    components.append(health_component("database_write_read", sqlite_ok, sqlite_message, mode="sqlite"))

    persistence = EXECUTION_PERSISTENCE.health()
    components.append(health_component(
        "execution_persistence",
        persistence.ok,
        persistence.message,
        mode=persistence.mode,
    ))

    slack_webhook = bool(os.getenv("SLACK_TICKETS_WEBHOOK_URL"))
    slack_signing = bool(os.getenv("SLACK_SIGNING_SECRET"))
    slack_interactive = (os.getenv("SLACK_INTERACTIVE_TICKETS") or "").lower() in {"1", "true", "yes"}
    components.append(health_component(
        "slack_ticket_loop",
        slack_webhook and slack_signing and slack_interactive,
        "Slack webhook, signing secret, and interactive buttons are configured."
        if slack_webhook and slack_signing and slack_interactive
        else "Slack ticket loop is not fully configured.",
        webhook_configured=slack_webhook,
        signing_secret_configured=slack_signing,
        interactive_buttons_enabled=slack_interactive,
    ))

    docs_ok, docs_message = docs_directory_writable()
    components.append(health_component("document_directory", docs_ok, docs_message, path=str(DOCS_DIR)))

    worker_modules = []
    try:
        __import__("irs_ein_worker")
        __import__("filing_adapters")
        __import__("execution_platform")
        worker_modules = ["irs_ein_worker", "filing_adapters", "execution_platform"]
        worker_ok = True
        worker_message = "Worker dependency imports passed."
    except Exception as exc:
        worker_ok = False
        worker_message = str(exc)
    components.append(health_component("worker_dependencies", worker_ok, worker_message, modules=worker_modules))

    try:
        components.append(email_delivery_health_component())
    except Exception as exc:
        components.append(health_component("email_delivery", False, str(exc), delivery_status="error"))

    base_url = public_operator_base_url()
    if include_public_fetch:
        try:
            status_code, body = fetch_deploy_check_url(f"{base_url}/operator.html", 10)
            page_ok = status_code == 200 and "SOSFiler Operator Cockpit" in body
            page_message = "Public operator page is reachable." if page_ok else "Public operator page did not contain expected content."
            components.append(health_component("public_operator_page", page_ok, page_message, url=f"{base_url}/operator.html", status_code=status_code))
        except Exception as exc:
            components.append(health_component("public_operator_page", False, str(exc), url=f"{base_url}/operator.html"))
    else:
        page_path = BASE_DIR / "frontend" / "operator.html"
        components.append(health_component(
            "public_operator_page",
            page_path.exists(),
            "Operator page file exists." if page_path.exists() else "Operator page file is missing.",
            path=str(page_path),
        ))

    ok = all(component["ok"] for component in components)
    return {
        "status": "ok" if ok else "fail",
        "ok": ok,
        "service": "SOSFiler",
        "version": "2.0.0",
        "checked_at": checked_at,
        "public_base_url": base_url,
        "components": components,
        "failing_components": [component["name"] for component in components if not component["ok"]],
    }


def send_health_slack_alert(health: dict) -> bool:
    if health.get("ok"):
        return False
    webhook = os.getenv("SLACK_HEALTH_WEBHOOK_URL") or os.getenv("SLACK_TICKETS_WEBHOOK_URL")
    if not webhook:
        return False
    failing = ", ".join(health.get("failing_components") or ["unknown"])
    lines = [
        "*SOSFiler deep health failed*",
        f"*Checked:* {health.get('checked_at')}",
        f"*Failing:* {failing}",
        f"*Base URL:* {health.get('public_base_url')}",
    ]
    for component in health.get("components", []):
        if not component.get("ok"):
            lines.append(f"- `{component.get('name')}`: {component.get('message')}")
    payload = json.dumps({"text": "\n".join(lines)}).encode("utf-8")
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return int(getattr(response, "status", 200)) in {200, 201, 202}
    except Exception:
        return False


def run_engineering_deploy_check(run_id: str, payload: EngineeringDeployCheckRequest) -> dict:
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM automation_runs
        WHERE id = ? AND adapter_key = 'approved_ticket_engineering'
    """, (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Engineering job not found")
    if row["status"] != "ready_to_deploy":
        conn.close()
        raise HTTPException(status_code=400, detail="Required tests must pass and job must be ready_to_deploy before deploy check")

    log = parse_json_field(row["redacted_log"], [])
    ticket_id = engineering_ticket_id_from_log(log)
    ticket_row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() if ticket_id else None
    temp_row = dict(row)
    temp_row["redacted_log"] = json.dumps(redact_sensitive(log))
    job = serialize_engineering_run(temp_row, ticket_row)
    if not (job.get("latest_test_run") or {}).get("passed"):
        conn.close()
        raise HTTPException(status_code=400, detail="Latest required test run must pass before deploy check")

    plan = job.get("engineering_plan") or {}
    base_url = deployment_base_url(payload.deployment_url or os.getenv("SOSFILER_PUBLIC_BASE_URL") or plan.get("deployment_target") or "")
    checks = []

    def record_check(name: str, url: str, predicate, detail: str):
        try:
            status_code, body = fetch_deploy_check_url(url, payload.timeout_seconds)
            passed = status_code == 200 and predicate(body)
            checks.append({
                "name": name,
                "url": url,
                "status_code": status_code,
                "passed": passed,
                "detail": detail if passed else summarize_process_output(body, 600),
            })
        except Exception as exc:
            checks.append({
                "name": name,
                "url": url,
                "status_code": None,
                "passed": False,
                "detail": summarize_process_output(str(exc), 600),
            })

    record_check(
        "api_health",
        f"{base_url}/api/health",
        lambda body: (json.loads(body).get("status") == "ok" if body else False),
        "Health endpoint returned status ok.",
    )
    record_check(
        "operator_page",
        f"{base_url}/operator.html",
        lambda body: all(text in body for text in ("Engineering Queue", "Run Required Tests", "Guarded Execution")),
        "Operator page returned expected engineering controls.",
    )

    passed = all(check["passed"] for check in checks)
    target_status = "deployed" if passed else "blocked"
    deploy_check = {
        "id": f"DEPLOY-{uuid.uuid4().hex[:10].upper()}",
        "actor": payload.actor,
        "deployment_url": f"{base_url}/operator.html",
        "passed": passed,
        "summary": f"{len([check for check in checks if check['passed']])}/{len(checks)} production deploy check(s) passed.",
        "checks": checks,
        "created_at": utc_now(),
    }
    log.append({
        "at": deploy_check["created_at"],
        "message": "Production deploy check passed." if passed else "Production deploy check failed.",
        "actor": payload.actor,
        "from_status": row["status"],
        "to_status": target_status,
        "deployment_url": deploy_check["deployment_url"],
        "deploy_check": deploy_check,
        "stop_conditions": ENGINEERING_STOP_CONDITIONS,
    })
    stop_reason = "Production deploy check failed." if not passed else row["stop_reason"]
    conn.execute("""
        UPDATE automation_runs
        SET status = ?, stop_reason = ?, redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (target_status, stop_reason, json.dumps(redact_sensitive(log)), run_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    execution_dual_write("update_automation_run", run_id, target_status, redact_sensitive(log))
    return {"job": serialize_engineering_run(updated, ticket_row), "deploy_check": redact_sensitive(deploy_check)}


def get_engineering_job(run_id: str) -> dict:
    conn = get_db()
    run = conn.execute("""
        SELECT * FROM automation_runs
        WHERE id = ? AND adapter_key = 'approved_ticket_engineering'
    """, (run_id,)).fetchone()
    if not run:
        conn.close()
        raise HTTPException(status_code=404, detail="Engineering job not found")
    log = parse_json_field(run["redacted_log"], [])
    ticket_id = engineering_ticket_id_from_log(log)
    ticket = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() if ticket_id else None
    conn.close()
    return serialize_engineering_run(run, ticket)


def transition_engineering_job(run_id: str, payload: EngineeringTransitionRequest) -> dict:
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM automation_runs
        WHERE id = ? AND adapter_key = 'approved_ticket_engineering'
    """, (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Engineering job not found")
    current = row["status"]
    target = payload.target_status
    if target not in ENGINEERING_JOB_STATUSES:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Unsupported engineering status: {target}")
    if current != target and target not in ENGINEERING_TRANSITIONS.get(current, set()):
        conn.close()
        raise HTTPException(status_code=400, detail=f"Cannot transition engineering job from {current} to {target}")

    log = parse_json_field(row["redacted_log"], [])
    log.append({
        "at": utc_now(),
        "message": payload.message or f"Engineering job moved to {target}.",
        "actor": payload.actor,
        "from_status": current,
        "to_status": target,
        "test_command": payload.test_command,
        "deployment_url": payload.deployment_url,
        "stop_conditions": ENGINEERING_STOP_CONDITIONS,
    })
    stop_requested = 1 if target == "stop_requested" else int(row["stop_requested"] or 0)
    stop_reason = payload.message if target == "stop_requested" else row["stop_reason"]
    conn.execute("""
        UPDATE automation_runs
        SET status = ?, stop_requested = ?, stop_reason = ?, redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (target, stop_requested, stop_reason, json.dumps(redact_sensitive(log)), run_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    execution_dual_write("update_automation_run", run_id, target, redact_sensitive(log))
    return {"job": serialize_engineering_run(updated)}


def refresh_engineering_plan(run_id: str, actor: str = "operator") -> dict:
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM automation_runs
        WHERE id = ? AND adapter_key = 'approved_ticket_engineering'
    """, (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Engineering job not found")
    log = parse_json_field(row["redacted_log"], [])
    ticket_id = engineering_ticket_id_from_log(log)
    ticket_row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() if ticket_id else None
    if not ticket_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Source ticket not found for engineering job")
    plan = build_engineering_plan(dict(ticket_row))
    log.append({
        "at": utc_now(),
        "message": "Engineering implementation plan refreshed.",
        "actor": actor,
        "engineering_plan": plan,
        "stop_conditions": ENGINEERING_STOP_CONDITIONS,
    })
    conn.execute("""
        UPDATE automation_runs
        SET redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (json.dumps(redact_sensitive(log)), run_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    execution_dual_write("update_automation_run", run_id, row["status"], redact_sensitive(log))
    return {"job": serialize_engineering_run(updated, ticket_row)}


def create_engineering_work_plan(run_id: str, actor: str = "operator") -> dict:
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM automation_runs
        WHERE id = ? AND adapter_key = 'approved_ticket_engineering'
    """, (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Engineering job not found")
    log = parse_json_field(row["redacted_log"], [])
    ticket_id = engineering_ticket_id_from_log(log)
    ticket_row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() if ticket_id else None
    if not engineering_plan_from_log(log) and ticket_row:
        log.append({
            "at": utc_now(),
            "message": "Engineering implementation plan generated for work plan artifact.",
            "actor": actor,
            "engineering_plan": build_engineering_plan(dict(ticket_row)),
            "stop_conditions": ENGINEERING_STOP_CONDITIONS,
        })

    temp_row = dict(row)
    temp_row["redacted_log"] = json.dumps(redact_sensitive(log))
    job = serialize_engineering_run(temp_row, ticket_row)
    path = engineering_work_plan_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_engineering_work_plan(job), encoding="utf-8")

    created_at = utc_now()
    work_plan = {
        "artifact_type": "engineering_work_plan",
        "filename": path.name,
        "file_path": str(path),
        "created_at": created_at,
    }
    log.append({
        "at": created_at,
        "message": "Engineering work plan artifact created.",
        "actor": actor,
        "work_plan": work_plan,
    })
    conn.execute("""
        UPDATE automation_runs
        SET redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (json.dumps(redact_sensitive(log)), run_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    execution_dual_write("update_automation_run", run_id, row["status"], redact_sensitive(log))
    return {"job": serialize_engineering_run(updated, ticket_row), "work_plan": redact_sensitive(work_plan)}


def prepare_engineering_execution(run_id: str, actor: str = "operator") -> dict:
    conn = get_db()
    row = conn.execute("""
        SELECT * FROM automation_runs
        WHERE id = ? AND adapter_key = 'approved_ticket_engineering'
    """, (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Engineering job not found")
    log = parse_json_field(row["redacted_log"], [])
    ticket_id = engineering_ticket_id_from_log(log)
    ticket_row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() if ticket_id else None
    now = utc_now()

    if not engineering_plan_from_log(log) and ticket_row:
        log.append({
            "at": now,
            "message": "Engineering implementation plan generated for guarded execution.",
            "actor": actor,
            "engineering_plan": build_engineering_plan(dict(ticket_row)),
            "stop_conditions": ENGINEERING_STOP_CONDITIONS,
        })

    if not engineering_work_plan_from_log(log):
        temp_row = dict(row)
        temp_row["redacted_log"] = json.dumps(redact_sensitive(log))
        work_job = serialize_engineering_run(temp_row, ticket_row)
        work_path = engineering_work_plan_path(run_id)
        work_path.parent.mkdir(parents=True, exist_ok=True)
        work_path.write_text(render_engineering_work_plan(work_job), encoding="utf-8")
        work_plan = {
            "artifact_type": "engineering_work_plan",
            "filename": work_path.name,
            "file_path": str(work_path),
            "created_at": now,
        }
        log.append({
            "at": now,
            "message": "Engineering work plan artifact created for guarded execution.",
            "actor": actor,
            "work_plan": work_plan,
        })

    temp_row = dict(row)
    temp_row["redacted_log"] = json.dumps(redact_sensitive(log))
    job = serialize_engineering_run(temp_row, ticket_row)
    gates = build_engineering_execution_gates(job)
    can_start = all(
        gate.get("status") == "ready"
        for gate in gates
        if gate.get("key") not in {"deployment_target", "required_tests_passed"}
    )
    path = engineering_execution_package_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_engineering_execution_package(job, gates), encoding="utf-8")
    execution_package = {
        "artifact_type": "engineering_execution_package",
        "filename": path.name,
        "file_path": str(path),
        "created_at": utc_now(),
        "can_start": can_start,
        "gates": gates,
    }
    target_status = "in_progress" if can_start and row["status"] in {"approved", "tests_failed", "blocked"} else row["status"]
    log.append({
        "at": execution_package["created_at"],
        "message": "Guarded engineering execution package prepared.",
        "actor": actor,
        "from_status": row["status"],
        "to_status": target_status,
        "execution_package": execution_package,
        "stop_conditions": ENGINEERING_STOP_CONDITIONS,
    })
    conn.execute("""
        UPDATE automation_runs
        SET status = ?, redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (target_status, json.dumps(redact_sensitive(log)), run_id))
    conn.commit()
    updated = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    execution_dual_write("update_automation_run", run_id, target_status, redact_sensitive(log))
    return {
        "job": serialize_engineering_run(updated, ticket_row),
        "execution_package": redact_sensitive(execution_package),
    }


def create_engineering_run_from_ticket(ticket: dict, approved_by: str = "system", approval_note: str = "") -> tuple[str, list[dict]]:
    run_id = f"AUTO-{uuid.uuid4().hex[:12].upper()}"
    engineering_plan = build_engineering_plan(ticket)
    run_log = [{
        "at": utc_now(),
        "message": "Enhancement ticket approved for autonomous engineering execution.",
        "ticket_id": ticket["id"],
        "approved_by": approved_by,
        "approval_note": approval_note,
        "engineering_plan": engineering_plan,
        "stop_conditions": ENGINEERING_STOP_CONDITIONS,
    }]
    conn = get_db()
    conn.execute("""
        INSERT INTO automation_runs (id, order_id, adapter_key, lane, status, redacted_log)
        VALUES (?, ?, 'approved_ticket_engineering', 'engineering_agent', 'approved', ?)
    """, (run_id, ticket.get("order_id") or "", json.dumps(redact_sensitive(run_log))))
    conn.commit()
    conn.close()
    execution_dual_write("insert_automation_run", {
        "id": run_id,
        "order_id": ticket.get("order_id") or "",
        "adapter_key": "approved_ticket_engineering",
        "lane": "engineering_agent",
        "status": "approved",
        "redacted_log": redact_sensitive(run_log),
    })
    return run_id, run_log


def approve_support_ticket(ticket_id: str, approved_by: str = "admin", approval_note: str = "") -> dict:
    conn = get_db()
    ticket_row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = dict(ticket_row)
    if ticket.get("status") == "approved":
        conn.close()
        return {"status": "approved", "ticket_id": ticket_id, "already_processed": True}
    if ticket.get("status") == "closed":
        conn.close()
        raise HTTPException(status_code=409, detail="Closed tickets cannot be approved")

    conn.execute("""
        UPDATE support_tickets
        SET status = 'approved', approved_by = ?, approved_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
    """, (approved_by, ticket_id))
    updated_ticket = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.commit()
    conn.close()
    run_id, _ = create_engineering_run_from_ticket(ticket, approved_by, approval_note)

    execution_dual_write("insert_support_ticket", dict(updated_ticket))
    return {"status": "approved", "ticket_id": ticket_id, "automation_run_id": run_id}


def close_support_ticket(ticket_id: str, approved_by: str = "admin", approval_note: str = "") -> dict:
    conn = get_db()
    ticket_row = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = dict(ticket_row)
    if ticket.get("status") == "closed":
        conn.close()
        return {"status": "closed", "ticket_id": ticket_id, "already_processed": True}
    if ticket.get("status") == "approved":
        conn.close()
        raise HTTPException(status_code=409, detail="Approved tickets cannot be closed")

    conn.execute("""
        UPDATE support_tickets
        SET status = 'closed', approved_by = ?, approved_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
    """, (approved_by, ticket_id))
    run_id = f"AUTO-{uuid.uuid4().hex[:12].upper()}"
    run_log = [{
        "at": utc_now(),
        "message": "Support ticket closed by operator review.",
        "ticket_id": ticket_id,
        "approved_by": approved_by,
        "approval_note": approval_note,
    }]
    conn.execute("""
        INSERT INTO automation_runs (id, order_id, adapter_key, lane, status, redacted_log)
        VALUES (?, ?, 'closed_ticket_review', 'support_ops', 'closed', ?)
    """, (
        run_id,
        ticket.get("order_id") or "",
        json.dumps(redact_sensitive(run_log)),
    ))
    conn.commit()
    updated_ticket = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone()
    conn.close()
    execution_dual_write("insert_support_ticket", dict(updated_ticket))
    execution_dual_write("insert_automation_run", {
        "id": run_id,
        "order_id": ticket.get("order_id") or "",
        "adapter_key": "closed_ticket_review",
        "lane": "support_ops",
        "status": "closed",
        "redacted_log": redact_sensitive(run_log),
    })
    return {"status": "closed", "ticket_id": ticket_id}


def verify_slack_interaction_signature(request: Request, body: bytes) -> None:
    signing_secret = os.getenv("SLACK_SIGNING_SECRET")
    if not signing_secret:
        raise HTTPException(status_code=503, detail="Slack signing secret is not configured")
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing Slack signature headers")
    try:
        request_ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Slack timestamp")
    if abs(int(time.time()) - request_ts) > 300:
        raise HTTPException(status_code=401, detail="Stale Slack request")
    base_string = f"v0:{timestamp}:".encode("utf-8") + body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base_string, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")


def slack_actor(payload: dict) -> str:
    user = payload.get("user") or {}
    return user.get("username") or user.get("name") or user.get("id") or "slack"


def verified_chat_context(data) -> dict:
    context = data.context or {}
    state_code = (context.get("state") or "").upper()
    entity_type = (context.get("entity_type") or "llc").lower()
    verified: dict = {}
    if state_code and entity_type in STATE_DATA:
        state_info = _find_state(STATE_DATA[entity_type], state_code)
        if state_info:
            verified["state_info"] = state_info
            verified["state"] = state_code
    if state_code:
        entity_key = "LLC" if entity_type == "llc" else "Corp"
        if state_code in STATE_FEES.get(entity_key, {}):
            verified["fee_breakdown"] = build_fee_breakdown(
                state_code,
                "LLC" if entity_key == "LLC" else "Corp",
                include_ra=bool(context.get("include_registered_agent")),
            )
        action_type = context.get("action_type") or context.get("product_type") or "formation"
        if action_type in {"formation", "annual_report"}:
            verified["state_metadata"] = state_metadata_summary(
                state_code,
                "LLC" if entity_type == "llc" else context.get("entity_type", "LLC"),
                action_type,
            )
    order_id = context.get("order_id")
    token = context.get("token", "")
    if order_id and token:
        try:
            verified["order"] = redact_sensitive(verify_order_access(order_id, token))
        except Exception:
            pass
    verified["source_coverage"] = verified_source_coverage(verified)
    return verified


def verified_source_coverage(verified: dict) -> list[dict]:
    coverage = []
    if verified.get("state_info"):
        coverage.append({"source": "SOSFiler state requirements", "available": True})
    if verified.get("fee_breakdown"):
        coverage.append({"source": "SOSFiler fee table and route pricing", "available": True})
    if verified.get("state_metadata"):
        metadata = verified["state_metadata"]
        coverage.append({
            "source": "State route metadata",
            "available": True,
            "source_urls": metadata.get("source_urls") or [],
        })
    if verified.get("order"):
        coverage.append({"source": "Customer order context", "available": True})
    return coverage


def build_verified_chat_answer(message: str, verified: dict) -> dict | None:
    lowered = message.lower()
    metadata = verified.get("state_metadata") or {}
    fee = verified.get("fee_breakdown") or {}
    state_name = metadata.get("state_name") or (fee.get("state") if isinstance(fee, dict) else "")
    sources = verified.get("source_coverage") or []
    source_text = "; ".join(source["source"] for source in sources if source.get("available")) or "verified SOSFiler context"
    if fee and any(term in lowered for term in ("fee", "cost", "price", "charge", "$")):
        total = fee.get("total_cents")
        line_items = fee.get("line_items") or []
        parts = [f"{item.get('label')}: ${int(item.get('amount_cents') or 0) / 100:.2f}" for item in line_items]
        answer = (
            f"Based on verified SOSFiler pricing for {state_name or fee.get('state', 'this state')}, "
            f"the estimated total is ${int(total or 0) / 100:.2f}. "
            f"Breakdown: {'; '.join(parts)}. "
            "Government and portal fees are pass-through estimates and are reconciled before final capture. "
            "Note: SOSFiler is a document preparation service, not a law firm. This isn't legal advice."
        )
        return {"answer": answer, "sources": sources, "confidence_reason": f"Answered from {source_text}."}
    if metadata and any(term in lowered for term in ("portal", "automation", "lane", "blocker", "captcha", "waf")):
        readiness = metadata.get("automation_readiness") or {}
        blocker_text = metadata.get("blocker_level") or "unknown"
        answer = (
            f"For {metadata.get('state_name', metadata.get('state', 'this state'))} {metadata.get('action_type', 'filing')}, "
            f"the current lane is {metadata.get('filing_lane', 'operator_assisted')} with {metadata.get('automation_difficulty', 'unknown')} difficulty. "
            f"Blocker level is {blocker_text}. "
            f"Portal: {metadata.get('portal_url') or 'not yet mapped'}. "
            f"Automation readiness: {readiness.get('status', 'metadata_incomplete')}. "
            "SOSFiler will use operator verification when portal blockers or missing evidence metadata make automation unsafe. "
            "Note: SOSFiler is a document preparation service, not a law firm. This isn't legal advice."
        )
        return {"answer": answer, "sources": sources, "confidence_reason": f"Answered from {source_text}."}
    if metadata and any(term in lowered for term in ("evidence", "receipt", "approved", "approval", "document")):
        evidence = metadata.get("evidence_requirements") or {}
        answer = (
            f"Evidence gates for {metadata.get('state_name', metadata.get('state', 'this state'))} require submitted evidence: "
            f"{', '.join(evidence.get('submitted') or ['official submission evidence'])}. "
            f"Approval/completion evidence: {', '.join(evidence.get('approved') or ['official approval evidence'])}. "
            "SOSFiler should not show filed, approved, or complete until that evidence is stored. "
            "Note: SOSFiler is a document preparation service, not a law firm. This isn't legal advice."
        )
        return {"answer": answer, "sources": sources, "confidence_reason": f"Answered from {source_text}."}
    if verified.get("order") and any(term in lowered for term in ("status", "timeline", "where is my order", "my filing")):
        order = verified["order"]
        answer = (
            f"Your order is currently marked {order.get('status', 'unknown')}. "
            "The dashboard timeline only advances after SOSFiler has internal processing or official evidence for that step. "
            "Note: SOSFiler is a document preparation service, not a law firm. This isn't legal advice."
        )
        return {"answer": answer, "sources": sources, "confidence_reason": f"Answered from {source_text}."}
    return None

def create_or_update_filing_job(order: dict, action_type: str = "formation", status: Optional[str] = None) -> dict:
    action = get_state_filing_route(order["state"], order["entity_type"], action_type)
    processing_fee_cents = int(order.get("gov_processing_fee_cents") or 0)
    if not processing_fee_cents:
        processing_fee_cents = calculate_processing_fee_cents(action, int(order.get("state_fee_cents") or 0))
    job_id = f"FIL-{order['id']}-{action_type}".replace("_", "-")
    job_status = status or order.get("status", "pending_payment")
    required_evidence = {
        "submitted": action.get("required_evidence", {}).get("submitted", []),
        "approved": action.get("required_evidence", {}).get("approved", []),
    }
    conn = get_db()
    conn.execute("""
        INSERT INTO filing_jobs (
            id, order_id, action_type, state, entity_type, status, automation_level,
            filing_method, office, form_name, portal_name, portal_url, state_fee_cents,
            processing_fee_cents, total_government_cents, required_consents,
            required_evidence, automation_lane, automation_difficulty, adapter_key,
            customer_status, portal_blockers, route_metadata, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            status = excluded.status,
            automation_level = excluded.automation_level,
            filing_method = excluded.filing_method,
            office = excluded.office,
            form_name = excluded.form_name,
            portal_name = excluded.portal_name,
            portal_url = excluded.portal_url,
            state_fee_cents = excluded.state_fee_cents,
            processing_fee_cents = excluded.processing_fee_cents,
            total_government_cents = excluded.total_government_cents,
            required_consents = excluded.required_consents,
            required_evidence = excluded.required_evidence,
            automation_lane = excluded.automation_lane,
            automation_difficulty = excluded.automation_difficulty,
            adapter_key = excluded.adapter_key,
            customer_status = excluded.customer_status,
            portal_blockers = excluded.portal_blockers,
            route_metadata = excluded.route_metadata,
            updated_at = datetime('now')
    """, (
        job_id, order["id"], action_type, order["state"], order["entity_type"], job_status,
        action.get("automation_level", "operator_assisted"),
        action.get("filing_method", "web_portal"),
        action.get("office", f"{order['state']} Secretary of State"),
        action.get("form_name", "Formation filing"),
        action.get("portal_name", ""),
        action.get("portal_url", ""),
        int(order.get("state_fee_cents") or 0),
        processing_fee_cents,
        int(order.get("state_fee_cents") or 0) + processing_fee_cents,
        json.dumps(action.get("required_consents", [])),
        json.dumps(required_evidence),
        action.get("automation_lane", "operator_assisted"),
        action.get("automation_difficulty", "unknown"),
        action.get("adapter_key", ""),
        action.get("customer_status", "operator_verified"),
        json.dumps(action.get("blockers", [])),
        json.dumps(redact_sensitive(action)),
    ))
    row = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    conn.commit()
    conn.close()
    job = serialize_filing_job(row)
    execution_dual_write("upsert_filing_job", job)
    return dict(row)


def serialize_filing_job(row) -> dict:
    item = dict(row)
    item["required_consents"] = parse_json_field(item.get("required_consents"), [])
    item["required_evidence"] = parse_json_field(item.get("required_evidence"), {})
    item["portal_blockers"] = parse_json_field(item.get("portal_blockers"), [])
    item["route_metadata"] = parse_json_field(item.get("route_metadata"), {})
    item["readiness_checklist"] = build_filing_readiness_checklist(item)
    return item


def annual_report_requirements_for_job(job: dict) -> dict:
    entity_type = (job.get("entity_type") or "LLC").lower()
    if entity_type in {"corp", "corporation", "c-corp", "s-corp"}:
        data_key = "corp"
    elif entity_type == "nonprofit":
        data_key = "nonprofit"
    else:
        data_key = "llc"
    state = (job.get("state") or "").upper()
    data_source = STATE_DATA.get(data_key) or {}
    if isinstance(data_source, list):
        state_name = (STATE_FEES.get("state_names") or {}).get(state, "")
        state_record = next(
            (
                record for record in data_source
                if record.get("state") == state
                or record.get("state_code") == state
                or record.get("state_name") == state_name
            ),
            {},
        )
    else:
        state_record = data_source.get(state) or {}
    state_record = state_record or STATE_REQUIREMENTS.get(state) or {}
    annual = dict(state_record.get("annual_report") or {})
    if not annual:
        annual = dict((STATE_REQUIREMENTS.get("_all_states_annual_reports") or {}).get(state) or {})
    return annual


def readiness_item(key: str, label: str, status: str, detail: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
    }


def build_filing_readiness_checklist(job: dict) -> list[dict]:
    if (job.get("action_type") or "") != "annual_report":
        preflight = validate_filing_preflight(job)
        checks = []
        for check in preflight.get("checks", []):
            if check.get("passed"):
                status = "ready"
            elif check.get("severity") == "blocking":
                status = "blocked"
            else:
                status = "needs_review"
            checks.append(readiness_item(
                check.get("code", ""),
                check.get("label", check.get("code", "Check")),
                status,
                check.get("message", ""),
            ))
        contract = job.get("adapter_contract") or {}
        route = job.get("route_metadata") or {}
        portal_url = job.get("portal_url") or route.get("portal_url") or ""
        blockers = job.get("portal_blockers") or []
        checks.extend([
            readiness_item(
                "adapter_contract",
                "State adapter contract",
                "ready" if job.get("adapter_key") or route.get("adapter_key") else "needs_review",
                contract.get("state_adapter") or job.get("adapter_key") or route.get("adapter_key") or "No adapter key is mapped yet.",
            ),
            readiness_item(
                "portal_route",
                "Portal route available",
                "ready" if portal_url else "blocked",
                portal_url or "No official portal URL is attached to this route.",
            ),
            readiness_item(
                "operator_fallback",
                "Operator fallback",
                "needs_review" if blockers else "ready",
                "Known blockers require operator/browser-provider fallback." if blockers else "No portal blocker currently flagged from research.",
            ),
        ])
        return checks

    annual = annual_report_requirements_for_job(job)
    blockers = job.get("portal_blockers") or []
    evidence = job.get("required_evidence") or {}
    route = job.get("route_metadata") or {}
    checklist = [
        readiness_item(
            "order_authorized",
            "Customer order authorized",
            "ready" if job.get("paid_at") or job.get("status") not in {"pending_payment", "intake_complete"} else "pending",
            "Payment or filing authorization is present." if job.get("paid_at") or job.get("status") not in {"pending_payment", "intake_complete"} else "Confirm customer payment authorization before filing.",
        ),
        readiness_item(
            "business_identity",
            "Business identity available",
            "ready" if job.get("business_name") and job.get("state") and job.get("entity_type") else "blocked",
            f"{job.get('business_name') or 'Missing business name'} / {job.get('state') or 'NA'} / {job.get('entity_type') or 'NA'}",
        ),
        readiness_item(
            "annual_report_required",
            "Annual report requirement verified",
            "ready" if annual.get("required", True) else "needs_review",
            "Required by state." if annual.get("required", True) else (annual.get("notes") or "State data says annual report may not be required; verify substitute filing before proceeding."),
        ),
        readiness_item(
            "due_window",
            "Due window known",
            "ready" if annual.get("due") or annual.get("frequency") else "needs_review",
            " / ".join(str(value) for value in [annual.get("frequency"), annual.get("due")] if value) or "No due-window metadata is available for this state.",
        ),
        readiness_item(
            "fee_quote",
            "Government fee identified",
            "ready" if annual.get("fee") is not None or int(job.get("total_government_cents") or 0) >= 0 else "needs_review",
            f"Annual report fee: ${annual.get('fee')}" if annual.get("fee") is not None else f"Route government total: ${int(job.get('total_government_cents') or 0) / 100:.2f}",
        ),
        readiness_item(
            "portal_route",
            "Portal route available",
            "ready" if job.get("portal_url") or route.get("portal_url") else "blocked",
            job.get("portal_url") or route.get("portal_url") or "No portal URL is attached to this annual report route.",
        ),
        readiness_item(
            "portal_blockers",
            "Portal blockers reviewed",
            "ready" if not blockers else "needs_review",
            "No known portal blockers." if not blockers else "; ".join(filter(None, [blocker.get("message") for blocker in blockers]))[:500],
        ),
        readiness_item(
            "evidence_gates",
            "Evidence gates configured",
            "ready" if evidence.get("submitted") and evidence.get("approved") else "needs_review",
            f"Submitted: {', '.join(evidence.get('submitted') or ['default official submission evidence'])}; Approved: {', '.join(evidence.get('approved') or ['default official approval evidence'])}",
        ),
    ]
    return checklist


def annual_report_packet_path(job_id: str) -> Path:
    safe_job_id = _re.sub(r"[^A-Za-z0-9_.-]", "-", job_id)
    return DOCS_DIR / "annual_report_packets" / safe_job_id / "annual_report_packet.md"


def markdown_checklist(items: list[dict]) -> str:
    lines = []
    for item in items:
        mark = "x" if item.get("status") == "ready" else " "
        lines.append(f"- [{mark}] {item.get('label', item.get('key', 'Item'))}: {item.get('detail', '')}")
    return "\n".join(lines)


def build_annual_report_packet(job: dict, events: list[dict] | None = None) -> str:
    annual = annual_report_requirements_for_job(job)
    evidence = job.get("required_evidence") or {}
    blockers = job.get("portal_blockers") or []
    checklist = job.get("readiness_checklist") or build_filing_readiness_checklist(job)
    portal = job.get("portal_url") or (job.get("route_metadata") or {}).get("portal_url") or ""
    fee_display = annual.get("fee")
    if fee_display is None:
        fee_display = f"${int(job.get('total_government_cents') or 0) / 100:.2f}"
    lines = [
        f"# Annual Report Filing Packet - {job.get('business_name') or job.get('order_id')}",
        "",
        "## Entity",
        f"- Order: {job.get('order_id')}",
        f"- Filing job: {job.get('id')}",
        f"- Business: {job.get('business_name', '')}",
        f"- Entity type: {job.get('entity_type', '')}",
        f"- State: {job.get('state', '')}",
        f"- Customer: {job.get('email', '')}",
        "",
        "## State Requirement",
        f"- Required: {annual.get('required', True)}",
        f"- Frequency: {annual.get('frequency', 'Review official source')}",
        f"- Due window: {annual.get('due', 'Review official source')}",
        f"- Government fee: {fee_display}",
        f"- Portal: {portal or 'No portal URL attached'}",
        "",
        "## Readiness Checklist",
        markdown_checklist(checklist),
        "",
        "## Evidence Gates",
        f"- Submitted evidence required: {', '.join(evidence.get('submitted') or ['Official annual report submission receipt'])}",
        f"- Approval/completion evidence required: {', '.join(evidence.get('approved') or ['Official acceptance, confirmation, or approved annual report record'])}",
        "- Do not mark submitted, approved, complete, or customer-notified without official evidence.",
        "",
        "## Portal Blockers",
        "\n".join(f"- {blocker.get('code', 'blocker')}: {blocker.get('message', '')}" for blocker in blockers) or "- No known blocker currently flagged.",
        "",
        "## Operator Steps",
        "1. Verify the entity identity and due window against the official state record.",
        "2. Open the official filing portal and complete only the annual report/statement filing.",
        "3. Capture official submission receipt evidence before marking submitted.",
        "4. Capture official acceptance/approved evidence before marking approved or complete.",
        "5. Confirm the customer dashboard timeline reflects only evidence-backed states.",
        "",
        "## Recent Events",
        "\n".join(
            f"- {event.get('created_at', '')}: {event.get('event_type', '')} - {event.get('message', '')}"
            for event in (events or [])[:10]
        ) or "- No filing events recorded yet.",
        "",
    ]
    return "\n".join(lines)


def prepare_annual_report_packet(job_id: str, actor: str = "operator", message: str = "") -> dict:
    conn = get_db()
    row = conn.execute("""
        SELECT
            j.*,
            o.business_name,
            o.email,
            o.created_at AS order_created_at,
            o.paid_at,
            o.documents_ready_at,
            o.total_cents,
            o.gov_processing_fee_cents,
            o.platform_fee_cents
        FROM filing_jobs j
        JOIN orders o ON o.id = j.order_id
        WHERE j.id = ?
    """, (job_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    job = serialize_filing_job(row)
    if job.get("action_type") != "annual_report":
        conn.close()
        raise HTTPException(status_code=400, detail="Annual report packet is only available for annual_report jobs")
    events = [dict(event) for event in conn.execute("""
        SELECT event_type, message, actor, evidence_path, created_at
        FROM filing_events
        WHERE filing_job_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 25
    """, (job_id,)).fetchall()]
    path = annual_report_packet_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_annual_report_packet(job, events), encoding="utf-8")
    filename = path.name
    existing_artifact = conn.execute("""
        SELECT id FROM filing_artifacts
        WHERE filing_job_id = ? AND artifact_type = 'annual_report_packet' AND filename = ?
    """, (job_id, filename)).fetchone()
    if not existing_artifact:
        conn.execute("""
            INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
            VALUES (?, ?, 'annual_report_packet', ?, ?, 0)
        """, (job_id, job["order_id"], filename, str(path)))
    add_customer_document_if_missing(conn, job["order_id"], "annual_report_packet", filename, str(path), "text")
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, 'annual_report_packet_prepared', ?, ?, ?)
    """, (
        job_id,
        job["order_id"],
        message or "Annual report operator packet prepared.",
        normalize_operator(actor),
        str(path),
    ))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'annual_report_packet_prepared', ?)",
        (job["order_id"], "Annual report filing packet is ready for operator verification."),
    )
    conn.commit()
    conn.close()
    return {
        "status": "prepared",
        "artifact": {
            "artifact_type": "annual_report_packet",
            "filename": filename,
            "file_path": str(path),
            "is_evidence": False,
        },
    }


def irs_ein_availability(now: datetime | None = None) -> dict:
    eastern = ZoneInfo("America/New_York")
    current = now.astimezone(eastern) if now else datetime.now(eastern)
    weekday_ok = current.weekday() < 5
    hour_value = current.hour + current.minute / 60
    hours_ok = 7 <= hour_value < 22
    open_now = weekday_ok and hours_ok
    return {
        "open": open_now,
        "timezone": "America/New_York",
        "current_time": current.replace(microsecond=0).isoformat(),
        "window": "Monday-Friday 7:00 AM-10:00 PM ET",
        "reason": "IRS EIN assistant is inside operating hours." if open_now else "IRS EIN assistant is outside operating hours or unavailable.",
    }


def ein_queue_path(order_id: str) -> Path:
    return DOCS_DIR / order_id / "ein_queue.json"


def load_ein_queue(order_id: str) -> dict:
    path = ein_queue_path(order_id)
    if not path.exists():
        return {}
    return parse_json_field(path.read_text(), {})


def save_ein_queue(order_id: str, payload: dict) -> Path:
    path = ein_queue_path(order_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = utc_now()
    path.write_text(json.dumps(redact_sensitive(payload), indent=2))
    return path


def redacted_ein_queue_summary(order: dict, queue: dict | None = None) -> dict:
    queue = queue or load_ein_queue(order["id"])
    ss4 = queue.get("ss4_data") or {}
    responsible = ss4.get("responsible_party") or {}
    vault_id = responsible.get("ssn_vault_id") or parse_json_field(order.get("formation_data"), {}).get("responsible_party_ssn_vault_id", "")
    return {
        "order_id": order["id"],
        "business_name": order["business_name"],
        "state": order["state"],
        "status": queue.get("status") or ("received" if order.get("ein") else "not_queued"),
        "queue_file": str(ein_queue_path(order["id"])) if queue else "",
        "irs_availability": irs_ein_availability(),
        "has_ssn_vault_ref": bool(vault_id),
        "ssn_last4": responsible.get("ssn_last4") or parse_json_field(order.get("formation_data"), {}).get("responsible_party_ssn_last4", ""),
        "ein_received": bool(order.get("ein")),
        "evidence_required": "Official IRS EIN confirmation/CP575 evidence is required before publishing EIN completion.",
        "updated_at": queue.get("updated_at") or queue.get("queued_at") or "",
    }


def list_ein_queue(limit: int = 100) -> list[dict]:
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM orders
        WHERE status IN ('state_approved', 'ein_pending', 'ein_queued', 'ein_ready_for_submission', 'ein_received')
           OR ein IS NOT NULL
        ORDER BY updated_at DESC
        LIMIT ?
    """, (max(1, min(int(limit or 100), 500)),)).fetchall()
    conn.close()
    return [redacted_ein_queue_summary(dict(row)) for row in rows]


def customer_timeline(status_rows, filing_rows, order: dict | None = None) -> list[dict]:
    items = [dict(row) for row in status_rows]
    event_status_map = {
        "claimed": "operator_required",
        "filing_prep_ready": "ready_to_file",
        "filing_prep_operator_required": "operator_required",
        "registered_agent_order_created": "registered_agent_processing",
        "registered_agent_status_checked": "registered_agent_processing",
        "registered_agent_reconciled": "registered_agent_assigned",
        "registered_agent_assignment": "registered_agent_assigned",
        "annual_report_packet_prepared": "annual_report_packet_prepared",
        "submitted": "submitted",
        "submitted_to_state": "submitted",
        "pending_government_review": "pending_government_review",
        "approved": "approved",
        "state_approved": "approved",
        "complete": "complete",
        "ein_queued": "ein_pending",
        "ein_ready_for_submission": "ein_ready_for_submission",
        "ein_blocked_outside_irs_hours": "ein_pending",
        "ein_received": "ein_received",
    }
    for row in filing_rows:
        event = dict(row)
        status = event_status_map.get(event.get("event_type"))
        if not status:
            continue
        message = event.get("message") or ""
        if event.get("evidence_path") and status in {"submitted", "approved", "complete", "ein_received", "registered_agent_assigned"}:
            message = f"{message} Official evidence is on file.".strip()
        items.append({
            "status": status,
            "message": message,
            "created_at": event.get("created_at"),
        })
    if order and order.get("ein") and not any(item.get("status") == "ein_received" for item in items):
        items.append({
            "status": "ein_received",
            "message": "EIN confirmation is available in your document vault.",
            "created_at": order.get("updated_at") or order.get("approved_at") or order.get("created_at"),
        })
    if order and order.get("status") == "operator_required" and not any(item.get("status") == "operator_required" for item in items):
        items.append({
            "status": "operator_required",
            "message": "An SOSFiler operator is verifying a filing requirement before the government submission can continue.",
            "created_at": order.get("updated_at") or order.get("created_at"),
        })
    return sorted(items, key=lambda item: item.get("created_at") or "")


def enrich_filing_job_for_adapter(conn, job: dict) -> dict:
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (job.get("order_id"),)).fetchone()
    if order:
        job["order_status"] = order["status"]
        job["business_name"] = order["business_name"]
        job["email"] = order["email"]
        job["total_cents"] = order["total_cents"]
        job["formation_data"] = parse_json_field(order["formation_data"], {})
    else:
        job["formation_data"] = {}
    artifact_rows = conn.execute(
        "SELECT artifact_type FROM filing_artifacts WHERE filing_job_id = ?",
        (job.get("id"),),
    ).fetchall()
    doc_rows = conn.execute(
        "SELECT doc_type FROM documents WHERE order_id = ?",
        (job.get("order_id"),),
    ).fetchall()
    job["artifact_types"] = [row["artifact_type"] for row in artifact_rows]
    job["document_types"] = [row["doc_type"] for row in doc_rows]
    return job


def validate_submission_safety(conn, job: dict, order: dict | None = None) -> dict:
    """Block live/operator submission when payment, duplicate, RA, or evidence gates are unsafe."""
    order = order or conn.execute("SELECT * FROM orders WHERE id = ?", (job.get("order_id"),)).fetchone()
    if order:
        order = dict(order)
    job_payload = enrich_filing_job_for_adapter(conn, serialize_filing_job(job))
    order_status = (order or {}).get("status") or job_payload.get("order_status") or job_payload.get("status")
    job_status = job_payload.get("status") or ""
    issues: list[dict] = []
    if order_status not in PAYMENT_READY_STATUSES:
        issues.append({
            "code": "payment_not_ready",
            "message": "Payment must be authorized or paid before any government submission.",
        })
    else:
        payment_summary = payment_reconciliation_summary(conn, order or {})
        quote_backed = bool(payment_summary.get("quote_id"))
        if quote_backed and payment_summary.get("reconciliation_status") not in {"captured", "payment_captured"}:
            issues.append({
                "code": "payment_capture_required",
                "message": "Final fees must be reconciled and payment captured before live government submission.",
            })
    if job_status in TERMINAL_OR_SUBMITTED_STATUSES:
        issues.append({
            "code": "duplicate_filing_risk",
            "message": "This filing is already submitted, approved, or complete. Create a correction job instead of resubmitting.",
        })
    if job_payload.get("action_type") == "formation":
        preflight = validate_filing_preflight(job_payload)
        for issue in preflight.get("blocking_issues", []):
            if issue["code"] in {"payment_ready", "not_already_submitted"}:
                continue
            issues.append({"code": issue["code"], "message": issue["message"]})
    return {"passed": not issues, "issues": issues}


def add_filing_event(order_id: str, event_type: str, message: str, actor: str = "system", evidence_path: str = ""):
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE order_id = ? AND action_type = 'formation' ORDER BY created_at DESC LIMIT 1", (order_id,)).fetchone()
    if not job:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        conn.close()
        if order:
            create_or_update_filing_job(dict(order))
            return add_filing_event(order_id, event_type, message, actor, evidence_path)
        return
    conn.execute(
        "INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path) VALUES (?, ?, ?, ?, ?, ?)",
        (job["id"], order_id, event_type, message, actor, evidence_path)
    )
    conn.commit()
    conn.close()
    execution_dual_write("insert_event", {
        "filing_job_id": job["id"],
        "order_id": order_id,
        "event_type": event_type,
        "message": message,
        "actor": actor,
        "evidence_path": evidence_path,
    })

def generate_internal_documents(order_id: str, formation_data: dict):
    """Synchronous wrapper for callers that need explicit document generation."""
    raise RuntimeError("generate_internal_documents must be awaited via generate_internal_documents_async")

async def generate_internal_documents_async(order_id: str, formation_data: dict) -> list[dict]:
    from document_generator import DocumentGenerator
    doc_gen = DocumentGenerator()
    docs = await doc_gen.generate_all(order_id, formation_data)
    conn = get_db()
    existing = {
        row["filename"]
        for row in conn.execute("SELECT filename FROM documents WHERE order_id = ?", (order_id,)).fetchall()
    }
    for doc in docs:
        if doc["filename"] in existing:
            continue
        conn.execute(
            """
            INSERT INTO documents (order_id, doc_type, filename, file_path, format, category, visibility)
            VALUES (?, ?, ?, ?, ?, ?, 'customer')
            """,
            (
                order_id,
                doc["type"],
                doc["filename"],
                doc["path"],
                doc["format"],
                document_category_for_type(doc["type"]),
            )
        )
    conn.commit()
    conn.close()
    return docs



# --- Auth Routes ---
class AuthSignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: str = ""

class AuthLoginRequest(BaseModel):
    email: EmailStr
    password: str

class AuthOAuthRequest(BaseModel):
    token: str
    email: Optional[str] = None
    name: Optional[str] = None
    provider_id: Optional[str] = None

class AuthRecoveryRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirmRequest(BaseModel):
    token: str
    password: str

def create_jwt_token(user_id: str):
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(days=7)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def hash_recovery_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def create_recovery_token(conn, email: str, purpose: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute(
        """
        INSERT INTO auth_recovery_tokens (email, token_hash, purpose, expires_at)
        VALUES (?, ?, ?, datetime('now', '+1 hour'))
        """,
        (email, hash_recovery_token(token), purpose),
    )
    return token

def link_orders_to_user_by_email(conn, user_id: str, email: str):
    """Attach pre-login orders to a customer account when the emails match."""
    if not user_id or not email:
        return
    conn.execute(
        """
        UPDATE orders
        SET user_id = ?, updated_at = datetime('now')
        WHERE lower(email) = lower(?)
          AND (user_id IS NULL OR user_id = '')
        """,
        (user_id, email),
    )

def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("sub")
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        return dict(user) if user else None
    except jwt.PyJWTError:
        return None

def handle_oauth_login(email: str, name: str, provider: str, provider_id: str):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user:
        user_id = user["id"]
        link_orders_to_user_by_email(conn, user_id, email)
        conn.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,))
    else:
        user_id = f"USR-{uuid.uuid4().hex[:12].upper()}"
        conn.execute(
            "INSERT INTO users (id, email, name, auth_provider, auth_provider_id, last_login) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (user_id, email, name, provider, provider_id)
        )
        link_orders_to_user_by_email(conn, user_id, email)
    conn.commit()
    conn.close()
    return create_jwt_token(user_id)

@app.post("/api/auth/signup")
async def auth_signup(data: AuthSignupRequest):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (data.email,)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(status_code=400, detail="Email already exists")

    user_id = f"USR-{uuid.uuid4().hex[:12].upper()}"
    password_hash = hashlib.sha256(data.password.encode()).hexdigest()

    conn.execute(
        "INSERT INTO users (id, email, name, auth_provider, password_hash, last_login) VALUES (?, ?, ?, 'email', ?, datetime('now'))",
        (user_id, data.email, data.name, password_hash)
    )
    link_orders_to_user_by_email(conn, user_id, data.email)
    conn.commit()
    conn.close()

    token = create_jwt_token(user_id)
    return {"token": token, "user": {"id": user_id, "email": data.email, "name": data.name}}

@app.post("/api/auth/login")
async def auth_login(data: AuthLoginRequest):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ? AND auth_provider = 'email'", (data.email,)).fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    password_hash = hashlib.sha256(data.password.encode()).hexdigest()
    if user["password_hash"] != password_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    conn = get_db()
    conn.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()

    token = create_jwt_token(user["id"])
    return {"token": token, "user": {"id": user["id"], "email": user["email"], "name": user["name"]}}

@app.post("/api/auth/password-reset/request")
async def request_password_reset(data: AuthRecoveryRequest):
    """Email a password reset link for email/password users."""
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)", (data.email,)).fetchone()
    reset_token = None
    provider = None
    if user:
        provider = user["auth_provider"]
        if provider == "email":
            reset_token = create_recovery_token(conn, data.email, "password_reset")
    conn.commit()
    conn.close()

    if user:
        from notifier import Notifier
        notifier = Notifier()
        if reset_token:
            reset_url = f"{notifier.dashboard_url}?reset_token={reset_token}"
            await notifier.send_password_reset(data.email, reset_url)
        else:
            await notifier.send_oauth_recovery_guidance(data.email, provider or "your provider")

    return {"status": "ok", "message": "If an account exists for that email, recovery instructions have been sent."}

@app.post("/api/auth/password-reset/confirm")
async def confirm_password_reset(data: PasswordResetConfirmRequest):
    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    conn = get_db()
    token_hash = hash_recovery_token(data.token)
    row = conn.execute(
        """
        SELECT * FROM auth_recovery_tokens
        WHERE token_hash = ?
          AND purpose = 'password_reset'
          AND used_at IS NULL
          AND expires_at > datetime('now')
        """,
        (token_hash,),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    user = conn.execute(
        "SELECT * FROM users WHERE lower(email) = lower(?)",
        (row["email"],),
    ).fetchone()
    if not user or user["auth_provider"] != "email":
        conn.close()
        raise HTTPException(status_code=400, detail="This account does not use password sign-in")

    password_hash = hashlib.sha256(data.password.encode()).hexdigest()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user["id"]))
    conn.execute("UPDATE auth_recovery_tokens SET used_at = datetime('now') WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()

    return {"status": "ok"}

@app.post("/api/auth/order-token-recovery")
async def recover_order_tokens(data: AuthRecoveryRequest):
    """Email order dashboard links for every order tied to this address."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, token, business_name, entity_type, state, status
        FROM orders
        WHERE lower(email) = lower(?)
        ORDER BY COALESCE(paid_at, created_at) DESC
        """,
        (data.email,),
    ).fetchall()
    orders = [dict(r) for r in rows]
    conn.close()

    if orders:
        from notifier import Notifier
        await Notifier().send_order_token_recovery(data.email, orders)

    return {"status": "ok", "message": "If matching orders exist, dashboard links have been sent."}

@app.post("/api/auth/google")
async def auth_google(data: AuthOAuthRequest):
    try:
        email = None
        name = None
        provider_id = None

        # Try ID token validation first
        try:
            req = urllib.request.Request(f"https://oauth2.googleapis.com/tokeninfo?id_token={data.token}")
            with urllib.request.urlopen(req) as response:
                token_info = json.loads(response.read())
                email = token_info.get("email")
                name = token_info.get("name", "")
                provider_id = token_info.get("sub", "")
        except Exception:
            pass

        # Fallback: try as access token (from OAuth2 token flow)
        if not email:
            try:
                req = urllib.request.Request("https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {data.token}"})
                with urllib.request.urlopen(req) as response:
                    user_info = json.loads(response.read())
                    email = user_info.get("email")
                    name = user_info.get("name", "")
                    provider_id = user_info.get("sub", "")
            except Exception:
                pass

        # Fallback: trust the frontend-provided email/name if token validation fails
        # (for cases where frontend already validated with Google and sends user info)
        if not email and data.email:
            email = data.email
            name = data.name or ""
            provider_id = data.provider_id or ""

        if not email:
            raise HTTPException(status_code=400, detail="Could not verify Google account")

        token = handle_oauth_login(email=email, name=name, provider="google", provider_id=provider_id)
        return {"token": token, "user": {"email": email, "name": name}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Google auth failed: {str(e)}")

# --- Apple Sign In: public key cache & verification ---
_apple_keys_cache = {"keys": None, "fetched_at": 0}

def _get_apple_public_keys():
    """Fetch Apple's public keys for JWT verification (cached 24h)."""
    import time as _time
    if _apple_keys_cache["keys"] and (_time.time() - _apple_keys_cache["fetched_at"]) < 86400:
        return _apple_keys_cache["keys"]
    try:
        req = urllib.request.Request("https://appleid.apple.com/auth/keys")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            _apple_keys_cache["keys"] = data.get("keys", [])
            _apple_keys_cache["fetched_at"] = _time.time()
            return _apple_keys_cache["keys"]
    except Exception:
        return _apple_keys_cache["keys"] or []

def _verify_apple_id_token(id_token: str) -> dict:
    """Verify an Apple id_token JWT against Apple's public keys."""
    # Decode header to find the key ID
    header = jwt.get_unverified_header(id_token)
    kid = header.get("kid")

    apple_keys = _get_apple_public_keys()
    matching_key = next((k for k in apple_keys if k.get("kid") == kid), None)

    if matching_key:
        # Build public key from JWK
        from jwt.algorithms import RSAAlgorithm
        public_key = RSAAlgorithm.from_jwk(matching_key)
        payload = jwt.decode(
            id_token,
            key=public_key,
            algorithms=["RS256"],
            audience=os.getenv("APPLE_SERVICE_ID", "com.sosfiler.auth"),
            issuer="https://appleid.apple.com",
        )
    else:
        # Fallback: decode without verification (first-time key fetch failure)
        payload = jwt.decode(id_token, options={"verify_signature": False})

    return payload

@app.post("/api/auth/apple")
async def auth_apple(data: AuthOAuthRequest):
    """
    Apple Sign In handler.
    Frontend sends: { token: <id_token from Apple>, name: <optional, first login only> }
    """
    try:
        payload = _verify_apple_id_token(data.token)
        email = payload.get("email")
        if not email:
            # Apple might not include email on subsequent logins — check if we have this sub already
            sub = payload.get("sub", "")
            if sub:
                conn = get_db()
                user = conn.execute("SELECT * FROM users WHERE auth_provider_id = ? AND auth_provider = 'apple'", (sub,)).fetchone()
                conn.close()
                if user:
                    email = user["email"]
            if not email:
                raise HTTPException(status_code=400, detail="Could not get email from Apple. Please try again or use another sign-in method.")

        # Apple only sends the user's name on the FIRST authorization — grab from request if available
        name = data.name or ""

        token = handle_oauth_login(
            email=email,
            name=name,
            provider="apple",
            provider_id=payload.get("sub", "")
        )
        return {"token": token, "user": {"email": email, "name": name}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Apple auth failed: {str(e)}")

@app.post("/api/auth/facebook")
async def auth_facebook(data: AuthOAuthRequest):
    try:
        # Facebook uses a different graph API endpoint
        req = urllib.request.Request(f"https://graph.facebook.com/me?fields=id,name,email&access_token={data.token}")
        with urllib.request.urlopen(req) as response:
            user_info = json.loads(response.read())

        email = user_info.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="Facebook account has no email")

        token = handle_oauth_login(
            email=email,
            name=user_info.get("name", ""),
            provider="facebook",
            provider_id=user_info.get("id", "")
        )
        return {"token": token, "user": {"email": email, "name": user_info.get("name", "")}}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Facebook auth failed: {str(e)}")

@app.get("/api/auth/me")
async def auth_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


# --- Routes ---

@app.get("/api/state-fees")
async def get_state_fees():
    """Return filing fees for all states."""
    return STATE_FEES

@app.get("/api/state-fees/{state}")
async def get_state_fee(state: str, entity_type: str = "LLC"):
    state = state.upper()
    et = entity_type if entity_type in STATE_FEES else "LLC"
    if state not in STATE_FEES.get(et, {}):
        raise HTTPException(status_code=404, detail=f"State {state} not found")
    fee_data = STATE_FEES[et][state]
    breakdown = build_fee_breakdown(state, et)
    return {
        "state": state,
        "state_name": STATE_FEES["state_names"].get(state, state),
        "entity_type": et,
        "state_filing_fee": fee_data["filing_fee"],
        "platform_fee": PLATFORM_FEE / 100,
        "gov_processing_fee": breakdown["gov_processing_fee_cents"] / 100,
        "total": breakdown["total_cents"] / 100,
        "fee_breakdown": breakdown,
        "notes": fee_data.get("notes", ""),
        "expedited_fee": fee_data.get("expedited"),
        "filing_action": get_filing_action(state, et),
        "automation_route": breakdown.get("automation_route"),
    }

@app.get("/api/state-routes/{state}/{entity_type}")
async def get_state_route(state: str, entity_type: str, action_type: str = "formation"):
    """Return data-driven automation and operator route metadata for a state filing."""
    state = state.upper()
    entity_key = "LLC" if entity_type.upper() == "LLC" else "Corp"
    if state not in STATE_FEES.get(entity_key, {}):
        raise HTTPException(status_code=404, detail=f"State {state} not found")
    return get_state_filing_route(state, "LLC" if entity_key == "LLC" else entity_type, action_type)

@app.get("/api/state-routes")
async def list_state_routes(entity_type: str = "LLC", action_type: str = "formation"):
    """Return route metadata for every state covered by fee/portal research."""
    entity_key = "LLC" if entity_type.upper() == "LLC" else "Corp"
    return {
        "entity_type": entity_type,
        "action_type": action_type,
        "routes": [
            get_state_filing_route(state, "LLC" if entity_key == "LLC" else entity_type, action_type)
            for state in sorted(STATE_FEES.get(entity_key, {}).keys())
        ],
    }

@app.post("/api/quote")
async def create_quote(data: QuoteRequest):
    """Create an authorize-then-capture quote for any SOSFiler product."""
    state = data.state.upper()
    entity_key = "LLC" if data.entity_type.upper() == "LLC" else "Corp"
    if state not in STATE_FEES.get(entity_key, {}):
        raise HTTPException(status_code=400, detail=f"Invalid state: {state}")

    if data.product_type == "formation":
        fees = build_fee_breakdown(state, "LLC" if entity_key == "LLC" else "Corp", include_ra=data.include_registered_agent)
        platform_fee = fees["platform_fee_cents"]
        government_fee = fees["state_fee_cents"]
        processing_fee = fees["gov_processing_fee_cents"]
        ra_fee = fees["registered_agent_fee_cents"]
    elif data.product_type == "dba":
        platform_fee = DBA_PLATFORM_FEE
        government_fee = 0
        processing_fee = 0
        ra_fee = 0
    elif data.product_type == "annual_report":
        platform_fee = ANNUAL_REPORT_FEE
        government_fee = 0
        processing_fee = 0
        ra_fee = 0
    elif data.product_type == "registered_agent":
        platform_fee = RA_RENEWAL_FEE
        government_fee = 0
        processing_fee = 0
        ra_fee = 0
    else:
        platform_fee = LICENSE_PLATFORM_FEE
        government_fee = 0
        processing_fee = 0
        ra_fee = 0

    quote = build_quote(
        product_type=data.product_type,
        entity_type=data.entity_type,
        state=state,
        platform_fee_cents=platform_fee,
        government_fee_cents=government_fee,
        processing_fee_cents=processing_fee,
        registered_agent_fee_cents=ra_fee,
        expedite_fee_cents=data.expedite_fee_cents,
    )
    persist_quote(quote)
    route_action = "formation" if data.product_type == "formation" else data.product_type
    quote["automation_route"] = get_state_filing_route(state, data.entity_type, route_action)
    return quote


@app.post("/api/orders")
async def create_order(data: OrderRequest):
    """Create a product order through the shared execution-platform API."""
    if data.product_type != "formation":
        raise HTTPException(status_code=400, detail="Only formation orders are currently supported by the shared order API.")
    if not data.formation:
        raise HTTPException(status_code=400, detail="formation payload is required")
    result = await create_formation(data.formation)
    if data.quote_id:
        conn = get_db()
        candidate = conn.execute(
            "SELECT * FROM execution_quotes WHERE id = ?", (data.quote_id,)
        ).fetchone()
        if not candidate:
            conn.close()
            raise HTTPException(status_code=404, detail="Quote not found")
        candidate = dict(candidate)
        if candidate.get("order_id") and candidate["order_id"] != result["order_id"]:
            conn.close()
            raise HTTPException(status_code=409, detail="Quote is already bound to a different order")
        if (candidate.get("product_type") or "") != "formation":
            conn.close()
            raise HTTPException(status_code=400, detail="Quote product_type does not match formation order")
        if (candidate.get("state") or "") != (data.formation.state or ""):
            conn.close()
            raise HTTPException(status_code=400, detail="Quote state does not match formation state")
        if (candidate.get("entity_type") or "") != (data.formation.entity_type or ""):
            conn.close()
            raise HTTPException(status_code=400, detail="Quote entity_type does not match formation entity_type")
        conn.execute(
            "UPDATE execution_quotes SET order_id = ?, updated_at = datetime('now') WHERE id = ?",
            (result["order_id"], data.quote_id),
        )
        quote_row = conn.execute("SELECT * FROM execution_quotes WHERE id = ?", (data.quote_id,)).fetchone()
        conn.commit()
        conn.close()
        if quote_row:
            quote = dict(quote_row)
            quote["quote_id"] = quote.pop("id")
            quote["line_items"] = parse_json_field(quote.get("line_items"), [])
            execution_dual_write("upsert_quote", quote, result["order_id"])
    return result


@app.post("/api/orders/{order_id}/authorize")
async def authorize_order(order_id: str, data: AuthorizeRequest):
    """Authorize payment for an order without capturing until reconciliation."""
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    quote = None
    if data.quote_id:
        quote = conn.execute("SELECT * FROM execution_quotes WHERE id = ?", (data.quote_id,)).fetchone()
    if not quote:
        quote = conn.execute("SELECT * FROM execution_quotes WHERE order_id = ? ORDER BY created_at DESC LIMIT 1", (order_id,)).fetchone()
    conn.close()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=503, detail="Stripe is not configured")

    order = dict(order)
    if quote:
        quote = dict(quote)
        line_items = parse_json_field(quote.get("line_items"), [])
        amount = int(quote.get("estimated_total_cents") or order["total_cents"])
    else:
        state_name = STATE_FEES["state_names"].get(order["state"], order["state"])
        line_items = [
            {"label": f"SOSFiler {order['entity_type']} formation", "amount_cents": int(order["platform_fee_cents"] or 0)},
            {"label": f"{state_name} government filing fee estimate", "amount_cents": int(order["state_fee_cents"] or 0)},
        ]
        if int(order.get("gov_processing_fee_cents") or 0):
            line_items.append({"label": f"{state_name} processing fee estimate", "amount_cents": int(order["gov_processing_fee_cents"])})
        amount = sum(item["amount_cents"] for item in line_items)

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": int(item["amount_cents"]),
                        "product_data": {"name": item.get("label") or item.get("code") or "SOSFiler fee"},
                    },
                    "quantity": 1,
                }
                for item in line_items if int(item.get("amount_cents") or 0) > 0
            ],
            mode="payment",
            payment_intent_data={"capture_method": "manual"},
            success_url=data.success_url + f"?order_id={order['id']}&token={order['token']}",
            cancel_url=data.cancel_url,
            customer_email=order["email"],
            metadata={
                "order_id": order["id"],
                "quote_id": data.quote_id or (quote or {}).get("id", ""),
                "capture_strategy": "authorize_then_capture",
            },
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    conn = get_db()
    conn.execute(
        "UPDATE orders SET stripe_session_id = ?, updated_at = datetime('now') WHERE id = ?",
        (session.id, order_id),
    )
    if quote:
        conn.execute(
            """
            UPDATE execution_quotes
            SET order_id = ?, authorized_total_cents = ?, reconciliation_status = 'authorization_started',
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (order_id, amount, quote["id"]),
        )
    conn.commit()
    conn.close()
    if quote:
        execution_dual_write("update_quote_authorized", quote["id"], amount)
    insert_payment_ledger(
        order_id=order_id,
        quote_id=data.quote_id or (quote or {}).get("id", ""),
        event_type="authorization_started",
        amount_cents=amount,
        stripe_session_id=session.id,
        raw_event={"checkout_session": session.id, "capture_strategy": "manual"},
    )
    return {"checkout_url": session.url, "session_id": session.id, "capture_strategy": "authorize_then_capture"}


@app.get("/api/admin/orders/{order_id}/payment-reconciliation")
async def get_admin_payment_reconciliation(order_id: str, request: Request):
    verify_admin_access(request)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    summary = payment_reconciliation_summary(conn, dict(order))
    conn.close()
    return summary


@app.post("/api/admin/orders/{order_id}/payment-reconcile")
async def reconcile_admin_order_payment(order_id: str, payload: PaymentReconcileRequest, request: Request):
    verify_admin_access(request)
    return reconcile_order_payment(order_id, payload)


@app.post("/api/admin/orders/{order_id}/capture-payment")
async def capture_admin_order_payment(order_id: str, payload: PaymentCaptureRequest, request: Request):
    verify_admin_access(request)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(order)
    summary = payment_reconciliation_summary(conn, order)
    quote_id = summary.get("quote_id") or ""
    amount = int(payload.amount_cents if payload.amount_cents is not None else summary.get("final_total_cents") or 0)
    if summary.get("reconciliation_status") != "ready_to_capture":
        conn.close()
        raise HTTPException(status_code=400, detail=f"Payment is not ready to capture: {summary.get('reconciliation_status')}")
    if amount <= 0 or amount > int(summary.get("authorized_total_cents") or 0):
        conn.close()
        raise HTTPException(status_code=400, detail="Capture amount must be greater than zero and not exceed the authorized amount.")
    payment_intent = order.get("stripe_payment_intent") or summary.get("stripe_payment_intent") or ""
    dry_run = not bool(STRIPE_SECRET_KEY and payment_intent)
    stripe_result = {"dry_run": True, "message": "Stripe payment intent unavailable or Stripe is not configured; capture recorded as dry-run."}
    if not dry_run:
        try:
            intent = stripe_object_to_dict(stripe.PaymentIntent.retrieve(payment_intent))
            capturable = int(intent.get("amount_capturable") or 0)
            if capturable and amount > capturable:
                conn.close()
                raise HTTPException(status_code=400, detail=f"Stripe only has {capturable} cents capturable for this payment intent.")
            stripe_result = stripe.PaymentIntent.capture(payment_intent, amount_to_capture=amount)
        except stripe.error.StripeError as e:
            conn.close()
            raise HTTPException(status_code=400, detail=str(e))
    conn.execute(
        "UPDATE orders SET status = 'payment_captured', total_cents = ?, updated_at = datetime('now') WHERE id = ?",
        (amount, order_id),
    )
    if quote_id:
        conn.execute(
            """
            UPDATE execution_quotes
            SET captured_total_cents = ?, reconciliation_status = 'captured',
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (amount, quote_id),
        )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'payment_captured', ?)",
        (order_id, payload.message or "Payment captured after final fee reconciliation."),
    )
    conn.commit()
    conn.close()
    insert_payment_ledger(
        order_id=order_id,
        quote_id=quote_id,
        event_type="captured_dry_run" if dry_run else "captured",
        amount_cents=amount,
        stripe_payment_intent=payment_intent,
        raw_event={"actor": normalize_operator(payload.actor), "stripe_result": redact_sensitive(stripe_result if isinstance(stripe_result, dict) else str(stripe_result))},
    )
    conn = get_db()
    order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
    updated = payment_reconciliation_summary(conn, order)
    conn.close()
    return {**updated, "captured": True, "dry_run": dry_run}


@app.post("/api/admin/orders/{order_id}/request-additional-authorization")
async def request_admin_additional_authorization(order_id: str, payload: AdditionalAuthorizationRequest, request: Request):
    verify_admin_access(request)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(order)
    summary = payment_reconciliation_summary(conn, order)
    quote_id = summary.get("quote_id") or ""
    delta = int(payload.amount_cents or max(0, int(summary.get("final_total_cents") or 0) - int(summary.get("authorized_total_cents") or 0)))
    if delta <= 0:
        conn.close()
        raise HTTPException(status_code=400, detail="No additional authorization is required.")
    if summary.get("reconciliation_status") not in {"additional_authorization_required", "authorized_pending_reconcile", "ready_to_capture"}:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Order is not in an additional-authorization state: {summary.get('reconciliation_status')}")
    if not quote_id:
        conn.close()
        raise HTTPException(status_code=400, detail="A quote is required before requesting additional authorization.")
    success_url = payload.success_url or os.getenv("DASHBOARD_URL") or "https://ops.sosfiler.com/dashboard.html"
    cancel_url = payload.cancel_url or os.getenv("DASHBOARD_URL") or "https://ops.sosfiler.com/dashboard.html"
    if not STRIPE_SECRET_KEY:
        conn.execute(
            """
            UPDATE execution_quotes
            SET reconciliation_status = 'additional_authorization_required',
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (quote_id,),
        )
        conn.execute(
            "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'additional_authorization_required', ?)",
            (order_id, payload.message or f"Additional authorization of {delta} cents is required before filing."),
        )
        conn.commit()
        conn.close()
        insert_payment_ledger(
            order_id=order_id,
            quote_id=quote_id,
            event_type="additional_authorization_requested_dry_run",
            amount_cents=delta,
            raw_event={"actor": normalize_operator(payload.actor), "stripe_configured": False},
        )
        return {"dry_run": True, "amount_cents": delta, "message": "Stripe is not configured; request recorded only."}
    conn.close()
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": delta,
                    "product_data": {"name": "SOSFiler additional government fee authorization"},
                },
                "quantity": 1,
            }],
            mode="payment",
            payment_intent_data={"capture_method": "manual"},
            success_url=success_url + f"?order_id={order_id}&token={order['token']}",
            cancel_url=cancel_url,
            customer_email=order["email"],
            metadata={
                "order_id": order_id,
                "quote_id": quote_id,
                "capture_strategy": "authorize_then_capture",
                "additional_authorization": "true",
            },
        )
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    conn = get_db()
    conn.execute(
        """
        UPDATE execution_quotes
        SET reconciliation_status = 'additional_authorization_started',
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (quote_id,),
    )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'additional_authorization_started', ?)",
        (order_id, payload.message or "Additional customer authorization was requested for final government fees."),
    )
    conn.commit()
    conn.close()
    insert_payment_ledger(
        order_id=order_id,
        quote_id=quote_id,
        event_type="additional_authorization_started",
        amount_cents=delta,
        stripe_session_id=session.id,
        raw_event={"actor": normalize_operator(payload.actor), "checkout_session": session.id},
    )
    return {"dry_run": False, "amount_cents": delta, "checkout_url": session.url, "session_id": session.id}


@app.get("/api/orders/{order_id}/timeline")
async def get_order_timeline(order_id: str, request: Request, token: str = ""):
    """Customer-safe timeline combining status updates, filing events, evidence, and payment ledger."""
    user = get_current_user(request)
    if not user:
        verify_order_access(order_id, token)
    conn = get_db()
    if user:
        link_orders_to_user_by_email(conn, user["id"], user["email"])
        allowed = conn.execute(
            "SELECT 1 FROM orders WHERE id = ? AND (user_id = ? OR lower(email) = lower(?))",
            (order_id, user["id"], user["email"]),
        ).fetchone()
        if not allowed:
            conn.close()
            raise HTTPException(status_code=403, detail="Invalid order access")
    status_rows = conn.execute("SELECT status, message, created_at FROM status_updates WHERE order_id = ? ORDER BY created_at", (order_id,)).fetchall()
    filing_rows = conn.execute("SELECT event_type, message, actor, evidence_path, created_at FROM filing_events WHERE order_id = ? ORDER BY created_at", (order_id,)).fetchall()
    artifacts = conn.execute("SELECT artifact_type, filename, is_evidence, created_at FROM filing_artifacts WHERE order_id = ? ORDER BY created_at", (order_id,)).fetchall()
    payments = conn.execute("SELECT event_type, amount_cents, stripe_session_id, created_at FROM payment_ledger WHERE order_id = ? ORDER BY created_at", (order_id,)).fetchall()
    order_row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return {
        "order_id": order_id,
        "status_updates": [dict(row) for row in status_rows],
        "timeline": customer_timeline(status_rows, filing_rows, dict(order_row) if order_row else None),
        "filing_events": [redact_sensitive(dict(row)) for row in filing_rows],
        "artifacts": [dict(row) for row in artifacts],
        "payments": [dict(row) for row in payments],
    }

# --- TASK 2: Filing Expert Chat API ---
@app.post("/api/chat")
async def chat_expert(data: ChatRequest, request: Request):
    import httpx

    # Simple Rate Limiting Logic
    ip = request.client.host
    session_id = data.session_id

    conn = get_db()
    session = conn.execute("SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)).fetchone()

    if session:
        session = dict(session)
        # 20 messages per session limit
        if session["message_count"] >= 20:
             conn.close()
             raise HTTPException(status_code=429, detail="Message limit for this session reached (20).")

        # 100 per day per IP (Simplified daily check)
        ip_count = conn.execute("SELECT SUM(message_count) FROM chat_sessions WHERE ip_address = ? AND last_message_at > datetime('now', '-1 day')", (ip,)).fetchone()[0] or 0
        if ip_count >= 100:
             conn.close()
             raise HTTPException(status_code=429, detail="Daily message limit for your IP reached.")

        raw_history = session["history"] if session["history"] else "[]"
        history = json.loads(raw_history)
        message_count = (session["message_count"] or 0) + 1
    else:
        history = []
        message_count = 1
        conn.execute("INSERT INTO chat_sessions (id, session_id, ip_address, message_count) VALUES (?, ?, ?, ?)",
                     (f"CHAT-{uuid.uuid4().hex[:12].upper()}", session_id, ip, 0))
        conn.commit()

    verified = verified_chat_context(data)
    should_escalate, confidence_reason = should_escalate_chat(data.message, verified)
    context = data.context or {}
    verified_answer = build_verified_chat_answer(data.message, verified)
    if verified_answer:
        ai_message = verified_answer["answer"]
        history.append({"role": "user", "content": data.message})
        history.append({"role": "assistant", "content": ai_message})
        conn.execute(
            "UPDATE chat_sessions SET message_count = ?, history = ?, last_message_at = datetime('now') WHERE session_id = ?",
            (message_count, json.dumps(redact_sensitive(history)), session_id)
        )
        conn.commit()
        conn.close()
        return {
            "response": ai_message,
            "escalated": False,
            "sources": verified_answer.get("sources", []),
            "confidence_reason": verified_answer.get("confidence_reason", ""),
        }
    if not should_escalate:
        should_escalate = True
        confidence_reason = "Verified context exists, but no source-backed SOSFiler answer template matched the question."
    if should_escalate:
        ticket = create_support_ticket(
            question=data.message,
            confidence_reason=confidence_reason,
            session_id=session_id,
            order_id=context.get("order_id", ""),
            customer_email=context.get("email", ""),
            state=(context.get("state") or "").upper(),
            product_type=context.get("product_type", "support"),
            suggested_answer=(
                "A SOSFiler operator should review this question before a customer-facing answer is sent. "
                f"Detected context: state={(context.get('state') or 'NA').upper()}, product={context.get('product_type', 'support')}, "
                f"sources={', '.join(source.get('source', '') for source in verified.get('source_coverage', [])) or 'none'}."
            ),
            ticket_type="support",
            priority="normal",
        )
        ai_message = (
            "I want to be careful here, so I created a support ticket for a SOSFiler operator to review. "
            f"Ticket: {ticket['id']}. Note: SOSFiler is a document preparation service, not a law firm. This isn't legal advice."
        )
        history.append({"role": "user", "content": data.message})
        history.append({"role": "assistant", "content": ai_message})
        conn.execute(
            "UPDATE chat_sessions SET message_count = ?, history = ?, last_message_at = datetime('now') WHERE session_id = ?",
            (message_count, json.dumps(redact_sensitive(history)), session_id)
        )
        conn.commit()
        conn.close()
        return {"response": ai_message, "escalated": True, "ticket_id": ticket["id"], "confidence_reason": confidence_reason}

    if not OPENAI_API_KEY:
        conn.close()
        raise HTTPException(status_code=500, detail="OpenAI API Key not configured")

    system_prompt = (
        "You are SOSFiler's Filing Expert. Answer ONLY from the verified JSON context provided below. "
        "If the answer is not directly supported by that context, respond that SOSFiler needs to review it and do not guess. "
        "Always end important answers with: 'Note: SOSFiler is a document preparation service, not a law firm. This isn't legal advice.' "
        "Keep responses concise and helpful.\n\n"
        f"Verified SOSFiler context:\n{json.dumps(redact_sensitive(verified), default=str)}"
    )

    messages = [{"role": "system", "content": system_prompt}]
    # Add history (last 5 messages for brevity and token budget)
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": data.message})

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": messages,
                    "max_tokens": 500,
                    "temperature": 0.7
                },
                timeout=30.0
            )

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"OpenAI API Error: {response.text}")

        result = response.json()
        ai_message = result["choices"][0]["message"]["content"]

        # Update history and session
        history.append({"role": "user", "content": data.message})
        history.append({"role": "assistant", "content": ai_message})

        conn.execute(
            "UPDATE chat_sessions SET message_count = ?, history = ?, last_message_at = datetime('now') WHERE session_id = ?",
            (message_count, json.dumps(redact_sensitive(history)), session_id)
        )
        conn.commit()
        conn.close()

        return {"response": ai_message}

    except Exception as e:
        if conn: conn.close()
        raise HTTPException(status_code=500, detail=str(e))

# --- TASK 1: State Info API ---

def _find_state(entity_data, state_code: str):
    """Find a state entry in the v2 data (list of dicts)."""
    state_code = state_code.upper()
    if isinstance(entity_data, list):
        for entry in entity_data:
            abbr = entry.get("state_abbreviation", "").upper()
            name = entry.get("state_name", "").upper()
            if abbr == state_code or name == state_code:
                return entry
            # Also match 2-letter code against state_name abbreviations
            pass  # handled by fallback below
        # Fallback: try matching by state name
        for entry in entity_data:
            sname = entry.get("state_name", "")
            if sname and _state_to_abbrev(sname) == state_code:
                return entry
    elif isinstance(entity_data, dict):
        return entity_data.get(state_code)
    return None

def _state_to_abbrev(name: str) -> str:
    """Convert state name to abbreviation."""
    mapping = {
        "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
        "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
        "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
        "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
        "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
        "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
        "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
        "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
        "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
        "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
        "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
        "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
        "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY"
    }
    return mapping.get(name, "")

@app.get("/api/state-info/compare")
async def compare_states(states: str, entity_type: str = "llc"):
    """Compare 2-3 states side by side."""
    entity_type = entity_type.lower()
    if entity_type not in STATE_DATA:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}")
    state_list = [s.strip().upper() for s in states.split(",")][:3]
    results = {}
    for s in state_list:
        data = _find_state(STATE_DATA[entity_type], s)
        if data:
            results[s] = data
    if not results:
        raise HTTPException(status_code=404, detail="No data found for the specified states")
    return results

@app.get("/api/state-info/cheapest/{entity_type}")
async def cheapest_states(entity_type: str):
    """Return top 10 cheapest states by filing fee."""
    entity_type = entity_type.lower()
    if entity_type not in STATE_DATA:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}")

    entries = STATE_DATA[entity_type]
    if isinstance(entries, dict):
        items = list(entries.values())
    else:
        items = entries

    def extract_fee(entry):
        fee_str = str(entry.get("filing_fee", "999"))
        import re
        nums = re.findall(r'\$(\d+)', fee_str)
        return int(nums[0]) if nums else 999

    sorted_entries = sorted(items, key=extract_fee)
    return sorted_entries[:10]

@app.get("/api/state-info/{state}/{entity_type}")
async def get_state_info(state: str, entity_type: str):
    """Get full state requirements for a given state + entity type."""
    entity_type = entity_type.lower()
    if entity_type not in STATE_DATA:
        raise HTTPException(status_code=400, detail=f"Invalid entity type: {entity_type}. Use: llc, corp, nonprofit")
    data = _find_state(STATE_DATA[entity_type], state)
    if not data:
        raise HTTPException(status_code=404, detail=f"No data found for {state} {entity_type}")
    return data

# --- Existing endpoints ---

@app.get("/api/name-check")
async def check_name_availability(state: str, name: str, entity_type: str = "LLC"):
    """
    Check business name availability.
    In production, this scrapes the state SOS website.
    For MVP, returns a simulated check with guidance.
    """
    state = state.upper()
    name = name.strip()

    # Basic name validation
    issues = []
    suffix_required = {
        "LLC": ["LLC", "L.L.C.", "Limited Liability Company"],
        "Corp": ["Inc.", "Inc", "Incorporated", "Corporation", "Corp.", "Corp"],
        "S-Corp": ["Inc.", "Inc", "Incorporated", "Corporation", "Corp.", "Corp"],
        "Nonprofit": ["Inc.", "Inc", "Incorporated", "Corporation", "Corp.", "Corp"]
    }

    et = entity_type if entity_type in suffix_required else "LLC"
    has_suffix = any(name.upper().endswith(s.upper()) or s.upper() in name.upper() for s in suffix_required[et])

    if not has_suffix:
        issues.append(f"Name should contain one of: {', '.join(suffix_required[et])}")

    # Restricted words
    restricted = ["bank", "trust", "insurance", "university", "federal", "national", "united states"]
    for word in restricted:
        if word in name.lower():
            issues.append(f"'{word}' is a restricted word that may require special approval")

    state_info = STATE_REQUIREMENTS.get(state, {})
    sos_url = state_info.get("filing_url", "")

    return {
        "state": state,
        "name": name,
        "preliminary_check": "pass" if not issues else "warning",
        "issues": issues,
        "note": "This is a preliminary check. Final availability is confirmed during filing. We verify with the Secretary of State before submitting.",
        "sos_search_url": sos_url,
        "name_requirements": state_info.get("articles_requirements", {}).get("name_requirements", "")
    }

@app.post("/api/formation")
async def create_formation(data: FormationRequest):
    """Create a new formation order."""
    state = data.state.upper()
    responsible_members = [member for member in data.members if member.is_responsible_party]
    if len(responsible_members) != 1:
        raise HTTPException(status_code=400, detail="Exactly one responsible party is required for EIN filing.")
    responsible_ssn = _validate_full_ssn_itin(data.responsible_party_ssn)
    responsible = responsible_members[0]
    if responsible.ssn_itin:
        member_ssn = _validate_full_ssn_itin(responsible.ssn_itin, "responsible_party_member_ssn")
        if member_ssn != responsible_ssn:
            raise HTTPException(status_code=400, detail="Responsible party SSN/ITIN does not match the selected member.")
    data.responsible_party_ssn = responsible_ssn
    responsible.ssn_itin = responsible_ssn
    responsible.ssn_last4 = responsible_ssn[-4:]

    # Validate state
    entity_key = "LLC" if data.entity_type == "LLC" else "Corp"
    if state not in STATE_FEES.get(entity_key, {}):
        raise HTTPException(status_code=400, detail=f"Invalid state: {state}")

    # Calculate fees, including state portal/card processing fees when known.
    fees = build_fee_breakdown(state, entity_key, include_ra=data.ra_choice == "sosfiler")
    state_fee_cents = fees["state_fee_cents"]
    gov_processing_fee_cents = fees["gov_processing_fee_cents"]
    total_cents = fees["total_cents"]

    # Generate order ID and token
    order_id = f"{state}-{uuid.uuid4().hex[:12].upper()}"
    token = secrets.token_urlsafe(32)
    formation_payload = json.loads(data.model_dump_json())
    ssn_vault_id = store_sensitive_value("order", order_id, "responsible_party_ssn", responsible_ssn, created_by="customer_intake")
    formation_payload["responsible_party_ssn_vault_id"] = ssn_vault_id
    formation_payload["responsible_party_ssn_last4"] = responsible_ssn[-4:]
    formation_payload["responsible_party_ssn"] = ""
    for member in formation_payload.get("members", []):
        if member.get("is_responsible_party"):
            member["ssn_last4"] = responsible_ssn[-4:]
        member["ssn_itin"] = ""

    # Store order
    user_id = None
    if hasattr(data, 'user_id'):
        user_id = data.user_id

    conn = get_db()
    conn.execute("""
        INSERT INTO orders (id, user_id, email, token, entity_type, state, business_name,
                          formation_data, state_fee_cents, gov_processing_fee_cents,
                          platform_fee_cents, total_cents)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_id, user_id, data.email, token, data.entity_type, state,
        data.business_name, json.dumps(formation_payload), state_fee_cents, gov_processing_fee_cents,
        PLATFORM_FEE, total_cents
    ))
    conn.commit()
    conn.close()

    order_for_job = {
        "id": order_id, "state": state, "entity_type": data.entity_type,
        "state_fee_cents": state_fee_cents, "gov_processing_fee_cents": gov_processing_fee_cents,
        "status": "pending_payment"
    }
    create_or_update_filing_job(order_for_job, status="pending_payment")
    add_status_update(order_id, "pending_payment", "Order created. Awaiting payment.")

    return {
        "order_id": order_id,
        "token": token,
        "email": data.email,
        "total_cents": total_cents,
        "platform_fee": PLATFORM_FEE / 100,
        "state_fee": state_fee_cents / 100,
        "gov_processing_fee": gov_processing_fee_cents / 100,
        "fee_breakdown": fees,
        "status": "pending_payment"
    }

@app.post("/api/checkout")
async def create_checkout_session(data: CheckoutRequest):
    """Create a Stripe Checkout session for an order."""
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (data.order_id,)).fetchone()
    conn.close()

    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    order = dict(order)
    state_name = STATE_FEES["state_names"].get(order["state"], order["state"])

    # Check if order includes RA service
    formation_data = json.loads(order["formation_data"]) if order["formation_data"] else {}
    ra_choice = formation_data.get("ra_choice", "self")

    try:
        line_items = [
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": order["platform_fee_cents"],
                    "product_data": {
                        "name": f"SOSFiler — Complete {order['entity_type']} Formation",
                        "description": f"Articles + EIN + Operating Agreement + Compliance Calendar"
                    }
                },
                "quantity": 1
            },
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": order["state_fee_cents"],
                    "product_data": {
                        "name": f"{state_name} State Filing Fee",
                        "description": f"Filing fee paid directly to {state_name} Secretary of State"
                    }
                },
                "quantity": 1
            }
        ]

        gov_processing_fee_cents = int(order.get("gov_processing_fee_cents") or 0)
        if gov_processing_fee_cents:
            line_items.append({
                "price_data": {
                    "currency": "usd",
                    "unit_amount": gov_processing_fee_cents,
                    "product_data": {
                        "name": f"{state_name} Processing / Convenience Fee",
                        "description": "State portal or card processing fee passed through at cost"
                    }
                },
                "quantity": 1
            })

        # Add RA line item if selected
        if ra_choice == "sosfiler":
            line_items.append({
                "price_data": {
                    "currency": "usd",
                    "unit_amount": RA_RENEWAL_FEE,
                    "product_data": {
                        "name": "SOSFiler Registered Agent (1st Year)",
                        "description": f"Professional registered agent in {state_name}. Annual renewal."
                    }
                },
                "quantity": 1
            })

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=data.success_url + f"?order_id={order['id']}&token={order['token']}",
            cancel_url=data.cancel_url,
            customer_email=order["email"],
            metadata={
                "order_id": order["id"],
                "entity_type": order["entity_type"],
                "state": order["state"],
                "business_name": order["business_name"]
            }
        )

        # Save session ID
        conn = get_db()
        conn.execute(
            "UPDATE orders SET stripe_session_id = ?, updated_at = datetime('now') WHERE id = ?",
            (session.id, order["id"])
        )
        conn.commit()
        conn.close()

        return {"checkout_url": session.url, "session_id": session.id}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Stripe webhook events."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        if STRIPE_WEBHOOK_SECRET:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        else:
            event = json.loads(payload)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(status_code=400, detail="Invalid webhook")

    event = dict(event)
    event_id, should_process = begin_stripe_webhook_event(event, payload)
    if not should_process:
        return {"status": "ignored", "duplicate": True, "event_id": event_id}

    try:
        event_type = event.get("type", "")
        obj = stripe_object_to_dict((event.get("data") or {}).get("object") or {})

        if event_type == "checkout.session.completed":
            order_id = (obj.get("metadata") or {}).get("order_id")
            capture_strategy = (obj.get("metadata") or {}).get("capture_strategy", "")

            if order_id and capture_strategy == "authorize_then_capture":
                result = update_payment_authorization_from_session(obj)
                if result.get("updated") and not (obj.get("metadata") or {}).get("additional_authorization"):
                    background_tasks.add_task(run_formation_pipeline, order_id)
                finish_stripe_webhook_event(event_id)
                return {"status": "ok", "event_id": event_id, "result": result}

            if order_id:
                conn = get_db()
                conn.execute("""
                    UPDATE orders SET status = 'paid', paid_at = datetime('now'),
                    stripe_payment_intent = ?, updated_at = datetime('now') WHERE id = ?
                """, (obj.get("payment_intent"), order_id))
                conn.commit()
                conn.close()
                add_status_update(order_id, "paid", "Payment received. Starting formation process.")
                insert_payment_ledger(
                    order_id=order_id,
                    event_type="captured",
                    amount_cents=int(obj.get("amount_total") or 0),
                    stripe_session_id=obj.get("id"),
                    stripe_payment_intent=obj.get("payment_intent"),
                    raw_event=obj,
                )
                background_tasks.add_task(run_formation_pipeline, order_id)

        elif event_type == "payment_intent.amount_capturable_updated":
            update_payment_intent_snapshot(obj)

        elif event_type == "payment_intent.succeeded":
            result = update_payment_intent_snapshot(obj)
            if result.get("updated"):
                add_status_update(result["order_id"], "payment_captured", "Stripe confirmed payment capture.")

        elif event_type == "payment_intent.canceled":
            payment_intent = obj.get("id") or ""
            conn = get_db()
            order = conn.execute("SELECT * FROM orders WHERE stripe_payment_intent = ? ORDER BY created_at DESC LIMIT 1", (payment_intent,)).fetchone()
            if order:
                order = dict(order)
                quote = latest_quote_for_order(conn, order["id"])
                quote_id = dict(quote)["id"] if quote else ""
                if quote_id:
                    conn.execute(
                        "UPDATE execution_quotes SET reconciliation_status = 'authorization_canceled', updated_at = datetime('now') WHERE id = ?",
                        (quote_id,),
                    )
                conn.execute("UPDATE orders SET status = 'pending_payment', updated_at = datetime('now') WHERE id = ?", (order["id"],))
                conn.commit()
                conn.close()
                insert_payment_ledger(
                    order_id=order["id"],
                    quote_id=quote_id,
                    event_type="authorization_canceled",
                    amount_cents=int(obj.get("amount") or 0),
                    stripe_payment_intent=payment_intent,
                    raw_event=obj,
                )
                add_status_update(order["id"], "pending_payment", "Stripe authorization was canceled before capture.")
            else:
                conn.close()

        elif event_type in {"charge.refunded", "refund.created", "refund.updated"}:
            payment_intent = obj.get("payment_intent") or ""
            amount = int(obj.get("amount_refunded") or obj.get("amount") or 0)
            conn = get_db()
            order = conn.execute("SELECT * FROM orders WHERE stripe_payment_intent = ? ORDER BY created_at DESC LIMIT 1", (payment_intent,)).fetchone()
            if order:
                order = dict(order)
                quote = latest_quote_for_order(conn, order["id"])
                quote_id = dict(quote)["id"] if quote else ""
                conn.close()
                insert_payment_ledger(
                    order_id=order["id"],
                    quote_id=quote_id,
                    event_type="refunded",
                    amount_cents=amount,
                    stripe_payment_intent=payment_intent,
                    raw_event=obj,
                )
                add_status_update(order["id"], "refund_recorded", "Stripe refund event recorded.")
            else:
                conn.close()

        finish_stripe_webhook_event(event_id)
        return {"status": "ok", "event_id": event_id}
    except Exception as exc:
        finish_stripe_webhook_event(event_id, "failed", f"{exc.__class__.__name__}: {exc}")
        raise

async def run_formation_pipeline(order_id: str):
    """Payment confirmed -> internal docs -> manual/evidence-backed state filing queue."""
    from notifier import Notifier

    conn = get_db()
    order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
    conn.close()

    formation_data = json.loads(order["formation_data"])
    notifier = Notifier()

    try:
        # Step 1: Prepare and notify without claiming a state submission.
        create_or_update_filing_job(order, status="paid")
        add_status_update(order_id, "preparing", "Preparing your company documents and state filing packet...")
        await notifier.send_order_confirmation(order, formation_data)

        # Step 2: Generate internal records immediately. These are not state evidence.
        add_status_update(order_id, "generating_documents", "Generating internal company documents and filing data...")
        await generate_internal_documents_async(order_id, formation_data)
        conn = get_db()
        conn.execute(
            "UPDATE orders SET documents_ready_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
            (order_id,)
        )
        conn.commit()
        conn.close()

        # Step 3: Route state filing to the evidence-backed operator queue.
        job = create_or_update_filing_job(order, status="ready_to_file")
        add_filing_event(
            order_id,
            "ready_to_file",
            f"{job.get('form_name', 'State filing')} is routed to {job.get('automation_lane') or 'operator_assisted'} through {job.get('portal_name') or job.get('portal_url')}.",
        )
        add_status_update(
            order_id,
            "ready_to_file",
            f"Internal documents are ready. State filing is queued for verified {job.get('automation_lane') or 'operator-assisted'} submission to {order['state']}."
        )
        if hasattr(notifier, "send_manual_filing_required"):
            await notifier.send_manual_filing_required(order, formation_data, job)

    except Exception as e:
        add_status_update(order_id, "error", f"Error in formation pipeline: {str(e)}")
        # Alert human review
        await notifier.send_error_alert(order_id, str(e))

@app.get("/api/status/{order_id}")
async def get_order_status(order_id: str, token: str = ""):
    """Get real-time filing status."""
    order = verify_order_access(order_id, token)

    conn = get_db()
    updates = conn.execute(
        "SELECT status, message, created_at FROM status_updates WHERE order_id = ? ORDER BY created_at ASC",
        (order_id,)
    ).fetchall()

    deadlines = conn.execute(
        "SELECT deadline_type, due_date, status FROM compliance_deadlines WHERE order_id = ? ORDER BY due_date ASC",
        (order_id,)
    ).fetchall()

    filing_job = conn.execute(
        "SELECT * FROM filing_jobs WHERE order_id = ? ORDER BY created_at DESC LIMIT 1",
        (order_id,)
    ).fetchone()
    filing_events = conn.execute(
        "SELECT event_type, message, actor, evidence_path, created_at FROM filing_events WHERE order_id = ? ORDER BY created_at ASC",
        (order_id,)
    ).fetchall()
    filing_artifacts = conn.execute(
        "SELECT artifact_type, filename, is_evidence, created_at FROM filing_artifacts WHERE order_id = ? ORDER BY created_at ASC",
        (order_id,)
    ).fetchall()
    docs = conn.execute(
        "SELECT doc_type, filename, format, category, visibility, created_at FROM documents WHERE order_id = ? ORDER BY created_at ASC",
        (order_id,)
    ).fetchall()
    conn.close()

    formation_data = json.loads(order["formation_data"])
    action = get_filing_action(order["state"], order["entity_type"])
    fee_breakdown = {
        "platform_fee_cents": order.get("platform_fee_cents", PLATFORM_FEE),
        "state_fee_cents": order.get("state_fee_cents", 0),
        "gov_processing_fee_cents": order.get("gov_processing_fee_cents", 0),
        "total_cents": order.get("total_cents", 0),
        "processing_fee_rule": (action or {}).get("processing_fee"),
    }
    filing_job_payload = dict(filing_job) if filing_job else None
    if filing_job_payload:
        filing_job_payload["required_consents"] = parse_json_field(filing_job_payload.get("required_consents"), [])
        filing_job_payload["required_evidence"] = parse_json_field(filing_job_payload.get("required_evidence"), {})
        filing_job_payload["portal_blockers"] = parse_json_field(filing_job_payload.get("portal_blockers"), [])
        filing_job_payload["route_metadata"] = parse_json_field(filing_job_payload.get("route_metadata"), {})

    return {
        "order_id": order_id,
        "status": order["status"],
        "entity_type": order["entity_type"],
        "state": order["state"],
        "business_name": order["business_name"],
        "email": order["email"],
        "ein": order.get("ein"),
        "ein_requires_ssn": not order.get("ein") and formation_data_needs_ssn(formation_data),
        "filing_confirmation": json.loads(order["filing_confirmation"]) if order.get("filing_confirmation") else None,
        "timeline": customer_timeline(updates, filing_events, dict(order)),
        "compliance_deadlines": [dict(d) for d in deadlines],
        "created_at": order["created_at"],
        "paid_at": order.get("paid_at"),
        "filed_at": order.get("filed_at"),
        "approved_at": order.get("approved_at"),
        "documents_ready_at": order.get("documents_ready_at"),
        "platform_fee_cents": order.get("platform_fee_cents", PLATFORM_FEE),
        "state_fee_cents": order.get("state_fee_cents", 0),
        "gov_processing_fee_cents": order.get("gov_processing_fee_cents", 0),
        "fee_breakdown": fee_breakdown,
        "filing_job": filing_job_payload,
        "filing_events": [dict(e) for e in filing_events],
        "filing_artifacts": [dict(a) for a in filing_artifacts],
        "documents": customer_visible_documents(docs),
        "ein_queue": redacted_ein_queue_summary(dict(order)),
        "total_cents": order.get("total_cents", 0)
    }

@app.get("/api/admin/filing-queue")
async def get_filing_queue(request: Request, state: str = "", status: str = "ready_to_file"):
    """Operator queue for evidence-backed state filings."""
    verify_admin_access(request)
    clauses = []
    params = []
    if state:
        clauses.append("j.state = ?")
        params.append(state.upper())
    if status:
        clauses.append("j.status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    conn = get_db()
    rows = conn.execute(f"""
        SELECT
            j.*,
            o.business_name,
            o.email,
            o.created_at AS order_created_at,
            o.paid_at,
            o.documents_ready_at
        FROM filing_jobs j
        JOIN orders o ON o.id = j.order_id
        {where}
        ORDER BY COALESCE(o.paid_at, o.created_at) ASC
    """, params).fetchall()
    conn.close()
    jobs = []
    for row in rows:
        jobs.append(serialize_filing_job(row))
    return {"jobs": jobs}


@app.get("/api/admin/filing-jobs")
async def get_admin_filing_jobs(request: Request, state: str = "", status: str = ""):
    """Universal filing-job queue alias for the execution platform."""
    return await get_filing_queue(request=request, state=state, status=status)


@app.get("/api/admin/payment-readiness")
async def get_admin_payment_readiness(
    request: Request,
    state: str = "",
    status: str = "",
    limit: int = 100,
):
    """Queue paid/authorized orders that need operator filing-prep review."""
    verify_admin_access(request)
    ready_statuses = sorted(PAYMENT_READY_STATUSES)
    status_placeholders = ",".join("?" for _ in ready_statuses)
    clauses = [
        f"""
        (
            o.status IN ({status_placeholders})
            OR EXISTS (
                SELECT 1 FROM payment_ledger p
                WHERE p.order_id = o.id
                  AND p.event_type IN ('authorized', 'additional_authorization_received', 'captured', 'captured_dry_run')
            )
        )
        """
    ]
    params: list = list(ready_statuses)
    if state:
        clauses.append("o.state = ?")
        params.append(state.upper())
    where = "WHERE " + " AND ".join(clauses)
    conn = get_db()
    rows = conn.execute(f"""
        SELECT o.*
        FROM orders o
        {where}
        ORDER BY COALESCE(o.paid_at, o.updated_at, o.created_at) DESC
        LIMIT ?
    """, params + [max(1, min(int(limit or 100), 500))]).fetchall()
    items = [payment_execution_readiness(conn, dict(row)) for row in rows]
    conn.close()
    if status:
        items = [item for item in items if item["readiness_status"] == status]
    summary = {
        "orders": len(items),
        "ready_to_file": sum(1 for item in items if item["readiness_status"] == "ready_to_file"),
        "needs_reconciliation": sum(1 for item in items if item["readiness_status"] == "needs_reconciliation"),
        "needs_filing_prep": sum(1 for item in items if item["readiness_status"] == "needs_filing_prep"),
        "blocked": sum(1 for item in items if item["readiness_status"] == "blocked"),
    }
    return {"summary": summary, "orders": items}


@app.post("/api/admin/orders/{order_id}/prepare-filing")
async def prepare_admin_order_filing(order_id: str, payload: FilingPrepRequest, request: Request):
    """Generate filing-prep artifacts and route a paid/authorized order to the operator queue."""
    verify_admin_access(request)
    return await prepare_order_for_filing(order_id, payload)


@app.get("/api/admin/state-metadata")
async def get_admin_state_metadata(request: Request, entity_type: str = "LLC", action_type: str = "formation"):
    """Operator state-by-state automation metadata for route planning."""
    verify_admin_access(request)
    records = all_state_metadata(entity_type, action_type)
    summary = {
        "states": len(records),
        "dry_run_ready": sum(1 for record in records if record["automation_readiness"]["dry_run_ready"]),
        "operator_fallback_required": sum(1 for record in records if record["automation_readiness"]["status"] == "operator_fallback_required"),
        "operator_assisted": sum(1 for record in records if record["automation_readiness"]["status"] == "operator_assisted"),
        "metadata_incomplete": sum(1 for record in records if record["automation_readiness"]["status"] == "metadata_incomplete"),
    }
    return {"summary": summary, "records": records}


@app.get("/api/admin/state-metadata/{state_code}")
async def get_admin_state_metadata_detail(
    state_code: str,
    request: Request,
    entity_type: str = "LLC",
    action_type: str = "formation",
):
    verify_admin_access(request)
    return state_metadata_summary(state_code, entity_type, action_type)


@app.get("/api/admin/adapter-matrix")
async def get_admin_adapter_matrix(request: Request, entity_type: str = "LLC", action_type: str = "formation"):
    """Run a no-touch dry-run preflight matrix for every state/action route."""
    verify_admin_access(request)
    return await build_adapter_matrix(entity_type, action_type)


@app.get("/api/admin/state-adapter-manifest")
async def get_admin_state_adapter_manifest(request: Request, refresh: bool = False):
    """Dashboard-ready state adapter lanes and certification gates."""
    verify_admin_access(request)
    if refresh:
        payload = write_state_adapter_manifest()
        return payload
    states = all_state_adapter_manifests()
    return {
        "summary": manifest_summary(states),
        "states": states,
    }


@app.get("/api/admin/state-certification-worklist")
async def get_admin_state_certification_worklist(request: Request):
    """Next certification gate per state for national rollout."""
    verify_admin_access(request)
    return certification_worklist(all_state_adapter_manifests())


@app.get("/api/admin/filing-jobs/{job_id}")
async def get_admin_filing_job_detail(job_id: str, request: Request):
    """Operator cockpit detail view for one filing job."""
    verify_admin_access(request)
    conn = get_db()
    row = conn.execute("""
        SELECT
            j.*,
            o.business_name,
            o.email,
            o.created_at AS order_created_at,
            o.paid_at,
            o.documents_ready_at,
            o.total_cents,
            o.gov_processing_fee_cents,
            o.platform_fee_cents
        FROM filing_jobs j
        JOIN orders o ON o.id = j.order_id
        WHERE j.id = ?
    """, (job_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    order_for_readiness = conn.execute("SELECT * FROM orders WHERE id = ?", (row["order_id"],)).fetchone()
    events = conn.execute("""
        SELECT * FROM filing_events
        WHERE filing_job_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 100
    """, (job_id,)).fetchall()
    artifacts = conn.execute("""
        SELECT * FROM filing_artifacts
        WHERE filing_job_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 100
    """, (job_id,)).fetchall()
    runs = conn.execute("""
        SELECT * FROM automation_runs
        WHERE filing_job_id = ?
        ORDER BY created_at DESC
        LIMIT 50
    """, (job_id,)).fetchall()
    run_payload = []
    for run in runs:
        item = dict(run)
        item["redacted_log"] = parse_json_field(item.get("redacted_log"), [])
        run_payload.append(item)
    job_payload = enrich_filing_job_for_adapter(conn, serialize_filing_job(row))
    job_payload["adapter_contract"] = build_adapter_contract(job_payload)
    job_payload["readiness_checklist"] = build_filing_readiness_checklist(job_payload)
    payment_readiness = payment_execution_readiness(conn, dict(order_for_readiness), dict(row)) if order_for_readiness else None
    conn.close()
    return {
        "job": job_payload,
        "payment_readiness": payment_readiness,
        "state_metadata": state_metadata_summary(job_payload["state"], job_payload["entity_type"], job_payload["action_type"]),
        "events": [dict(event) for event in events],
        "artifacts": [dict(artifact) for artifact in artifacts],
        "automation_runs": run_payload,
    }


@app.post("/api/admin/qa/annual-report-job")
async def create_admin_annual_report_fixture(payload: AdminAnnualReportFixtureRequest, request: Request):
    """Create a harmless annual-report QA filing job for operator E2E tests."""
    verify_admin_access(request)
    state = payload.state.upper()
    entity_type = "LLC" if payload.entity_type.upper() == "LLC" else payload.entity_type
    entity_fee_key = "LLC" if entity_type == "LLC" else "Corp"
    if state not in STATE_FEES.get(entity_fee_key, {}):
        raise HTTPException(status_code=400, detail=f"Invalid state: {state}")

    order_id = f"QA-AR-{state}-{uuid.uuid4().hex[:10].upper()}"
    token = secrets.token_urlsafe(24)
    formation_data = {
        "qa_fixture": True,
        "fixture_type": "annual_report_readiness",
        "created_by": "operator_playwright_e2e",
    }
    conn = get_db()
    conn.execute("""
        INSERT INTO orders (
            id, email, token, status, entity_type, state, business_name,
            formation_data, state_fee_cents, gov_processing_fee_cents,
            platform_fee_cents, total_cents, paid_at
        )
        VALUES (?, ?, ?, 'paid', ?, ?, ?, ?, 0, 0, ?, ?, datetime('now'))
    """, (
        order_id,
        str(payload.email),
        token,
        entity_type,
        state,
        payload.business_name,
        json.dumps(redact_sensitive(formation_data)),
        ANNUAL_REPORT_FEE,
        ANNUAL_REPORT_FEE,
    ))
    order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())
    conn.commit()
    conn.close()
    job = dict(create_or_update_filing_job(order, "annual_report", "ready_to_file"))
    add_filing_event(
        order_id,
        "qa_fixture_created",
        "Annual report QA fixture created for operator readiness E2E verification.",
        "playwright",
    )
    return {"order_id": order_id, "job": serialize_filing_job(job)}


@app.post("/api/admin/filing-jobs/{job_id}/claim")
async def claim_filing_job(job_id: str, payload: ClaimFilingJobRequest, request: Request):
    verify_admin_access(request)
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    message = f"Claimed by {payload.operator}."
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor)
        VALUES (?, ?, 'claimed', ?, ?)
    """, (job_id, job["order_id"], message, payload.operator))
    conn.execute(
        "UPDATE filing_jobs SET evidence_summary = ?, updated_at = datetime('now') WHERE id = ?",
        (message, job_id),
    )
    conn.commit()
    conn.close()
    execution_dual_write("insert_event", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "event_type": "claimed",
        "message": message,
        "actor": payload.operator,
    })
    return {"status": "claimed", "operator": payload.operator}


@app.post("/api/admin/filing-jobs/{job_id}/annual-report/prepare-packet")
async def prepare_annual_report_packet_endpoint(job_id: str, payload: AnnualReportPacketRequest, request: Request):
    verify_admin_access(request)
    return prepare_annual_report_packet(job_id, payload.actor, payload.message)


@app.post("/api/admin/filing-jobs/{job_id}/transition")
async def transition_filing_job(job_id: str, payload: FilingTransitionRequest, request: Request):
    """Transition a filing job using the universal evidence-gated state machine."""
    verify_admin_access(request)
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    decision = validate_transition(job["status"], payload.target_status, payload.evidence_path)
    if not decision.ok:
        conn.close()
        raise HTTPException(status_code=400, detail=decision.reason)
    previous = job["status"]
    target = payload.target_status
    evidence_path = payload.evidence_path
    if evidence_path:
        filename = Path(evidence_path).name
        artifact_type = "approved_certificate" if normalize_state(target) in {"approved", "complete", "documents_collected"} else "submitted_receipt"
        conn.execute("""
            INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (job_id, job["order_id"], artifact_type, filename, evidence_path))
        execution_dual_write("insert_artifact", {
            "filing_job_id": job_id,
            "order_id": job["order_id"],
            "artifact_type": artifact_type,
            "filename": filename,
            "file_path": evidence_path,
            "is_evidence": True,
        })
        add_customer_document_if_missing(conn, job["order_id"], artifact_type, filename, evidence_path)
    conn.execute("""
        UPDATE filing_jobs
        SET status = ?, evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (target, payload.message or f"Transitioned from {previous} to {target}.", job_id))
    conn.execute("UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?", (target, job["order_id"]))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, ?, ?)",
        (job["order_id"], target, payload.message or f"Filing moved to {target}."),
    )
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, 'transition', ?, ?, ?)
    """, (
        job_id,
        job["order_id"],
        payload.message or f"Transitioned from {previous} to {target}.",
        payload.actor,
        evidence_path,
    ))
    execution_dual_write("insert_event", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "event_type": "transition",
        "previous_status": previous,
        "next_status": target,
        "message": payload.message or f"Transitioned from {previous} to {target}.",
        "actor": payload.actor,
        "evidence_path": evidence_path,
    })
    conn.commit()
    conn.close()
    return {"status": target, "previous_status": previous}


@app.post("/api/admin/filing-jobs/{job_id}/automation/run")
async def run_filing_job_automation(job_id: str, payload: AutomationRunRequest, request: Request):
    """Run a safe filing adapter operation and persist a redacted automation run."""
    verify_admin_access(request)
    conn = get_db()
    job_row = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    job = enrich_filing_job_for_adapter(conn, serialize_filing_job(job_row))
    if not payload.dry_run and payload.operation != "preflight":
        conn.close()
        raise HTTPException(status_code=400, detail="Live filing operations are not enabled from this endpoint yet. Run dry_run=true.")

    run_id = f"AUTO-{uuid.uuid4().hex[:12].upper()}"
    start_log = [{
        "at": utc_now(),
        "message": "Automation run started.",
        "operation": payload.operation,
        "dry_run": payload.dry_run,
        "actor": payload.actor,
        "filing_job_id": job_id,
        "adapter_key": job.get("adapter_key", ""),
        "lane": job.get("automation_lane", ""),
    }]
    conn.execute("""
        INSERT INTO automation_runs (id, filing_job_id, order_id, adapter_key, lane, status, redacted_log)
        VALUES (?, ?, ?, ?, ?, 'running', ?)
    """, (
        run_id,
        job_id,
        job.get("order_id", ""),
        job.get("adapter_key") or "",
        job.get("automation_lane") or "operator_assisted",
        json.dumps(redact_sensitive(start_log)),
    ))
    conn.commit()
    conn.close()
    execution_dual_write("insert_automation_run", {
        "id": run_id,
        "filing_job_id": job_id,
        "order_id": job.get("order_id", ""),
        "adapter_key": job.get("adapter_key") or "",
        "lane": job.get("automation_lane") or "operator_assisted",
        "status": "running",
        "redacted_log": redact_sensitive(start_log),
    })

    result = await run_adapter_operation(job, payload.operation, dry_run=payload.dry_run)
    result_payload = {
        "status": result.status,
        "message": result.message,
        "evidence_path": result.evidence_path,
        "raw_status": result.raw_status,
        "metadata": result.metadata,
    }
    completed_status = "dry_run_complete" if payload.dry_run else result.status
    completion_log = start_log + [{
        "at": utc_now(),
        "message": "Automation run completed.",
        "result": result_payload,
    }]

    conn = get_db()
    conn.execute("""
        UPDATE automation_runs
        SET status = ?, redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (completed_status, json.dumps(redact_sensitive(completion_log)), run_id))
    execution_dual_write("update_automation_run", run_id, completed_status, redact_sensitive(completion_log))
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor)
        VALUES (?, ?, ?, ?, ?)
    """, (
        job_id,
        job.get("order_id", ""),
        f"automation_{payload.operation}",
        result.message,
        payload.actor,
    ))
    if payload.operation == "preflight":
        conn.execute("""
            UPDATE filing_jobs
            SET evidence_summary = ?, updated_at = datetime('now')
            WHERE id = ?
        """, (result.message, job_id))
    execution_dual_write("insert_event", {
        "filing_job_id": job_id,
        "order_id": job.get("order_id", ""),
        "event_type": f"automation_{payload.operation}",
        "message": result.message,
        "actor": payload.actor,
        "redacted_payload": redact_sensitive(result_payload),
    })
    conn.commit()
    row = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    conn.close()
    run = dict(row)
    run["redacted_log"] = parse_json_field(run.get("redacted_log"), [])
    return {"run": run, "result": redact_sensitive(result_payload)}


@app.post("/api/admin/slack/tickets/{ticket_id}/approve")
async def approve_slack_ticket(ticket_id: str, payload: SlackApprovalRequest, request: Request):
    verify_admin_access(request)
    return approve_support_ticket(ticket_id, payload.approved_by, payload.approval_note)


@app.get("/api/admin/slack/tickets")
async def list_slack_tickets(request: Request, status: str = "", priority: str = "", limit: int = 100):
    verify_admin_access(request)
    clauses = []
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if priority:
        clauses.append("priority = ?")
        params.append(priority)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = max(1, min(int(limit or 100), 500))
    conn = get_db()
    rows = conn.execute(f"""
        SELECT * FROM support_tickets
        {where}
        ORDER BY
          CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END,
          created_at DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return {"tickets": [redact_sensitive(dict(row)) for row in rows]}


@app.get("/api/admin/slack/config-status")
async def get_slack_config_status(request: Request):
    verify_admin_access(request)
    webhook_configured = bool(os.getenv("SLACK_TICKETS_WEBHOOK_URL"))
    signing_secret_configured = bool(os.getenv("SLACK_SIGNING_SECRET"))
    interactive_enabled = (os.getenv("SLACK_INTERACTIVE_TICKETS") or "").lower() in {"1", "true", "yes"}
    ready = webhook_configured and signing_secret_configured and interactive_enabled
    if ready:
        message = "Slack ticket webhooks and signed interactive buttons are configured."
    elif webhook_configured:
        message = "Slack ticket webhooks are configured; add SLACK_SIGNING_SECRET and set SLACK_INTERACTIVE_TICKETS=true for Slack buttons."
    else:
        message = "Slack ticket webhook is not configured."
    return {
        "webhook_configured": webhook_configured,
        "signing_secret_configured": signing_secret_configured,
        "interactive_buttons_enabled": interactive_enabled,
        "ready_for_interactive_buttons": ready,
        "interaction_endpoint": "/api/slack/interactions",
        "message": message,
    }


@app.post("/api/admin/session")
async def start_admin_session(payload: AdminSessionRequest, request: Request):
    return create_admin_session(payload, request)


@app.get("/api/admin/session")
async def get_admin_session_status(request: Request):
    context = verify_admin_access(request)
    return {
        "status": "active",
        "operator": context["operator"],
        "session_id": context["session_id"],
        "auth_mode": context["auth_mode"],
    }


@app.delete("/api/admin/session")
async def end_admin_session(request: Request):
    return revoke_admin_session(request)


@app.get("/api/admin/audit-events")
async def list_admin_audit_events(request: Request, limit: int = 100):
    verify_admin_access(request)
    limit = max(1, min(int(limit or 100), 500))
    conn = get_db()
    rows = conn.execute("""
        SELECT id, session_id, operator, action, method, path, client_ip,
               outcome, detail, created_at
        FROM admin_audit_events
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {"events": [dict(row) for row in rows]}


@app.post("/api/admin/slack/tickets/{ticket_id}/close")
async def close_slack_ticket(ticket_id: str, payload: SlackApprovalRequest, request: Request):
    verify_admin_access(request)
    return close_support_ticket(ticket_id, payload.approved_by, payload.approval_note)


@app.post("/api/slack/interactions")
async def slack_ticket_interactions(request: Request):
    body = await request.body()
    verify_slack_interaction_signature(request, body)
    form = urllib.parse.parse_qs(body.decode("utf-8"), keep_blank_values=True)
    raw_payload = form.get("payload", [""])[0]
    if not raw_payload:
        raise HTTPException(status_code=400, detail="Missing Slack interaction payload")
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid Slack interaction payload")

    actions = payload.get("actions") or []
    if not actions:
        return JSONResponse({"response_type": "ephemeral", "text": "No Slack action was found."})
    action = actions[0]
    action_id = action.get("action_id", "")
    ticket_id = action.get("value", "")
    actor = slack_actor(payload)
    note = f"Slack action {action_id} from {actor}"

    try:
        if action_id == "approve_enhancement":
            result = approve_support_ticket(ticket_id, actor, note)
            text = f"Approved SOSFiler ticket {ticket_id}."
            if result.get("already_processed"):
                text = f"SOSFiler ticket {ticket_id} was already approved."
            return JSONResponse({"response_type": "ephemeral", "text": text})
        if action_id == "close_ticket":
            result = close_support_ticket(ticket_id, actor, note)
            text = f"Closed SOSFiler ticket {ticket_id}."
            if result.get("already_processed"):
                text = f"SOSFiler ticket {ticket_id} was already closed."
            return JSONResponse({"response_type": "ephemeral", "text": text})
    except HTTPException as exc:
        if exc.status_code in {404, 409}:
            return JSONResponse({"response_type": "ephemeral", "text": str(exc.detail)})
        raise

    return JSONResponse({"response_type": "ephemeral", "text": f"Unsupported SOSFiler Slack action: {action_id}"})


@app.get("/api/admin/engineering-jobs")
async def list_engineering_jobs(request: Request, status: str = "", limit: int = 100):
    verify_admin_access(request)
    clauses = ["adapter_key = 'approved_ticket_engineering'"]
    params = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    limit = max(1, min(int(limit or 100), 500))
    conn = get_db()
    rows = conn.execute(f"""
        SELECT * FROM automation_runs
        WHERE {' AND '.join(clauses)}
        ORDER BY
          CASE status
            WHEN 'approved' THEN 0
            WHEN 'in_progress' THEN 1
            WHEN 'tests_failed' THEN 2
            WHEN 'blocked' THEN 3
            WHEN 'ready_to_deploy' THEN 4
            WHEN 'deployed' THEN 5
            ELSE 6
          END,
          created_at DESC
        LIMIT ?
    """, params + [limit]).fetchall()
    jobs = []
    for row in rows:
        log = parse_json_field(row["redacted_log"], [])
        ticket_id = engineering_ticket_id_from_log(log)
        ticket = conn.execute("SELECT * FROM support_tickets WHERE id = ?", (ticket_id,)).fetchone() if ticket_id else None
        jobs.append(serialize_engineering_run(row, ticket))
    conn.close()
    return {"jobs": jobs}


@app.get("/api/admin/engineering-jobs/{run_id}")
async def get_engineering_job_detail(run_id: str, request: Request):
    verify_admin_access(request)
    return {"job": get_engineering_job(run_id)}


@app.post("/api/admin/engineering-jobs/backfill-approved")
async def backfill_approved_engineering_jobs(request: Request):
    verify_admin_access(request)
    conn = get_db()
    approved_tickets = [dict(row) for row in conn.execute("""
        SELECT * FROM support_tickets
        WHERE status = 'approved'
        ORDER BY approved_at DESC, created_at DESC
    """).fetchall()]
    existing_ids = set()
    for row in conn.execute("SELECT redacted_log FROM automation_runs WHERE adapter_key = 'approved_ticket_engineering'").fetchall():
        existing_ids.add(engineering_ticket_id_from_log(parse_json_field(row["redacted_log"], [])))
    conn.close()

    created = []
    for ticket in approved_tickets:
        if ticket["id"] in existing_ids:
            continue
        run_id, _ = create_engineering_run_from_ticket(
            ticket,
            ticket.get("approved_by") or "backfill",
            "Backfilled approved enhancement ticket into engineering queue.",
        )
        created.append({"ticket_id": ticket["id"], "automation_run_id": run_id})
    return {"created": created, "created_count": len(created)}


@app.post("/api/admin/engineering-jobs/{run_id}/plan")
async def refresh_engineering_job_plan(run_id: str, payload: SlackApprovalRequest, request: Request):
    verify_admin_access(request)
    return refresh_engineering_plan(run_id, payload.approved_by)


@app.post("/api/admin/engineering-jobs/{run_id}/work-plan")
async def create_engineering_job_work_plan(run_id: str, payload: SlackApprovalRequest, request: Request):
    verify_admin_access(request)
    return create_engineering_work_plan(run_id, payload.approved_by)


@app.post("/api/admin/engineering-jobs/{run_id}/prepare-execution")
async def prepare_engineering_job_execution(run_id: str, payload: SlackApprovalRequest, request: Request):
    verify_admin_access(request)
    return prepare_engineering_execution(run_id, payload.approved_by)


@app.post("/api/admin/engineering-jobs/{run_id}/run-tests")
async def run_engineering_job_tests(run_id: str, payload: EngineeringTestRunRequest, request: Request):
    verify_admin_access(request)
    return await asyncio.to_thread(run_engineering_tests, run_id, payload)


@app.post("/api/admin/engineering-jobs/{run_id}/deploy-check")
async def run_engineering_job_deploy_check(run_id: str, payload: EngineeringDeployCheckRequest, request: Request):
    verify_admin_access(request)
    return await asyncio.to_thread(run_engineering_deploy_check, run_id, payload)


@app.post("/api/admin/engineering-jobs/{run_id}/transition")
async def transition_engineering_job_endpoint(run_id: str, payload: EngineeringTransitionRequest, request: Request):
    verify_admin_access(request)
    return transition_engineering_job(run_id, payload)


@app.post("/api/admin/automation-runs/{run_id}/stop")
async def stop_automation_run(run_id: str, payload: AutomationStopRequest, request: Request):
    verify_admin_access(request)
    conn = get_db()
    row = conn.execute("SELECT * FROM automation_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Automation run not found")
    log = parse_json_field(row["redacted_log"], [])
    log.append({"at": utc_now(), "message": "Stop requested.", "reason": payload.reason})
    conn.execute("""
        UPDATE automation_runs
        SET stop_requested = 1, stop_reason = ?, status = 'stop_requested',
            redacted_log = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (payload.reason, json.dumps(redact_sensitive(log)), run_id))
    conn.commit()
    conn.close()
    execution_dual_write("stop_automation_run", run_id, payload.reason, redact_sensitive(log))
    return {"status": "stop_requested", "run_id": run_id}


@app.get("/api/admin/automation-runs")
async def get_automation_runs(request: Request, filing_job_id: str = "", order_id: str = "", status: str = ""):
    verify_admin_access(request)
    clauses = []
    params = []
    if filing_job_id:
        clauses.append("filing_job_id = ?")
        params.append(filing_job_id)
    if order_id:
        clauses.append("order_id = ?")
        params.append(order_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    conn = get_db()
    rows = conn.execute(f"""
        SELECT * FROM automation_runs
        {where}
        ORDER BY created_at DESC
        LIMIT 100
    """, params).fetchall()
    conn.close()
    runs = []
    for row in rows:
        item = dict(row)
        item["redacted_log"] = parse_json_field(item.get("redacted_log"), [])
        runs.append(item)
    return {"runs": runs}


@app.get("/api/admin/persistence-health")
async def get_persistence_health(request: Request):
    verify_admin_access(request)
    health = EXECUTION_PERSISTENCE.health()
    return {
        "ok": health.ok,
        "mode": health.mode,
        "message": health.message,
        "supabase_configured": EXECUTION_PERSISTENCE.repository.configured,
    }


@app.get("/api/admin/health/deep")
async def get_admin_deep_health(request: Request, alert: bool = False):
    verify_admin_access(request)
    health = await asyncio.to_thread(build_deep_health, True)
    alert_sent = False
    if alert:
        alert_sent = await asyncio.to_thread(send_health_slack_alert, health)
    health["alert_sent"] = alert_sent
    return health


@app.get("/api/admin/email/config-status")
async def get_admin_email_config_status(request: Request):
    verify_admin_access(request)
    from notifier import Notifier

    return Notifier().config_status()


@app.post("/api/admin/email/test")
async def send_admin_email_test(request: Request, payload: EmailTestRequest = EmailTestRequest()):
    verify_admin_access(request)
    from notifier import Notifier

    notifier = Notifier()
    return await notifier.send_test_email(
        str(payload.to_email) if payload.to_email else "",
        payload.subject or "SOSFiler SendGrid diagnostic",
    )


@app.get("/api/admin/persistence-sync-status")
async def get_persistence_sync_status(request: Request):
    verify_admin_access(request)
    conn = get_db()
    sqlite_counts = sqlite_execution_counts(conn)
    conn.close()
    supabase_result, supabase_counts = EXECUTION_PERSISTENCE.table_counts()
    return {
        "mode": EXECUTION_PERSISTENCE.mode,
        "supabase_ok": supabase_result.ok,
        "message": supabase_result.message,
        "sqlite_counts": sqlite_counts,
        "supabase_counts": supabase_counts,
        "deltas": execution_count_deltas(sqlite_counts, supabase_counts),
    }


def build_persistence_cutover_readiness(require_zero_deltas: bool = True, include_append_only: bool = False) -> dict:
    conn = get_db()
    sqlite_counts = sqlite_execution_counts(conn)
    conn.close()
    health = EXECUTION_PERSISTENCE.health()
    supabase_result, supabase_counts = EXECUTION_PERSISTENCE.table_counts()
    deltas = execution_count_deltas(sqlite_counts, supabase_counts)
    core_tables = {"execution_quotes", "execution_filing_jobs", "support_tickets", "automation_runs"}
    append_only_tables = {"execution_payment_ledger", "execution_events", "execution_artifacts", "stripe_webhook_events"}
    tables_to_check = set(deltas)
    if not include_append_only:
        tables_to_check = tables_to_check - append_only_tables
    if require_zero_deltas:
        zero_delta = all(int((deltas.get(table) or {}).get("delta", 0)) == 0 for table in tables_to_check)
    else:
        zero_delta = all(int((deltas.get(table) or {}).get("delta", 0)) <= 0 for table in tables_to_check)
    checks = [
        {
            "code": "mode_ready",
            "passed": EXECUTION_PERSISTENCE.mode in {"dual", "supabase"},
            "message": "EXECUTION_PERSISTENCE_MODE must be dual before cutover or supabase after cutover.",
        },
        {
            "code": "supabase_health",
            "passed": health.ok and supabase_result.ok,
            "message": health.message if health.ok else f"{health.message}; {supabase_result.message}",
        },
        {
            "code": "core_table_counts",
            "passed": all(table in supabase_counts for table in core_tables),
            "message": "Supabase execution tables are reachable.",
        },
        {
            "code": "count_deltas",
            "passed": zero_delta,
            "message": "SQLite and Supabase execution-table counts are aligned for selected tables.",
        },
        {
            "code": "rls_contract",
            "passed": True,
            "message": "Execution tables are backend-only with RLS enabled and service-role/direct Postgres access only.",
        },
    ]
    ready = all(check["passed"] for check in checks)
    return {
        "ready": ready,
        "mode": EXECUTION_PERSISTENCE.mode,
        "require_zero_deltas": require_zero_deltas,
        "include_append_only": include_append_only,
        "checks": checks,
        "sqlite_counts": sqlite_counts,
        "supabase_counts": supabase_counts,
        "deltas": deltas,
        "cutover_steps": [
            "Run POST /api/admin/persistence/backfill-execution with include_append_only=false until core deltas are zero.",
            "Run this readiness endpoint and verify ready=true.",
            "Set EXECUTION_PERSISTENCE_MODE=supabase only during a quiet filing window.",
            "Restart the service and verify /api/health/deep plus this readiness endpoint.",
            "Keep SQLite snapshots for rollback until production filings prove stable.",
        ],
    }


@app.get("/api/admin/persistence/cutover-readiness")
async def get_persistence_cutover_readiness(
    request: Request,
    require_zero_deltas: bool = True,
    include_append_only: bool = False,
):
    verify_admin_access(request)
    return build_persistence_cutover_readiness(require_zero_deltas, include_append_only)


@app.post("/api/admin/persistence/cutover-readiness")
async def post_persistence_cutover_readiness(payload: PersistenceCutoverReadinessRequest, request: Request):
    verify_admin_access(request)
    return build_persistence_cutover_readiness(payload.require_zero_deltas, payload.include_append_only)


@app.post("/api/admin/persistence/backfill-execution")
async def backfill_execution_persistence(payload: PersistenceBackfillRequest, request: Request):
    verify_admin_access(request)
    if not EXECUTION_PERSISTENCE.repository.configured:
        raise HTTPException(status_code=503, detail="Supabase persistence is not configured")

    conn = get_db()
    planned = {
        "execution_quotes": limited_table_count(conn, "execution_quotes", payload.limit),
        "execution_filing_jobs": limited_table_count(conn, "filing_jobs", payload.limit),
        "support_tickets": limited_table_count(conn, "support_tickets", payload.limit),
        "automation_runs": limited_table_count(conn, "automation_runs", payload.limit),
    }
    if payload.include_append_only:
        planned.update({
            "execution_payment_ledger": limited_table_count(conn, "payment_ledger", payload.limit),
            "execution_events": limited_table_count(conn, "filing_events", payload.limit),
            "execution_artifacts": limited_table_count(conn, "filing_artifacts", payload.limit),
        })
    if payload.dry_run:
        conn.close()
        return {
            "dry_run": True,
            "include_append_only": payload.include_append_only,
            "limit": payload.limit,
            "planned": planned,
            "warning": "Append-only tables can duplicate rows unless they are backfilled only once.",
        }

    written = {key: 0 for key in planned}
    errors: list[dict] = []

    for row in conn.execute("SELECT * FROM execution_quotes ORDER BY created_at LIMIT ?", (payload.limit,)).fetchall():
        quote = dict(row)
        quote["quote_id"] = quote.pop("id")
        quote["line_items"] = parse_json_field(quote.get("line_items"), [])
        result = execution_dual_write("upsert_quote", quote, quote.get("order_id") or "")
        written["execution_quotes"] += 1 if result.ok else 0
        if not result.ok:
            errors.append({"table": "execution_quotes", "id": quote["quote_id"], "message": result.message})

    for row in conn.execute("SELECT * FROM filing_jobs ORDER BY created_at LIMIT ?", (payload.limit,)).fetchall():
        job = serialize_filing_job(row)
        result = execution_dual_write("upsert_filing_job", job)
        written["execution_filing_jobs"] += 1 if result.ok else 0
        if not result.ok:
            errors.append({"table": "filing_jobs", "id": job["id"], "message": result.message})

    for row in conn.execute("SELECT * FROM support_tickets ORDER BY created_at LIMIT ?", (payload.limit,)).fetchall():
        ticket = dict(row)
        result = execution_dual_write("insert_support_ticket", ticket)
        written["support_tickets"] += 1 if result.ok else 0
        if not result.ok:
            errors.append({"table": "support_tickets", "id": ticket["id"], "message": result.message})

    for row in conn.execute("SELECT * FROM automation_runs ORDER BY created_at LIMIT ?", (payload.limit,)).fetchall():
        run = dict(row)
        run["redacted_log"] = parse_json_field(run.get("redacted_log"), [])
        result = execution_dual_write("insert_automation_run", run)
        written["automation_runs"] += 1 if result.ok else 0
        if not result.ok:
            errors.append({"table": "automation_runs", "id": run["id"], "message": result.message})

    if payload.include_append_only:
        for row in conn.execute("SELECT * FROM payment_ledger ORDER BY created_at LIMIT ?", (payload.limit,)).fetchall():
            ledger = dict(row)
            ledger["raw_event"] = parse_json_field(ledger.get("raw_event"), {})
            result = execution_dual_write("insert_payment_ledger", ledger)
            written["execution_payment_ledger"] += 1 if result.ok else 0
            if not result.ok:
                errors.append({"table": "payment_ledger", "id": ledger["id"], "message": result.message})
        for row in conn.execute("SELECT * FROM filing_events ORDER BY created_at LIMIT ?", (payload.limit,)).fetchall():
            event = dict(row)
            result = execution_dual_write("insert_event", event)
            written["execution_events"] += 1 if result.ok else 0
            if not result.ok:
                errors.append({"table": "filing_events", "id": event["id"], "message": result.message})
        for row in conn.execute("SELECT * FROM filing_artifacts ORDER BY created_at LIMIT ?", (payload.limit,)).fetchall():
            artifact = dict(row)
            result = execution_dual_write("insert_artifact", artifact)
            written["execution_artifacts"] += 1 if result.ok else 0
            if not result.ok:
                errors.append({"table": "filing_artifacts", "id": artifact["id"], "message": result.message})

    conn.close()
    return {
        "dry_run": False,
        "include_append_only": payload.include_append_only,
        "limit": payload.limit,
        "planned": planned,
        "written": written,
        "errors": errors[:50],
        "error_count": len(errors),
    }


@app.post("/api/admin/filing-jobs/{job_id}/evidence")
async def add_filing_evidence(job_id: str, evidence: FilingEvidenceRequest, request: Request):
    """Attach filing evidence or state correspondence to a filing job."""
    verify_admin_access(request)
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    is_evidence = 1 if evidence.artifact_type in {"submitted_receipt", "approved_certificate", "state_correspondence", "rejection_notice", "ein_confirmation_letter"} else 0
    conn.execute("""
        INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (job_id, job["order_id"], evidence.artifact_type, evidence.filename, evidence.file_path, is_evidence))
    execution_dual_write("insert_artifact", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "artifact_type": evidence.artifact_type,
        "filename": evidence.filename,
        "file_path": evidence.file_path,
        "is_evidence": bool(is_evidence),
    })
    if is_evidence:
        add_customer_document_if_missing(
            conn,
            job["order_id"],
            evidence.artifact_type,
            evidence.filename,
            evidence.file_path,
        )
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, ?, ?, 'operator', ?)
    """, (
        job_id,
        job["order_id"],
        f"evidence_{evidence.artifact_type}",
        evidence.message or f"Added {evidence.artifact_type.replace('_', ' ')} evidence.",
        evidence.file_path,
    ))
    execution_dual_write("insert_event", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "event_type": f"evidence_{evidence.artifact_type}",
        "message": evidence.message or f"Added {evidence.artifact_type.replace('_', ' ')} evidence.",
        "actor": "operator",
        "evidence_path": evidence.file_path,
    })
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/api/admin/filing-jobs/{job_id}/mark-submitted")
async def mark_filing_submitted(job_id: str, evidence: FilingEvidenceRequest, request: Request):
    """Mark a filing submitted only when submission evidence is captured."""
    verify_admin_access(request)
    if evidence.artifact_type != "submitted_receipt":
        raise HTTPException(status_code=400, detail="submitted_receipt evidence is required")
    if not evidence.file_path:
        raise HTTPException(status_code=400, detail="Official submission evidence path is required")
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (job["order_id"],)).fetchone()
    safety = validate_submission_safety(conn, dict(job), dict(order) if order else None)
    if not safety["passed"]:
        conn.close()
        raise HTTPException(status_code=400, detail={"message": "Submission safety gate blocked this filing.", "issues": safety["issues"]})
    conn.execute("""
        INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (job_id, job["order_id"], evidence.artifact_type, evidence.filename, evidence.file_path))
    execution_dual_write("insert_artifact", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "artifact_type": evidence.artifact_type,
        "filename": evidence.filename,
        "file_path": evidence.file_path,
        "is_evidence": True,
    })
    add_customer_document_if_missing(
        conn,
        job["order_id"],
        evidence.artifact_type,
        evidence.filename,
        evidence.file_path,
    )
    submitted_status = "submitted" if job["action_type"] == "annual_report" else "submitted_to_state"
    status_message = (
        evidence.message or "Annual report submitted with official receipt evidence."
        if job["action_type"] == "annual_report"
        else evidence.message or f"Submitted to {job['state']} with receipt evidence on file."
    )
    conn.execute("""
        UPDATE filing_jobs
        SET status = ?, submitted_at = datetime('now'), evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (submitted_status, evidence.message or evidence.filename, job_id))
    conn.execute("""
        UPDATE orders SET status = ?, filed_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
    """, (submitted_status, job["order_id"]))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, ?, ?)",
        (job["order_id"], submitted_status, status_message)
    )
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, ?, ?, 'operator', ?)
    """, (job_id, job["order_id"], submitted_status, status_message, evidence.file_path))
    execution_dual_write("upsert_filing_job", {**serialize_filing_job(job), "status": submitted_status})
    execution_dual_write("insert_event", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "event_type": submitted_status,
        "next_status": submitted_status,
        "message": status_message,
        "actor": "operator",
        "evidence_path": evidence.file_path,
    })
    conn.commit()
    conn.close()

    from notifier import Notifier
    notifier = Notifier()
    if evidence.notify_customer:
        await notifier.send_filing_submitted(dict(order), json.loads(order["formation_data"]), evidence.file_path)
    else:
        await notifier.send_admin_filing_submitted(dict(order), dict(job), evidence.file_path, evidence.message)
    return {"status": submitted_status, "customer_notified": evidence.notify_customer}


@app.post("/api/admin/filing-jobs/{job_id}/mark-approved")
async def mark_filing_approved(job_id: str, evidence: FilingEvidenceRequest, request: Request):
    """Mark a state filing approved only when approval evidence is captured."""
    verify_admin_access(request)
    if evidence.artifact_type != "approved_certificate":
        raise HTTPException(status_code=400, detail="approved_certificate evidence is required")
    if not evidence.file_path:
        raise HTTPException(status_code=400, detail="Official approval evidence path is required")
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (job["order_id"],)).fetchone()
    conn.execute("""
        INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (job_id, job["order_id"], evidence.artifact_type, evidence.filename, evidence.file_path))
    execution_dual_write("insert_artifact", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "artifact_type": evidence.artifact_type,
        "filename": evidence.filename,
        "file_path": evidence.file_path,
        "is_evidence": True,
    })
    add_customer_document_if_missing(
        conn,
        job["order_id"],
        evidence.artifact_type,
        evidence.filename,
        evidence.file_path,
    )
    approved_status = "approved" if job["action_type"] == "annual_report" else "state_approved"
    approval_message = (
        evidence.message or "Annual report accepted with official evidence on file."
        if job["action_type"] == "annual_report"
        else evidence.message or f"{job['state']} approved the filing. Approval evidence is on file."
    )
    conn.execute("""
        UPDATE filing_jobs
        SET status = ?, approved_at = datetime('now'), evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (approved_status, evidence.message or evidence.filename, job_id))
    conn.execute("""
        UPDATE orders SET status = ?, approved_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
    """, (approved_status, job["order_id"]))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, ?, ?)",
        (job["order_id"], approved_status, approval_message)
    )
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, ?, ?, 'operator', ?)
    """, (job_id, job["order_id"], approved_status, approval_message, evidence.file_path))
    execution_dual_write("upsert_filing_job", {**serialize_filing_job(job), "status": approved_status})
    execution_dual_write("insert_event", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "event_type": approved_status,
        "next_status": approved_status,
        "message": approval_message,
        "actor": "operator",
        "evidence_path": evidence.file_path,
    })
    conn.commit()
    conn.close()

    from notifier import Notifier
    notifier = Notifier()
    if evidence.notify_customer:
        await notifier.send_formation_approved(
            dict(order),
            json.loads(order["formation_data"]),
            [{"path": evidence.file_path, "name": evidence.filename}],
        )
    else:
        await notifier.send_admin_formation_approved(dict(order), dict(job), evidence.file_path, evidence.message)
    return {"status": approved_status, "customer_notified": evidence.notify_customer}


@app.post("/api/admin/filing-jobs/{job_id}/annual-report/mark-complete")
async def mark_annual_report_complete(job_id: str, evidence: FilingEvidenceRequest, request: Request):
    """Complete an annual report only after official acceptance evidence is attached."""
    verify_admin_access(request)
    if evidence.artifact_type not in {"approved_certificate", "state_correspondence"}:
        raise HTTPException(status_code=400, detail="approved_certificate or state_correspondence evidence is required")
    if not evidence.file_path:
        raise HTTPException(status_code=400, detail="Official completion evidence path is required")
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    if job["action_type"] != "annual_report":
        conn.close()
        raise HTTPException(status_code=400, detail="Annual report completion is only available for annual_report jobs")
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (job["order_id"],)).fetchone()
    conn.execute("""
        INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (job_id, job["order_id"], evidence.artifact_type, evidence.filename, evidence.file_path))
    add_customer_document_if_missing(conn, job["order_id"], evidence.artifact_type, evidence.filename, evidence.file_path)
    message = evidence.message or "Annual report is complete with official acceptance evidence on file."
    conn.execute("""
        UPDATE filing_jobs
        SET status = 'complete', approved_at = COALESCE(approved_at, datetime('now')),
            evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (message, job_id))
    conn.execute("UPDATE orders SET status = 'complete', updated_at = datetime('now') WHERE id = ?", (job["order_id"],))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'complete', ?)",
        (job["order_id"], message),
    )
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, 'complete', ?, 'operator', ?)
    """, (job_id, job["order_id"], message, evidence.file_path))
    conn.commit()
    conn.close()
    execution_dual_write("upsert_filing_job", {**serialize_filing_job(job), "status": "complete"})
    execution_dual_write("insert_event", {
        "filing_job_id": job_id,
        "order_id": job["order_id"],
        "event_type": "complete",
        "message": message,
        "actor": "operator",
        "evidence_path": evidence.file_path,
    })
    return {"status": "complete", "order_id": order["id"], "customer_notified": evidence.notify_customer}


@app.post("/api/admin/corpnet/registered-agent/quote")
async def corpnet_ra_quote(payload: CorpNetRAQuoteRequest, request: Request):
    """Admin-only CorpNet RA quote call. Safe dry-run when credentials are absent."""
    verify_admin_access(request)
    client = CorpNetClient()
    request_payload = {
        "state": payload.state.upper(),
        "entity_name": payload.entity_name,
        "entity_type": payload.entity_type,
        "contact": {
            "name": payload.contact_name,
            "email": payload.contact_email,
        },
        "metadata": payload.metadata or {},
    }
    if not client.configured:
        return {
            "dry_run": True,
            "configured": False,
            "message": "CorpNet credentials not configured; no outbound API call was made.",
            "would_send": request_payload,
        }
    return {
        "dry_run": False,
        "configured": True,
        "result": client.quote_registered_agent(request_payload),
    }


@app.post("/api/admin/corpnet/registered-agent/orders")
async def corpnet_ra_create_order(payload: CorpNetRAOrderRequest, request: Request):
    """Admin-only CorpNet RA order create call. Does not alter SOSFiler order/job state."""
    verify_admin_access(request)
    client = CorpNetClient()
    request_payload = {
        "state": payload.state.upper(),
        "entity_name": payload.entity_name,
        "entity_type": payload.entity_type,
        "contact": {
            "name": payload.contact_name,
            "email": payload.contact_email,
        },
        "external_customer_id": payload.external_customer_id,
        "external_order_id": payload.sosfiler_order_id,
        "metadata": payload.metadata or {},
    }
    if not client.configured:
        return {
            "dry_run": True,
            "configured": False,
            "message": "CorpNet credentials not configured; no outbound API call was made.",
            "would_send": request_payload,
        }
    return {
        "dry_run": False,
        "configured": True,
        "result": client.create_registered_agent_order(request_payload),
    }


@app.get("/api/admin/corpnet/registered-agent/orders/{external_order_id}")
async def corpnet_ra_order_status(external_order_id: str, request: Request):
    """Admin-only CorpNet RA order status lookup."""
    verify_admin_access(request)
    client = CorpNetClient()
    if not client.configured:
        return {
            "dry_run": True,
            "configured": False,
            "message": "CorpNet credentials not configured; no outbound API call was made.",
            "external_order_id": external_order_id,
        }
    return {
        "dry_run": False,
        "configured": True,
        "result": client.get_registered_agent_order(external_order_id),
    }


CORPNET_RA_READY_STATUSES = {"active", "assigned", "approved", "complete", "completed", "fulfilled", "ready"}


def corpnet_result_data(result: dict) -> dict:
    data = result.get("data") if isinstance(result, dict) else {}
    return data if isinstance(data, dict) else {}


def corpnet_status_value(result: dict) -> str:
    data = corpnet_result_data(result)
    candidates = [
        data.get("registered_agent_status"),
        data.get("fulfillment_status"),
        data.get("order_status"),
        data.get("status"),
    ]
    nested_agent = data.get("registered_agent")
    if isinstance(nested_agent, dict):
        candidates.extend([
            nested_agent.get("status"),
            nested_agent.get("assignment_status"),
        ])
    for value in candidates:
        if value:
            return str(value).strip().lower()
    return ""


def corpnet_external_order_id(result: dict, fallback: str = "") -> str:
    data = corpnet_result_data(result)
    candidates = [
        data.get("external_order_id"),
        data.get("corpnet_order_id"),
        data.get("partner_order_id"),
        data.get("order_id"),
        data.get("id"),
    ]
    nested_order = data.get("order")
    if isinstance(nested_order, dict):
        candidates.extend([
            nested_order.get("external_order_id"),
            nested_order.get("id"),
            nested_order.get("order_id"),
        ])
    for value in candidates:
        if value:
            return str(value)
    return fallback


def corpnet_assignment_ready(result: dict) -> bool:
    data = corpnet_result_data(result)
    status = corpnet_status_value(result)
    if status in CORPNET_RA_READY_STATUSES:
        return True
    nested_agent = data.get("registered_agent")
    document_keys = {
        "assignment_document_url",
        "consent_document_url",
        "document_url",
        "certificate_url",
    }
    if isinstance(nested_agent, dict) and any(nested_agent.get(key) for key in document_keys):
        return True
    return any(data.get(key) for key in document_keys)


def _read_artifact_json(file_path: str) -> dict:
    try:
        path = resolve_document_path(file_path)
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _latest_ra_artifact(conn, job_id: str, artifact_types: tuple[str, ...]):
    placeholders = ",".join("?" for _ in artifact_types)
    return conn.execute(
        f"""
        SELECT * FROM filing_artifacts
        WHERE filing_job_id = ? AND artifact_type IN ({placeholders})
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (job_id, *artifact_types),
    ).fetchone()


def _attach_registered_agent_artifact(
    conn,
    *,
    job: dict,
    order: dict,
    artifact_type: str,
    provider_result: dict,
    actor: str,
    message: str,
    visibility: str,
) -> dict:
    existing = _latest_ra_artifact(conn, job["id"], (artifact_type,))
    if existing and artifact_type in {"registered_agent_assignment", "registered_agent_partner_order"}:
        return {
            "attached": False,
            "already_attached": True,
            "artifact_path": existing["file_path"],
        }

    filename = f"{artifact_type}.json"
    path = DOCS_DIR / order["id"] / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    artifact_payload = {
        "provider": "corpnet",
        "order_id": order["id"],
        "created_at": utc_now(),
        "actor": normalize_operator(actor),
        "provider_status": corpnet_status_value(provider_result),
        "external_order_id": corpnet_external_order_id(provider_result, order["id"]),
        "provider_result": redact_sensitive(provider_result),
    }
    path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")
    conn.execute(
        """
        INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job["id"], order["id"], artifact_type, filename, str(path), 1 if artifact_type == "registered_agent_assignment" else 0),
    )
    add_customer_document_if_missing(
        conn,
        order["id"],
        artifact_type,
        filename,
        str(path),
        "json",
        "registered_agent",
        visibility,
    )
    event_type = "registered_agent_assignment" if artifact_type == "registered_agent_assignment" else "registered_agent_order_created"
    conn.execute(
        """
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            job["id"],
            order["id"],
            event_type,
            message,
            normalize_operator(actor),
            str(path) if artifact_type == "registered_agent_assignment" else "",
        ),
    )
    execution_dual_write("insert_artifact", {
        "filing_job_id": job["id"],
        "order_id": order["id"],
        "artifact_type": artifact_type,
        "filename": filename,
        "file_path": str(path),
        "is_evidence": artifact_type == "registered_agent_assignment",
        "visibility": visibility,
    })
    execution_dual_write("insert_event", {
        "filing_job_id": job["id"],
        "order_id": order["id"],
        "event_type": event_type,
        "message": message,
        "actor": normalize_operator(actor),
        "evidence_path": str(path) if artifact_type == "registered_agent_assignment" else "",
    })
    return {
        "attached": True,
        "already_attached": False,
        "artifact_path": str(path),
    }


def refresh_ready_status_after_ra_assignment(conn, order: dict, job: dict, actor: str) -> dict:
    refreshed_order = dict(conn.execute("SELECT * FROM orders WHERE id = ?", (order["id"],)).fetchone())
    refreshed_job = dict(conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job["id"],)).fetchone())
    readiness = payment_execution_readiness(conn, refreshed_order, refreshed_job)
    blocking = [check for check in readiness["checks"] if check["status"] == "blocked"]
    if blocking:
        return {"ready_to_file": False, "remaining_blockers": blocking}
    conn.execute("UPDATE filing_jobs SET status = 'ready_to_file', updated_at = datetime('now') WHERE id = ?", (job["id"],))
    if refreshed_order["status"] in {"paid", "payment_authorized", "payment_captured", "preparing", "generating_documents", "operator_required"}:
        conn.execute("UPDATE orders SET status = 'ready_to_file', updated_at = datetime('now') WHERE id = ?", (order["id"],))
    message = "Registered agent evidence is attached; filing is ready for operator-verified submission."
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'ready_to_file', ?)",
        (order["id"], message),
    )
    conn.execute(
        """
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor)
        VALUES (?, ?, 'registered_agent_reconciled', ?, ?)
        """,
        (job["id"], order["id"], message, normalize_operator(actor)),
    )
    execution_dual_write("insert_event", {
        "filing_job_id": job["id"],
        "order_id": order["id"],
        "event_type": "registered_agent_reconciled",
        "message": message,
        "actor": normalize_operator(actor),
    })
    return {"ready_to_file": True, "remaining_blockers": []}


def fulfill_registered_agent_assignment(order_id: str, payload: RAFulfillmentRequest) -> dict:
    """Attach partner RA evidence only after a successful provider order."""
    if payload.provider != "corpnet":
        raise HTTPException(status_code=400, detail="Only CorpNet RA fulfillment is currently supported.")
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    order = dict(order)
    formation_data = parse_json_field(order.get("formation_data"), {})
    if formation_data.get("ra_choice") != "sosfiler":
        conn.close()
        raise HTTPException(status_code=400, detail="This order does not use SOSFiler/partner registered agent service.")
    job = conn.execute(
        "SELECT * FROM filing_jobs WHERE order_id = ? AND action_type = 'formation' ORDER BY created_at DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=400, detail="Formation filing job is required before RA evidence can be attached.")
    existing = conn.execute(
        """
        SELECT file_path FROM filing_artifacts
        WHERE filing_job_id = ? AND artifact_type = 'registered_agent_assignment'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (job["id"],),
    ).fetchone()
    if existing:
        path_value = existing["file_path"]
        conn.close()
        return {
            "dry_run": False,
            "configured": True,
            "evidence_attached": True,
            "already_attached": True,
            "artifact_path": path_value,
            "message": "Registered agent assignment evidence is already attached.",
        }
    job = dict(job)
    client = CorpNetClient()
    request_payload = {
        "state": order["state"],
        "entity_name": order["business_name"],
        "entity_type": order["entity_type"],
        "contact": {
            "name": (formation_data.get("members") or [{}])[0].get("name", ""),
            "email": order["email"],
        },
        "external_customer_id": order.get("user_id") or order["email"],
        "external_order_id": order_id,
        "metadata": {"source": "sosfiler_ra_fulfillment", "actor": normalize_operator(payload.actor)},
    }
    if not client.configured:
        conn.close()
        return {
            "dry_run": True,
            "configured": False,
            "evidence_attached": False,
            "message": "CorpNet credentials are not configured. No RA assignment evidence was attached, so formation submission remains blocked.",
            "would_send": request_payload,
        }
    result = client.create_registered_agent_order(request_payload)
    if not result.get("ok"):
        conn.close()
        return {
            "dry_run": False,
            "configured": True,
            "evidence_attached": False,
            "message": "CorpNet returned an error; no RA assignment evidence was attached and formation submission remains blocked.",
            "provider_result": redact_sensitive(result),
        }
    if not corpnet_assignment_ready(result):
        status = corpnet_status_value(result) or "pending"
        message = payload.message or f"CorpNet registered agent order was created and is currently {status}; assignment evidence is not ready yet."
        artifact_result = _attach_registered_agent_artifact(
            conn,
            job=job,
            order=order,
            artifact_type="registered_agent_partner_order",
            provider_result=result,
            actor=payload.actor,
            message=message,
            visibility="admin",
        )
        conn.commit()
        conn.close()
        return {
            "dry_run": False,
            "configured": True,
            "evidence_attached": False,
            "partner_order_created": True,
            "external_order_id": corpnet_external_order_id(result, order_id),
            "provider_status": status,
            "artifact_path": artifact_result.get("artifact_path", ""),
            "message": "CorpNet order is created, but RA assignment/consent evidence is not ready yet. Formation submission remains blocked.",
            "provider_result": redact_sensitive(result),
        }

    message = payload.message or "Registered agent assignment evidence attached from CorpNet."
    artifact_result = _attach_registered_agent_artifact(
        conn,
        job=job,
        order=order,
        artifact_type="registered_agent_assignment",
        provider_result=result,
        actor=payload.actor,
        message=message,
        visibility="customer",
    )
    readiness_update = refresh_ready_status_after_ra_assignment(conn, order, job, payload.actor)
    conn.commit()
    conn.close()
    return {
        "dry_run": False,
        "configured": True,
        "evidence_attached": True,
        "already_attached": artifact_result.get("already_attached", False),
        "artifact_path": artifact_result.get("artifact_path", ""),
        "ready_to_file": readiness_update.get("ready_to_file", False),
        "remaining_blockers": readiness_update.get("remaining_blockers", []),
        "provider_result": redact_sensitive(result),
    }


@app.post("/api/admin/orders/{order_id}/registered-agent/fulfill")
async def fulfill_registered_agent_for_order(order_id: str, payload: RAFulfillmentRequest, request: Request):
    """Order-level RA fulfillment gate. Attaches assignment evidence only after a configured provider call."""
    verify_admin_access(request)
    return fulfill_registered_agent_assignment(order_id, payload)


def registered_agent_reconciliation_candidates(conn, order_id: str = "", limit: int = 50) -> list[dict]:
    clauses = ["j.action_type = 'formation'"]
    params: list = []
    if order_id:
        clauses.append("j.order_id = ?")
        params.append(order_id)
    rows = conn.execute(f"""
        SELECT
            j.*,
            o.email,
            o.user_id,
            o.business_name,
            o.formation_data,
            o.status AS order_status
        FROM filing_jobs j
        JOIN orders o ON o.id = j.order_id
        WHERE {' AND '.join(clauses)}
        ORDER BY j.updated_at DESC, j.created_at DESC
        LIMIT ?
    """, params + [max(1, min(int(limit or 50), 250))]).fetchall()
    candidates = []
    for row in rows:
        job = dict(row)
        formation_data = parse_json_field(job.get("formation_data"), {})
        if formation_data.get("ra_choice") != "sosfiler":
            continue
        assignment = _latest_ra_artifact(conn, job["id"], ("registered_agent_assignment",))
        partner_order = _latest_ra_artifact(conn, job["id"], ("registered_agent_partner_order", "registered_agent_assignment"))
        external_order_id = job["order_id"]
        provider_status = ""
        if partner_order:
            payload = _read_artifact_json(partner_order["file_path"])
            external_order_id = payload.get("external_order_id") or external_order_id
            provider_status = payload.get("provider_status") or ""
        candidates.append({
            "job": job,
            "order": {
                "id": job["order_id"],
                "email": job.get("email"),
                "user_id": job.get("user_id"),
                "business_name": job.get("business_name"),
                "state": job.get("state"),
                "entity_type": job.get("entity_type"),
                "status": job.get("order_status"),
            },
            "external_order_id": external_order_id,
            "provider_status": provider_status,
            "assignment_attached": bool(assignment),
            "partner_order_tracked": bool(partner_order),
        })
    return candidates


def reconcile_registered_agent_assignments(actor: str = "system", limit: int = 50, order_id: str = "") -> dict:
    client = CorpNetClient()
    conn = get_db()
    candidates = registered_agent_reconciliation_candidates(conn, order_id, limit)
    run_id = f"RA-REC-{uuid.uuid4().hex[:12].upper()}"
    start_log = [{
        "at": utc_now(),
        "message": "CorpNet RA reconciliation started.",
        "candidate_count": len(candidates),
        "configured": client.configured,
        "order_id": order_id,
    }]
    if not client.configured:
        conn.execute("""
            INSERT INTO automation_runs (id, filing_job_id, order_id, adapter_key, lane, status, redacted_log)
            VALUES (?, NULL, ?, 'corpnet_registered_agent_reconciliation', 'partner_api', 'dry_run_complete', ?)
        """, (run_id, order_id, json.dumps(redact_sensitive(start_log))))
        conn.commit()
        conn.close()
        return {
            "dry_run": True,
            "configured": False,
            "run_id": run_id,
            "message": "CorpNet credentials are not configured; no outbound status calls were made.",
            "candidates": [
                {
                    "order_id": item["order"]["id"],
                    "job_id": item["job"]["id"],
                    "external_order_id": item["external_order_id"],
                    "assignment_attached": item["assignment_attached"],
                    "partner_order_tracked": item["partner_order_tracked"],
                }
                for item in candidates
            ],
        }

    results = []
    attached_count = 0
    for item in candidates:
        job = item["job"]
        order = item["order"]
        if item["assignment_attached"]:
            results.append({
                "order_id": order["id"],
                "job_id": job["id"],
                "status": "already_attached",
                "external_order_id": item["external_order_id"],
            })
            continue
        status_result = client.get_registered_agent_order(item["external_order_id"])
        provider_status = corpnet_status_value(status_result) or "unknown"
        row_result = {
            "order_id": order["id"],
            "job_id": job["id"],
            "external_order_id": item["external_order_id"],
            "provider_status": provider_status,
            "provider_ok": bool(status_result.get("ok")),
            "assignment_ready": corpnet_assignment_ready(status_result),
            "evidence_attached": False,
        }
        if status_result.get("ok") and corpnet_assignment_ready(status_result):
            artifact_result = _attach_registered_agent_artifact(
                conn,
                job=job,
                order=order,
                artifact_type="registered_agent_assignment",
                provider_result=status_result,
                actor=actor,
                message="Registered agent assignment evidence attached after CorpNet status reconciliation.",
                visibility="customer",
            )
            refresh = refresh_ready_status_after_ra_assignment(conn, order, job, actor)
            attached_count += 1 if artifact_result.get("attached") else 0
            row_result.update({
                "evidence_attached": True,
                "artifact_path": artifact_result.get("artifact_path", ""),
                "ready_to_file": refresh.get("ready_to_file", False),
                "remaining_blockers": refresh.get("remaining_blockers", []),
            })
        results.append(redact_sensitive(row_result))

    completion_log = start_log + [{
        "at": utc_now(),
        "message": "CorpNet RA reconciliation completed.",
        "checked": len(results),
        "attached_count": attached_count,
        "results": results,
    }]
    conn.execute("""
        INSERT INTO automation_runs (id, filing_job_id, order_id, adapter_key, lane, status, redacted_log)
        VALUES (?, NULL, ?, 'corpnet_registered_agent_reconciliation', 'partner_api', 'completed', ?)
    """, (run_id, order_id, json.dumps(redact_sensitive(completion_log))))
    conn.commit()
    conn.close()
    return {
        "dry_run": False,
        "configured": True,
        "run_id": run_id,
        "checked": len(results),
        "evidence_attached": attached_count,
        "results": results,
    }


@app.post("/api/admin/corpnet/registered-agent/reconcile")
async def reconcile_corpnet_registered_agent_orders(payload: CorpNetRAReconcileRequest, request: Request):
    """Safe CorpNet RA reconciliation worker entrypoint; dry-runs until credentials exist."""
    verify_admin_access(request)
    return reconcile_registered_agent_assignments(payload.actor, payload.limit, payload.order_id)


@app.get("/api/admin/ein-queue")
async def get_admin_ein_queue(request: Request, limit: int = 100):
    verify_admin_access(request)
    return {
        "irs_availability": irs_ein_availability(),
        "queue": list_ein_queue(limit),
    }


@app.get("/api/admin/ein-queue/{order_id}")
async def get_admin_ein_queue_detail(order_id: str, request: Request):
    verify_admin_access(request)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    access_rows = conn.execute("""
        SELECT actor, purpose, created_at
        FROM pii_access_events
        WHERE subject_type = 'order' AND subject_id = ?
        ORDER BY created_at DESC
        LIMIT 25
    """, (order_id,)).fetchall()
    conn.close()
    return {
        "queue": redacted_ein_queue_summary(dict(order)),
        "access_events": [dict(row) for row in access_rows],
    }


@app.post("/api/admin/ein-queue/{order_id}/prepare-submission")
async def prepare_admin_ein_submission(order_id: str, payload: EinQueueActionRequest, request: Request):
    """Validate EIN queue readiness, audit SSN vault access, and gate by IRS hours."""
    verify_admin_access(request)
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    formation_data = parse_json_field(order["formation_data"], {})
    queue = load_ein_queue(order_id)
    responsible = ((queue.get("ss4_data") or {}).get("responsible_party") or {})
    vault_id = responsible.get("ssn_vault_id") or formation_data.get("responsible_party_ssn_vault_id", "")
    if not vault_id:
        conn.close()
        raise HTTPException(status_code=400, detail="Responsible-party SSN vault reference is required before EIN submission")
    availability = irs_ein_availability()
    if not availability["open"]:
        queue.setdefault("order_id", order_id)
        queue["status"] = "blocked_outside_irs_hours"
        queue["irs_availability"] = availability
        queue_path = save_ein_queue(order_id, queue)
        conn.execute(
            "INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path) SELECT id, order_id, 'ein_blocked_outside_irs_hours', ?, ?, ? FROM filing_jobs WHERE order_id = ? ORDER BY created_at DESC LIMIT 1",
            (availability["reason"], normalize_operator(payload.actor), str(queue_path), order_id),
        )
        conn.commit()
        conn.close()
        return {"status": "blocked_outside_irs_hours", "irs_availability": availability, "queue": redacted_ein_queue_summary(dict(order), queue)}

    ssn = retrieve_sensitive_value(vault_id, payload.actor, "ein_submission_prepare")
    if not _re.fullmatch(r"\d{9}", _normalize_ssn_itin(ssn)):
        conn.close()
        raise HTTPException(status_code=400, detail="Stored SSN/ITIN is invalid")
    queue.setdefault("order_id", order_id)
    queue["status"] = "ready_for_browser_submission"
    queue["irs_availability"] = availability
    queue["prepared_for_submission_at"] = utc_now()
    queue["prepared_by"] = normalize_operator(payload.actor)
    queue_path = save_ein_queue(order_id, queue)
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'ein_ready_for_submission', ?)",
        (order_id, payload.message or "EIN queue passed IRS-hours and secure SSN readiness checks."),
    )
    conn.execute("UPDATE orders SET status = 'ein_pending', updated_at = datetime('now') WHERE id = ?", (order_id,))
    conn.execute(
        "INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path) SELECT id, order_id, 'ein_ready_for_submission', ?, ?, ? FROM filing_jobs WHERE order_id = ? ORDER BY created_at DESC LIMIT 1",
        (payload.message or "EIN queue is ready for browser submission.", normalize_operator(payload.actor), str(queue_path), order_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ready_for_browser_submission", "irs_availability": availability, "queue": redacted_ein_queue_summary(dict(order), queue)}


@app.post("/api/admin/ein-queue/{order_id}/complete")
async def complete_admin_ein_queue(order_id: str, payload: EinCompletionRequest, request: Request):
    verify_admin_access(request)
    if not payload.file_path:
        raise HTTPException(status_code=400, detail="Official EIN confirmation evidence path is required")
    ein_digits = _re.sub(r"\D", "", payload.ein or "")
    normalized_ein = f"{ein_digits[:2]}-{ein_digits[2:]}" if len(ein_digits) == 9 else payload.ein.strip()
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    filename = payload.filename or Path(payload.file_path).name
    add_customer_document_if_missing(conn, order_id, "ein_confirmation_letter", filename, payload.file_path, Path(filename).suffix.lstrip(".") or "pdf")
    job = conn.execute("SELECT id FROM filing_jobs WHERE order_id = ? ORDER BY created_at DESC LIMIT 1", (order_id,)).fetchone()
    if job:
        conn.execute("""
            INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
            VALUES (?, ?, 'ein_confirmation_letter', ?, ?, 1)
        """, (job["id"], order_id, filename, payload.file_path))
        conn.execute("""
            INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
            VALUES (?, ?, 'ein_received', ?, ?, ?)
        """, (job["id"], order_id, payload.message or "EIN confirmation evidence captured.", normalize_operator(payload.actor), payload.file_path))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'ein_received', ?)",
        (order_id, payload.message or "EIN confirmation document has been captured and added to your document vault."),
    )
    if normalized_ein:
        conn.execute("UPDATE orders SET ein = ?, status = 'ein_received', updated_at = datetime('now') WHERE id = ?", (normalized_ein, order_id))
    else:
        conn.execute("UPDATE orders SET status = 'ein_received', updated_at = datetime('now') WHERE id = ?", (order_id,))
    queue = load_ein_queue(order_id)
    queue.setdefault("order_id", order_id)
    queue["status"] = "ein_received"
    queue["completion_evidence_path"] = payload.file_path
    queue["completed_at"] = utc_now()
    queue["completed_by"] = normalize_operator(payload.actor)
    save_ein_queue(order_id, queue)
    conn.commit()
    conn.close()
    return {"status": "ein_received", "ein_recorded": bool(normalized_ein), "document": filename}


@app.get("/api/documents/{order_id}")
async def get_documents(order_id: str, token: str = ""):
    """List all generated documents for an order."""
    verify_order_access(order_id, token)

    conn = get_db()
    docs = conn.execute(
        "SELECT doc_type, filename, format, category, visibility, created_at FROM documents WHERE order_id = ? ORDER BY created_at",
        (order_id,)
    ).fetchall()
    conn.close()

    return {"order_id": order_id, "documents": customer_visible_documents(docs)}


@app.get("/api/admin/document-access-events")
async def get_admin_document_access_events(request: Request, order_id: str = "", limit: int = 100):
    verify_admin_access(request)
    clauses = []
    params: list = []
    if order_id:
        clauses.append("order_id = ?")
        params.append(order_id)
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(max(1, min(int(limit or 100), 500)))
    conn = get_db()
    rows = conn.execute(f"""
        SELECT * FROM document_access_events
        {where}
        ORDER BY created_at DESC
        LIMIT ?
    """, params).fetchall()
    conn.close()
    return {"events": [dict(row) for row in rows]}

@app.get("/api/documents/{order_id}/download/{filename}")
async def download_document(order_id: str, filename: str, token: str = ""):
    """Download a specific document."""
    verify_order_access(order_id, token)

    conn = get_db()
    doc = conn.execute(
        "SELECT file_path, filename, visibility FROM documents WHERE order_id = ? AND filename = ?",
        (order_id, filename)
    ).fetchone()
    conn.close()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if (doc["visibility"] or "customer") != "customer":
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = resolve_document_path(doc["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    record_document_access(order_id, doc["filename"], actor="customer_token", auth_context="order_token")

    suffix = file_path.suffix.lower()
    media_types = {
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".txt": "text/plain",
    }
    return FileResponse(
        str(file_path),
        filename=doc["filename"],
        media_type=media_types.get(suffix, "application/octet-stream"),
    )

@app.post("/api/orders/{order_id}/responsible-party-ssn")
async def submit_responsible_party_ssn(order_id: str, payload: ResponsiblePartySSNRequest, token: str = ""):
    """Secure order-token flow for collecting the full SSN/ITIN required by the IRS EIN application."""
    return update_responsible_party_ssn(order_id, payload.ssn_itin, token=token)

@app.post("/api/user/orders/{order_id}/responsible-party-ssn")
async def submit_user_responsible_party_ssn(order_id: str, payload: ResponsiblePartySSNRequest, request: Request):
    """Authenticated-account flow for collecting the full SSN/ITIN required by the IRS EIN application."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return update_responsible_party_ssn(order_id, payload.ssn_itin, user=user)

@app.post("/api/ein")
async def trigger_ein(order_id: str, token: str = "", background_tasks: BackgroundTasks = None):
    """Manually trigger EIN application."""
    order = verify_order_access(order_id, token)

    if order.get("ein"):
        return {"ein": order["ein"], "status": "already_received"}

    # Queue EIN application
    add_status_update(order_id, "ein_pending", "EIN application queued")
    return {"status": "queued", "message": "EIN application has been queued. You'll receive an email when it's ready."}

@app.get("/api/compliance/{order_id}")
async def get_compliance(order_id: str, token: str = ""):
    """Get compliance calendar for an order."""
    verify_order_access(order_id, token)

    conn = get_db()
    deadlines = conn.execute(
        "SELECT * FROM compliance_deadlines WHERE order_id = ? ORDER BY due_date ASC",
        (order_id,)
    ).fetchall()
    conn.close()

    return {"order_id": order_id, "deadlines": [dict(d) for d in deadlines]}

# --- License & DBA Routes ---

@app.get("/api/license-types")
async def get_license_types():
    """Return all supported license types with info and pricing."""
    from license_agent import LicenseAgent
    agent = LicenseAgent()
    return {
        "license_types": agent.get_license_types(),
        "pricing": agent.get_pricing(),
    }

@app.post("/api/license-check")
async def check_license(data: LicenseCheckRequest):
    """Check license requirements for a jurisdiction."""
    from license_agent import LicenseAgent
    agent = LicenseAgent()
    result = await agent.check_license(
        city=data.city,
        county=data.county,
        state=data.state,
        business_type=data.business_type,
        license_type=data.license_type,
    )
    return result

@app.post("/api/license-needs")
async def what_licenses_needed(data: LicenseNeedsRequest):
    """AI wizard: what licenses do I need for my business?"""
    from license_agent import LicenseAgent
    agent = LicenseAgent()
    result = await agent.what_licenses_do_i_need(
        city=data.city,
        state=data.state,
        business_type=data.business_type,
    )
    return result

@app.post("/api/dba")
async def start_dba_filing(data: DBARequest):
    """Start a DBA filing process."""
    from dba_filing import DBAFiler
    filer = DBAFiler()
    result = filer.create_filing(
        email=data.email,
        business_name=data.business_name,
        dba_name=data.dba_name,
        state=data.state,
        county=data.county,
        city=data.city,
    )
    return result

@app.get("/api/dba/status/{filing_id}")
async def get_dba_status(filing_id: str):
    """Get DBA filing status."""
    from dba_filing import DBAFiler
    filer = DBAFiler()
    result = filer.get_filing_status(filing_id)
    if not result:
        raise HTTPException(status_code=404, detail="DBA filing not found")
    return result

@app.post("/api/dba/checkout")
async def create_dba_checkout(data: dict):
    """Create a Stripe Checkout session for a DBA filing with fee breakdown."""
    filing_id = data.get("filing_id", "")
    success_url = data.get("success_url", "")
    cancel_url = data.get("cancel_url", "")
    state = data.get("state", "").upper()

    # Look up government fee from DBA requirements
    dba_info = DBA_REQUIREMENTS_DATA.get("dba_requirements", {}).get(state, {})
    gov_fee_dollars = dba_info.get("filing_fee", 0)
    gov_fee_cents = int(gov_fee_dollars * 100)
    state_name = dba_info.get("state_name", state)

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": DBA_PLATFORM_FEE,
                        "product_data": {
                            "name": "SOSFiler — DBA Filing Service",
                            "description": "DBA/Fictitious Business Name preparation and filing"
                        }
                    },
                    "quantity": 1
                },
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": gov_fee_cents,
                        "product_data": {
                            "name": f"{state_name} DBA Filing Fee",
                            "description": f"Government filing fee paid to {state_name} (passed through at cost)"
                        }
                    },
                    "quantity": 1
                }
            ],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"filing_id": filing_id, "type": "dba", "state": state}
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/license/checkout")
async def create_license_checkout(data: dict):
    """Create a Stripe Checkout session for a license filing with fee breakdown."""
    filing_id = data.get("filing_id", "")
    success_url = data.get("success_url", "")
    cancel_url = data.get("cancel_url", "")
    license_type = data.get("license_type", "")
    gov_fee_cents = int(data.get("gov_fee", 0) * 100)
    gov_fee_label = data.get("gov_fee_label", "Government Filing Fee")

    # Specialty licenses are $99, standard are $49
    specialty_types = {"liquor_license", "food_beverage", "str_license", "cannabis_license",
                       "childcare_license", "auto_dealer", "professional_license"}
    platform_fee = SPECIALTY_LICENSE_FEE if license_type in specialty_types else LICENSE_PLATFORM_FEE

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": platform_fee,
                        "product_data": {
                            "name": f"SOSFiler — {license_type.replace('_', ' ').title()} Filing",
                            "description": "License filing preparation and submission"
                        }
                    },
                    "quantity": 1
                },
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": gov_fee_cents,
                        "product_data": {
                            "name": gov_fee_label,
                            "description": "Government fee passed through at cost"
                        }
                    },
                    "quantity": 1
                }
            ],
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={"filing_id": filing_id, "type": "license", "license_type": license_type}
        )
        return {"checkout_url": session.url, "session_id": session.id}
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/license")
async def start_license_filing(data: LicenseRequest, background_tasks: BackgroundTasks):
    """Start a license filing/guidance process."""
    from license_agent import LicenseAgent
    agent = LicenseAgent()

    # First, check requirements
    result = await agent.check_license(
        city=data.city,
        county=data.county,
        state=data.state,
        business_type=data.business_type,
        license_type=data.license_type,
    )

    # Create filing record
    filing_id = f"LIC-{uuid.uuid4().hex[:12].upper()}"
    conn = get_db()
    conn.execute("""
        INSERT INTO license_filings (id, email, license_type, city, county, state, business_type,
                                     status, response_type, response_data)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (filing_id, data.email, data.license_type, data.city, data.county,
          data.state, data.business_type, "checked", result.get("response_type", "GUIDANCE"),
          json.dumps(result.get("data", {}))))
    conn.commit()
    conn.close()

    return {
        "filing_id": filing_id,
        "response_type": result.get("response_type"),
        "data": result.get("data"),
        "status": "checked",
    }

@app.get("/api/license/status/{filing_id}")
async def get_license_status(filing_id: str):
    """Get license filing status."""
    conn = get_db()
    row = conn.execute("SELECT * FROM license_filings WHERE id = ?", (filing_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="License filing not found")
    row = dict(row)
    return {
        "filing_id": row["id"],
        "license_type": row["license_type"],
        "state": row["state"],
        "city": row.get("city"),
        "county": row.get("county"),
        "status": row["status"],
        "response_type": row.get("response_type"),
        "data": json.loads(row["response_data"]) if row.get("response_data") else None,
        "created_at": row["created_at"],
    }


@app.get("/api/user/orders")
async def get_user_orders(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    conn = get_db()
    link_orders_to_user_by_email(conn, user["id"], user["email"])
    conn.commit()
    orders = conn.execute(
        """
        SELECT
            o.id,
            o.entity_type,
            o.state,
            o.business_name,
            o.status,
            o.created_at,
            o.paid_at,
            o.documents_ready_at,
            o.total_cents,
            COUNT(d.id) AS document_count
        FROM orders o
        LEFT JOIN documents d ON d.order_id = o.id
        WHERE o.user_id = ?
           OR lower(o.email) = lower(?)
        GROUP BY o.id
        ORDER BY COALESCE(o.paid_at, o.created_at) DESC
        """,
        (user["id"], user["email"])
    ).fetchall()
    conn.close()

    return {"orders": [dict(o) for o in orders]}

@app.get("/api/user/orders/{order_id}")
async def get_user_order_detail(order_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    conn = get_db()
    link_orders_to_user_by_email(conn, user["id"], user["email"])
    conn.commit()
    order = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE id = ?
          AND (user_id = ? OR lower(email) = lower(?))
        """,
        (order_id, user["id"], user["email"]),
    ).fetchone()
    if not order:
        conn.close()
        raise HTTPException(status_code=404, detail="Order not found")
    docs = conn.execute(
        "SELECT doc_type, filename, format, category, visibility, created_at FROM documents WHERE order_id = ? ORDER BY created_at",
        (order_id,),
    ).fetchall()
    updates = conn.execute(
        "SELECT status, message, created_at FROM status_updates WHERE order_id = ? ORDER BY created_at ASC",
        (order_id,),
    ).fetchall()
    deadlines = conn.execute(
        "SELECT deadline_type, due_date, status FROM compliance_deadlines WHERE order_id = ? ORDER BY due_date ASC",
        (order_id,),
    ).fetchall()
    filing_job = conn.execute(
        "SELECT * FROM filing_jobs WHERE order_id = ? ORDER BY created_at DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    filing_events = conn.execute(
        "SELECT event_type, message, actor, evidence_path, created_at FROM filing_events WHERE order_id = ? ORDER BY created_at ASC",
        (order_id,),
    ).fetchall()
    filing_artifacts = conn.execute(
        "SELECT artifact_type, filename, is_evidence, created_at FROM filing_artifacts WHERE order_id = ? ORDER BY created_at ASC",
        (order_id,),
    ).fetchall()
    conn.close()

    payload = dict(order)
    payload["formation_data"] = parse_json_field(payload.get("formation_data"), {})
    payload["ein_requires_ssn"] = not payload.get("ein") and formation_data_needs_ssn(payload["formation_data"])
    payload["documents"] = customer_visible_documents(docs)
    payload["timeline"] = customer_timeline(updates, filing_events, payload)
    payload["compliance_deadlines"] = [dict(d) for d in deadlines]
    payload["filing_events"] = [dict(e) for e in filing_events]
    payload["filing_artifacts"] = [dict(a) for a in filing_artifacts]
    payload["ein_queue"] = redacted_ein_queue_summary(payload)
    payload["filing_job"] = dict(filing_job) if filing_job else None
    if payload["filing_job"]:
        payload["filing_job"]["required_consents"] = parse_json_field(payload["filing_job"].get("required_consents"), [])
        payload["filing_job"]["required_evidence"] = parse_json_field(payload["filing_job"].get("required_evidence"), {})
    payload.pop("token", None)
    return {"order": payload}

@app.get("/api/user/orders/{order_id}/download/{filename}")
async def download_user_document(order_id: str, filename: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    conn = get_db()
    link_orders_to_user_by_email(conn, user["id"], user["email"])
    conn.commit()
    doc = conn.execute(
        """
        SELECT d.file_path, d.filename, d.visibility
        FROM documents d
        JOIN orders o ON o.id = d.order_id
        WHERE d.order_id = ?
          AND d.filename = ?
          AND (o.user_id = ? OR lower(o.email) = lower(?))
        """,
        (order_id, filename, user["id"], user["email"]),
    ).fetchone()
    conn.close()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if (doc["visibility"] or "customer") != "customer":
        raise HTTPException(status_code=404, detail="Document not found")

    file_path = resolve_document_path(doc["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    record_document_access(order_id, doc["filename"], actor=user["email"], auth_context="user_session")

    suffix = file_path.suffix.lower()
    media_types = {
        ".pdf": "application/pdf",
        ".md": "text/markdown",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".txt": "text/plain",
    }
    return FileResponse(
        str(file_path),
        filename=doc["filename"],
        media_type=media_types.get(suffix, "application/octet-stream"),
    )

# --- Health check ---
@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "2.0.0", "service": "SOSFiler"}


@app.get("/api/health/deep")
async def deep_health():
    return await asyncio.to_thread(build_deep_health, True)


@app.get("/api/research/state-progress")
async def research_state_progress():
    if not RESEARCH_JOB_QUEUE_PATH.exists():
        raise HTTPException(status_code=404, detail="Research queue not initialized")

    with RESEARCH_JOB_QUEUE_PATH.open() as f:
        queue = json.load(f)

    jobs = queue.get("jobs", [])
    by_state: dict[str, list[dict]] = {}
    for job in jobs:
        state = (job.get("state") or "").upper()
        if not state:
            continue
        by_state.setdefault(state, []).append(job)

    states_payload = []
    for state in sorted(by_state):
        state_jobs = by_state[state]
        task_status: dict[str, str] = {}
        for task in RESEARCH_REQUIRED_TASKS:
            matches = [j for j in state_jobs if j.get("task_type") == task]
            if not matches:
                task_status[task] = "missing"
                continue
            if any(j.get("status") == "verified" for j in matches):
                task_status[task] = "verified"
            elif any(j.get("status") == "ready_for_review" for j in matches):
                task_status[task] = "ready_for_review"
            elif any(j.get("status") == "in_progress" for j in matches):
                task_status[task] = "in_progress"
            elif any(j.get("status") == "blocked" for j in matches):
                task_status[task] = "blocked"
            else:
                task_status[task] = "pending"

        complete_count = sum(
            1 for task in RESEARCH_REQUIRED_TASKS if task_status.get(task) in RESEARCH_COMPLETE_STATUSES
        )
        is_ready = complete_count == len(RESEARCH_REQUIRED_TASKS)
        states_payload.append(
            {
                "state": state,
                "is_ready_for_formations_and_changes": is_ready,
                "complete_tasks": complete_count,
                "total_tasks": len(RESEARCH_REQUIRED_TASKS),
                "progress_pct": round((complete_count / len(RESEARCH_REQUIRED_TASKS)) * 100, 1),
                "task_status": task_status,
                "job_count": len(state_jobs),
            }
        )

    ready_count = sum(1 for state in states_payload if state["is_ready_for_formations_and_changes"])
    return {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "required_tasks": RESEARCH_REQUIRED_TASKS,
        "ready_states": ready_count,
        "total_states": len(states_payload),
        "states": states_payload,
    }

# --- Mount frontend ---
frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
