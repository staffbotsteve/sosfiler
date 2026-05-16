-- Plan v2.6 §4.2.2 — evidence-invariant precondition columns.
-- Adds sha256_hex and superseded_by_artifact_id to public.execution_artifacts so
-- the BEFORE-UPDATE evidence trigger in §4.2.4 step 4 can require a hashed
-- artifact at the time of any approved/complete transition.
--
-- The trigger function itself lands in a later migration once the FilePro
-- workstream and filing_confirmation column are also in place; this migration
-- only widens the schema so downstream writes can populate the new fields.

alter table public.execution_artifacts
  add column if not exists sha256_hex text;

alter table public.execution_artifacts
  add column if not exists superseded_by_artifact_id uuid
    references public.execution_artifacts(id);

create index if not exists idx_execution_artifacts_job_type
  on public.execution_artifacts(filing_job_id, artifact_type, is_evidence);
