"""
SOSFiler research worker.

Turns the 50-state and local-jurisdiction research queues into explicit,
auditable jobs. The worker is intentionally source-first: it can inventory
jobs, mark work in progress, record official-source evidence, and produce a
coverage report without relying on NotebookLM as the driver.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open() as f:
        return json.load(f)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


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
                requirements=completion_requirements + next_actions,
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
    return queue


def load_queue() -> dict[str, Any]:
    if not JOB_QUEUE_PATH.exists():
        return init_queue()
    return load_json(JOB_QUEUE_PATH, {})


def save_queue(queue: dict[str, Any]) -> None:
    queue["generated_at"] = utc_now()
    write_json(JOB_QUEUE_PATH, queue)


def select_jobs(queue: dict[str, Any], status: str, limit: int, state: str | None = None) -> list[dict[str, Any]]:
    jobs = queue.get("jobs", [])
    selected = [
        job for job in jobs
        if job.get("status", STATUS_PENDING) == status
        and (state is None or job.get("state") == state.upper())
    ]
    return sorted(selected, key=lambda job: (job.get("priority", 999), job["id"]))[:limit]


def fetch_url_summary(url: str, timeout: int = 15) -> dict[str, Any]:
    started = utc_now()
    req = Request(url, headers={"User-Agent": "SOSFilerResearchBot/0.1 (+https://sosfiler.com)"})
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read(200_000)
            content_type = response.headers.get("content-type", "")
            text = body.decode("utf-8", errors="ignore")
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
            }
    except (OSError, URLError) as exc:
        return {
            "url": url,
            "checked_at": started,
            "status": "error",
            "error": str(exc),
        }


def run_jobs(limit: int, state: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    queue = load_queue()
    jobs = select_jobs(queue, STATUS_PENDING, limit, state=state)
    run = {
        "id": str(uuid.uuid4()),
        "started_at": utc_now(),
        "dry_run": dry_run,
        "jobs": [],
    }
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
            for url in job.get("seed_urls", []):
                job_run["seed_results"].append(fetch_url_summary(url))
            reachable = [result for result in job_run["seed_results"] if result["status"] == "reachable"]
            job["status"] = STATUS_IN_PROGRESS if reachable else STATUS_BLOCKED
            job["updated_at"] = utc_now()
            if reachable:
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
                job.setdefault("notes", []).append("Official seed URLs are reachable; research details still need human/agent extraction.")
            else:
                job.setdefault("notes", []).append("No reachable seed URLs yet; add official sources before extraction.")
            job_run["next_status"] = job["status"]
        run["jobs"].append(job_run)

    run["finished_at"] = utc_now()
    if not dry_run:
        save_queue(queue)
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
        print(json.dumps(run_jobs(args.limit, state=args.state, dry_run=args.dry_run), indent=2, sort_keys=True))
        return 0

    if args.command == "set-status":
        print(json.dumps(update_status(args.job_ids, args.status, note=args.note), indent=2, sort_keys=True))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
