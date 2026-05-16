-- SOSFiler automated execution platform schema.
-- Public-schema tables enable RLS by default; backend workers should use the
-- service role or direct database credentials, never browser-exposed keys.

create extension if not exists pgcrypto;

create table if not exists public.execution_quotes (
  id uuid primary key default gen_random_uuid(),
  external_quote_id text unique not null,
  order_id text,
  product_type text not null,
  entity_type text,
  state text,
  currency text not null default 'usd',
  line_items jsonb not null default '[]'::jsonb,
  estimated_total_cents integer not null default 0,
  authorized_total_cents integer,
  captured_total_cents integer,
  reconciliation_status text not null default 'quoted',
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.execution_payment_ledger (
  id uuid primary key default gen_random_uuid(),
  order_id text not null,
  quote_id uuid references public.execution_quotes(id),
  stripe_session_id text,
  stripe_payment_intent text,
  event_type text not null,
  amount_cents integer not null default 0,
  currency text not null default 'usd',
  idempotency_key text not null,
  raw_event jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.execution_filing_jobs (
  id uuid primary key default gen_random_uuid(),
  legacy_job_id text unique,
  order_id text not null,
  product_type text not null,
  action_type text not null,
  state text not null,
  entity_type text,
  status text not null,
  automation_lane text not null default 'operator_assisted',
  automation_difficulty text not null default 'unknown',
  adapter_key text,
  customer_status text,
  official_status text,
  portal_url text,
  portal_blockers jsonb not null default '[]'::jsonb,
  claimed_by text,
  claimed_at timestamptz,
  evidence_required jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.execution_events (
  id uuid primary key default gen_random_uuid(),
  order_id text not null,
  filing_job_id uuid references public.execution_filing_jobs(id),
  event_type text not null,
  previous_status text,
  next_status text,
  actor text not null default 'system',
  message text,
  evidence_path text,
  redacted_payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.execution_artifacts (
  id uuid primary key default gen_random_uuid(),
  order_id text not null,
  filing_job_id uuid references public.execution_filing_jobs(id),
  artifact_type text not null,
  filename text not null,
  storage_path text not null,
  issuer text,
  source_url text,
  visibility text not null default 'customer',
  is_evidence boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists public.pii_vault (
  id uuid primary key default gen_random_uuid(),
  subject_type text not null,
  subject_id text not null,
  pii_type text not null,
  encrypted_payload text not null,
  fingerprint text not null,
  last4 text,
  retention_until timestamptz,
  created_by text not null default 'system',
  created_at timestamptz not null default now(),
  accessed_at timestamptz
);

create table if not exists public.support_tickets (
  id uuid primary key default gen_random_uuid(),
  external_ticket_id text unique not null,
  ticket_type text not null default 'support',
  status text not null default 'open',
  priority text not null default 'normal',
  customer_email text,
  order_id text,
  session_id text,
  state text,
  product_type text,
  question text not null,
  confidence_reason text,
  suggested_answer text,
  slack_message_ts text,
  approved_by text,
  approved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.automation_runs (
  id uuid primary key default gen_random_uuid(),
  run_id text unique not null,
  filing_job_id uuid references public.execution_filing_jobs(id),
  order_id text,
  adapter_key text not null,
  lane text not null,
  status text not null default 'queued',
  stop_requested boolean not null default false,
  stop_reason text,
  started_at timestamptz,
  finished_at timestamptz,
  redacted_log jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_execution_quotes_order on public.execution_quotes(order_id);
create index if not exists idx_payment_ledger_order on public.execution_payment_ledger(order_id);
create index if not exists idx_execution_jobs_order on public.execution_filing_jobs(order_id);
create index if not exists idx_execution_jobs_status on public.execution_filing_jobs(status);
create index if not exists idx_execution_events_order on public.execution_events(order_id);
create index if not exists idx_execution_artifacts_order on public.execution_artifacts(order_id);
create index if not exists idx_pii_vault_subject on public.pii_vault(subject_type, subject_id);
create index if not exists idx_support_tickets_status on public.support_tickets(status, priority);
create index if not exists idx_automation_runs_status on public.automation_runs(status, stop_requested);

alter table public.execution_quotes enable row level security;
alter table public.execution_payment_ledger enable row level security;
alter table public.execution_filing_jobs enable row level security;
alter table public.execution_events enable row level security;
alter table public.execution_artifacts enable row level security;
alter table public.pii_vault enable row level security;
alter table public.support_tickets enable row level security;
alter table public.automation_runs enable row level security;

grant select, insert, update, delete on table
  public.execution_quotes,
  public.execution_payment_ledger,
  public.execution_filing_jobs,
  public.execution_events,
  public.execution_artifacts,
  public.pii_vault,
  public.support_tickets,
  public.automation_runs
to service_role;

-- No anon/authenticated policies are created intentionally. Customer-facing
-- access should go through backend APIs that enforce order-token/JWT checks.
