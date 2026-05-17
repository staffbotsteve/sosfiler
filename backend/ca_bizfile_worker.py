"""
California BizFile worker.

This worker is designed for a trusted SOSFiler browser profile, not a generic
cloud scrape. It can run unattended once the profile/session/payment method are
valid. If California presents an access checkpoint, the worker records that as a
blocked machine/session condition instead of pretending it can bypass the state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"
RUN_DIR = BASE_DIR / "data" / "filing_listener_runs" / "ca_bizfile"
PROTOCOL_MANIFEST_PATH = BASE_DIR / "data" / "portal_maps" / "ca_bizfile_protocol_manifest.json"

BIZFILE_HOME = "https://bizfileonline.sos.ca.gov/"
BIZFILE_FORMS = "https://bizfileonline.sos.ca.gov/forms/business"
BIZFILE_SEARCH = "https://bizfileonline.sos.ca.gov/search/business"

ACTIVE_STATUS_SET = {
    "submitted_to_state",
    "submitted",
    "awaiting_state",
    "pending_state_review",
    "received_needs_sosdirect_check",
}

SUBMISSION_READY_STATUS_SET = {
    "ready_to_file",
    "automation_started",
}

APPROVED_WORDS = (
    "approved",
    "filed",
    "active",
    "completed",
    "accepted",
)

PENDING_WORDS = (
    "pending review",
    "pending",
    "in review",
    "submitted",
    "received",
)

REJECTED_WORDS = (
    "needs correction",
    "rejected",
    "returned",
    "denied",
    "not filed",
    "deficient",
)

RAW_CARD_ENV_KEYS = (
    "CA_BIZFILE_CARD_NUMBER",
    "CA_BIZFILE_CARD_CVV",
    "CA_BIZFILE_CARD_EXPIRATION",
    "CA_BIZFILE_CARD_EXP_MONTH",
    "CA_BIZFILE_CARD_EXP_YEAR",
)

SENSITIVE_QUERY_KEYS = {
    "access_token",
    "auth",
    "authorization",
    "client_secret",
    "code",
    "id_token",
    "password",
    "passcode",
    "refresh_token",
    "session",
    "state",
    "token",
}

PROTOCOL_CAPTURE_HOSTS = (
    "bizfileonline.sos.ca.gov",
    "login.sos.ca.gov",
    "accounts.google.com",
    "okta.com",
)


class BizFileBlocked(RuntimeError):
    """Raised when the portal requires a checkpoint the worker must not bypass."""

    def __init__(self, code: str, message: str, evidence_path: str = ""):
        super().__init__(message)
        self.code = code
        self.evidence_path = evidence_path


class PaymentNotReady(RuntimeError):
    pass


@dataclass
class FilingPayload:
    business_name: str
    principal_address: str
    principal_city: str
    principal_state: str
    principal_zip: str
    mailing_address: str
    mailing_city: str
    mailing_state: str
    mailing_zip: str
    ra_first_name: str
    ra_last_name: str
    ra_address: str
    ra_city: str
    ra_zip: str
    organizer_name: str
    submitter_name: str
    submitter_email: str
    management_type: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def parse_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def safe_name(value: str, default: str = "bizfile") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:140] or default


def redact_url(value: str) -> str:
    """Keep endpoint structure while removing OAuth/session-shaped query data."""
    try:
        parsed = urlsplit(value)
    except Exception:
        return ""
    query = []
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            query.append((key, "REDACTED"))
        else:
            query.append((key, item_value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def should_capture_protocol_url(value: str, resource_type: str = "") -> bool:
    lowered = (value or "").lower()
    resource = (resource_type or "").lower()
    if not any(host in lowered for host in PROTOCOL_CAPTURE_HOSTS):
        return False
    return (
        "/api/" in lowered
        or "_incapsula_resource" in lowered
        or "oauth" in lowered
        or "okta" in lowered
        or "login" in lowered
        or resource in {"document", "fetch", "xhr"}
    )


def classify_protocol_endpoint(method: str, url: str, resource_type: str = "", content_type: str = "") -> str:
    lowered = (url or "").lower()
    resource = (resource_type or "").lower()
    content = (content_type or "").lower()
    if "_incapsula_resource" in lowered:
        return "access_challenge"
    if "oauth" in lowered or "okta" in lowered or "login" in lowered or "accounts.google.com" in lowered:
        return "identity_provider"
    if "/api/report/getimagebynum" in lowered:
        return "document_image_endpoint"
    if "/api/" in lowered and "report" in lowered:
        return "report_api_candidate"
    if "/api/" in lowered:
        return "portal_api_candidate"
    if "search/business" in lowered:
        return "business_search_page"
    if "forms/business" in lowered:
        return "business_forms_page"
    if resource in {"xhr", "fetch"}:
        return "client_api_candidate"
    if "pdf" in content:
        return "document_download"
    return "page_or_asset"


def build_protocol_manifest(observations: list[dict[str, Any]], *, business_name: str = "") -> dict[str, Any]:
    endpoints: dict[tuple[str, str], dict[str, Any]] = {}
    for observation in observations:
        key = (observation.get("method", "GET"), observation.get("url", ""))
        if not key[1]:
            continue
        existing = endpoints.setdefault(
            key,
            {
                "method": key[0],
                "url": key[1],
                "kind": observation.get("kind", "unknown"),
                "resource_type": observation.get("resource_type", ""),
                "content_types": [],
                "statuses": [],
                "count": 0,
            },
        )
        existing["count"] += 1
        status = observation.get("status")
        if status is not None and status not in existing["statuses"]:
            existing["statuses"].append(status)
        content_type = observation.get("content_type") or ""
        if content_type and content_type not in existing["content_types"]:
            existing["content_types"].append(content_type)
        if observation.get("kind") != "page_or_asset":
            existing["kind"] = observation.get("kind")

    return {
        "version": 1,
        "generated_at": utc_now(),
        "state": "CA",
        "portal": BIZFILE_HOME,
        "purpose": "Identify official BizFile request endpoints that can replace page-by-page Playwright for status and document collection.",
        "business_name_probe": business_name,
        "redaction": "OAuth/session/token-shaped query values are replaced with REDACTED. Request bodies, cookies, and headers are not persisted.",
        "recommended_next_step": "Promote stable portal_api_candidate/document_image_endpoint calls into a protocol status adapter only after comparing the manifest against live approval evidence.",
        "endpoint_count": len(endpoints),
        "endpoints": sorted(endpoints.values(), key=lambda item: (item["kind"], item["url"])),
    }


def write_protocol_manifest(manifest: dict[str, Any], path: Path = PROTOCOL_MANIFEST_PATH) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return str(path)


def split_person_name(value: str) -> tuple[str, str]:
    parts = [part for part in re.split(r"\s+", (value or "").strip()) if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def formation_value(formation: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = formation.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def filing_payload_from_order(order: sqlite3.Row) -> FilingPayload:
    formation = parse_json(order["formation_data"], {})
    business_name = formation_value(formation, "business_name", default=order["business_name"])
    principal_address = formation_value(formation, "principal_address")
    principal_city = formation_value(formation, "principal_city")
    principal_state = formation_value(formation, "principal_state", default="CA")
    principal_zip = formation_value(formation, "principal_zip")
    mailing_address = formation_value(formation, "mailing_address", default=principal_address)
    mailing_city = formation_value(formation, "mailing_city", default=principal_city)
    mailing_state = formation_value(formation, "mailing_state", default=principal_state)
    mailing_zip = formation_value(formation, "mailing_zip", default=principal_zip)

    ra_name = (
        os.environ.get("CA_BIZFILE_RA_NAME")
        or formation_value(formation, "ra_name")
        or formation_value(formation, "registered_agent_name")
    )
    ra_first, ra_last = split_person_name(ra_name)
    submitter = os.environ.get("CA_BIZFILE_SUBMITTER_NAME") or os.environ.get("CA_BIZFILE_ORGANIZER_NAME") or "Steven Swan"
    email = os.environ.get("CA_BIZFILE_EMAIL") or "admin@sosfiler.com"
    return FilingPayload(
        business_name=business_name,
        principal_address=principal_address,
        principal_city=principal_city,
        principal_state=principal_state,
        principal_zip=principal_zip,
        mailing_address=mailing_address,
        mailing_city=mailing_city,
        mailing_state=mailing_state,
        mailing_zip=mailing_zip,
        ra_first_name=ra_first,
        ra_last_name=ra_last,
        ra_address=os.environ.get("CA_BIZFILE_RA_ADDRESS") or formation_value(formation, "ra_address", "registered_agent_address"),
        ra_city=os.environ.get("CA_BIZFILE_RA_CITY") or formation_value(formation, "ra_city", "registered_agent_city"),
        ra_zip=os.environ.get("CA_BIZFILE_RA_ZIP") or formation_value(formation, "ra_zip", "registered_agent_zip"),
        organizer_name=os.environ.get("CA_BIZFILE_ORGANIZER_NAME") or submitter,
        submitter_name=submitter,
        submitter_email=email,
        management_type=formation_value(formation, "management_type", default="member-managed"),
    )


def missing_payload_fields(payload: FilingPayload) -> list[str]:
    required = (
        "business_name",
        "principal_address",
        "principal_city",
        "principal_state",
        "principal_zip",
        "ra_first_name",
        "ra_last_name",
        "ra_address",
        "ra_city",
        "ra_zip",
        "submitter_name",
        "submitter_email",
    )
    return [field for field in required if not getattr(payload, field)]


def payment_configuration() -> dict[str, Any]:
    raw_keys = [key for key in RAW_CARD_ENV_KEYS if os.environ.get(key)]
    if raw_keys:
        return {
            "ready": False,
            "mode": "blocked_raw_card_env",
            "message": "Raw card number/CVV environment variables are not supported. Use a saved portal method or PCI vault token integration.",
            "raw_card_env_keys": raw_keys,
        }
    mode = (os.environ.get("CA_BIZFILE_PAYMENT_MODE") or "").strip().lower()
    if mode == "saved_portal_method":
        return {
            "ready": True,
            "mode": mode,
            "message": "Worker may submit payment only if BizFile exposes a saved portal payment method.",
        }
    return {
        "ready": False,
        "mode": mode or "not_configured",
        "message": "Set CA_BIZFILE_PAYMENT_MODE=saved_portal_method after the portal account has a compliant saved payment method.",
    }


def classify_ca_status(text: str) -> str:
    lowered = re.sub(r"\s+", " ", (text or "").lower())
    if any(word in lowered for word in REJECTED_WORDS):
        return "rejected_or_needs_correction"
    if any(word in lowered for word in APPROVED_WORDS):
        return "approved"
    if any(word in lowered for word in PENDING_WORDS):
        return "pending_state_review"
    return "unknown"


def snippet_for_business(page_text: str, business_name: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (page_text or "").splitlines()]
    business_lower = business_name.lower()
    for index, line in enumerate(lines):
        if business_lower and business_lower in line.lower():
            window = lines[max(0, index - 4): index + 8]
            return " | ".join(item for item in window if item)
    return re.sub(r"\s+", " ", page_text or "").strip()[:1200]


def event_exists(conn: sqlite3.Connection, job_id: str, event_type: str, message: str) -> bool:
    return conn.execute(
        """
        SELECT 1 FROM filing_events
        WHERE filing_job_id = ? AND event_type = ? AND message = ?
        LIMIT 1
        """,
        (job_id, event_type, message),
    ).fetchone() is not None


def insert_event(
    conn: sqlite3.Connection,
    job_id: str,
    order_id: str,
    event_type: str,
    message: str,
    evidence_path: str = "",
    actor: str = "ca_bizfile_worker",
) -> None:
    if event_exists(conn, job_id, event_type, message):
        return
    conn.execute(
        """
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, order_id, event_type, message, actor, evidence_path),
    )


