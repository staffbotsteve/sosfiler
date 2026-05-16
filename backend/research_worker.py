"""
SOSFiler research worker.

Turns the 50-state and local-jurisdiction research queues into explicit,
auditable jobs. The worker is intentionally source-first: it can inventory
jobs, mark work in progress, record official-source evidence, and produce a
coverage report without relying on NotebookLM as the driver.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_QUEUE_PATH = DATA_DIR / "state_action_research_queue.json"
LOCAL_QUEUE_PATH = DATA_DIR / "local_jurisdiction_research_queue.json"
JOB_QUEUE_PATH = DATA_DIR / "research_jobs.json"
SOURCE_SEEDS_PATH = DATA_DIR / "research_source_seeds.json"
RUN_DIR = DATA_DIR / "research_runs"
STATUS_DOC_PATH = BASE_DIR / "docs" / "research_status.md"
STATE_CONTROLS_PATH = DATA_DIR / "research_state_controls.json"
BLOCKER_LOG_PATH = DATA_DIR / "research_blockers.jsonl"
QUEUE_LOCK_PATH = DATA_DIR / "research_jobs.lock"

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in_progress"
STATUS_BLOCKED = "blocked"
STATUS_READY_FOR_REVIEW = "ready_for_review"
STATUS_VERIFIED = "verified"

JOB_STATUSES = {
    STATUS_PENDING,
    STATUS_IN_PROGRESS,
    STATUS_BLOCKED,
    STATUS_READY_FOR_REVIEW,
    STATUS_VERIFIED,
}

TASK_TYPES = {
    "state_filing_map",
    "state_tax_gateway",
    "county_directory",
    "municipality_directory",
    "county_local_filings",
    "city_local_filings",
    "productization",
}

NON_EXTRACT_TASK_TYPES = {"productization"}

MUST_HAVE_SERVICE_AREAS = [
    "Name reservation (where offered/required) with exact fee, validity, and prerequisites.",
    "Foreign qualification/authority registration and withdrawal paths with exact fees and processing options.",
    "Amendments/changes (entity name, registered agent, principal office, officers/managers/members where state-filed).",
    "Reinstatement/reactivation paths after lapse/dissolution, including penalties and cure steps.",
    "Dissolution/termination/withdrawal filings with exact fees and evidence requirements.",
    "Certificates of Good Standing/Status and certified copies with order channels, fees, and delivery methods.",
    "Apostille/authentication options tied to business records and official issuing authority.",
    "BOI reporting obligations and state guidance touchpoints (including state reminders/gates where present).",
    "Payroll tax registration and sales/use tax permit registration gateways and dependencies.",
    "Registered agent appointment/change dependencies and required state forms or portal steps.",
]

PRICING_REQUIREMENTS = [
    "For every filing/service lane researched, capture the exact official government cost (including required add-ons, processing, expedite, and recurring components where applicable).",
    "Compute and record SOSFiler list price as official cost + $9.00, with each component shown explicitly.",
]

NV_WAF_MARKERS = (
    "request unsuccessful",
    "incapsula incident id",
)
NV_WAF_HIT_THRESHOLD = 2
NV_COOLDOWN_MINUTES = 30
GENERIC_BLOCKED_STREAK_THRESHOLD = 2
GENERIC_COOLDOWN_MINUTES = 20
CLAIM_LEASE_SECONDS = 20 * 60


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    last_error: json.JSONDecodeError | None = None
    for _ in range(3):
        try:
            with path.open() as f:
                return json.load(f)
        except json.JSONDecodeError as exc:
            last_error = exc
            time.sleep(0.05)
    if path.stat().st_size == 0:
        return default
    raise last_error


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp_path.replace(path)


@contextlib.contextmanager
def queue_lock():
    QUEUE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_LOCK_PATH.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def parse_utc(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_claim_active(job: dict[str, Any], now: datetime) -> bool:
    owner = job.get("claim_owner")
    expires = parse_utc(job.get("claim_expires_at", ""))
    if not owner or not expires:
        return False
    return now < expires


def clear_expired_claims(queue: dict[str, Any], now: datetime) -> None:
    for job in queue.get("jobs", []):
        if not job.get("claim_owner"):
            continue
        expires = parse_utc(job.get("claim_expires_at", ""))
        if not expires or now >= expires:
            job.pop("claim_owner", None)
            job.pop("claim_expires_at", None)


def load_state_controls() -> dict[str, Any]:
    return load_json(STATE_CONTROLS_PATH, {"states": {}})


def save_state_controls(controls: dict[str, Any]) -> None:
    write_json(STATE_CONTROLS_PATH, controls)


def state_on_cooldown(controls: dict[str, Any], state: str) -> tuple[bool, str]:
    state_cfg = controls.get("states", {}).get(state.upper(), {})
    until = state_cfg.get("cooldown_until", "")
    if not until:
        return (False, "")
    until_dt = parse_utc(until)
    if not until_dt:
        return (False, "")
    now = datetime.now(timezone.utc)
    if now < until_dt:
        return (True, until)
    return (False, "")


def set_state_cooldown(controls: dict[str, Any], state: str, minutes: int, reason: str) -> None:
    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    state_cfg = controls.setdefault("states", {}).setdefault(state.upper(), {})
    state_cfg["cooldown_until"] = until.replace(microsecond=0).isoformat()
    state_cfg["last_reason"] = reason
    state_cfg["updated_at"] = utc_now()


def append_blocker_log(entry: dict[str, Any]) -> None:
    BLOCKER_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BLOCKER_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def classify_blocker(job_run: dict[str, Any]) -> str:
    if job_run.get("block_reason") == "waf_blocked":
        return "waf_blocked"
    errors = [str(result.get("error", "")).lower() for result in job_run.get("seed_results", []) if result.get("status") == "error"]
    if any("timed out" in err for err in errors):
        return "timeout"
    if any("403" in err or "forbidden" in err for err in errors):
        return "http_403"
    if any("ssl" in err or "certificate" in err or "tls" in err for err in errors):
        return "tls_or_cert"
    if errors:
        return "source_error"
    return "no_reachable_sources"


def render_status_document(queue: dict[str, Any]) -> str:
    jobs = queue.get("jobs", [])
    states = sorted({job.get("state", "") for job in jobs if job.get("state")})
    by_state: dict[str, dict[str, Any]] = {}
    for state in states:
        state_jobs = [j for j in jobs if j.get("state") == state]
        def _status(task_type: str) -> str:
            matches = [j for j in state_jobs if j.get("task_type") == task_type]
            if not matches:
                return "not_applicable"
            if any(j.get("status") == STATUS_VERIFIED for j in matches):
                return STATUS_VERIFIED
            if any(j.get("status") == STATUS_READY_FOR_REVIEW for j in matches):
                return STATUS_READY_FOR_REVIEW
            if any(j.get("status") == STATUS_IN_PROGRESS for j in matches):
                return STATUS_IN_PROGRESS
            if any(j.get("status") == STATUS_BLOCKED for j in matches):
                return STATUS_BLOCKED
            return STATUS_PENDING
        state_status = _status("state_filing_map")
        county_status = _status("county_local_filings")
        city_status = _status("city_local_filings")
        county_directory_status = _status("county_directory")
        municipality_directory_status = _status("municipality_directory")
        tax_status = _status("state_tax_gateway")
        product_status = _status("productization")
        by_state[state] = {
            "state_status": state_status,
            "state_tax_gateway_status": tax_status,
            "county_directory_status": county_directory_status,
            "county_status": county_status,
            "municipality_directory_status": municipality_directory_status,
            "city_status": city_status,
            "productization_status": product_status,
            "job_count": len(state_jobs),
        }

    counts = {s: 0 for s in [STATUS_PENDING, STATUS_IN_PROGRESS, STATUS_BLOCKED, STATUS_READY_FOR_REVIEW, STATUS_VERIFIED]}
    for job in jobs:
        counts[job.get("status", STATUS_PENDING)] = counts.get(job.get("status", STATUS_PENDING), 0) + 1

    lines = [
        "# SOSFiler Research Status",
        "",
        f"Last updated: {utc_now()}",
        "",
        "## Queue Summary",
        "",
        f"- Total jobs: {len(jobs)}",
        f"- pending: {counts.get(STATUS_PENDING, 0)}",
        f"- in_progress: {counts.get(STATUS_IN_PROGRESS, 0)}",
        f"- blocked: {counts.get(STATUS_BLOCKED, 0)}",
        f"- ready_for_review: {counts.get(STATUS_READY_FOR_REVIEW, 0)}",
        f"- verified: {counts.get(STATUS_VERIFIED, 0)}",
        "",
        "## Status by State / County / City",
        "",
        "| State | State Status | State Tax | County Directory | County Status | Municipality Directory | City Status | Productization | Jobs |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for state in states:
        row = by_state[state]
        lines.append(
            f"| {state} | {row['state_status']} | {row['state_tax_gateway_status']} | "
            f"{row['county_directory_status']} | {row['county_status']} | "
            f"{row['municipality_directory_status']} | {row['city_status']} | "
            f"{row['productization_status']} | {row['job_count']} |"
        )
    lines.append("")
    lines.append("Status legend: pending, in_progress, blocked, ready_for_review, verified.")
    return "\n".join(lines) + "\n"


def write_status_document(queue: dict[str, Any]) -> None:
    STATUS_DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_DOC_PATH.write_text(render_status_document(queue))


def slug(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def job_id(state: str, task_type: str, scope: str = "") -> str:
    parts = [state.upper(), task_type]
    if scope:
        parts.append(scope)
    return "-".join(slug(part) for part in parts)


@dataclass
class ResearchJob:
    id: str
    state: str
    task_type: str
    priority: int
    title: str
    scope: str
    source_queue: str
    status: str = STATUS_PENDING
    requirements: list[str] = field(default_factory=list)
    seed_urls: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "task_type": self.task_type,
            "priority": self.priority,
            "title": self.title,
            "scope": self.scope,
            "source_queue": self.source_queue,
            "status": self.status,
            "requirements": self.requirements,
            "seed_urls": self.seed_urls,
            "evidence": self.evidence,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def seed_urls_for(seeds: dict[str, Any], state: str, task_type: str, scope: str = "") -> list[str]:
    state_seeds = seeds.get("states", {}).get(state.upper(), {})
    urls: list[str] = []
    urls.extend(state_seeds.get("all", []))
    urls.extend(state_seeds.get(task_type, []))
    if scope:
        urls.extend(state_seeds.get(scope, []))
    return sorted(set(urls))


def build_jobs() -> list[dict[str, Any]]:
    state_queue = load_json(STATE_QUEUE_PATH, {})
    local_queue = load_json(LOCAL_QUEUE_PATH, {})
    seeds = load_json(SOURCE_SEEDS_PATH, {})
    completion_requirements = state_queue.get("state_completion_requirements", [])
    jobs: list[ResearchJob] = []

    for state, state_data in state_queue.get("states", {}).items():
        priority = int(state_data.get("priority", 999))
        next_actions = state_data.get("next_actions", [])
        jobs.append(
            ResearchJob(
                id=job_id(state, "state_filing_map"),
                state=state,
                task_type="state_filing_map",
                priority=priority,
                title=f"{state} state business filing map",
                scope="state",
                source_queue="state_action_research_queue",
                requirements=completion_requirements + MUST_HAVE_SERVICE_AREAS + PRICING_REQUIREMENTS + next_actions,
                seed_urls=seed_urls_for(seeds, state, "state_filing_map"),
            )
        )
        jobs.append(
            ResearchJob(
                id=job_id(state, "state_tax_gateway"),
                state=state,
                task_type="state_tax_gateway",
                priority=priority + 100,
                title=f"{state} state tax/business gateway map",
                scope="state",
                source_queue="state_action_research_queue",
                requirements=[
                    "Map general state business license, tax registration, franchise/public information report, annual report, and portal dependencies.",
                    "Capture exact fees, recurring due dates, processing fees, and evidence gates from official sources.",
                    "Capture official filing cost inputs needed to price each lane as cost + $9.00.",
                ],
                seed_urls=seed_urls_for(seeds, state, "state_tax_gateway"),
            )
        )
        jobs.append(
            ResearchJob(
                id=job_id(state, "productization"),
                state=state,
                task_type="productization",
                priority=priority + 400,
                title=f"{state} productization and operator flow",
                scope="state",
                source_queue="state_action_research_queue",
                requirements=[
                    "Convert verified filing maps into checkout fields, pricing, operator steps, document outputs, status gates, and customer portal items.",
                    "Enforce pricing rule for researched lanes: customer price = official cost + $9.00.",
                    "Keep unsupported or unverified filings operator-assisted until evidence is complete.",
                ],
                seed_urls=[],
            )
        )

    local_requirements = local_queue.get("coverage_rules", [])
    for shard in local_queue.get("priority_shards", []):
        state = shard["state"]
        scope = shard.get("scope", "local")
        base_priority = int(state_queue.get("states", {}).get(state, {}).get("priority", 999)) + 200

        jobs.append(
            ResearchJob(
                id=job_id(state, "county_directory", scope),
                state=state,
                task_type="county_directory",
                priority=base_priority,
                title=f"{state} official county directory",
                scope=scope,
                source_queue="local_jurisdiction_research_queue",
                requirements=[
                    "Enumerate all official counties or county-equivalent jurisdictions from an official source.",
                    "Store the official directory URL and date checked.",
                ],
                seed_urls=seed_urls_for(seeds, state, "county_directory", scope),
            )
        )
        jobs.append(
            ResearchJob(
                id=job_id(state, "municipality_directory", scope),
                state=state,
                task_type="municipality_directory",
                priority=base_priority + 1,
                title=f"{state} official municipality directory",
                scope=scope,
                source_queue="local_jurisdiction_research_queue",
                requirements=[
                    "Enumerate official municipalities/cities/towns from an official source.",
                    "Store the official directory URL and date checked.",
                ],
                seed_urls=seed_urls_for(seeds, state, "municipality_directory", scope),
            )
        )
        local_task_type = "county_local_filings" if scope == "county" else "city_local_filings"
        if scope in {"city_and_county", "state_city_county"}:
            local_task_type = "county_local_filings"
        jobs.append(
            ResearchJob(
                id=job_id(state, local_task_type, shard["id"]),
                state=state,
                task_type=local_task_type,
                priority=base_priority + 2,
                title=shard["topic"],
                scope=scope,
                source_queue="local_jurisdiction_research_queue",
                requirements=local_requirements + [shard["topic"]],
                seed_urls=seed_urls_for(seeds, state, local_task_type, scope),
                notes=[f"Original shard status: {shard.get('status', 'not_started')}"],
            )
        )
        if scope in {"city_and_county", "state_city_county"}:
            jobs.append(
                ResearchJob(
                    id=job_id(state, "city_local_filings", shard["id"]),
                    state=state,
                    task_type="city_local_filings",
                    priority=base_priority + 3,
                    title=shard["topic"],
                    scope=scope,
                    source_queue="local_jurisdiction_research_queue",
                    requirements=local_requirements + [shard["topic"]],
                    seed_urls=seed_urls_for(seeds, state, "city_local_filings", scope),
                    notes=[f"Original shard status: {shard.get('status', 'not_started')}"],
                )
            )

    deduped: dict[str, ResearchJob] = {}
    for job in jobs:
        deduped[job.id] = job
    return [job.as_dict() for job in sorted(deduped.values(), key=lambda item: (item.priority, item.id))]


def merge_existing_jobs(new_jobs: list[dict[str, Any]], existing_jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_id = {job["id"]: job for job in existing_jobs}
    merged: list[dict[str, Any]] = []
    for new_job in new_jobs:
        existing = existing_by_id.get(new_job["id"])
        if existing:
            preserved = {
                "status": existing.get("status", STATUS_PENDING),
                "evidence": existing.get("evidence", []),
                "notes": existing.get("notes", []),
                "created_at": existing.get("created_at", new_job["created_at"]),
                "updated_at": existing.get("updated_at", new_job["updated_at"]),
            }
            new_job.update(preserved)
        merged.append(new_job)
    return merged


def init_queue(force: bool = False) -> dict[str, Any]:
    new_jobs = build_jobs()
    existing = load_json(JOB_QUEUE_PATH, {})
    existing_jobs = existing.get("jobs", [])
    jobs = new_jobs if force else merge_existing_jobs(new_jobs, existing_jobs)
    queue = {
        "version": 1,
        "generated_at": utc_now(),
        "source_files": [
            str(STATE_QUEUE_PATH.relative_to(BASE_DIR)),
            str(LOCAL_QUEUE_PATH.relative_to(BASE_DIR)),
            str(SOURCE_SEEDS_PATH.relative_to(BASE_DIR)),
        ],
        "status_order": [
            STATUS_PENDING,
            STATUS_IN_PROGRESS,
            STATUS_BLOCKED,
            STATUS_READY_FOR_REVIEW,
            STATUS_VERIFIED,
        ],
        "jobs": jobs,
    }
    write_json(JOB_QUEUE_PATH, queue)
    write_status_document(queue)
    return queue


def load_queue() -> dict[str, Any]:
    if not JOB_QUEUE_PATH.exists():
        return init_queue()
    return load_json(JOB_QUEUE_PATH, {})


def save_queue(queue: dict[str, Any]) -> None:
    queue["generated_at"] = utc_now()
    write_json(JOB_QUEUE_PATH, queue)
    write_status_document(queue)


def select_jobs(queue: dict[str, Any], status: str, limit: int, state: str | None = None) -> list[dict[str, Any]]:
    jobs = queue.get("jobs", [])
    selected = [
        job for job in jobs
        if job.get("status", STATUS_PENDING) == status
        and (state is None or job.get("state") == state.upper())
    ]
    return sorted(selected, key=lambda job: (job.get("priority", 999), job["id"]))[:limit]


def state_prerequisites_ready(jobs: list[dict[str, Any]], state: str) -> bool:
    state_jobs = [
        job for job in jobs
        if job.get("state") == state and job.get("task_type") != "productization"
    ]
    return bool(state_jobs) and all(
        job.get("status") in {STATUS_READY_FOR_REVIEW, STATUS_VERIFIED}
        for job in state_jobs
    )


def select_runnable_jobs(
    queue: dict[str, Any],
    limit: int,
    state: str | None = None,
    retry_blocked: bool = False,
) -> list[dict[str, Any]]:
    """Prefer completable work; blocked jobs are skipped unless explicitly retried."""
    jobs = queue.get("jobs", [])
    active_statuses = {STATUS_PENDING, STATUS_IN_PROGRESS}
    if retry_blocked:
        active_statuses.add(STATUS_BLOCKED)
    eligible = [
        job for job in jobs
        if (state is None or job.get("state") == state.upper())
        and job.get("status", STATUS_PENDING) in active_statuses
        and (
            job.get("task_type") not in NON_EXTRACT_TASK_TYPES
            or (
                job.get("task_type") == "productization"
                and state_prerequisites_ready(jobs, job.get("state", ""))
            )
        )
    ]
    def sort_key(job: dict[str, Any]) -> tuple[int, int, str]:
        status = job.get("status")
        status_rank = 0 if status == STATUS_PENDING else (1 if status == STATUS_IN_PROGRESS else 2)
        return (status_rank, int(job.get("priority", 999)), job["id"])
    return sorted(eligible, key=sort_key)[:limit]


def _clean_text(value: str) -> str:
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_research_signals(text: str, task_type: str) -> dict[str, Any]:
    lower = text.lower()
    money = sorted(set(re.findall(r"\$\s?\d[\d,]*(?:\.\d{2})?", text)))[:25]
    keywords = [
        "fee", "filing", "form", "certificate", "good standing", "certified copy",
        "apostille", "annual report", "amendment", "dissolution", "reinstatement",
        "foreign qualification", "registered agent", "assumed name", "dba",
        "business license", "tax registration", "sales tax", "payroll",
        "county", "city", "municipal",
    ]
    found = sorted({k for k in keywords if k in lower})
    # Lightweight heuristic: enough structure to hand off for final review.
    ready = len(found) >= 3 and ("fee" in found or bool(money))
    if task_type in {"county_directory", "municipality_directory"}:
        ready = len(found) >= 2
    return {
        "money_mentions": money,
        "keyword_hits": found,
        "ready_signal": ready,
    }


def fetch_url_summary(url: str, timeout: int = 15) -> dict[str, Any]:
    started = utc_now()
    req = Request(url, headers={"User-Agent": "SOSFilerResearchBot/0.1 (+https://sosfiler.com)"})
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read(200_000)
            content_type = response.headers.get("content-type", "")
            text = body.decode("utf-8", errors="ignore")
            clean_text = _clean_text(text)
            title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
            title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
            return {
                "url": url,
                "checked_at": started,
                "status": "reachable",
                "http_status": response.status,
                "content_type": content_type,
                "title": title,
                "sample_chars": len(text),
                "text_sample": clean_text[:4000],
            }
    except (OSError, URLError) as exc:
        return {
            "url": url,
            "checked_at": started,
            "status": "error",
            "error": str(exc),
        }


def run_jobs(
    limit: int,
    state: str | None = None,
    dry_run: bool = False,
    retry_blocked: bool = False,
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())
    queue = load_queue()
    controls = load_state_controls()
    jobs: list[dict[str, Any]] = []
    run = {
        "id": run_id,
        "started_at": utc_now(),
        "dry_run": dry_run,
        "jobs": [],
        "skipped": [],
    }
    if not dry_run:
        with queue_lock():
            queue = load_queue()
            controls = load_state_controls()
            now = datetime.now(timezone.utc)
            clear_expired_claims(queue, now)
            candidates = select_runnable_jobs(
                queue,
                max(limit * 6, limit),
                state=state,
                retry_blocked=retry_blocked,
            )
            claim_until = (now + timedelta(seconds=CLAIM_LEASE_SECONDS)).replace(microsecond=0).isoformat()
            for candidate in candidates:
                on_cooldown, until = state_on_cooldown(controls, candidate.get("state", ""))
                if on_cooldown:
                    run["skipped"].append(
                        {
                            "job_id": candidate.get("id"),
                            "state": candidate.get("state"),
                            "reason": "state_cooldown_active",
                            "cooldown_until": until,
                        }
                    )
                    continue
                if is_claim_active(candidate, now):
                    run["skipped"].append(
                        {
                            "job_id": candidate.get("id"),
                            "state": candidate.get("state"),
                            "reason": "already_claimed",
                            "claim_owner": candidate.get("claim_owner"),
                            "claim_expires_at": candidate.get("claim_expires_at"),
                        }
                    )
                    continue
                candidate["claim_owner"] = run_id
                candidate["claim_expires_at"] = claim_until
                jobs.append(json.loads(json.dumps(candidate)))
                if len(jobs) >= limit:
                    break
            save_queue(queue)
            save_state_controls(controls)
    else:
        candidates = select_runnable_jobs(
            queue,
            max(limit * 6, limit),
            state=state,
            retry_blocked=retry_blocked,
        )
        for candidate in candidates:
            on_cooldown, until = state_on_cooldown(controls, candidate.get("state", ""))
            if on_cooldown:
                run["skipped"].append(
                    {
                        "job_id": candidate.get("id"),
                        "state": candidate.get("state"),
                        "reason": "state_cooldown_active",
                        "cooldown_until": until,
                    }
                )
                continue
            jobs.append(candidate)
            if len(jobs) >= limit:
                break

    for job in jobs:
        job_run = {
            "job_id": job["id"],
            "state": job["state"],
            "task_type": job["task_type"],
            "title": job["title"],
            "seed_results": [],
        }
        if dry_run:
            job_run["next_status"] = STATUS_IN_PROGRESS
        else:
            if job.get("task_type") == "productization":
                job["status"] = STATUS_READY_FOR_REVIEW
                job["updated_at"] = utc_now()
                job.setdefault("evidence", []).append(
                    {
                        "type": "productization_prerequisites_ready",
                        "checked_at": utc_now(),
                        "description": "All non-productization research jobs for this state are ready for review or verified.",
                    }
                )
                job.setdefault("notes", []).append(
                    "Prerequisite research is complete; productization/operator flow is ready for review."
                )
                job_run["next_status"] = job["status"]
                run["jobs"].append(job_run)
                continue
            for url in job.get("seed_urls", []):
                job_run["seed_results"].append(fetch_url_summary(url))
            reachable = [result for result in job_run["seed_results"] if result["status"] == "reachable"]
            job["status"] = STATUS_IN_PROGRESS if reachable else STATUS_BLOCKED
            job["updated_at"] = utc_now()
            if job.get("state") == "NV" and reachable:
                waf_hits = 0
                for result in reachable:
                    sample = (result.get("text_sample", "") or "").lower()
                    if all(marker in sample for marker in NV_WAF_MARKERS):
                        waf_hits += 1
                if waf_hits >= NV_WAF_HIT_THRESHOLD:
                    job["status"] = STATUS_BLOCKED
                    job.setdefault("notes", []).append(
                        "NV WAF/Incapsula blocks detected; auto-routed to manual review and cooldown."
                    )
                    set_state_cooldown(
                        controls,
                        "NV",
                        NV_COOLDOWN_MINUTES,
                        "Repeated Incapsula block pages detected on official seeds.",
                    )
                    job_run["next_status"] = STATUS_BLOCKED
                    job_run["block_reason"] = "waf_blocked"
                    job_run["waf_hit_count"] = waf_hits
                    run["jobs"].append(job_run)
                    continue
            if reachable:
                ready_votes = 0
                job.setdefault("evidence", []).extend(
                    {
                        "type": "source_seed_reachable",
                        "url": result["url"],
                        "checked_at": result["checked_at"],
                        "title": result.get("title", ""),
                        "http_status": result.get("http_status"),
                    }
                    for result in reachable
                )
                for result in reachable:
                    signals = extract_research_signals(result.get("text_sample", ""), job["task_type"])
                    if signals["ready_signal"]:
                        ready_votes += 1
                    job.setdefault("evidence", []).append(
                        {
                            "type": "auto_extract_snapshot",
                            "url": result["url"],
                            "checked_at": result["checked_at"],
                            "title": result.get("title", ""),
                            "signals": signals,
                        }
                    )
                if ready_votes > 0:
                    job["status"] = STATUS_READY_FOR_REVIEW
                    job.setdefault("notes", []).append(
                        "Auto extraction captured fee/process signals; queued for reviewer verification."
                    )
                else:
                    job.setdefault("notes", []).append(
                        "Official seed URLs are reachable; auto extraction captured limited signals, continuing passes."
                    )
            else:
                job.setdefault("notes", []).append("No reachable seed URLs yet; add official sources before extraction.")
            job_run["next_status"] = job["status"]
        run["jobs"].append(job_run)
        state_cfg = controls.setdefault("states", {}).setdefault(job.get("state", "").upper(), {})
        if job_run.get("next_status") == STATUS_BLOCKED:
            blocker_type = classify_blocker(job_run)
            job_run["blocker_type"] = blocker_type
            state_cfg["blocked_streak"] = int(state_cfg.get("blocked_streak", 0)) + 1
            state_cfg["last_blocker_type"] = blocker_type
            state_cfg["updated_at"] = utc_now()
            append_blocker_log(
                {
                    "logged_at": utc_now(),
                    "run_id": run["id"],
                    "job_id": job.get("id"),
                    "state": job.get("state"),
                    "task_type": job.get("task_type"),
                    "blocker_type": blocker_type,
                    "seed_urls": [result.get("url") for result in job_run.get("seed_results", [])],
                }
            )
            if (
                job.get("state") != "NV"
                and int(state_cfg.get("blocked_streak", 0)) >= GENERIC_BLOCKED_STREAK_THRESHOLD
            ):
                set_state_cooldown(
                    controls,
                    job.get("state", ""),
                    GENERIC_COOLDOWN_MINUTES,
                    f"Repeated blocked jobs ({blocker_type})",
                )
        else:
            state_cfg["blocked_streak"] = 0
            state_cfg["updated_at"] = utc_now()

    run["finished_at"] = utc_now()
    if not dry_run:
        with queue_lock():
            queue = load_queue()
            controls = load_state_controls()
            by_id = {job["id"]: job for job in queue.get("jobs", [])}
            for job_run in run["jobs"]:
                job_id_value = job_run.get("job_id", "")
                job = by_id.get(job_id_value)
                if not job:
                    continue
                if job.get("claim_owner") != run_id:
                    continue
                source = next((j for j in jobs if j.get("id") == job_id_value), None)
                if source:
                    job["status"] = source.get("status", job.get("status"))
                    job["updated_at"] = source.get("updated_at", job.get("updated_at", utc_now()))
                    if source.get("evidence"):
                        job["evidence"] = source["evidence"]
                    if source.get("notes"):
                        job["notes"] = source["notes"]
                job.pop("claim_owner", None)
                job.pop("claim_expires_at", None)
            # Release any stale claims still owned by this run even if no output row was written.
            for job in queue.get("jobs", []):
                if job.get("claim_owner") == run_id:
                    job.pop("claim_owner", None)
                    job.pop("claim_expires_at", None)
            save_queue(queue)
            save_state_controls(controls)
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        write_json(RUN_DIR / f"{run['id']}.json", run)
    return run


def report(queue: dict[str, Any]) -> dict[str, Any]:
    jobs = queue.get("jobs", [])
    by_status: dict[str, int] = {status: 0 for status in JOB_STATUSES}
    by_state: dict[str, dict[str, int]] = {}
    for job in jobs:
        status = job.get("status", STATUS_PENDING)
        by_status[status] = by_status.get(status, 0) + 1
        state = job.get("state", "??")
        by_state.setdefault(state, {})
        by_state[state][status] = by_state[state].get(status, 0) + 1
    return {
        "job_count": len(jobs),
        "by_status": by_status,
        "by_state": dict(sorted(by_state.items())),
        "next_jobs": select_jobs(queue, STATUS_PENDING, 10),
    }


def run_daemon(
    limit: int,
    interval_seconds: int,
    state: str | None = None,
    max_cycles: int = 0,
    retry_blocked: bool = False,
) -> int:
    cycle = 0
    while True:
        cycle += 1
        started = utc_now()
        run_payload = run_jobs(
            limit=limit,
            state=state,
            dry_run=False,
            retry_blocked=retry_blocked,
        )
        snapshot = report(load_queue())
        print(
            json.dumps(
                {
                    "cycle": cycle,
                    "started_at": started,
                    "finished_at": run_payload.get("finished_at"),
                    "jobs_processed": len(run_payload.get("jobs", [])),
                    "jobs_skipped": len(run_payload.get("skipped", [])),
                    "by_status": snapshot.get("by_status", {}),
                },
                sort_keys=True,
            )
        )
        sys.stdout.flush()
        if max_cycles > 0 and cycle >= max_cycles:
            return 0
        time.sleep(max(1, interval_seconds))


def update_status(job_ids: list[str], status: str, note: str = "") -> dict[str, Any]:
    if status not in JOB_STATUSES:
        raise ValueError(f"Unsupported status {status!r}. Expected one of {sorted(JOB_STATUSES)}")
    queue = load_queue()
    wanted = set(job_ids)
    updated: list[str] = []
    for job in queue.get("jobs", []):
        if job["id"] in wanted:
            job["status"] = status
            job["updated_at"] = utc_now()
            if note:
                job.setdefault("notes", []).append(note)
            updated.append(job["id"])
    save_queue(queue)
    missing = sorted(wanted - set(updated))
    return {"updated": updated, "missing": missing, "status": status}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive SOSFiler state/local filing research jobs.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create or refresh the persistent research job queue.")
    init_parser.add_argument("--force", action="store_true", help="Rebuild jobs and discard existing statuses/evidence.")

    report_parser = subparsers.add_parser("report", help="Show research job coverage.")
    report_parser.add_argument("--json", action="store_true", help="Print JSON instead of a concise text report.")

    next_parser = subparsers.add_parser("next", help="List the next pending research jobs.")
    next_parser.add_argument("--limit", type=int, default=10)
    next_parser.add_argument("--state")
    next_parser.add_argument("--json", action="store_true")

    run_parser = subparsers.add_parser("run", help="Run a small source-seed pass for pending jobs.")
    run_parser.add_argument("--limit", type=int, default=3)
    run_parser.add_argument("--state")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--retry-blocked", action="store_true", help="Include blocked jobs in this run.")

    daemon_parser = subparsers.add_parser("daemon", help="Run persistent small-batch research cycles.")
    daemon_parser.add_argument("--limit", type=int, default=5, help="Jobs per cycle.")
    daemon_parser.add_argument("--interval-seconds", type=int, default=120, help="Delay between cycles.")
    daemon_parser.add_argument("--state", help="Optional two-letter state filter.")
    daemon_parser.add_argument("--max-cycles", type=int, default=0, help="0 = run forever.")
    daemon_parser.add_argument("--retry-blocked", action="store_true", help="Include blocked jobs in daemon cycles.")

    status_parser = subparsers.add_parser("set-status", help="Manually update job status after review.")
    status_parser.add_argument("status", choices=sorted(JOB_STATUSES))
    status_parser.add_argument("job_ids", nargs="+")
    status_parser.add_argument("--note", default="")

    args = parser.parse_args(argv)

    if args.command == "init":
        queue = init_queue(force=args.force)
        print(f"Initialized {len(queue['jobs'])} research jobs at {JOB_QUEUE_PATH}")
        return 0

    if args.command == "report":
        payload = report(load_queue())
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Research jobs: {payload['job_count']}")
            print("By status:")
            for status, count in sorted(payload["by_status"].items()):
                print(f"  {status}: {count}")
            print("Next jobs:")
            for job in payload["next_jobs"]:
                print(f"  {job['id']} [{job['state']}] {job['title']}")
        return 0

    if args.command == "next":
        jobs = select_jobs(load_queue(), STATUS_PENDING, args.limit, state=args.state)
        if args.json:
            print(json.dumps(jobs, indent=2, sort_keys=True))
        else:
            for job in jobs:
                print(f"{job['id']}\t{job['state']}\t{job['task_type']}\t{job['title']}")
        return 0

    if args.command == "run":
        print(json.dumps(
            run_jobs(
                args.limit,
                state=args.state,
                dry_run=args.dry_run,
                retry_blocked=args.retry_blocked,
            ),
            indent=2,
            sort_keys=True,
        ))
        return 0

    if args.command == "daemon":
        return run_daemon(
            limit=args.limit,
            interval_seconds=args.interval_seconds,
            state=args.state,
            max_cycles=args.max_cycles,
            retry_blocked=args.retry_blocked,
        )

    if args.command == "set-status":
        print(json.dumps(update_status(args.job_ids, args.status, note=args.note), indent=2, sort_keys=True))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
