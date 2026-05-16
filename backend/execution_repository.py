"""Persistence boundary for SOSFiler execution-platform records.

SQLite remains the local compatibility store while Supabase/Postgres is rolled
in. This module provides backend-only dual-write helpers for the execution
tables created by the Supabase migration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    import psycopg
    from psycopg.types.json import Jsonb
except Exception:  # pragma: no cover - exercised only when dependency missing
    psycopg = None
    Jsonb = None


def _jsonb(value: Any) -> Any:
    if Jsonb is None:
        return value
    return Jsonb(value if value is not None else {})


def _database_url() -> str:
    return os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL") or ""


def execution_persistence_mode() -> str:
    return (os.getenv("EXECUTION_PERSISTENCE_MODE") or "sqlite").strip().lower()


def supabase_configured() -> bool:
    return bool(_database_url()) and psycopg is not None


@dataclass
class RepositoryResult:
    ok: bool
    mode: str
    message: str = ""


class SupabaseExecutionRepository:
    """Direct Postgres repository for backend service writes."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or _database_url()

    @property
    def configured(self) -> bool:
        return bool(self.database_url) and psycopg is not None

    def connect(self):
        if not self.configured:
            raise RuntimeError("SUPABASE_DB_URL or DATABASE_URL is required for Supabase persistence")
        return psycopg.connect(self.database_url, autocommit=True)

    def table_counts(self) -> dict[str, int]:
        with self.connect() as conn:
            counts = {
                "execution_quotes": conn.execute("select count(*) from public.execution_quotes").fetchone()[0],
                "execution_payment_ledger": conn.execute("select count(*) from public.execution_payment_ledger").fetchone()[0],
                "execution_filing_jobs": conn.execute("select count(*) from public.execution_filing_jobs").fetchone()[0],
                "execution_events": conn.execute("select count(*) from public.execution_events").fetchone()[0],
                "execution_artifacts": conn.execute("select count(*) from public.execution_artifacts").fetchone()[0],
                "support_tickets": conn.execute("select count(*) from public.support_tickets").fetchone()[0],
                "automation_runs": conn.execute("select count(*) from public.automation_runs").fetchone()[0],
            }
            try:
                counts["stripe_webhook_events"] = conn.execute("select count(*) from public.stripe_webhook_events").fetchone()[0]
            except Exception:
                counts["stripe_webhook_events"] = 0
            return counts

    @staticmethod
    def quote_payload(quote: dict[str, Any], order_id: str = "") -> dict[str, Any]:
        return {
            "external_quote_id": quote["quote_id"],
            "order_id": order_id or quote.get("order_id") or None,
            "product_type": quote["product_type"],
            "entity_type": quote.get("entity_type") or None,
            "state": quote.get("state") or None,
            "currency": quote.get("currency", "usd"),
            "line_items": quote.get("line_items", []),
            "estimated_total_cents": int(quote.get("estimated_total_cents") or 0),
            "authorized_total_cents": quote.get("authorized_total_cents"),
            "captured_total_cents": quote.get("captured_total_cents"),
            "reconciliation_status": quote.get("reconciliation_status", "quoted"),
            "idempotency_key": quote.get("idempotency_key", ""),
        }

    @staticmethod
    def filing_job_payload(job: dict[str, Any]) -> dict[str, Any]:
        return {
            "legacy_job_id": job["id"],
            "order_id": job["order_id"],
            "product_type": job.get("product_type", "formation"),
            "action_type": job.get("action_type", "formation"),
            "state": job.get("state", ""),
            "entity_type": job.get("entity_type"),
            "status": job.get("status", ""),
            "automation_lane": job.get("automation_lane", "operator_assisted"),
            "automation_difficulty": job.get("automation_difficulty", "unknown"),
            "adapter_key": job.get("adapter_key"),
            "customer_status": job.get("customer_status"),
            "portal_url": job.get("portal_url"),
            "portal_blockers": job.get("portal_blockers", []),
            "evidence_required": job.get("required_evidence", {}),
            # Plan v2.6 §4.2.4 step 4 / PR5: mirror orders.filing_confirmation
            # (canonical JSON shape) so Postgres-side invariants can read it.
            # Callers populate `filing_confirmation_raw` on the job dict before
            # dispatching the dual-write (see server.serialize_filing_job_with_confirmation).
            "filing_confirmation": job.get("filing_confirmation_raw"),
            "metadata": job.get("route_metadata") or {
                "office": job.get("office"),
                "form_name": job.get("form_name"),
                "portal_name": job.get("portal_name"),
                "filing_method": job.get("filing_method"),
                "state_fee_cents": job.get("state_fee_cents"),
                "processing_fee_cents": job.get("processing_fee_cents"),
                "total_government_cents": job.get("total_government_cents"),
            },
        }

    def upsert_quote(self, quote: dict[str, Any], order_id: str = "") -> None:
        payload = self.quote_payload(quote, order_id)
        with self.connect() as conn:
            conn.execute(
                """
                insert into public.execution_quotes (
                  external_quote_id, order_id, product_type, entity_type, state, currency,
                  line_items, estimated_total_cents, authorized_total_cents, captured_total_cents,
                  reconciliation_status, idempotency_key, updated_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                on conflict (external_quote_id) do update set
                  order_id = coalesce(excluded.order_id, public.execution_quotes.order_id),
                  line_items = excluded.line_items,
                  estimated_total_cents = excluded.estimated_total_cents,
                  authorized_total_cents = excluded.authorized_total_cents,
                  captured_total_cents = excluded.captured_total_cents,
                  reconciliation_status = excluded.reconciliation_status,
                  idempotency_key = excluded.idempotency_key,
                  updated_at = now()
                """,
                (
                    payload["external_quote_id"], payload["order_id"], payload["product_type"],
                    payload["entity_type"], payload["state"], payload["currency"],
                    _jsonb(payload["line_items"]), payload["estimated_total_cents"],
                    payload["authorized_total_cents"], payload["captured_total_cents"],
                    payload["reconciliation_status"], payload["idempotency_key"],
                ),
            )

    def update_quote_authorized(self, external_quote_id: str, amount_cents: int | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update public.execution_quotes
                set reconciliation_status = 'authorized',
                    authorized_total_cents = coalesce(%s, authorized_total_cents),
                    updated_at = now()
                where external_quote_id = %s
                """,
                (amount_cents, external_quote_id),
            )

    def insert_payment_ledger(self, ledger: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into public.execution_payment_ledger (
                  order_id, quote_id, stripe_session_id, stripe_payment_intent, event_type,
                  amount_cents, currency, idempotency_key, raw_event
                )
                values (
                  %s,
                  (select id from public.execution_quotes where external_quote_id = %s limit 1),
                  %s, %s, %s, %s, %s, %s, %s
                )
                on conflict (idempotency_key) do nothing
                """,
                (
                    ledger["order_id"], ledger.get("quote_id") or None,
                    ledger.get("stripe_session_id"), ledger.get("stripe_payment_intent"),
                    ledger["event_type"], int(ledger.get("amount_cents") or 0),
                    ledger.get("currency", "usd"), ledger.get("idempotency_key", ""),
                    _jsonb(ledger.get("raw_event", {})),
                ),
            )

    def upsert_filing_job(self, job: dict[str, Any]) -> None:
        payload = self.filing_job_payload(job)
        with self.connect() as conn:
            conn.execute(
                """
                insert into public.execution_filing_jobs (
                  legacy_job_id, order_id, product_type, action_type, state, entity_type,
                  status, automation_lane, automation_difficulty, adapter_key,
                  customer_status, portal_url, portal_blockers, evidence_required,
                  filing_confirmation, metadata, updated_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                on conflict (legacy_job_id) do update set
                  status = excluded.status,
                  automation_lane = excluded.automation_lane,
                  automation_difficulty = excluded.automation_difficulty,
                  adapter_key = excluded.adapter_key,
                  customer_status = excluded.customer_status,
                  portal_url = excluded.portal_url,
                  portal_blockers = excluded.portal_blockers,
                  evidence_required = excluded.evidence_required,
                  -- Plan v2.6 PR5 codex round-1 P2 #2: COALESCE so callers
                  -- that pass plain serialize_filing_job() (no _raw field)
                  -- do not clobber an already-captured confirmation. A
                  -- non-NULL excluded value still wins, so adapters that
                  -- DO populate filing_confirmation_raw can update it.
                  filing_confirmation = coalesce(excluded.filing_confirmation, public.execution_filing_jobs.filing_confirmation),
                  metadata = excluded.metadata,
                  updated_at = now()
                """,
                (
                    payload["legacy_job_id"], payload["order_id"], payload["product_type"],
                    payload["action_type"], payload["state"], payload["entity_type"],
                    payload["status"], payload["automation_lane"], payload["automation_difficulty"],
                    payload["adapter_key"], payload["customer_status"], payload["portal_url"],
                    _jsonb(payload["portal_blockers"]), _jsonb(payload["evidence_required"]),
                    payload["filing_confirmation"], _jsonb(payload["metadata"]),
                ),
            )

    def insert_event(self, event: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into public.execution_events (
                  order_id, filing_job_id, event_type, previous_status, next_status,
                  actor, message, evidence_path, redacted_payload
                )
                values (
                  %s,
                  (select id from public.execution_filing_jobs where legacy_job_id = %s limit 1),
                  %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    event["order_id"], event.get("filing_job_id") or None,
                    event["event_type"], event.get("previous_status"), event.get("next_status"),
                    event.get("actor", "system"), event.get("message"), event.get("evidence_path"),
                    _jsonb(event.get("redacted_payload", {})),
                ),
            )

    def insert_artifact(self, artifact: dict[str, Any]) -> None:
        with self.connect() as conn:
            # Plan v2.6 §4.2.4 step 4 / PR5: persist evidence digest so
            # mirror-side invariants see the same tamper-evident fields the
            # local SQLite store carries.
            #
            # NOTE: SQLite filing_artifacts.superseded_by_artifact_id is a
            # local INTEGER row id; the Postgres column is a UUID FK to
            # execution_artifacts(id). Writing the integer directly would
            # fail type checks in Postgres. We deliberately do NOT propagate
            # the supersession link here until a separate workstream wires
            # an INTEGER → UUID mapping (codex PR5 round-2 P2). The column
            # stays in the schema so the future mapping can populate it.
            conn.execute(
                """
                insert into public.execution_artifacts (
                  order_id, filing_job_id, artifact_type, filename, storage_path,
                  issuer, source_url, visibility, is_evidence,
                  sha256_hex
                )
                values (
                  %s,
                  (select id from public.execution_filing_jobs where legacy_job_id = %s limit 1),
                  %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    artifact["order_id"], artifact.get("filing_job_id") or None,
                    artifact["artifact_type"], artifact["filename"], artifact["file_path"],
                    artifact.get("issuer"), artifact.get("source_url"),
                    artifact.get("visibility", "customer"), bool(artifact.get("is_evidence")),
                    artifact.get("sha256_hex"),
                ),
            )

    def insert_support_ticket(self, ticket: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into public.support_tickets (
                  external_ticket_id, ticket_type, status, priority, customer_email,
                  order_id, session_id, state, product_type, question,
                  confidence_reason, suggested_answer, updated_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                on conflict (external_ticket_id) do update set
                  status = excluded.status,
                  priority = excluded.priority,
                  confidence_reason = excluded.confidence_reason,
                  suggested_answer = excluded.suggested_answer,
                  updated_at = now()
                """,
                (
                    ticket["id"], ticket.get("ticket_type", "support"), ticket.get("status", "open"),
                    ticket.get("priority", "normal"), ticket.get("customer_email"),
                    ticket.get("order_id"), ticket.get("session_id"), ticket.get("state"),
                    ticket.get("product_type"), ticket["question"], ticket.get("confidence_reason"),
                    ticket.get("suggested_answer"),
                ),
            )

    def insert_automation_run(self, run: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into public.automation_runs (
                  run_id, filing_job_id, order_id, adapter_key, lane, status,
                  started_at, finished_at, redacted_log, updated_at
                )
                values (
                  %s,
                  (select id from public.execution_filing_jobs where legacy_job_id = %s limit 1),
                  %s, %s, %s, %s, now(), %s, %s, now()
                )
                on conflict (run_id) do update set
                  status = excluded.status,
                  finished_at = excluded.finished_at,
                  redacted_log = excluded.redacted_log,
                  updated_at = now()
                """,
                (
                    run["id"], run.get("filing_job_id"), run.get("order_id"),
                    run.get("adapter_key", ""), run.get("lane", "operator_assisted"),
                    run.get("status", "queued"), run.get("finished_at"),
                    _jsonb(run.get("redacted_log", [])),
                ),
            )

    def update_automation_run(self, run_id: str, status: str, redacted_log: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update public.automation_runs
                set status = %s, finished_at = now(), redacted_log = %s, updated_at = now()
                where run_id = %s
                """,
                (status, _jsonb(redacted_log), run_id),
            )

    def stop_automation_run(self, run_id: str, reason: str, redacted_log: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update public.automation_runs
                set stop_requested = true, stop_reason = %s, status = 'stop_requested',
                    redacted_log = %s, updated_at = now()
                where run_id = %s
                """,
                (reason, _jsonb(redacted_log), run_id),
            )


class ExecutionPersistence:
    """Mode-aware wrapper used by the FastAPI server."""

    def __init__(self, repository: SupabaseExecutionRepository | None = None):
        self.repository = repository or SupabaseExecutionRepository()

    @property
    def mode(self) -> str:
        return execution_persistence_mode()

    @property
    def supabase_enabled(self) -> bool:
        return self.mode in {"dual", "supabase"} and self.repository.configured

    def health(self) -> RepositoryResult:
        if self.mode == "sqlite":
            return RepositoryResult(True, self.mode, "SQLite compatibility store is active.")
        if not self.repository.configured:
            return RepositoryResult(False, self.mode, "Supabase database URL or psycopg is not configured.")
        try:
            with self.repository.connect() as conn:
                conn.execute("select 1")
            return RepositoryResult(True, self.mode, "Supabase/Postgres connection verified.")
        except Exception as exc:
            return RepositoryResult(False, self.mode, f"Supabase/Postgres connection failed: {exc.__class__.__name__}")

    def table_counts(self) -> tuple[RepositoryResult, dict[str, int]]:
        if not self.repository.configured:
            return RepositoryResult(False, self.mode, "Supabase database URL or psycopg is not configured."), {}
        try:
            return RepositoryResult(True, self.mode, "Supabase table counts loaded."), self.repository.table_counts()
        except Exception as exc:
            return RepositoryResult(False, self.mode, f"Supabase table counts failed: {exc.__class__.__name__}"), {}

    def dual_write(self, method_name: str, *args: Any, **kwargs: Any) -> RepositoryResult:
        if not self.supabase_enabled:
            return RepositoryResult(True, self.mode, "Supabase dual-write disabled.")
        try:
            method = getattr(self.repository, method_name)
            method(*args, **kwargs)
            return RepositoryResult(True, self.mode, f"Supabase {method_name} succeeded.")
        except Exception as exc:
            return RepositoryResult(False, self.mode, f"Supabase {method_name} failed: {exc.__class__.__name__}")