def add_document(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    artifact_type: str,
    filename: str,
    file_path: str,
) -> None:
    existing_artifact = conn.execute(
        "SELECT 1 FROM filing_artifacts WHERE order_id = ? AND filename = ? LIMIT 1",
        (job["order_id"], filename),
    ).fetchone()
    if not existing_artifact:
        # Plan v2.6 §4.2.2: every evidence-bearing artifact carries sha256_hex
        # at write time. Workers operate on absolute paths under their own
        # receipts directory; hash directly without server-side path resolution.
        from execution_platform import insert_filing_artifact_row, sha256_for_file_path
        sha256_hex = sha256_for_file_path(file_path)
        insert_filing_artifact_row(
            conn,
            filing_job_id=job["id"],
            order_id=job["order_id"],
            artifact_type=artifact_type,
            filename=filename,
            file_path=file_path,
            is_evidence=True,
            sha256_hex=sha256_hex,
        )
    existing_doc = conn.execute(
        "SELECT 1 FROM documents WHERE order_id = ? AND filename = ? LIMIT 1",
        (job["order_id"], filename),
    ).fetchone()
    if not existing_doc:
        fmt = Path(filename).suffix.lstrip(".").lower() or "file"
        conn.execute(
            """
            INSERT INTO documents (order_id, doc_type, filename, file_path, format)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job["order_id"], artifact_type, filename, file_path, fmt),
        )


def mark_pending(conn: sqlite3.Connection, job: sqlite3.Row, message: str, evidence_path: str = "") -> None:
    conn.execute(
        """
        UPDATE filing_jobs
        SET status = 'pending_state_review', evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (message, job["id"]),
    )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'pending_state_review', ?)",
        (job["order_id"], message),
    )
    insert_event(conn, job["id"], job["order_id"], "ca_bizfile_status", message, evidence_path)


