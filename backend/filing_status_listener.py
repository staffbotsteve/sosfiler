"""
SOSFiler filing status listener.

Designed for cron. Scans active state filing jobs, checks official status
sources, records source-specific filing events, and downloads official state
documents into the customer's portal when an approval/rejection document is
available.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"
LISTENER_RUN_DIR = BASE_DIR / "data" / "filing_listener_runs"

ACTIVE_STATUSES = {
    "submitted_to_state",
    "awaiting_state",
    "pending_state_review",
    "received_needs_sosdirect_check",
}

APPROVED_SIGNALS = {
    "filed",
    "approved",
    "in existence",
    "completed",
    "accepted",
}

REJECTED_SIGNALS = {
    "rejected",
    "denied",
    "returned",
    "not filed",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def fetch_text(url: str, data: dict[str, str] | None = None, timeout: int = 20) -> str:
    body = None
    headers = {
        "User-Agent": "SOSFilerStatusListener/0.1 (+https://sosfiler.com)",
    }
    if data is not None:
        body = urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = Request(url, data=body, headers=headers)
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def hidden_fields(page: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', page)
        if match:
            fields[name] = html.unescape(match.group(1))
    return fields


def clean_cell(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def extract_table_rows(page: str) -> list[dict[str, str]]:
    table_match = re.search(r'<table[^>]+id="grdAll"[^>]*>(.*?)</table>', page, flags=re.I | re.S)
    if not table_match:
        return []
    table = table_match.group(1)
    headers = [
        clean_cell(cell)
        for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, flags=re.I | re.S)
    ]
    rows = []
    for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", table, flags=re.I | re.S)[1:]:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
        if not cells:
            continue
        row = {headers[i]: clean_cell(cells[i]) for i in range(min(len(headers), len(cells)))}
        link_match = re.search(r'href="([^"]+)"', row_html, flags=re.I)
        if link_match:
            row["view_url"] = html.unescape(link_match.group(1))
        rows.append(row)
    return rows


@dataclass
class ListenerResult:
    job_id: str
    order_id: str
    state: str
    status: str
    message: str
    source_statuses: dict[str, Any]
    downloaded_documents: list[dict[str, str]]


def event_exists(conn: sqlite3.Connection, job_id: str, event_type: str, message: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM filing_events
        WHERE filing_job_id = ? AND event_type = ? AND message = ?
        LIMIT 1
        """,
        (job_id, event_type, message),
    ).fetchone()
    return row is not None


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
        VALUES (?, ?, ?, ?, 'listener', ?)
        """,
        (job_id, order_id, event_type, message, evidence_path),
    )


def add_customer_document(
    conn: sqlite3.Connection,
    job_id: str,
    order_id: str,
    artifact_type: str,
    filename: str,
    file_path: str,
) -> None:
    existing_artifact = conn.execute(
        """
        SELECT 1 FROM filing_artifacts
        WHERE order_id = ? AND filename = ?
        LIMIT 1
        """,
        (order_id, filename),
    ).fetchone()
    if not existing_artifact:
        conn.execute(
            """
            INSERT INTO filing_artifacts (filing_job_id, order_id, artifact_type, filename, file_path, is_evidence)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (job_id, order_id, artifact_type, filename, file_path),
        )
    existing_doc = conn.execute(
        "SELECT 1 FROM documents WHERE order_id = ? AND filename = ? LIMIT 1",
        (order_id, filename),
    ).fetchone()
    if not existing_doc:
        fmt = Path(filename).suffix.lstrip(".").lower() or "file"
        conn.execute(
            "INSERT INTO documents (order_id, doc_type, filename, file_path, format) VALUES (?, ?, ?, ?, ?)",
            (order_id, artifact_type, filename, file_path, fmt),
        )


def download_document(url: str, order_id: str, filename: str) -> Path:
    target_dir = DOCS_DIR / order_id / "state_filings"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    req = Request(url, headers={"User-Agent": "SOSFilerStatusListener/0.1 (+https://sosfiler.com)"})
    with urlopen(req, timeout=30) as response:
        target.write_bytes(response.read())
    return target


