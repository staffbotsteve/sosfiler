-- Partner API schema for SOSFiler formation infrastructure.
-- Partner secrets are hash-only. Backend APIs enforce partner ownership before
-- returning order, document, or webhook-event data.

create table if not exists public.partners (
  id text primary key,
  name text not null,
  slug text unique not null,
  status text not null default 'active',
  support_email text,
  branding jsonb not null default '{}'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.partner_api_keys (
  id text primary key,
  partner_id text not null references public.partners(id),
  key_hash text unique not null,
  key_prefix text not null,
  name text not null default 'default',
  scopes jsonb not null default '["quotes:write","orders:write","orders:read","webhooks:write"]'::jsonb,
  status text not null default 'active',
  last_used_at timestamptz,
  created_at timestamptz not null default now(),
  revoked_at timestamptz
);

create table if not exists public.partner_orders (
  id text primary key,
  partner_id text not null references public.partners(id),
  order_id text not null,
  external_order_id text not null,
  external_customer_id text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(partner_id, external_order_id),
  unique(partner_id, order_id)
);

create table if not exists public.partner_webhook_endpoints (
  id text primary key,
  partner_id text not null references public.partners(id),
  url text not null,
  event_types jsonb not null default '[]'::jsonb,
  signing_secret_hash text not null,
  secret_prefix text not null,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.partner_webhook_events (
  id text primary key,
  partner_id text not null references public.partners(id),
  order_id text,
  endpoint_id text references public.partner_webhook_endpoints(id),
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued',
  attempts integer not null default 0,
  last_error text,
  created_at timestamptz not null default now(),
  delivered_at timestamptz
);

create table if not exists public.partner_applications (
  id text primary key,
  company_name text not null,
  contact_name text not null,
  contact_email text not null,
  website text,
  use_case text not null,
  expected_monthly_volume text,
  plan text not null default 'starter',
  status text not null default 'submitted',
  payment_status text not null default 'not_started',
  stripe_session_id text,
  stripe_payment_intent text,
  partner_id text references public.partners(id),
  notes text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  approved_at timestamptz
);

create index if not exists idx_partner_api_keys_hash on public.partner_api_keys(key_hash);
create index if not exists idx_partner_orders_partner on public.partner_orders(partner_id, created_at);
create index if not exists idx_partner_orders_order on public.partner_orders(order_id);
create index if not exists idx_partner_webhooks_partner on public.partner_webhook_endpoints(partner_id, status);
create index if not exists idx_partner_webhook_events_partner on public.partner_webhook_events(partner_id, status);
create index if not exists idx_partner_applications_status on public.partner_applications(status, created_at);
create index if not exists idx_partner_applications_email on public.partner_applications(contact_email);

alter table public.partners enable row level security;
alter table public.partner_api_keys enable row level security;
alter table public.partner_orders enable row level security;
alter table public.partner_webhook_endpoints enable row level security;
alter table public.partner_webhook_events enable row level security;
alter table public.partner_applications enable row level security;

grant select, insert, update, delete on table
  public.partners,
  public.partner_api_keys,
  public.partner_orders,
  public.partner_webhook_endpoints,
  public.partner_webhook_events,
  public.partner_applications
to service_role;

-- No anon/authenticated policies are created intentionally. Partner access must
-- go through backend APIs that verify hashed partner API keys and ownership.