def mark_rejected(conn: sqlite3.Connection, job: sqlite3.Row, message: str, evidence_path: str = "") -> None:
    conn.execute(
        """
        UPDATE filing_jobs
        SET status = 'operator_review', evidence_summary = ?, updated_at = datetime('now')
        WHERE id = ?
        """,
        (message, job["id"]),
    )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'operator_review', ?)",
        (job["order_id"], message),
    )
    if evidence_path:
        add_document(conn, job, "rejection_notice", Path(evidence_path).name, evidence_path)
    insert_event(conn, job["id"], job["order_id"], "ca_bizfile_rejected_or_needs_correction", message, evidence_path)


def mark_approved(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    message: str,
    evidence_path: str,
    filing_confirmation: str | None = None,
) -> None:
    add_document(conn, job, "approved_certificate", Path(evidence_path).name, evidence_path)
    _persist_filing_confirmation(conn, job["order_id"], filing_confirmation, "adapter")
    conn.execute(
        """
        UPDATE filing_jobs
        SET status = 'state_approved',
            approved_at = COALESCE(approved_at, datetime('now')),
            evidence_summary = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (message, job["id"]),
    )
    conn.execute(
        """
        UPDATE orders
        SET status = 'state_approved',
            approved_at = COALESCE(approved_at, datetime('now')),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (job["order_id"],),
    )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'state_approved', ?)",
        (job["order_id"], message),
    )
    insert_event(conn, job["id"], job["order_id"], "state_approved", message, evidence_path)


def record_access_block(conn: sqlite3.Connection, job: sqlite3.Row, blocked: BizFileBlocked) -> None:
    message = f"California BizFile worker blocked: {blocked.code}. {blocked}"
    if job["status"] in ACTIVE_STATUS_SET:
        conn.execute(
            """
            UPDATE filing_jobs
            SET evidence_summary = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (message, job["id"]),
        )
    else:
        conn.execute(
            """
            UPDATE filing_jobs
            SET status = 'operator_required', evidence_summary = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (message, job["id"]),
        )
    insert_event(conn, job["id"], job["order_id"], "ca_bizfile_worker_blocked", message, blocked.evidence_path)