class StateStatusAdapter:
    state = ""

    def check(self, conn: sqlite3.Connection, job: sqlite3.Row, order: sqlite3.Row, dry_run: bool = False) -> ListenerResult:
        message = f"No automated listener adapter exists for {job['state']} yet; operator review required."
        if not dry_run:
            insert_event(conn, job["id"], job["order_id"], "listener_no_adapter", message)
        return ListenerResult(job["id"], job["order_id"], job["state"], "no_adapter", message, {}, [])


class TexasStatusAdapter(StateStatusAdapter):
    state = "TX"
    tracker_url = "https://webservices.sos.state.tx.us/filing-status/status.aspx"

    def check(self, conn: sqlite3.Connection, job: sqlite3.Row, order: sqlite3.Row, dry_run: bool = False) -> ListenerResult:
        page = fetch_text(self.tracker_url)
        fields = hidden_fields(page)
        fields.update({
            "txtEntityName": order["business_name"],
            "btnEntityName": "Search",
        })
        result_page = fetch_text(self.tracker_url, data=fields)
        rows = extract_table_rows(result_page)
        matching = rows[0] if rows else {}
        document_status = matching.get("Status", "")
        filing_number = matching.get("Filing Number", "")
        document_number = matching.get("Document Number", "")
        source_statuses = {
            "document_tracker_status": document_status,
            "document_number": document_number,
            "filing_number_from_tracker": filing_number,
            "delivery_method": matching.get("Delivery Method", ""),
            "received": matching.get("Received", ""),
            "document_type": matching.get("Document Type", ""),
        }

        if not matching:
            message = "Texas Business Filing Tracker returned no matching public document row; SOSDirect entity/briefcase check required."
            if not dry_run:
                insert_event(conn, job["id"], job["order_id"], "tx_tracker_no_match", message)
            return ListenerResult(job["id"], job["order_id"], "TX", "needs_operator_review", message, source_statuses, [])

        message = (
            f"Texas document tracker status: {document_status or 'unknown'}"
            f" for document {document_number or 'unknown'}."
        )
        if not dry_run:
            insert_event(conn, job["id"], job["order_id"], "tx_document_tracker_status", message)

        normalized = document_status.lower()
        downloaded: list[dict[str, str]] = []
        view_url = matching.get("view_url")
        artifact_type = "approved_certificate" if any(s in normalized for s in APPROVED_SIGNALS) else "state_correspondence"
        if view_url and any(s in normalized for s in APPROVED_SIGNALS | REJECTED_SIGNALS):
            absolute_url = urljoin(self.tracker_url, view_url)
            suffix = ".pdf" if ".pdf" in absolute_url.lower() or "pdf" in view_url.lower() else ".html"
            filename = f"tx-{artifact_type}-{job['order_id']}-{document_number or 'document'}{suffix}"
            if not dry_run:
                path = download_document(absolute_url, job["order_id"], filename)
                add_customer_document(conn, job["id"], job["order_id"], artifact_type, filename, str(path))
                downloaded.append({"filename": filename, "path": str(path), "url": absolute_url})

        if any(s in normalized for s in REJECTED_SIGNALS):
            if not dry_run:
                conn.execute(
                    "UPDATE filing_jobs SET status = 'operator_review', updated_at = datetime('now'), evidence_summary = ? WHERE id = ?",
                    (message, job["id"]),
                )
                conn.execute(
                    "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'operator_review', ?)",
                    (job["order_id"], "Texas posted rejection/return status. Operator review required."),
                )
            return ListenerResult(job["id"], job["order_id"], "TX", "operator_review", message, source_statuses, downloaded)

        if any(s in normalized for s in APPROVED_SIGNALS) and downloaded:
            if not dry_run:
                conn.execute(
                    """
                    UPDATE filing_jobs
                    SET status = 'state_approved', approved_at = datetime('now'), evidence_summary = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (message, job["id"]),
                )
                conn.execute(
                    "UPDATE orders SET status = 'state_approved', approved_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                    (job["order_id"],),
                )
                conn.execute(
                    "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'state_approved', ?)",
                    (job["order_id"], "Texas approval evidence was captured and added to your document vault."),
                )
            return ListenerResult(job["id"], job["order_id"], "TX", "state_approved", message, source_statuses, downloaded)

        # Public tracker alone is not the full Texas lifecycle. Do not downgrade an
        # entity status already known from SOSDirect inquiry.
        if normalized == "received" and job["status"] == "submitted_to_state":
            if not dry_run:
                conn.execute(
                    "UPDATE filing_jobs SET status = 'received_needs_sosdirect_check', updated_at = datetime('now'), evidence_summary = ? WHERE id = ?",
                    (message, job["id"]),
                )
        return ListenerResult(job["id"], job["order_id"], "TX", "checked", message, source_statuses, downloaded)


ADAPTERS = {
    "TX": TexasStatusAdapter(),
}


def active_jobs(conn: sqlite3.Connection, state: str = "", limit: int = 50) -> list[tuple[sqlite3.Row, sqlite3.Row]]:
    params: list[Any] = []
    clauses = [f"j.status IN ({','.join('?' for _ in ACTIVE_STATUSES)})"]
    params.extend(sorted(ACTIVE_STATUSES))
    if state:
        clauses.append("j.state = ?")
        params.append(state.upper())
    rows = conn.execute(
        f"""
        SELECT j.*, o.business_name, o.email, o.status AS order_status, o.token
        FROM filing_jobs j
        JOIN orders o ON o.id = j.order_id
        WHERE {' AND '.join(clauses)}
        ORDER BY COALESCE(j.submitted_at, j.updated_at, o.created_at) ASC
        LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [(row, row) for row in rows]


def run_listener(limit: int = 50, state: str = "", dry_run: bool = False) -> dict[str, Any]:
    conn = get_db()
    results: list[dict[str, Any]] = []
    try:
        missing_tables = [
            table for table in ("filing_jobs", "orders", "filing_events", "filing_artifacts", "documents", "status_updates")
            if not table_exists(conn, table)
        ]
        if missing_tables:
            return {
                "ran_at": utc_now(),
                "dry_run": dry_run,
                "limit": limit,
                "state": state.upper() if state else "",
                "status": "database_not_initialized",
                "missing_tables": missing_tables,
                "results": [],
            }
        for job, order in active_jobs(conn, state=state, limit=limit):
            adapter = ADAPTERS.get(job["state"], StateStatusAdapter())
            try:
                result = adapter.check(conn, job, order, dry_run=dry_run)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if not dry_run:
                    insert_event(
                        conn,
                        job["id"],
                        job["order_id"],
                        "listener_error",
                        message,
                    )
                result = ListenerResult(
                    job_id=job["id"],
                    order_id=job["order_id"],
                    state=job["state"],
                    status="error",
                    message=message,
                    source_statuses={},
                    downloaded_documents=[],
                )
            results.append({
                "job_id": result.job_id,
                "order_id": result.order_id,
                "state": result.state,
                "status": result.status,
                "message": result.message,
                "source_statuses": result.source_statuses,
                "downloaded_documents": result.downloaded_documents,
            })
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    payload = {
        "ran_at": utc_now(),
        "dry_run": dry_run,
        "limit": limit,
        "state": state.upper() if state else "",
        "status": "ok",
        "results": results,
    }
    if not dry_run:
        write_json(LISTENER_RUN_DIR / f"{payload['ran_at'].replace(':', '').replace('+', 'Z')}.json", payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check official state filing status sources and ingest state documents.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--state", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = run_listener(limit=args.limit, state=args.state, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Checked {len(payload['results'])} filing jobs.")
        for result in payload["results"]:
            print(f"{result['order_id']} {result['state']} {result['status']}: {result['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
