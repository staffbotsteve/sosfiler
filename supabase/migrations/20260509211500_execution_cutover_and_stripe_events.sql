-- Execution cutover hardening and Stripe webhook idempotency.
-- Created manually because the Supabase CLI is not available in this workspace.

create table if not exists public.stripe_webhook_events (
  id text primary key,
  event_type text not null,
  status text not null default 'processing',
  order_id text,
  stripe_object_id text,
  error text,
  raw_event jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  processed_at timestamptz
);

create index if not exists idx_stripe_webhook_events_type
  on public.stripe_webhook_events(event_type, created_at);

create unique index if not exists idx_execution_payment_ledger_idempotency
  on public.execution_payment_ledger(idempotency_key);

alter table public.stripe_webhook_events enable row level security;

grant select, insert, update, delete on table public.stripe_webhook_events to service_role;

-- No anon/authenticated policies are created intentionally. Stripe webhook
-- payloads are backend-only operational records and must not be browser-readable.
