"""
Ingest completed EIN confirmation documents into customer portals.

This script is cron-safe. It does not submit anything to the IRS. It looks for
operator- or automation-captured EIN confirmation files in each approved order's
generated document folder, adds the file to the customer document vault, and
records the EIN completion timeline event exactly once.
"""

import argparse
import json
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"

EIN_FILE_PATTERNS = (
    "ein_confirmation*.pdf",
    "ein_confirmation*.png",
    "irs_ein_confirmation*.pdf",
    "irs_ein_confirmation*.png",
    "cp575*.pdf",
    "cp575*.png",
)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone() is not None


def file_format(path: Path) -> str:
    return path.suffix.lstrip(".").lower() or "file"


def find_ein_document(order_id: str) -> Path | None:
    order_dir = DOCS_DIR / order_id
    if not order_dir.exists():
        return None

    matches: list[Path] = []
    for pattern in EIN_FILE_PATTERNS:
        matches.extend(order_dir.glob(pattern))
    matches = [path for path in matches if path.is_file() and path.name != "ein_queue.json"]
    if not matches:
        return None
    return sorted(matches, key=lambda path: path.stat().st_mtime, reverse=True)[0]


def extract_ein_from_text(path: Path) -> str:
    if path.suffix.lower() not in {".txt", ".json"}:
        return ""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return ""
    match = re.search(r"\b(\d{2}-\d{7})\b", text)
    return match.group(1) if match else ""


def document_exists(conn: sqlite3.Connection, order_id: str, filename: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM documents WHERE order_id = ? AND filename = ? LIMIT 1",
        (order_id, filename),
    ).fetchone() is not None


def status_exists(conn: sqlite3.Connection, order_id: str, status: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM status_updates WHERE order_id = ? AND status = ? LIMIT 1",
        (order_id, status),
    ).fetchone() is not None


def event_exists(conn: sqlite3.Connection, order_id: str, event_type: str, evidence_path: str) -> bool:
    return conn.execute(
        """
        SELECT 1
        FROM filing_events
        WHERE order_id = ?
          AND event_type = ?
          AND COALESCE(evidence_path, '') = ?
        LIMIT 1
        """,
        (order_id, event_type, evidence_path),
    ).fetchone() is not None


def latest_formation_job_id(conn: sqlite3.Connection, order_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT id
        FROM filing_jobs
        WHERE order_id = ? AND action_type = 'formation'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    return row["id"] if row else None


def ingest_order(conn: sqlite3.Connection, order: sqlite3.Row, dry_run: bool = False) -> dict:
    order_id = order["id"]
    doc_path = find_ein_document(order_id)
    if not doc_path:
        return {"order_id": order_id, "business_name": order["business_name"], "status": "no_ein_document_found"}

    evidence_path = str(doc_path)
    ein = extract_ein_from_text(doc_path)
    changes: list[str] = []

    if not document_exists(conn, order_id, doc_path.name):
        changes.append("document")
        if not dry_run:
            conn.execute(
                """
                INSERT INTO documents (order_id, doc_type, filename, file_path, format)
                VALUES (?, 'ein_confirmation_letter', ?, ?, ?)
                """,
                (order_id, doc_path.name, evidence_path, file_format(doc_path)),
            )

    if not status_exists(conn, order_id, "ein_received"):
        changes.append("status_update")
        if not dry_run:
            conn.execute(
                "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'ein_received', ?)",
                (order_id, "EIN confirmation document has been captured and added to your document vault."),
            )

    if not event_exists(conn, order_id, "ein_received", evidence_path):
        changes.append("filing_event")
        if not dry_run:
            job_id = latest_formation_job_id(conn, order_id)
            if job_id:
                conn.execute(
                    """
                    INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
                    VALUES (?, ?, 'ein_received', ?, 'system', ?)
                    """,
                    (job_id, order_id, "EIN confirmation document captured.", evidence_path),
                )

    if ein and not order["ein"]:
        changes.append("order_ein")
        if not dry_run:
            conn.execute(
                "UPDATE orders SET ein = ?, status = 'ein_received', updated_at = datetime('now') WHERE id = ?",
                (ein, order_id),
            )
    elif not dry_run and changes and order["status"] != "complete":
        conn.execute(
            "UPDATE orders SET status = 'ein_received', updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )

    if not dry_run:
        conn.commit()

    return {
        "order_id": order_id,
        "business_name": order["business_name"],
        "status": "ingested" if changes else "already_ingested",
        "document": evidence_path,
        "changes": changes,
        "ein_detected": bool(ein),
    }


def ingest_completed_ein_documents(limit: int = 50, dry_run: bool = False) -> list[dict]:
    conn = get_db()
    try:
        required = ("orders", "documents", "status_updates", "filing_events", "filing_jobs")
        missing = [table for table in required if not table_exists(conn, table)]
        if missing:
            return [{"status": "database_not_initialized", "missing_tables": missing}]

        orders = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE status IN ('state_approved', 'ein_pending', 'ein_queued', 'ein_received')
              AND approved_at IS NOT NULL
            ORDER BY approved_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [ingest_order(conn, order, dry_run=dry_run) for order in orders]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest captured EIN confirmation documents into customer portals.")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = ingest_completed_ein_documents(limit=args.limit, dry_run=args.dry_run)
    print(json.dumps({"count": len(results), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
