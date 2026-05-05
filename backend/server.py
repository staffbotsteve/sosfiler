"""
SOSFiler — FastAPI Backend Server
Production-grade LLC formation platform.
"""

import os
import jwt
import urllib.request
import json
import json
import uuid
import sqlite3
import hashlib
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import re as _re

import stripe
from fastapi import FastAPI, HTTPException, Request, Header, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, EmailStr, Field, field_validator
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

# --- Configuration ---
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

stripe.api_key = STRIPE_SECRET_KEY

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"
DOCS_DIR.mkdir(exist_ok=True)

PLATFORM_FEE = 4900  # $49.00 in cents
DBA_PLATFORM_FEE = 2900  # $29.00 in cents
LICENSE_PLATFORM_FEE = 4900  # $49.00 in cents
SPECIALTY_LICENSE_FEE = 9900  # $99.00 in cents
RA_RENEWAL_FEE = 4900  # $49/yr
ANNUAL_REPORT_FEE = 2500  # $25/yr

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

        CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email);
        CREATE INDEX IF NOT EXISTS idx_orders_token ON orders(token);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
        CREATE INDEX IF NOT EXISTS idx_status_updates_order ON status_updates(order_id);
        CREATE INDEX IF NOT EXISTS idx_documents_order ON documents(order_id);
        CREATE INDEX IF NOT EXISTS idx_filing_jobs_order ON filing_jobs(order_id);
        CREATE INDEX IF NOT EXISTS idx_filing_jobs_status ON filing_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_filing_events_order ON filing_events(order_id);
        CREATE INDEX IF NOT EXISTS idx_filing_artifacts_order ON filing_artifacts(order_id);
        CREATE INDEX IF NOT EXISTS idx_compliance_order ON compliance_deadlines(order_id);
        CREATE INDEX IF NOT EXISTS idx_license_filings_email ON license_filings(email);
    """)
    _ensure_column(conn, "orders", "gov_processing_fee_cents", "INTEGER NOT NULL DEFAULT 0")
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
    with open(DATA_DIR / "filing_actions.json") as f:
        FILING_ACTIONS = json.load(f)
except Exception:
    FILING_ACTIONS = {}

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

class NameCheckRequest(BaseModel):
    state: str
    name: str
    entity_type: str = "LLC"

class ChatRequest(BaseModel):
    message: str
    session_id: str
    context: Optional[dict] = None

class FilingEvidenceRequest(BaseModel):
    artifact_type: str = Field(..., pattern="^(submitted_receipt|submitted_document|approved_certificate|state_correspondence|other)$")
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


# --- Auth helpers ---
def parse_json_field(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def verify_admin_access(request: Request):
    admin_token = os.getenv("ADMIN_TOKEN")
    if not admin_token:
        raise HTTPException(status_code=503, detail="Admin filing API is not configured")
    provided = request.headers.get("x-admin-token", "")
    if not secrets.compare_digest(provided, admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token")


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
        responsible["ssn_itin"] = ssn
        responsible["ssn_last4"] = ssn[-4:]
        responsible["is_responsible_party"] = True
        formation_data["responsible_party_ssn"] = ssn
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
            queue["ss4_data"]["responsible_party"]["ssn"] = ssn
            queue["status"] = "ready_for_submission"
            queue["ssn_received_at"] = datetime.utcnow().isoformat()
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
    conn.execute(
        "INSERT INTO documents (order_id, doc_type, filename, file_path, format) VALUES (?, ?, ?, ?, ?)",
        (order_id, doc_type, filename, file_path, file_format),
    )

def get_filing_action(state: str, entity_type: str, action_type: str = "formation") -> Optional[dict]:
    state = state.upper()
    entity_type = "LLC" if entity_type == "LLC" else entity_type
    return (
        FILING_ACTIONS.get(state, {})
        .get(entity_type, {})
        .get(action_type)
    )

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
    state_fee_cents = int(STATE_FEES[entity_key][state]["filing_fee"] * 100)
    action = get_filing_action(state, entity_type)
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
    }

def create_or_update_filing_job(order: dict, action_type: str = "formation", status: Optional[str] = None) -> dict:
    action = get_filing_action(order["state"], order["entity_type"], action_type) or {}
    processing_fee_cents = int(order.get("gov_processing_fee_cents") or 0)
    if not processing_fee_cents:
        processing_fee_cents = calculate_processing_fee_cents(action, int(order.get("state_fee_cents") or 0))
    job_id = f"FIL-{order['id']}-{action_type}".replace("_", "-")
    job_status = status or order.get("status", "pending_payment")
    required_evidence = {
        "submitted": action.get("required_evidence_for_submitted", []),
        "approved": action.get("required_evidence_for_approved", []),
    }
    conn = get_db()
    conn.execute("""
        INSERT INTO filing_jobs (
            id, order_id, action_type, state, entity_type, status, automation_level,
            filing_method, office, form_name, portal_name, portal_url, state_fee_cents,
            processing_fee_cents, total_government_cents, required_consents,
            required_evidence, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
            updated_at = datetime('now')
    """, (
        job_id, order["id"], action_type, order["state"], order["entity_type"], job_status,
        action.get("automation_level", "manual_review"),
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
    ))
    row = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    conn.commit()
    conn.close()
    return dict(row)

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
            "INSERT INTO documents (order_id, doc_type, filename, file_path, format) VALUES (?, ?, ?, ?, ?)",
            (order_id, doc["type"], doc["filename"], doc["path"], doc["format"])
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
        from notifier import Notifier, DASHBOARD_URL
        notifier = Notifier()
        if reset_token:
            reset_url = f"{DASHBOARD_URL}?reset_token={reset_token}"
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
        "filing_action": get_filing_action(state, et)
    }

# --- TASK 2: Filing Expert Chat API ---
@app.post("/api/chat")
async def chat_expert(data: ChatRequest, request: Request):
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API Key not configured")

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

    # Context injection from state data
    state_context = ""
    if data.context and data.context.get("state") and data.context.get("entity_type"):
        state_code = data.context["state"].upper()
        entity_type = data.context["entity_type"].lower()
        if entity_type in STATE_DATA:
            state_info = _find_state(STATE_DATA[entity_type], state_code)
            if state_info:
                state_context = f"\nRelevant state data for {state_code} {entity_type.upper()}:\n{json.dumps(state_info, default=str)}"

    system_prompt = (
        "You are SOSFiler's Filing Expert. You ONLY answer questions about business formation, "
        "LLC/Corp/Nonprofit filing, state requirements, compliance, entity selection, registered agents, "
        "EIN applications, and business licenses. "
        "If asked about ANYTHING else (sports, weather, coding, general knowledge), respond: "
        "'I can only help with business formation and filing questions. What would you like to know about forming your business?' "
        "Always end important answers with: 'Note: SOSFiler is a document preparation service, not a law firm. This isn't legal advice.' "
        "Keep responses concise and helpful."
    )
    
    if state_context:
        system_prompt += state_context

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
            (message_count, json.dumps(history), session_id)
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
    order_id = f"IL-{uuid.uuid4().hex[:12].upper()}"
    token = secrets.token_urlsafe(32)
    
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
        data.business_name, data.model_dump_json(), state_fee_cents, gov_processing_fee_cents,
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
    
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        order_id = session.get("metadata", {}).get("order_id")
        
        if order_id:
            conn = get_db()
            conn.execute("""
                UPDATE orders SET status = 'paid', paid_at = datetime('now'),
                stripe_payment_intent = ?, updated_at = datetime('now') WHERE id = ?
            """, (session.get("payment_intent"), order_id))
            conn.commit()
            conn.close()
            
            add_status_update(order_id, "paid", "Payment received. Starting formation process.")
            
            # Trigger formation pipeline in background
            background_tasks.add_task(run_formation_pipeline, order_id)
    
    return {"status": "ok"}

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
            f"{job.get('form_name', 'State filing')} is ready for operator submission through {job.get('portal_name') or job.get('portal_url')}.",
        )
        add_status_update(
            order_id,
            "ready_to_file",
            f"Internal documents are ready. State filing is queued for verified submission to {order['state']}."
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
        "SELECT * FROM filing_jobs WHERE order_id = ? AND action_type = 'formation' ORDER BY created_at DESC LIMIT 1",
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
        "SELECT doc_type, filename, format, created_at FROM documents WHERE order_id = ? ORDER BY created_at ASC",
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
        "timeline": [dict(u) for u in updates],
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
        "documents": [dict(d) for d in docs],
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
        item = dict(row)
        item["required_consents"] = parse_json_field(item.get("required_consents"), [])
        item["required_evidence"] = parse_json_field(item.get("required_evidence"), {})
        jobs.append(item)
    return {"jobs": jobs}


@app.post("/api/admin/filing-jobs/{job_id}/evidence")
async def add_filing_evidence(job_id: str, evidence: FilingEvidenceRequest, request: Request):
    """Attach filing evidence or state correspondence to a filing job."""
    verify_admin_access(request)
    conn = get_db()
    job = conn.execute("SELECT * FROM filing_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        conn.close()
        raise HTTPException(status_code=404, detail="Filing job not found")
    is_evidence = 1 if evidence.artifact_type in {"submitted_receipt", "approved_certificate"} else 0
    conn.execute("""
        INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (job_id, job["order_id"], evidence.artifact_type, evidence.filename, evidence.file_path, is_evidence))
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
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/api/admin/filing-jobs/{job_id}/mark-submitted")
async def mark_filing_submitted(job_id: str, evidence: FilingEvidenceRequest, request: Request):
    """Mark a filing submitted only when submission evidence is captured."""
    verify_admin_access(request)
    if evidence.artifact_type != "submitted_receipt":
        raise HTTPException(status_code=400, detail="submitted_receipt evidence is required")
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
    add_customer_document_if_missing(
        conn,
        job["order_id"],
        evidence.artifact_type,
        evidence.filename,
        evidence.file_path,
    )
    conn.execute("""
        UPDATE filing_jobs
        SET status = 'submitted_to_state', submitted_at = datetime('now'), evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (evidence.message or evidence.filename, job_id))
    conn.execute("""
        UPDATE orders SET status = 'submitted_to_state', filed_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
    """, (job["order_id"],))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'submitted_to_state', ?)",
        (job["order_id"], evidence.message or f"Submitted to {job['state']} with receipt evidence on file.")
    )
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, 'submitted_to_state', ?, 'operator', ?)
    """, (job_id, job["order_id"], evidence.message or "State filing submitted with evidence.", evidence.file_path))
    conn.commit()
    conn.close()

    from notifier import Notifier
    notifier = Notifier()
    if evidence.notify_customer:
        await notifier.send_filing_submitted(dict(order), json.loads(order["formation_data"]), evidence.file_path)
    else:
        await notifier.send_admin_filing_submitted(dict(order), dict(job), evidence.file_path, evidence.message)
    return {"status": "submitted_to_state", "customer_notified": evidence.notify_customer}


