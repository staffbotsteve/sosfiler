"""
Queue EIN filing work after state approval.

This script is intended to be run from cron. It is intentionally conservative:
it does not submit anything to the IRS and does not email customers. It creates
an internal EIN queue artifact for orders that have state approval and no EIN.
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sosfiler.db"
DOCS_DIR = BASE_DIR / "generated_docs"

EIN_PENDING_STATUSES = ("ein_pending", "ein_queued", "ein_received")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def orders_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orders'"
    ).fetchone()
    return row is not None


def already_queued(conn: sqlite3.Connection, order_id: str) -> bool:
    placeholders = ",".join("?" for _ in EIN_PENDING_STATUSES)
    row = conn.execute(
        f"""
        SELECT 1 FROM status_updates
        WHERE order_id = ? AND status IN ({placeholders})
        LIMIT 1
        """,
        (order_id, *EIN_PENDING_STATUSES),
    ).fetchone()
    queue_path = DOCS_DIR / order_id / "ein_queue.json"
    return row is not None or queue_path.exists()


def approval_evidence_exists(conn: sqlite3.Connection, order_id: str) -> bool:
    """Require captured state approval evidence before EIN work is queued."""
    artifact = conn.execute(
        """
        SELECT 1
        FROM filing_artifacts
        WHERE order_id = ?
          AND artifact_type = 'approved_certificate'
          AND is_evidence = 1
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    if artifact:
        return True

    document = conn.execute(
        """
        SELECT 1
        FROM documents
        WHERE order_id = ?
          AND doc_type = 'approved_certificate'
        LIMIT 1
        """,
        (order_id,),
    ).fetchone()
    return document is not None


def prepare_ss4(data: dict) -> dict:
    members = data.get("members", [])
    responsible = next(
        (m for m in members if m.get("is_responsible_party")),
        members[0] if members else {},
    )

    return {
        "entity_name": data.get("business_name", ""),
        "entity_type": data.get("entity_type", "LLC"),
        "state": data.get("state", ""),
        "num_members": len(members),
        "responsible_party": {
            "name": responsible.get("name", ""),
            "ssn": "",
            "ssn_vault_id": data.get("responsible_party_ssn_vault_id", ""),
            "ssn_last4": data.get("responsible_party_ssn_last4") or responsible.get("ssn_last4", ""),
            "address": responsible.get("address", ""),
            "city": responsible.get("city", ""),
            "state": responsible.get("state", ""),
            "zip": responsible.get("zip_code", ""),
        },
        "business_address": {
            "street": data.get("principal_address", ""),
            "city": data.get("principal_city", ""),
            "state": data.get("principal_state", ""),
            "zip": data.get("principal_zip", ""),
        },
        "purpose": data.get("purpose", "Any lawful purpose"),
        "fiscal_year_end": data.get("fiscal_year_end", "December"),
        "date_started": datetime.now().strftime("%m/%d/%Y"),
    }


def write_ein_queue(order_id: str, ss4_data: dict) -> str:
    queue_path = DOCS_DIR / order_id / "ein_queue.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_data = {
        "order_id": order_id,
        "ss4_data": ss4_data,
        "queued_at": datetime.utcnow().isoformat(),
        "status": "queued",
        "notes": "Apply via https://sa.www4.irs.gov/modiein/individual/index.jsp (Mon-Fri 7am-10pm ET)",
    }
    queue_path.write_text(json.dumps(queue_data, indent=2))
    return str(queue_path)


def queue_order(conn: sqlite3.Connection, order: sqlite3.Row, dry_run: bool = False) -> dict:
    formation_data = json.loads(order["formation_data"] or "{}")
    formation_data.setdefault("business_name", order["business_name"])
    formation_data.setdefault("entity_type", order["entity_type"])
    formation_data.setdefault("state", order["state"])

    if dry_run:
        return {
            "order_id": order["id"],
            "business_name": order["business_name"],
            "status": "would_queue",
        }

    ss4_data = prepare_ss4(formation_data)
    queue_file = write_ein_queue(order["id"], ss4_data)

    conn.execute(
        "INSERT INTO status_updates (order_id, status, message) VALUES (?, 'ein_pending', ?)",
        (
            order["id"],
            "State approval recorded. EIN application queued for operator processing.",
        ),
    )
    conn.execute(
        """
        INSERT INTO filing_events (filing_job_id, order_id, event_type, message, actor, evidence_path)
        SELECT id, order_id, 'ein_queued', ?, 'system', ?
        FROM filing_jobs
        WHERE order_id = ? AND action_type = 'formation'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (
            "State approval triggered EIN queue.",
            queue_file,
            order["id"],
        ),
    )
    conn.commit()

    return {
        "order_id": order["id"],
        "business_name": order["business_name"],
        "status": "queued",
        "queue_file": queue_file,
    }


def queue_ready_orders(limit: int = 25, dry_run: bool = False) -> list[dict]:
    conn = get_db()
    try:
        if not orders_table_exists(conn):
            return [{
                "status": "database_not_initialized",
                "database": str(DB_PATH),
            }]

        orders = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE status = 'state_approved'
              AND approved_at IS NOT NULL
              AND (ein IS NULL OR ein = '')
            ORDER BY approved_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        results = []
        for order in orders:
            if not approval_evidence_exists(conn, order["id"]):
                results.append({
                    "order_id": order["id"],
                    "business_name": order["business_name"],
                    "status": "missing_approval_evidence",
                })
                continue
            if already_queued(conn, order["id"]):
                results.append({
                    "order_id": order["id"],
                    "business_name": order["business_name"],
                    "status": "already_queued",
                })
                continue
            results.append(queue_order(conn, order, dry_run=dry_run))
        return results
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Queue EIN work for approved formation orders.")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = queue_ready_orders(limit=args.limit, dry_run=args.dry_run)
    print(json.dumps({"count": len(results), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
