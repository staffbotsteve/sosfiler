"""JSON-backed storage for regulatory research records and run artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Any

from .paths import regulatory_data_dir
from .schemas import FilingRecord, ReminderRule, utc_now


DATA_DIR = regulatory_data_dir()
FILINGS_PATH = DATA_DIR / "filings.json"
SNAPSHOT_DIR = DATA_DIR / "source_snapshots"
RUN_DIR = DATA_DIR / "research_runs"
FILINGS_LOCK_PATH = DATA_DIR / ".filings.lock"


@contextmanager
def filings_lock():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with FILINGS_LOCK_PATH.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_filing_records(path: Path = FILINGS_PATH) -> list[dict[str, Any]]:
    payload = load_json(path, {"version": 1, "records": []})
    return payload.get("records", [])


def upsert_filing_records(records: list[FilingRecord], path: Path = FILINGS_PATH) -> dict[str, Any]:
    with filings_lock():
        payload = load_json(path, {"version": 1, "records": []})
        existing = {record.get("filing_id"): record for record in payload.get("records", [])}
        upserted: list[str] = []
        skipped: list[str] = []
        verification_rank = {
            "discovered": 0,
            "extracted": 1,
            "needs_operator_review": 2,
            "portal_observed": 3,
            "verified_official": 4,
            "stale": -1,
        }
        price_rank = {
            "unknown": 0,
            "needs_operator_quote": 1,
            "range_only": 2,
            "formula_verified": 3,
            "verified": 4,
        }
        for record in records:
            record_dict = record.to_dict()
            current = existing.get(record.filing_id)
            if current:
                current_score = (
                    verification_rank.get(str(current.get("verification_status", "")), 0),
                    price_rank.get(str(current.get("price_confidence", "")), 0),
                    len(current.get("source_citations") or []),
                )
                next_score = (
                    verification_rank.get(str(record_dict.get("verification_status", "")), 0),
                    price_rank.get(str(record_dict.get("price_confidence", "")), 0),
                    len(record_dict.get("source_citations") or []),
                )
                if current_score > next_score:
                    skipped.append(record.filing_id)
                    continue
            existing[record.filing_id] = record_dict
            upserted.append(record.filing_id)
        payload["records"] = sorted(existing.values(), key=lambda item: item.get("filing_id", ""))
        payload["updated_at"] = utc_now()
        write_json(path, payload)
        return {"upserted": upserted, "skipped": skipped, "record_count": len(payload["records"])}


def replace_batch_filing_records(
    batch_id: str,
    records: list[FilingRecord],
    path: Path = FILINGS_PATH,
) -> dict[str, Any]:
    with filings_lock():
        payload = load_json(path, {"version": 1, "records": []})
        prefix = f"{batch_id}_"
        retained = [
            record for record in payload.get("records", [])
            if not str(record.get("filing_id", "")).startswith(prefix)
        ]
        by_id = {record.get("filing_id"): record for record in retained}
        for record in records:
            by_id[record.filing_id] = record.to_dict()
        payload["records"] = sorted(by_id.values(), key=lambda item: item.get("filing_id", ""))
        payload["updated_at"] = utc_now()
        write_json(path, payload)
        return {
            "replaced_batch": batch_id,
            "upserted": [record.filing_id for record in records],
            "record_count": len(payload["records"]),
        }


def snapshot_id(batch_id: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return f"{batch_id}-{digest}"


def write_source_snapshot(batch_id: str, payload: dict[str, Any]) -> Path:
    sid = snapshot_id(batch_id, payload)
    path = SNAPSHOT_DIR / f"{sid}.json"
    write_json(path, {"batch_id": batch_id, "captured_at": utc_now(), "payload": payload})
    return path


def write_run_artifact(run: dict[str, Any]) -> Path:
    run_id = run["run_id"]
    path = RUN_DIR / f"{run_id}.json"
    write_json(path, run)
    return path


def backfill_reminder_rules(path: Path = FILINGS_PATH) -> dict[str, Any]:
    payload = load_json(path, {"version": 1, "records": []})
    updated: list[str] = []
    recurring_categories = {
        "annual_obligation",
        "renewal",
        "beneficial_ownership_report",
        "trademark",
        "patent",
    }
    for record in payload.get("records", []):
        if record.get("reminder_rules"):
            continue
        text = " ".join(
            str(record.get(field, ""))
            for field in ["filing_name", "deadline_rule"]
        ).lower()
        recurring = record.get("filing_category") in recurring_categories or any(
            term in text
            for term in ["annual", "biennial", "renewal", "renews", "maintenance fee", "maintenance filing"]
        )
        if not recurring:
            continue
        record["reminder_rules"] = [
            ReminderRule(trigger="before_due_date", offset_days=offset, message_key="regulatory_filing_due").to_dict()
            for offset in [90, 60, 30, 7]
        ]
        updated.append(record.get("filing_id", ""))
    if updated:
        payload["updated_at"] = utc_now()
        write_json(path, payload)
    return {"updated": updated, "updated_count": len(updated), "record_count": len(payload.get("records", []))}


def prune_unsupported_reminder_rules(path: Path = FILINGS_PATH) -> dict[str, Any]:
    payload = load_json(path, {"version": 1, "records": []})
    pruned: list[str] = []
    recurring_categories = {
        "annual_obligation",
        "renewal",
        "beneficial_ownership_report",
        "trademark",
        "patent",
    }
    for record in payload.get("records", []):
        if not record.get("reminder_rules"):
            continue
        text = " ".join(
            str(record.get(field, ""))
            for field in ["filing_name", "deadline_rule"]
        ).lower()
        recurring = record.get("filing_category") in recurring_categories or any(
            term in text
            for term in ["annual", "biennial", "renewal", "renews", "maintenance fee", "maintenance filing"]
        )
        if recurring:
            continue
        record["reminder_rules"] = []
        pruned.append(record.get("filing_id", ""))
    if pruned:
        payload["updated_at"] = utc_now()
        write_json(path, payload)
    return {"pruned": pruned, "pruned_count": len(pruned), "record_count": len(payload.get("records", []))}


def _normalized_filing_name(record: dict[str, Any]) -> str:
    name = str(record.get("filing_name", ""))
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    aliases = {
        "assumed name certificate dba": "assumed name certificate",
        "assumed name dba filing": "assumed name filing",
    }
    if "assumed" in name and ("name" in name or "business" in name):
        return "assumed name filing"
    if "alcohol" in name and "permit" in name:
        return "alcohol permit"
    return aliases.get(name, name)


def _normalized_place(record: dict[str, Any]) -> str:
    value = str(record.get("authority") or record.get("jurisdiction_name") or "")
    value = re.sub(r"[^a-z0-9]+", " ", value.lower())
    stopwords = {
        "clerk",
        "county",
        "city",
        "of",
        "special",
        "collections",
        "department",
        "development",
        "services",
        "permitting",
        "center",
        "environmental",
        "consumer",
        "health",
        "division",
    }
    tokens = [token for token in value.split() if token and token not in stopwords]
    return " ".join(tokens)


def _normalized_local_record_keys(record: dict[str, Any]) -> list[tuple[str, str]]:
    if record.get("jurisdiction_level") not in {"county", "city"}:
        return []
    keys: list[tuple[str, str]] = []
    name = _normalized_filing_name(record)
    place = _normalized_place(record)
    if place and name:
        keys.append((f"place:{place}", name))
    citations = record.get("source_citations") or []
    citation_url = ""
    for citation in citations:
        if isinstance(citation, dict) and citation.get("url"):
            citation_url = str(citation["url"]).rstrip("/")
            break
    if citation_url and name:
        keys.append((f"url:{citation_url}", name))
    return keys


def prune_superseded_local_review_records(path: Path = FILINGS_PATH) -> dict[str, Any]:
    """Remove weaker local review records when a verified replacement exists.

    Authority-manifest reruns intentionally create better authority-specific IDs
    rather than overwriting older generic model output. This cleanup removes the
    stale local records that point to the same official source and same filing.
    """

    payload = load_json(path, {"version": 1, "records": []})
    records = payload.get("records", [])
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        for key in _normalized_local_record_keys(record):
            by_key.setdefault(key, []).append(record)
    remove_ids: set[str] = set()
    for group in by_key.values():
        if not any(record.get("verification_status") == "verified_official" for record in group):
            continue
        for record in group:
            if record.get("verification_status") != "verified_official":
                remove_ids.add(str(record.get("filing_id", "")))
    if not remove_ids:
        return {"removed": [], "removed_count": 0, "record_count": len(records)}
    payload["records"] = [
        record for record in records
        if str(record.get("filing_id", "")) not in remove_ids
    ]
    payload["updated_at"] = utc_now()
    write_json(path, payload)
    return {
        "removed": sorted(remove_ids),
        "removed_count": len(remove_ids),
        "record_count": len(payload["records"]),
    }


def backfill_missing_output_documents(path: Path = FILINGS_PATH) -> dict[str, Any]:
    payload = load_json(path, {"version": 1, "records": []})
    updated: list[str] = []
    category_outputs = {
        "formation": ["file-stamped formation document"],
        "foreign_authority": ["file-stamped registration or qualification"],
        "amendment": ["file-stamped amendment"],
        "annual_obligation": ["filed annual, biennial, franchise, or information report receipt"],
        "registered_agent_change": ["file-stamped registered agent change"],
        "address_change": ["file-stamped address change"],
        "dissolution": ["file-stamped dissolution, cancellation, or withdrawal"],
        "reinstatement": ["file-stamped reinstatement"],
        "certificate_copy": ["issued certificate or certified copy"],
        "trademark": ["state trademark registration confirmation"],
    }
    for record in payload.get("records", []):
        if record.get("output_documents"):
            continue
        category = str(record.get("filing_category", ""))
        record["output_documents"] = category_outputs.get(category, ["file-stamped filing confirmation or official receipt"])
        updated.append(str(record.get("filing_id", "")))
    if updated:
        payload["updated_at"] = utc_now()
        write_json(path, payload)
    return {"updated": updated, "updated_count": len(updated), "record_count": len(payload.get("records", []))}