@app.post("/api/admin/filing-jobs/{job_id}/mark-approved")
async def mark_filing_approved(job_id: str, evidence: FilingEvidenceRequest, request: Request):
    """Mark a state filing approved only when approval evidence is captured."""
    verify_admin_access(request)
    if evidence.artifact_type != "approved_certificate":
        raise HTTPException(status_code=400, detail="approved_certificate evidence is required")
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
    add_customer_document_if_missing(
        conn,
        job["order_id"],
        evidence.artifact_type,
        evidence.filename,
        evidence.file_path,
    )
    conn.execute("""
        UPDATE filing_jobs
        SET status = 'state_approved', approved_at = datetime('now'), evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
    """, (evidence.message or evidence.filename, job_id))
    conn.execute("""
        UPDATE orders SET status = 'state_approved', approved_at = datetime('now'), updated_at = datetime('now')
        WHERE id = ?
    """, (job["order_id"],))
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'state_approved', ?)",
        (job["order_id"], evidence.message or f"{job['state']} approved the filing. Approval evidence is on file.")
    )
    conn.execute("""
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, 'state_approved', ?, 'operator', ?)
    """, (job_id, job["order_id"], evidence.message or "State filing approved with evidence.", evidence.file_path))
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
    return {"status": "state_approved", "customer_notified": evidence.notify_customer}