def active_ca_jobs(conn: sqlite3.Connection, operation: str, order_id: str = "", limit: int = 25) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    required = ("filing_jobs", "orders", "filing_events", "filing_artifacts", "documents", "status_updates")
    missing = [table for table in required if not table_exists(conn, table)]
    if missing:
        raise RuntimeError(f"database_not_initialized: missing {', '.join(missing)}")
    statuses = ACTIVE_STATUS_SET if operation == "status" else SUBMISSION_READY_STATUS_SET
    params: list[Any] = ["CA", *sorted(statuses)]
    where = f"j.state = ? AND j.status IN ({','.join('?' for _ in statuses)})"
    if order_id:
        where += " AND j.order_id = ?"
        params.append(order_id)
    rows = conn.execute(
        f"""
        SELECT
            j.*,
            o.business_name,
            o.email,
            o.token,
            o.status AS order_status,
            o.formation_data
        FROM filing_jobs j
        JOIN orders o ON o.id = j.order_id
        WHERE {where}
        ORDER BY COALESCE(j.submitted_at, j.updated_at, o.created_at) ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [(row, row) for row in rows]


def run_path(order_id: str, name: str, suffix: str) -> Path:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = RUN_DIR / safe_name(order_id) / f"{stamp}-{safe_name(name)}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


async def human_fill(locator, value: str) -> None:
    await locator.scroll_into_view_if_needed(timeout=10_000)
    await locator.click()
    await locator.press("Control+A")
    await locator.press("Meta+A")
    await locator.press("Backspace")
    await locator.type(value or "", delay=10)


async def checkpoint_snapshot(page, order_id: str, code: str) -> str:
    target = run_path(order_id, code, ".png")
    await page.screenshot(path=str(target), full_page=True)
    return str(target)


async def assert_not_blocked(page, order_id: str) -> None:
    url = page.url.lower()
    body = ""
    try:
        body = await page.locator("body").inner_text(timeout=3_000)
    except Exception:
        pass
    lowered = body.lower()
    if "request unsuccessful" in lowered or "incapsula" in lowered or "_incapsula_resource" in lowered:
        evidence = await checkpoint_snapshot(page, order_id, "trusted-access-checkpoint")
        raise BizFileBlocked("trusted_access_checkpoint", "BizFile returned an access-control challenge.", evidence)
    if "accounts.google.com" in url and "redirect_uri_mismatch" in url:
        evidence = await checkpoint_snapshot(page, order_id, "google-oauth-misconfigured")
        raise BizFileBlocked("oauth_redirect_uri_mismatch", "Google rejected the OAuth redirect URI.", evidence)
    if any(term in lowered for term in ("verify it's you", "enter a verification code", "multi-factor", "authenticator")):
        evidence = await checkpoint_snapshot(page, order_id, "mfa-checkpoint")
        raise BizFileBlocked("mfa_or_identity_checkpoint", "BizFile/identity provider requires a verification checkpoint.", evidence)


async def login_if_needed(page, order_id: str) -> None:
    email = os.environ.get("CA_BIZFILE_EMAIL") or "admin@sosfiler.com"
    password = os.environ.get("CA_BIZFILE_PASSWORD") or ""
    await assert_not_blocked(page, order_id)
    if await page.locator("input[name='identifier']").count():
        if not password:
            evidence = await checkpoint_snapshot(page, order_id, "login-required")
            raise BizFileBlocked("credentials_missing", "CA_BIZFILE_PASSWORD is not configured for unattended login.", evidence)
        await human_fill(page.locator("input[name='identifier']").first, email)
        await human_fill(page.locator("input[name='credentials.passcode']").first, password)
        await page.locator("input[type='submit'], button[type='submit']").first.click()
        await page.wait_for_load_state("domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(4_000)
    await assert_not_blocked(page, order_id)


async def open_work_queue(page, order_id: str) -> None:
    await page.goto(BIZFILE_HOME, wait_until="domcontentloaded", timeout=60_000)
    await login_if_needed(page, order_id)
    candidates = [
        page.get_by_text("My Work Queue", exact=True),
        page.get_by_role("link", name=re.compile("My Work Queue", re.I)),
        page.get_by_role("button", name=re.compile("My Work Queue", re.I)),
    ]
    for locator in candidates:
        try:
            if await locator.count():
                await locator.first.click(timeout=10_000)
                await page.wait_for_load_state("domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(4_000)
                await assert_not_blocked(page, order_id)
                return
        except Exception:
            continue
    # Some BizFile builds keep the queue link client-side; keep a screenshot if
    # this stops working so the portal map can be adjusted from evidence.
    evidence = await checkpoint_snapshot(page, order_id, "work-queue-link-not-found")
    raise BizFileBlocked("work_queue_link_not_found", "Could not find the BizFile My Work Queue link in the signed-in portal.", evidence)


async def public_business_search(page, order_id: str, business_name: str) -> tuple[str, str]:
    await page.goto(BIZFILE_SEARCH, wait_until="domcontentloaded", timeout=60_000)
    await assert_not_blocked(page, order_id)
    inputs = page.locator("input:visible")
    if await inputs.count():
        await human_fill(inputs.first, business_name)
        search_buttons = [
            page.get_by_role("button", name=re.compile("search", re.I)),
            page.locator("button[type='submit']:visible"),
            page.locator("button:visible").first,
        ]
        for button in search_buttons:
            try:
                if await button.count():
                    await button.first.click(timeout=10_000)
                    break
            except Exception:
                continue
        await page.wait_for_timeout(6_000)
    text = await page.locator("body").inner_text(timeout=10_000)
    screenshot = run_path(order_id, "public-search", ".png")
    await page.screenshot(path=str(screenshot), full_page=True)
    return text, str(screenshot)


async def candidate_document_links(page, business_name: str) -> list[tuple[str, str]]:
    links = []
    for link in await page.locator("a").all():
        try:
            href = await link.get_attribute("href") or ""
            text = re.sub(r"\s+", " ", await link.inner_text(timeout=1_000)).strip()
        except Exception:
            continue
        haystack = f"{href} {text}".lower()
        if any(word in haystack for word in ("download", "certificate", "filing", "approved", "pdf", "document", "view")):
            links.append((href, text))
    business_lower = business_name.lower()
    return [item for item in links if business_lower in f"{item[0]} {item[1]}".lower()] or links


async def try_download_document(page, order_id: str, business_name: str) -> str:
    target_dir = DOCS_DIR / order_id / "state_filings"
    target_dir.mkdir(parents=True, exist_ok=True)
    for index, (href, text) in enumerate(await candidate_document_links(page, business_name), start=1):
        abs_url = urljoin(page.url, href)
        suffix = Path(abs_url).suffix
        if suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            suffix = ".pdf"
        target = target_dir / f"ca-bizfile-{safe_name(text or business_name)}-{index}{suffix}"
        try:
            response = await page.context.request.get(abs_url, timeout=45_000)
            content_type = (response.headers.get("content-type") or "").lower()
            if response.ok and any(kind in content_type for kind in ("pdf", "image", "octet-stream")):
                target.write_bytes(await response.body())
                return str(target)
        except Exception:
            pass
        try:
            async with page.expect_download(timeout=8_000) as download_info:
                await page.locator(f'a[href="{href}"]').first.click(timeout=8_000)
            download = await download_info.value
            filename = safe_name(download.suggested_filename or target.name)
            saved = target_dir / filename
            await download.save_as(saved)
            return str(saved)
        except Exception:
            continue
    return ""


async def observe_protocol_requests(page, observations: list[dict[str, Any]]) -> None:
    def on_response(response) -> None:
        request = response.request
        url = request.url
        resource_type = request.resource_type
        if not should_capture_protocol_url(url, resource_type):
            return
        content_type = response.headers.get("content-type", "")
        observations.append({
            "method": request.method,
            "url": redact_url(url),
            "status": response.status,
            "resource_type": resource_type,
            "content_type": content_type.split(";")[0].strip(),
            "kind": classify_protocol_endpoint(request.method, url, resource_type, content_type),
        })

    page.on("response", on_response)


async def run_protocol_discovery_probe(page, *, business_name: str = "", order_id: str = "CA-PROTOCOL-DISCOVERY") -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    await observe_protocol_requests(page, observations)

    try:
        await page.goto(BIZFILE_HOME, wait_until="domcontentloaded", timeout=60_000)
        await login_if_needed(page, order_id)
        await page.wait_for_timeout(3_000)
    except BizFileBlocked as exc:
        blockers.append({"step": "home", "code": exc.code, "message": str(exc), "evidence_path": exc.evidence_path})
    except Exception as exc:
        blockers.append({"step": "home", "code": "discovery_home_error", "message": f"{type(exc).__name__}: {exc}"})

    try:
        await page.goto(BIZFILE_SEARCH, wait_until="domcontentloaded", timeout=60_000)
        await assert_not_blocked(page, order_id)
        if business_name:
            await public_business_search(page, order_id, business_name)
        else:
            await page.wait_for_timeout(3_000)
    except BizFileBlocked as exc:
        blockers.append({"step": "public_search", "code": exc.code, "message": str(exc), "evidence_path": exc.evidence_path})
    except Exception:
        # Discovery is best-effort. A missing search interaction should not erase
        # the protocol observations already captured from login/bootstrap.
        pass

    try:
        await page.goto(BIZFILE_FORMS, wait_until="domcontentloaded", timeout=60_000)
        await login_if_needed(page, order_id)
        await page.wait_for_timeout(3_000)
    except BizFileBlocked as exc:
        blockers.append({"step": "forms", "code": exc.code, "message": str(exc), "evidence_path": exc.evidence_path})
    except Exception:
        pass

    try:
        await open_work_queue(page, order_id)
    except BizFileBlocked as exc:
        blockers.append({"step": "work_queue", "code": exc.code, "message": str(exc), "evidence_path": exc.evidence_path})
    except Exception:
        pass

    manifest = build_protocol_manifest(observations, business_name=business_name)
    manifest_path = write_protocol_manifest(manifest)
    evidence = await checkpoint_snapshot(page, order_id, "protocol-discovery-final")
    return {
        "order_id": order_id,
        "business_name": business_name,
        "status": "protocol_manifest_captured_with_blockers" if blockers else "protocol_manifest_captured",
        "manifest_path": manifest_path,
        "evidence_path": evidence,
        "endpoint_count": manifest["endpoint_count"],
        "blockers": blockers,
        "candidate_counts": {
            kind: sum(1 for endpoint in manifest["endpoints"] if endpoint["kind"] == kind)
            for kind in sorted({endpoint["kind"] for endpoint in manifest["endpoints"]})
        },
    }


async def check_status_job(page, conn: sqlite3.Connection, job: sqlite3.Row, order: sqlite3.Row, dry_run: bool) -> dict[str, Any]:
    business = order["business_name"]
    await open_work_queue(page, job["order_id"])
    text = await page.locator("body").inner_text(timeout=10_000)
    screenshot = run_path(job["order_id"], "work-queue", ".png")
    await page.screenshot(path=str(screenshot), full_page=True)

    snippet = snippet_for_business(text, business)
    classification = classify_ca_status(snippet)
    if classification == "unknown":
        public_text, public_screenshot = await public_business_search(page, job["order_id"], business)
        public_snippet = snippet_for_business(public_text, business)
        public_classification = classify_ca_status(public_snippet)
        if public_classification != "unknown":
            snippet = public_snippet
            classification = public_classification
            screenshot = Path(public_screenshot)

    message = f"California BizFile status for {business}: {classification}; raw portal text: {snippet[:500]}"
    result = {
        "order_id": job["order_id"],
        "business_name": business,
        "status": classification,
        "message": message,
        "evidence_path": str(screenshot),
    }
    if dry_run:
        return result

    if classification == "pending_state_review":
        mark_pending(conn, job, message, str(screenshot))
    elif classification == "rejected_or_needs_correction":
        mark_rejected(conn, job, message, str(screenshot))
    elif classification == "approved":
        downloaded = await try_download_document(page, job["order_id"], business)
        if downloaded:
            from execution_platform import extract_filing_confirmation
            try:
                approval_text = await page.inner_text("body", timeout=5_000)
            except Exception:
                approval_text = ""
            extracted = extract_filing_confirmation(approval_text, CONFIRMATION_NUMBER_REGEX)
            mark_approved(
                conn,
                job,
                f"California approval evidence captured for {business}.",
                downloaded,
                filing_confirmation=extracted,
            )
            result["filing_confirmation"] = extracted
            result["downloaded_document"] = downloaded
            result["status"] = "state_approved"
        else:
            add_document(conn, job, "state_correspondence", Path(screenshot).name, str(screenshot))
            insert_event(
                conn,
                job["id"],
                job["order_id"],
                "ca_bizfile_approved_needs_document",
                "California appears approved in BizFile, but the worker did not capture the approval certificate yet.",
                str(screenshot),
            )
    else:
        insert_event(conn, job["id"], job["order_id"], "ca_bizfile_status_unknown", message, str(screenshot))
    return result


async def click_next(page, order_id: str) -> None:
    await assert_not_blocked(page, order_id)
    await page.get_by_role("button", name="Next Step").click(timeout=20_000)
    await page.wait_for_timeout(3_000)
    await assert_not_blocked(page, order_id)


async def current_heading(page) -> str:
    try:
        return (await page.locator("h2").first.inner_text(timeout=3_000)).strip()
    except Exception:
        return ""


async def fill_current_step(page, order_id: str, payload: FilingPayload) -> None:
    await assert_not_blocked(page, order_id)
    if await page.locator("input[name='ATTEST_1_YN']:visible").count():
        box = page.locator("input[name='ATTEST_1_YN']:visible").first
        if not await box.is_checked():
            await box.check(force=True)
    if await page.locator("input[name='SUBMITTER_NAME']:visible").count():
        await human_fill(page.locator("input[name='SUBMITTER_NAME']:visible").first, payload.submitter_name)
    if await page.locator("input[name='SUBMITTER_EMAIL']:visible").count():
        await human_fill(page.locator("input[name='SUBMITTER_EMAIL']:visible").first, payload.submitter_email)
    if await page.locator("input[name='reservedYN'][value='N']:visible").count():
        no_reserved = page.locator("input[name='reservedYN'][value='N']:visible").first
        if not await no_reserved.is_checked():
            await no_reserved.check(force=True)
            await page.wait_for_timeout(1_000)
    if await page.locator("#field-field1-undefined:visible").count():
        await human_fill(page.locator("#field-field1-undefined:visible").first, payload.business_name)
    if await page.locator("#field-field2-undefined:visible").count():
        await human_fill(page.locator("#field-field2-undefined:visible").first, payload.business_name)

    if await page.locator("input[aria-label='Principal Address: Address Line 1']:visible").count():
        await human_fill(page.locator("input[aria-label='Principal Address: Address Line 1']:visible").first, payload.principal_address)
        await human_fill(page.locator("input[aria-label='Principal Address: City']:visible").first, payload.principal_city)
        await page.locator("select[aria-label='Principal Address: State']:visible").first.select_option(payload.principal_state)
        await human_fill(page.locator("input[aria-label='Principal Address: ZIP code']:visible").first, payload.principal_zip)
        await page.locator("select[aria-label='Principal Address: country']:visible").first.select_option("United States")
    if await page.locator("input[aria-label='Mailing Address: Address Line 1']:visible").count():
        await human_fill(page.locator("input[aria-label='Mailing Address: Address Line 1']:visible").first, payload.mailing_address)
        await human_fill(page.locator("input[aria-label='Mailing Address: City']:visible").first, payload.mailing_city)
        await page.locator("select[aria-label='Mailing Address: State']:visible").first.select_option(payload.mailing_state)
        await human_fill(page.locator("input[aria-label='Mailing Address: ZIP code']:visible").first, payload.mailing_zip)
        await page.locator("select[aria-label='Mailing Address: country']:visible").first.select_option("United States")

    if await page.locator("input[name='RA_TYPE_ID'][value='460']:visible").count():
        individual_agent = page.locator("input[name='RA_TYPE_ID'][value='460']:visible").first
        if not await individual_agent.is_checked():
            await individual_agent.check(force=True)
            await page.wait_for_timeout(1_000)
    if await page.locator("input[name='FIRST_NAME']:visible").count():
        await human_fill(page.locator("input[name='FIRST_NAME']:visible").first, payload.ra_first_name)
    if await page.locator("input[name='LAST_NAME']:visible").count():
        await human_fill(page.locator("input[name='LAST_NAME']:visible").first, payload.ra_last_name)
    if await page.locator("input[aria-label='Agent Address: Address Line 1']:visible").count():
        await human_fill(page.locator("input[aria-label='Agent Address: Address Line 1']:visible").first, payload.ra_address)
        await human_fill(page.locator("input[aria-label='Agent Address: City']:visible").first, payload.ra_city)
        await human_fill(page.locator("input[aria-label='Agent Address: ZIP code']:visible").first, payload.ra_zip)

    if await page.locator("input[name='MANAGED_BY_TYPE_ID'][value='504']:visible").count():
        if "manager" in payload.management_type.lower() and "member" not in payload.management_type.lower():
            raise BizFileBlocked("unsupported_management_type", "CA BizFile worker currently supports member-managed LLC-1 filings only.")
        member_managed = page.locator("input[name='MANAGED_BY_TYPE_ID'][value='504']:visible").first
        if not await member_managed.is_checked():
            await member_managed.check(force=True)
    if await page.locator("input[name='FILING_DATE_TYPE_ID'][value='1']:visible").count():
        current_date = page.locator("input[name='FILING_DATE_TYPE_ID'][value='1']:visible").first
        if not await current_date.is_checked():
            await current_date.check(force=True)


async def open_llc1_form(page, order_id: str) -> None:
    await page.goto(BIZFILE_FORMS, wait_until="domcontentloaded", timeout=60_000)
    await login_if_needed(page, order_id)
    if await page.get_by_text("Articles of Organization - CA LLC", exact=True).count():
        await page.get_by_text("Articles of Organization - CA LLC", exact=True).first.click(timeout=30_000)
    await page.wait_for_selector("button:has-text('FILE ONLINE')", timeout=30_000)
    await page.get_by_role("button", name="FILE ONLINE").click(timeout=30_000)
    await page.wait_for_timeout(4_000)
    await login_if_needed(page, order_id)
    await page.wait_for_selector("text=Articles of Organization - CA LLC", timeout=60_000)


async def fill_signature_row(page, payload: FilingPayload) -> None:
    one_signature = page.locator("input[name='SIGNATURE_OPTIONS'][value='option1']:visible").first
    if not await one_signature.is_checked():
        await one_signature.check(force=True)
        await page.wait_for_timeout(1_000)
    agree = page.locator("input[name='SIGNATURE_AGREE_YN']:visible").first
    if not await agree.is_checked():
        await agree.check(force=True)
        await page.wait_for_timeout(1_000)
    await page.locator("button.add-row:visible").first.click(timeout=20_000)
    signature = page.locator("input[name='SIGNATURE']:visible").first
    await signature.wait_for(state="visible", timeout=20_000)
    await human_fill(signature, payload.organizer_name)
    today = page.get_by_role("button", name="Enter today's date for: Date")
    if await today.count():
        await today.first.click(timeout=10_000)
    else:
        await human_fill(page.locator("input[placeholder='MM/DD/YYYY']:visible").first, datetime.now().strftime("%m/%d/%Y"))
    await page.wait_for_timeout(1_000)
    await page.get_by_role("button", name="Save", exact=True).click(timeout=20_000)
    await page.wait_for_timeout(2_000)


async def fill_fee_step(page) -> None:
    standard = page.locator("input[name='PROCESSING_TYPE_ID'][value='901']:visible").first
    await standard.wait_for(state="visible", timeout=20_000)
    if not await standard.is_checked():
        await standard.check(force=True)
        await page.wait_for_timeout(1_000)
    certified = page.locator("input[name='AUTO_CERTIFY_YN']:visible").first
    if await certified.count() and await certified.is_checked():
        await certified.uncheck(force=True)
        await page.wait_for_timeout(1_000)


async def fill_to_payment(page, order_id: str, payload: FilingPayload) -> str:
    await open_llc1_form(page, order_id)
    for _ in range(8):
        await fill_current_step(page, order_id, payload)
        await click_next(page, order_id)
    await fill_signature_row(page, payload)
    await click_next(page, order_id)
    await fill_fee_step(page)
    await click_next(page, order_id)
    ready = run_path(order_id, "ready-to-file-online", ".png")
    await page.screenshot(path=str(ready), full_page=True)
    return str(ready)


async def submit_payment_if_configured(page, order_id: str) -> str:
    payment = payment_configuration()
    if not payment["ready"]:
        raise PaymentNotReady(payment["message"])
    if await page.locator("input[name*='card' i], input[autocomplete='cc-number'], input[name*='cvv' i]").count():
        evidence = await checkpoint_snapshot(page, order_id, "payment-card-fields-visible")
        raise BizFileBlocked(
            "payment_method_not_tokenized",
            "BizFile is asking for raw card fields; configure a saved portal payment method or PCI vault flow.",
            evidence,
        )
    submit = page.get_by_role("button", name=re.compile("submit payment|pay|submit", re.I))
    if not await submit.count():
        evidence = await checkpoint_snapshot(page, order_id, "submit-payment-not-found")
        raise BizFileBlocked("payment_submit_not_found", "Could not find a safe saved-method payment submit button.", evidence)
    await submit.first.click(timeout=20_000)
    await page.wait_for_load_state("domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(8_000)
    await assert_not_blocked(page, order_id)
    receipt = run_path(order_id, "after-payment", ".png")
    await page.screenshot(path=str(receipt), full_page=True)
    return str(receipt)


# Plan v2.6 §4.5 / PR6: California bizfile portal emits a 12-digit File Number
# on the receipt page following the "File Number" or "Filing Number" label.
# Pattern is best-effort; tighten after the first live submission ships a
# real DOM/PDF sample. The regex tolerates a colon, em dash, or whitespace
# separator and captures the first 8-15 alphanumeric digit/letter run.
CONFIRMATION_NUMBER_REGEX = r"(?:File|Filing|Confirmation)\s*(?:Number|No\.?|#)\s*[:\-—]?\s*([A-Z0-9]{8,15})"


def _persist_filing_confirmation(conn: sqlite3.Connection, order_id: str, value: str | None, source: str) -> None:
    """Write orders.filing_confirmation when the worker captured a value.

    Worker flows may not always extract a confirmation (CA returns nothing on
    declined payments, on session timeouts, etc.). Quiet no-op for missing
    values keeps the worker fault-tolerant; the operator cockpit can fill the
    gap via the manual input Steven approved in PR4.
    """
    if not value:
        return
    from execution_platform import build_filing_confirmation_payload
    try:
        payload = build_filing_confirmation_payload(value, source)
    except ValueError:
        return
    conn.execute(
        "UPDATE orders SET filing_confirmation = ?, updated_at = datetime('now') WHERE id = ?",
        (payload, order_id),
    )


def mark_submitted(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    evidence_path: str,
    message: str,
    filing_confirmation: str | None = None,
) -> None:
    add_document(conn, job, "submitted_receipt", Path(evidence_path).name, evidence_path)
    _persist_filing_confirmation(conn, job["order_id"], filing_confirmation, "adapter")
    conn.execute(
        """
        UPDATE filing_jobs
        SET status = 'submitted_to_state',
            submitted_at = COALESCE(submitted_at, datetime('now')),
            evidence_summary = ?,
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (message, job["id"]),
    )
    conn.execute(
        """
        UPDATE orders
        SET status = 'submitted_to_state',
            submitted_at = COALESCE(submitted_at, datetime('now')),
            updated_at = datetime('now')
        WHERE id = ?
        """,
        (job["order_id"],),
    )
    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'submitted_to_state', ?)",
        (job["order_id"], message),
    )
    insert_event(conn, job["id"], job["order_id"], "submitted_to_state", message, evidence_path)


