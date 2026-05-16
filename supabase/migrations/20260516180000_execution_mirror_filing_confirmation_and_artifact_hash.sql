-- Plan v2.6 §4.2.4 step 4 / PR5: Supabase mirror parity for evidence invariant.
-- Two changes that the SQLite store has carried since PR1+PR4 but the
-- Postgres mirror still lacked.
--
-- 1. execution_filing_jobs gains the filing_confirmation column so the
--    upsert_filing_job dual-write can persist the same JSON shape that
--    orders.filing_confirmation stores locally.
-- 2. execution_artifacts gained sha256_hex + superseded_by_artifact_id in
--    the round-1 migration (20260515170000), but
--    SupabaseExecutionRepository.insert_artifact() has not been wiring the
--    digest through. The repository code change ships in this PR; this
--    migration only ensures the columns exist (idempotent ALTER).

alter table public.execution_filing_jobs
  add column if not exists filing_confirmation text;

create index if not exists idx_execution_filing_jobs_filing_confirmation
  on public.execution_filing_jobs ((filing_confirmation is not null));

-- Re-asserting the artifact columns + index to make this migration
-- self-sufficient even if 20260515170000 has not been pushed yet.
alter table public.execution_artifacts
  add column if not exists sha256_hex text;

alter table public.execution_artifacts
  add column if not exists superseded_by_artifact_id uuid
    references public.execution_artifacts(id);

create index if not exists idx_execution_artifacts_job_type
  on public.execution_artifacts(filing_job_id, artifact_type, is_evidence);
