create extension if not exists pgcrypto;

create table if not exists public.regulatory_jurisdictions (
  jurisdiction_id text primary key,
  name text not null,
  state_code text not null,
  jurisdiction_level text not null,
  official_domains text[] not null default '{}',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.regulatory_research_batches (
  batch_id text primary key,
  jurisdiction_id text references public.regulatory_jurisdictions(jurisdiction_id) on delete set null,
  title text not null,
  phase text not null,
  scope text not null,
  status text not null,
  priority integer not null default 9999,
  required_filings text[] not null default '{}',
  discovery_queries text[] not null default '{}',
  official_domains text[] not null default '{}',
  notes text[] not null default '{}',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.regulatory_entity_types (
  entity_type_code text primary key,
  display_name text not null,
  entity_family text not null,
  profit_type text not null default 'unknown',
  aliases text[] not null default '{}',
  description text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

insert into public.regulatory_entity_types
  (entity_type_code, display_name, entity_family, profit_type, aliases, description)
values
  ('llc', 'Limited Liability Company', 'limited_liability_company', 'for_profit', array['LLC', 'Limited Liability Company'], 'Domestic or foreign limited liability company.'),
  ('pllc', 'Professional Limited Liability Company', 'limited_liability_company', 'for_profit', array['PLLC', 'Professional LLC', 'Professional Limited Liability Company'], 'Professional limited liability company where available.'),
  ('corporation', 'For-Profit Corporation', 'corporation', 'for_profit', array['Corporation', 'INC', 'Inc.', 'C Corporation', 'For-Profit Corporation'], 'Domestic or foreign for-profit corporation.'),
  ('nonprofit_corporation', 'Nonprofit Corporation', 'corporation', 'nonprofit', array['Nonprofit Corporation', 'Non-Profit Corporation', 'Nonprofit', 'Non-Profit'], 'Domestic or foreign nonprofit corporation.'),
  ('lp', 'Limited Partnership', 'partnership', 'for_profit', array['LP', 'Limited Partnership'], 'Domestic or foreign limited partnership.'),
  ('llp', 'Limited Liability Partnership', 'partnership', 'for_profit', array['LLP', 'Limited Liability Partnership'], 'Domestic or foreign limited liability partnership.'),
  ('other', 'Other Entity or Filing', 'other', 'unknown', array['Other'], 'Entity type is not yet normalized or the filing applies across entity types.')
on conflict (entity_type_code) do update set
  display_name = excluded.display_name,
  entity_family = excluded.entity_family,
  profit_type = excluded.profit_type,
  aliases = excluded.aliases,
  description = excluded.description,
  updated_at = now();

create table if not exists public.regulatory_filing_records (
  filing_id text primary key,
  batch_id text references public.regulatory_research_batches(batch_id) on delete set null,
  jurisdiction_id text references public.regulatory_jurisdictions(jurisdiction_id) on delete set null,
  jurisdiction_name text not null,
  jurisdiction_level text not null,
  authority text not null default '',
  filing_name text not null,
  filing_category text not null,
  domestic_or_foreign text not null default 'unknown',
  primary_entity_type_code text references public.regulatory_entity_types(entity_type_code) on delete set null,
  entity_types text[] not null default '{}',
  form_number text not null default '',
  form_name text not null default '',
  portal_name text not null default '',
  portal_url text not null default '',
  submission_channels text[] not null default '{}',
  required_customer_inputs text[] not null default '{}',
  required_documents text[] not null default '{}',
  output_documents text[] not null default '{}',
  annual_or_renewal_rule text not null default '',
  deadline_rule text not null default '',
  expected_turnaround text not null default '',
  price_confidence text not null default 'unknown',
  verification_status text not null default 'discovered',
  last_verified_at timestamptz,
  notes text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.regulatory_filing_entity_types (
  filing_id text not null references public.regulatory_filing_records(filing_id) on delete cascade,
  entity_type_code text not null references public.regulatory_entity_types(entity_type_code) on delete restrict,
  source_label text not null default '',
  is_primary boolean not null default false,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  primary key (filing_id, entity_type_code)
);

create table if not exists public.regulatory_filing_fees (
  id uuid primary key default gen_random_uuid(),
  filing_id text not null references public.regulatory_filing_records(filing_id) on delete cascade,
  code text not null default '',
  label text not null default '',
  amount_cents integer,
  fee_type text not null default 'fixed',
  formula text not null default '',
  required boolean not null default true,
  source_url text not null default '',
  fee_role text not null default 'government',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.regulatory_expedite_options (
  id uuid primary key default gen_random_uuid(),
  filing_id text not null references public.regulatory_filing_records(filing_id) on delete cascade,
  label text not null,
  fee_cents integer,
  processing_time text not null default '',
  channel text not null default 'any',
  customer_selectable boolean not null default true,
  source_url text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.regulatory_process_steps (
  id uuid primary key default gen_random_uuid(),
  filing_id text not null references public.regulatory_filing_records(filing_id) on delete cascade,
  step_number integer not null,
  actor text not null default '',
  title text not null default '',
  description text not null default '',
  portal_page text not null default '',
  required_inputs text[] not null default '{}',
  evidence_output text[] not null default '{}',
  status_after_step text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.regulatory_source_citations (
  id uuid primary key default gen_random_uuid(),
  filing_id text references public.regulatory_filing_records(filing_id) on delete cascade,
  batch_id text references public.regulatory_research_batches(batch_id) on delete cascade,
  url text not null,
  title text not null default '',
  authority text not null default '',
  source_type text not null default 'official',
  retrieved_at timestamptz,
  content_hash text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.regulatory_automation_recipes (
  filing_id text primary key references public.regulatory_filing_records(filing_id) on delete cascade,
  method text not null default 'operator_assisted',
  automatable boolean not null default false,
  account_strategy text not null default '',
  submission_strategy text not null default '',
  status_query_strategy text not null default '',
  document_collection_strategy text not null default '',
  blockers text[] not null default '{}',
  selectors_or_api_notes text[] not null default '{}',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.regulatory_reminder_rules (
  id uuid primary key default gen_random_uuid(),
  filing_id text not null references public.regulatory_filing_records(filing_id) on delete cascade,
  trigger text not null,
  offset_days integer not null,
  channel text not null default 'email',
  audience text not null default 'customer',
  message_key text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.regulatory_research_runs (
  run_id uuid primary key,
  phase text not null default '',
  batch_count integer not null default 0,
  auto_verify boolean not null default true,
  dry_run boolean not null default false,
  started_at timestamptz,
  finished_at timestamptz,
  artifact_path text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.regulatory_competitors (
  competitor_code text primary key,
  name text not null,
  website_url text not null default '',
  notes text not null default '',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.regulatory_competitor_offerings (
  offering_id text primary key,
  competitor_code text not null references public.regulatory_competitors(competitor_code) on delete cascade,
  offering_name text not null,
  offering_category text not null,
  entity_type_code text references public.regulatory_entity_types(entity_type_code) on delete set null,
  jurisdiction_scope text not null default 'unknown',
  advertised_price_cents integer,
  advertised_price_text text not null default '',
  state_fees_included boolean,
  recurring boolean not null default false,
  advertised_turnaround text not null default '',
  included_features text[] not null default '{}',
  source_url text not null default '',
  source_title text not null default '',
  captured_at timestamptz not null default now(),
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.regulatory_competitor_gap_signals (
  id uuid primary key default gen_random_uuid(),
  offering_id text references public.regulatory_competitor_offerings(offering_id) on delete cascade,
  filing_id text references public.regulatory_filing_records(filing_id) on delete set null,
  signal_type text not null,
  priority text not null default 'medium',
  summary text not null,
  official_verification_required boolean not null default true,
  status text not null default 'open',
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.sosfiler_services (
  service_code text primary key,
  service_name text not null,
  service_category text not null,
  filing_category text,
  entity_type_code text references public.regulatory_entity_types(entity_type_code) on delete set null,
  lifecycle_stage text not null default 'any',
  description text not null default '',
  included boolean not null default false,
  active boolean not null default true,
  requires_official_filing_record boolean not null default true,
  renewal_or_recurring boolean not null default false,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.sosfiler_service_prices (
  id uuid primary key default gen_random_uuid(),
  service_code text not null references public.sosfiler_services(service_code) on delete cascade,
  price_type text not null default 'flat',
  amount_cents integer,
  minimum_amount_cents integer,
  maximum_amount_cents integer,
  currency text not null default 'USD',
  billing_interval text not null default 'one_time',
  price_formula text not null default '',
  excludes_government_fees boolean not null default true,
  effective_at timestamptz not null default now(),
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.sosfiler_service_competitor_benchmarks (
  id uuid primary key default gen_random_uuid(),
  service_code text not null references public.sosfiler_services(service_code) on delete cascade,
  competitor_code text not null references public.regulatory_competitors(competitor_code) on delete cascade,
  competitor_price_cents integer,
  competitor_price_text text not null default '',
  source_url text not null default '',
  notes text not null default '',
  captured_at timestamptz not null default now(),
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.state_automation_profiles (
  state_code text primary key,
  jurisdiction_id text references public.regulatory_jurisdictions(jurisdiction_id) on delete set null,
  state_name text not null,
  portal_urls text[] not null default '{}',
  account_required text not null default 'unknown',
  master_account_possible text not null default 'unknown',
  human_challenge_status text not null default 'unknown',
  online_llc boolean not null default false,
  online_corporation boolean not null default false,
  online_nonprofit boolean not null default false,
  online_foreign_qualification boolean not null default false,
  online_amendments boolean not null default false,
  online_annual_report boolean not null default false,
  payment_method text not null default 'unknown',
  status_check_method text not null default 'unknown',
  document_retrieval_method text not null default 'unknown',
  automation_lane text not null default 'needs_certification',
  production_readiness text not null default 'not_ready',
  blocker_summary text not null default '',
  next_action text not null default '',
  evidence_urls text[] not null default '{}',
  last_certified_at timestamptz,
  raw jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.regulatory_review_tasks (
  id uuid primary key default gen_random_uuid(),
  filing_id text references public.regulatory_filing_records(filing_id) on delete cascade,
  batch_id text references public.regulatory_research_batches(batch_id) on delete set null,
  status text not null default 'open',
  reason text not null,
  assigned_to text not null default '',
  notes text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_regulatory_filing_records_jurisdiction on public.regulatory_filing_records(jurisdiction_id);
create index if not exists idx_regulatory_filing_records_status on public.regulatory_filing_records(verification_status);
create index if not exists idx_regulatory_filing_records_category on public.regulatory_filing_records(filing_category);
create index if not exists idx_regulatory_filing_records_primary_entity_type on public.regulatory_filing_records(primary_entity_type_code);
create index if not exists idx_regulatory_filing_records_entity_types on public.regulatory_filing_records using gin(entity_types);
create index if not exists idx_regulatory_filing_entity_types_entity_type on public.regulatory_filing_entity_types(entity_type_code);
create index if not exists idx_regulatory_source_citations_filing on public.regulatory_source_citations(filing_id);
create index if not exists idx_regulatory_process_steps_filing on public.regulatory_process_steps(filing_id, step_number);
create index if not exists idx_regulatory_review_tasks_status on public.regulatory_review_tasks(status);
create index if not exists idx_regulatory_competitor_offerings_competitor on public.regulatory_competitor_offerings(competitor_code);
create index if not exists idx_regulatory_competitor_offerings_category on public.regulatory_competitor_offerings(offering_category);
create index if not exists idx_regulatory_competitor_gap_signals_status on public.regulatory_competitor_gap_signals(status);
create index if not exists idx_sosfiler_services_category on public.sosfiler_services(service_category);
create index if not exists idx_sosfiler_services_filing_category on public.sosfiler_services(filing_category);
create index if not exists idx_sosfiler_service_prices_service on public.sosfiler_service_prices(service_code);
create index if not exists idx_sosfiler_service_competitor_benchmarks_service on public.sosfiler_service_competitor_benchmarks(service_code);
create index if not exists idx_state_automation_profiles_lane on public.state_automation_profiles(automation_lane);
create index if not exists idx_state_automation_profiles_readiness on public.state_automation_profiles(production_readiness);

alter table public.regulatory_jurisdictions enable row level security;
alter table public.regulatory_research_batches enable row level security;
alter table public.regulatory_entity_types enable row level security;
alter table public.regulatory_filing_records enable row level security;
alter table public.regulatory_filing_entity_types enable row level security;
alter table public.regulatory_filing_fees enable row level security;
alter table public.regulatory_expedite_options enable row level security;
alter table public.regulatory_process_steps enable row level security;
alter table public.regulatory_source_citations enable row level security;
alter table public.regulatory_automation_recipes enable row level security;
alter table public.regulatory_reminder_rules enable row level security;
alter table public.regulatory_research_runs enable row level security;
alter table public.regulatory_competitors enable row level security;
alter table public.regulatory_competitor_offerings enable row level security;
alter table public.regulatory_competitor_gap_signals enable row level security;
alter table public.sosfiler_services enable row level security;
alter table public.sosfiler_service_prices enable row level security;
alter table public.sosfiler_service_competitor_benchmarks enable row level security;
alter table public.state_automation_profiles enable row level security;
alter table public.regulatory_review_tasks enable row level security;
