# Supabase Regulatory Migration

## Goal

Move SOSFiler regulatory research from JSON files into Supabase Postgres as the durable system of record.

JSON remains useful as an export/archive format, but Supabase should own:

- jurisdictions
- research batches and runs
- normalized formation/entity types
- filing records
- fees and processing fees
- expedite options
- process steps
- source citations
- automation recipes
- reminder rules
- review tasks
- competitor offerings and product-gap signals
- SOSFiler service catalog, pricing, and competitor benchmarks

## Files

- Migration: `../../supabase/migrations/20260508183000_regulatory_research_schema.sql`
- Importer: `../backend/regulatory/supabase_importer.py`
- Source JSON: `../data/regulatory/filings.json`

## Required Environment

Add these to `.env` on the trusted backend/operator machine:

```bash
SUPABASE_URL=https://your-project-ref.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_DB_URL=postgresql://postgres.your-project-ref:YOUR-PASSWORD@aws-0-us-west-1.pooler.supabase.com:6543/postgres?sslmode=require
```

Do not expose `SUPABASE_SERVICE_ROLE_KEY` or `SUPABASE_DB_URL` in browser code.

## Apply Schema

Preferred:

```bash
supabase link --project-ref <project-ref>
supabase db push
```

If the Supabase CLI is not installed, paste the SQL migration into the Supabase SQL editor and run it once.

The migration enables RLS on all public tables. No browser policies are created yet; backend workers should use service-role/database credentials.

## Import Existing JSON

Dry-run:

```bash
python3 -m backend.regulatory.supabase_importer --dry-run
```

Live import:

```bash
set -a
source .env
set +a
python3 -m backend.regulatory.supabase_importer
```

Expected current dry-run payload:

- `51` jurisdictions
- `102` batches
- `1257` filing records
- `93` research runs

## Entity Type Model

Formation/entity type is normalized through:

- `regulatory_entity_types`: canonical catalog such as `llc`, `corporation`, `nonprofit_corporation`, `lp`, `llp`, `pllc`, and `other`.
- `regulatory_filing_records.primary_entity_type_code`: primary type for product/dashboard filtering.
- `regulatory_filing_entity_types`: many-to-many mapping when one filing applies to multiple entity types.

`regulatory_filing_records.entity_types` remains as a denormalized source-label array during the JSON-to-Supabase transition.

## Competitor Intelligence

Competitor research is stored separately from official filing records:

- `regulatory_competitors`: competitor identity.
- `regulatory_competitor_offerings`: advertised services, price text, source URL, and included features.
- `regulatory_competitor_gap_signals`: missing SOSFiler capabilities or blocked filings that need official-source follow-up.

Competitor data must not mark a filing `verified_official`. It is a discovery and product-comparison signal only.

## SOSFiler Service Catalog

SOSFiler services are stored separately from government filing records:

- `sosfiler_services`: sellable service definitions, categories, lifecycle stage, and related filing category.
- `sosfiler_service_prices`: SOSFiler service prices. These exclude government fees unless explicitly stated.
- `sosfiler_service_competitor_benchmarks`: competitor prices and source URLs used to calibrate SOSFiler pricing.

Seed file:

```text
data/regulatory/sosfiler_service_catalog.json
```

## Next Build Step

After the first import succeeds, update the regulatory runner so new Firecrawl/Playwright results write directly to Supabase and optionally export JSON snapshots for audit.
