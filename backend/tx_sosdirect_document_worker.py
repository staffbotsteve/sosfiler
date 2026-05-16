"""
Authenticated Texas SOSDirect document retriever.

Cron-safe worker for Texas filings. It logs into the SOSFiler SOSDirect account,
checks Briefcase for active Texas filing jobs, downloads matching filed documents,
and attaches them to the customer's portal as approval evidence.

Credentials are read from environment variables or .env:
  TX_SOSDIRECT_USER_ID
  TX_SOSDIRECT_PASSWORD
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
from urllib.parse import unquote, urljoin


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"
RUN_DIR = BASE_DIR / "data" / "filing_listener_runs" / "tx_sosdirect"

LOGIN_URL = "https://direct.sos.state.tx.us/acct/acct-login.asp"
BRIEFCASE_URL = "https://direct.sos.state.tx.us/acct/acct-batch.asp?spage=batch-view"

ACTIVE_STATUSES = {
    "submitted_to_state",
    "awaiting_state",
    "pending_state_review",
    "received_needs_sosdirect_check",
    "operator_review",
}

DOCUMENT_LINK_WORDS = (
    "view",
    "download",
    "document",
    "certificate",
    "filing",
    "pdf",
    "image",
    "receipt",
    "response",
    "zip",
)

APPROVAL_WORDS = (
    "approved",
    "filed",
    "certificate",
    "formation",
    "filing",
    "in existence",
    "processed",
    "legal entity filing document",
)


def redact_sensitive_html(content: str) -> str:
    content = re.sub(
        r'(name="web_password"[^>]*value=")[^"]*(")',
        r'\1[REDACTED]\2',
        content,
        flags=re.I,
    )
    content = re.sub(
        r'(name="client_id"[^>]*value=")[^"]*(")',
        r'\1[REDACTED]\2',
        content,
        flags=re.I,
    )
    return content


@dataclass
class CandidateLink:
    href: str
    text: str
    row_text: str


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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def safe_name(value: str, default: str = "document") -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return value[:160] or default


def parse_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def known_identifiers(job: sqlite3.Row, order: sqlite3.Row) -> list[str]:
    identifiers = [order["business_name"] or "", order["id"] or ""]
    if job["evidence_summary"]:
        evidence = parse_json(job["evidence_summary"], {})
        for key in ("document_number", "filing_number", "session_id", "receipt_number"):
            if evidence.get(key):
                identifiers.append(str(evidence[key]))
    return [item.lower() for item in identifiers if item]


def evidence_value(job: sqlite3.Row, key: str) -> str:
    if not job["evidence_summary"]:
        return ""
    evidence = parse_json(job["evidence_summary"], {})
    return str(evidence.get(key) or "")


def row_matches(row_text: str, identifiers: list[str]) -> bool:
    text = row_text.lower()
    return any(identifier and identifier in text for identifier in identifiers)


def link_looks_like_document(link_text: str, href: str) -> bool:
    haystack = f"{link_text} {href}".lower()
    return any(word in haystack for word in DOCUMENT_LINK_WORDS)


def text_suggests_approval(text: str) -> bool:
    lowered = text.lower()
    if "receipt" in lowered and not any(word in lowered for word in ("approved", "filed", "certificate")):
        return False
    return any(word in lowered for word in APPROVAL_WORDS)


def write_debug(order_id: str, name: str, content: str) -> str:
    target = RUN_DIR / order_id / f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{name}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, errors="ignore")
    return str(target)


def event_exists(conn: sqlite3.Connection, job_id: str, event_type: str, message: str) -> bool:
    return conn.execute(
        """
        SELECT 1
        FROM filing_events
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
) -> None:
    if event_exists(conn, job_id, event_type, message):
        return
    conn.execute(
        """
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        VALUES (?, ?, ?, ?, 'tx_sosdirect_worker', ?)
        """,
        (job_id, order_id, event_type, message, evidence_path),
    )