async def submit_job(page, conn: sqlite3.Connection, job: sqlite3.Row, order: sqlite3.Row, dry_run: bool) -> dict[str, Any]:
    if (os.environ.get("CA_BIZFILE_ENABLE_LIVE_SUBMIT") or "").lower() not in {"1", "true", "yes"}:
        return {
            "order_id": job["order_id"],
            "business_name": order["business_name"],
            "status": "live_submit_disabled",
            "message": "Set CA_BIZFILE_ENABLE_LIVE_SUBMIT=true only on the trusted worker profile after certification.",
        }
    payload = filing_payload_from_order(order)
    missing = missing_payload_fields(payload)
    if missing:
        return {
            "order_id": job["order_id"],
            "business_name": order["business_name"],
            "status": "missing_required_fields",
            "missing_fields": missing,
        }
    if dry_run:
        return {
            "order_id": job["order_id"],
            "business_name": payload.business_name,
            "status": "would_submit",
            "payment": payment_configuration(),
        }
    ready_path = await fill_to_payment(page, job["order_id"], payload)
    await page.get_by_role("button", name="File Online", exact=True).last.click(timeout=20_000)
    await page.wait_for_timeout(10_000)
    cart_path = run_path(job["order_id"], "after-file-online", ".png")
    await page.screenshot(path=str(cart_path), full_page=True)
    try:
        receipt_path = await submit_payment_if_configured(page, job["order_id"])
    except PaymentNotReady as exc:
        message = f"CA BizFile filing prepared through payment cart for {payload.business_name}; payment not submitted: {exc}"
        conn.execute(
            """
            UPDATE filing_jobs
            SET status = 'payment_required', evidence_summary = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (message, job["id"]),
        )
        insert_event(conn, job["id"], job["order_id"], "ca_bizfile_payment_required", message, str(cart_path))
        return {
            "order_id": job["order_id"],
            "business_name": payload.business_name,
            "status": "payment_required",
            "ready_evidence": ready_path,
            "cart_evidence": str(cart_path),
            "message": message,
        }
    message = f"California BizFile receipt captured after automated payment for {payload.business_name}."
    # Plan v2.6 §4.5 / PR6 codex round-1: extract state-issued File Number from
    # the live receipt page before promotion so orders.filing_confirmation
    # gets populated under the trigger-required JSON shape.
    from execution_platform import extract_filing_confirmation
    try:
        receipt_text = await page.inner_text("body", timeout=5_000)
    except Exception:
        receipt_text = ""
    extracted = extract_filing_confirmation(receipt_text, CONFIRMATION_NUMBER_REGEX)
    mark_submitted(conn, job, receipt_path, message, filing_confirmation=extracted)
    return {
        "order_id": job["order_id"],
        "business_name": payload.business_name,
        "status": "submitted_to_state",
        "receipt_path": receipt_path,
        "filing_confirmation": extracted,
        "message": message,
    }


async def run_browser_worker(operation: str, jobs: list[tuple[sqlite3.Row, sqlite3.Row]], conn: sqlite3.Connection, dry_run: bool, headless: bool) -> list[dict[str, Any]]:
    from playwright.async_api import async_playwright

    profile_dir = Path(os.environ.get("CA_BIZFILE_PROFILE_DIR") or (BASE_DIR / "data" / "browser_profiles" / "ca_bizfile"))
    profile_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            channel=os.environ.get("CA_BIZFILE_BROWSER_CHANNEL") or "chrome",
            headless=headless,
            viewport={"width": 1440, "height": 950},
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        for job, order in jobs:
            try:
                if operation == "status":
                    result = await check_status_job(page, conn, job, order, dry_run=dry_run)
                else:
                    result = await submit_job(page, conn, job, order, dry_run=dry_run)
                results.append(result)
                if not dry_run:
                    conn.commit()
            except BizFileBlocked as exc:
                if not dry_run:
                    record_access_block(conn, job, exc)
                    conn.commit()
                results.append({
                    "order_id": job["order_id"],
                    "business_name": order["business_name"],
                    "status": "blocked",
                    "blocker_code": exc.code,
                    "message": str(exc),
                    "evidence_path": exc.evidence_path,
                })
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if not dry_run:
                    # Track B follow-up #2: escalate to operator_required so
                    # the cockpit work queue surfaces the failure. The
                    # PR7 trigger never fires on operator_required.
                    from execution_platform import escalate_to_operator_required
                    escalate_to_operator_required(
                        conn,
                        filing_job_id=job["id"],
                        order_id=job["order_id"],
                        source="ca_bizfile",
                        error_message=message,
                    )
                    conn.commit()
                results.append({
                    "order_id": job["order_id"],
                    "business_name": order["business_name"],
                    "status": "operator_required",
                    "message": message,
                })
        await context.close()
    return results


async def run_protocol_discovery(*, business_name: str = "", headless: bool = True) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    profile_dir = Path(os.environ.get("CA_BIZFILE_PROFILE_DIR") or (BASE_DIR / "data" / "browser_profiles" / "ca_bizfile"))
    profile_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile_dir),
            channel=os.environ.get("CA_BIZFILE_BROWSER_CHANNEL") or "chrome",
            headless=headless,
            viewport={"width": 1440, "height": 950},
            accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            result = await run_protocol_discovery_probe(page, business_name=business_name)
        finally:
            await context.close()
    return {"ran_at": utc_now(), "status": "ok", "operation": "discover", "results": [result]}


async def run_worker(
    *,
    operation: str = "status",
    limit: int = 25,
    order_id: str = "",
    dry_run: bool = False,
    headless: bool = True,
    business_name: str = "",
) -> dict[str, Any]:
    load_env()
    if operation not in {"status", "submit", "discover"}:
        return {"ran_at": utc_now(), "status": "invalid_operation", "operation": operation, "results": []}
    if operation == "discover":
        if dry_run:
            return {
                "ran_at": utc_now(),
                "status": "ok",
                "operation": operation,
                "dry_run": True,
                "results": [{
                    "status": "would_discover",
                    "business_name": business_name,
                    "manifest_path": str(PROTOCOL_MANIFEST_PATH),
                }],
            }
        return await run_protocol_discovery(business_name=business_name, headless=headless)
    conn = get_db()
    try:
        jobs = active_ca_jobs(conn, operation=operation, order_id=order_id, limit=limit)
        if not jobs:
            return {"ran_at": utc_now(), "status": "ok", "operation": operation, "results": []}
        if dry_run:
            results = []
            for job, order in jobs:
                result = {
                    "order_id": job["order_id"],
                    "business_name": order["business_name"],
                    "status": f"would_{operation}",
                }
                if operation == "submit":
                    result["payment"] = payment_configuration()
                    result["missing_fields"] = missing_payload_fields(filing_payload_from_order(order))
                results.append(result)
            return {"ran_at": utc_now(), "status": "ok", "operation": operation, "dry_run": True, "results": results}
        results = await run_browser_worker(operation, jobs, conn, dry_run=dry_run, headless=headless)
        return {"ran_at": utc_now(), "status": "ok", "operation": operation, "dry_run": dry_run, "results": results}
    except Exception as exc:
        return {"ran_at": utc_now(), "status": "error", "message": f"{type(exc).__name__}: {exc}", "results": []}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run California BizFile submission/status automation.")
    parser.add_argument("--operation", choices=["status", "submit", "discover"], default="status")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--order-id", default="")
    parser.add_argument("--business-name", default="", help="Optional read-only search probe for protocol discovery.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = asyncio.run(
        run_worker(
            operation=args.operation,
            limit=args.limit,
            order_id=args.order_id,
            dry_run=args.dry_run,
            headless=not args.headful,
            business_name=args.business_name,
        )
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"CA BizFile worker {payload.get('status')} for {len(payload.get('results', []))} jobs.")
        for result in payload.get("results", []):
            print(f"{result.get('order_id')} {result.get('status')}: {result.get('message', result.get('business_name', ''))}")
    return 0 if payload.get("status") in {"ok", "invalid_operation"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