@app.get("/api/documents/{order_id}")
async def get_documents(order_id: str, token: str = ""):
    """List all generated documents for an order."""
    verify_order_access(order_id, token)
    
    conn = get_db()
    docs = conn.execute(
        "SELECT doc_type, filename, format, created_at FROM documents WHERE order_id = ? ORDER BY created_at",
        (order_id,)
    ).fetchall()
    conn.close()
    
    return {"order_id": order_id, "documents": [dict(d) for d in docs]}

@app.get("/api/documents/{order_id}/download/{filename}")
async def download_document(order_id: str, filename: str, token: str = ""):
    """Download a specific document."""
    verify_order_access(order_id, token)
    
    conn = get_db()
    doc = conn.execute(
        "SELECT file_path, filename FROM documents WHERE order_id = ? AND filename = ?",
        (order_id, filename)
    ).fetchone()
    conn.close()
    
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    file_path = Path(doc["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")
    
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
        "SELECT doc_type, filename, format, created_at FROM documents WHERE order_id = ? ORDER BY created_at",
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
    payload["documents"] = [dict(d) for d in docs]
    payload["timeline"] = [dict(u) for u in updates]
    payload["compliance_deadlines"] = [dict(d) for d in deadlines]
    payload["filing_events"] = [dict(e) for e in filing_events]
    payload["filing_artifacts"] = [dict(a) for a in filing_artifacts]
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
        SELECT d.file_path, d.filename
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

    file_path = Path(doc["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

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

# --- Mount frontend ---
frontend_dir = BASE_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