def attach_approval_document(
    conn: sqlite3.Connection,
    job: sqlite3.Row,
    filename: str,
    file_path: str,
    message: str,
) -> None:
    existing_artifact = conn.execute(
        "SELECT 1 FROM filing_artifacts WHERE order_id = ? AND filename = ? LIMIT 1",
        (job["order_id"], filename),
    ).fetchone()
    if not existing_artifact:
        from execution_platform import insert_filing_artifact_row, sha256_for_file_path
        sha256_hex = sha256_for_file_path(file_path)
        insert_filing_artifact_row(
            conn,
            filing_job_id=job["id"],
            order_id=job["order_id"],
            artifact_type="approved_certificate",
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
            VALUES (?, 'approved_certificate', ?, ?, ?)
            """,
            (job["order_id"], filename, file_path, fmt),
        )

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
    insert_event(conn, job["id"], job["order_id"], "state_approved", message, file_path)


def active_texas_jobs(conn: sqlite3.Connection, order_id: str = "", limit: int = 25) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    required = ("filing_jobs", "orders", "filing_events", "filing_artifacts", "documents", "status_updates")
    missing = [table for table in required if not table_exists(conn, table)]
    if missing:
        raise RuntimeError(f"database_not_initialized: missing {', '.join(missing)}")

    params: list[Any] = ["TX", *ACTIVE_STATUSES]
    where = f"j.state = ? AND j.status IN ({','.join('?' for _ in ACTIVE_STATUSES)})"
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


async def login(page, user_id: str, password: str) -> str:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45_000)
    await page.fill('input[name="client_id"]', user_id)
    await page.fill('input[name="web_password"]', password)
    await page.click('input[type="submit"][name="submit"], input[type="Submit"][name="submit"]')
    await page.wait_for_load_state("domcontentloaded", timeout=45_000)
    content = await page.content()
    if await page.locator('select[name="payment_type_id"]').count():
        payment_type = os.environ.get("TX_SOSDIRECT_PAYMENT_TYPE_ID", "5")
        await page.select_option('select[name="payment_type_id"]', payment_type)
        contact_defaults = {
            "ordering_party_name": os.environ.get("TX_SOSDIRECT_CONTACT_NAME", "SOS Filer"),
            "ordering_party_phone": os.environ.get("TX_SOSDIRECT_CONTACT_PHONE", "9165057744"),
            "ordering_party_email": os.environ.get("TX_SOSDIRECT_CONTACT_EMAIL", "admin@sosfiler.com"),
        }
        for field, value in contact_defaults.items():
            locator = page.locator(f'input[name="{field}"]')
            if await locator.count():
                await locator.fill(value)
        await page.click('input[type="submit"][name="Submit"][value="Continue"]')
        await page.wait_for_load_state("domcontentloaded", timeout=45_000)
        content = await page.content()
    if "SOSDirect Account Login" in content and 'input type="text" name="client_id"' in content:
        failure_dir = RUN_DIR / "login"
        failure_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        html_path = failure_dir / f"{stamp}-login-failed.html"
        screenshot_path = failure_dir / f"{stamp}-login-failed.png"
        html_path.write_text(redact_sensitive_html(content), errors="ignore")
        await page.screenshot(path=str(screenshot_path), full_page=True)
        raise RuntimeError("SOSDirect login failed or password reset is required.")
    match = re.search(r"session code is:\s*([A-Z0-9]+)", content, flags=re.I)
    return match.group(1) if match else ""


async def collect_candidate_links(page, identifiers: list[str]) -> list[CandidateLink]:
    rows = await page.locator("tr").all()
    candidates: list[CandidateLink] = []
    seen_hrefs: set[str] = set()
    for row in rows:
        try:
            row_text = re.sub(r"\s+", " ", await row.inner_text(timeout=2_000)).strip()
        except Exception:
            continue
        if identifiers and not row_matches(row_text, identifiers):
            continue
        links = await row.locator("a").all()
        for link in links:
            href = await link.get_attribute("href") or ""
            link_text = re.sub(r"\s+", " ", await link.inner_text(timeout=2_000)).strip()
            href_key = href.lower()
            if href and href_key not in seen_hrefs and link_looks_like_document(link_text, href):
                seen_hrefs.add(href_key)
                candidates.append(CandidateLink(href=href, text=link_text, row_text=row_text))
    return candidates


async def save_response_body(response, target: Path) -> bool:
    if response is None:
        return False
    content_type = (response.headers.get("content-type") or "").lower()
    if not any(kind in content_type for kind in ("pdf", "octet-stream", "zip", "image")):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(await response.body())
    return True


async def download_candidate(page, order_id: str, candidate: CandidateLink, index: int) -> Path | None:
    target_dir = DOCS_DIR / order_id / "state_filings"
    target_dir.mkdir(parents=True, exist_ok=True)
    href = candidate.href.replace("\\", "/")
    abs_url = urljoin(page.url, href)

    base = safe_name(candidate.text or Path(abs_url).name or f"tx-sosdirect-document-{index}")
    suffix = Path(abs_url).suffix
    if suffix.lower() not in {".pdf", ".zip", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
        suffix = ".pdf"
    target = target_dir / f"tx-sosdirect-{base}-{index}{suffix}"

    try:
        response = await page.context.request.get(abs_url, timeout=45_000)
        content_type = (response.headers.get("content-type") or "").lower()
        disposition = response.headers.get("content-disposition") or ""
        filename_match = re.search(r'filename="?([^";]+)"?', disposition, flags=re.I)
        if filename_match:
            target = target_dir / safe_name(unquote(filename_match.group(1)))
        if response.ok and any(kind in content_type for kind in ("pdf", "octet-stream", "zip", "image")):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(await response.body())
            return target
    except Exception:
        pass

    link = page.locator(f'a[href="{candidate.href}"]').first
    try:
        async with page.expect_download(timeout=10_000) as download_info:
            await link.click(timeout=10_000)
        download = await download_info.value
        suggested = safe_name(download.suggested_filename or target.name)
        saved = target_dir / suggested
        await download.save_as(saved)
        return saved
    except Exception:
        return None


async def process_job(page, conn: sqlite3.Connection, job: sqlite3.Row, order: sqlite3.Row, dry_run: bool = False) -> dict[str, Any]:
    order_id = job["order_id"]
    identifiers = known_identifiers(job, order)
    session_id = evidence_value(job, "session_id")
    if session_id:
        await page.goto("https://direct.sos.state.tx.us/acct/acct-batch.asp?spage=batch-search", wait_until="domcontentloaded", timeout=45_000)
        await page.fill('input[name="sid"]', session_id)
        await page.fill('input[name="op_email"]', os.environ.get("TX_SOSDIRECT_CONTACT_EMAIL", "admin@sosfiler.com"))
        await page.locator('input[type="submit"][name="submit"][value="Search"]').first.click()
        await page.wait_for_load_state("domcontentloaded", timeout=45_000)
    else:
        await page.goto(BRIEFCASE_URL, wait_until="domcontentloaded", timeout=45_000)
    content = await page.content()
    debug_path = write_debug(order_id, "briefcase.html", content)

    if "SOSDirect Account Login" in content and 'name="client_id"' in content:
        raise RuntimeError("SOSDirect session returned to login while checking Briefcase.")

    candidates = await collect_candidate_links(page, identifiers)
    result: dict[str, Any] = {
        "order_id": order_id,
        "business_name": order["business_name"],
        "status": "checked",
        "debug_html": debug_path,
        "candidates": len(candidates),
        "downloaded_documents": [],
    }

    if not candidates:
        message = "SOSDirect Briefcase checked; no matching downloadable document found yet."
        if not dry_run:
            insert_event(conn, job["id"], order_id, "sosdirect_briefcase_checked", message, debug_path)
        result["status"] = "no_matching_document"
        result["message"] = message
        return result

    for index, candidate in enumerate(candidates, start=1):
        if not text_suggests_approval(f"{candidate.text} {candidate.row_text} {candidate.href}"):
            continue
        if dry_run:
            result["downloaded_documents"].append({"candidate": candidate.text, "href": candidate.href})
            continue
        downloaded = await download_candidate(page, order_id, candidate, index)
        if not downloaded:
            continue
        message = f"Texas SOSDirect approval document captured from Briefcase for {order['business_name']}."
        attach_approval_document(conn, job, downloaded.name, str(downloaded), message)
        result["downloaded_documents"].append({"filename": downloaded.name, "path": str(downloaded)})

    if result["downloaded_documents"]:
        result["status"] = "state_approved" if not dry_run else "would_download"
    else:
        result["status"] = "no_approval_document_downloaded"
    return result


async def run_worker(limit: int = 25, order_id: str = "", dry_run: bool = False, headless: bool = True) -> dict[str, Any]:
    load_env()
    user_id = os.environ.get("TX_SOSDIRECT_USER_ID", "")
    password = os.environ.get("TX_SOSDIRECT_PASSWORD", "")
    if not user_id or not password:
        return {
            "ran_at": utc_now(),
            "status": "credentials_missing",
            "message": "Set TX_SOSDIRECT_USER_ID and TX_SOSDIRECT_PASSWORD in .env or the cron environment.",
            "results": [],
        }

    conn = get_db()
    try:
        jobs = active_texas_jobs(conn, order_id=order_id, limit=limit)
        if not jobs:
            return {"ran_at": utc_now(), "status": "ok", "results": []}

        from playwright.async_api import async_playwright

        results = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            session_code = await login(page, user_id, password)
            for job, order in jobs:
                try:
                    results.append(await process_job(page, conn, job, order, dry_run=dry_run))
                    if not dry_run:
                        conn.commit()
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    if not dry_run:
                        insert_event(conn, job["id"], job["order_id"], "sosdirect_worker_error", message)
                        conn.commit()
                    results.append({
                        "order_id": job["order_id"],
                        "business_name": order["business_name"],
                        "status": "error",
                        "message": message,
                    })
            await browser.close()

        return {"ran_at": utc_now(), "status": "ok", "session_code": session_code, "results": results}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve Texas SOSDirect filed documents into customer portals.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--order-id", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--headful", action="store_true")
    args = parser.parse_args()

    payload = asyncio.run(
        run_worker(
            limit=args.limit,
            order_id=args.order_id,
            dry_run=args.dry_run,
            headless=not args.headful,
        )
    )
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("status") in {"ok", "credentials_missing"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
