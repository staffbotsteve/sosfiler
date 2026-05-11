# Execution Persistence Cutover

SOSFiler currently runs execution-platform persistence in dual mode:

```env
EXECUTION_PERSISTENCE_MODE=dual
```

SQLite remains the local compatibility store. Supabase/Postgres receives backend-only dual-writes for quotes, filing jobs, support tickets, automation runs, events, and evidence artifacts.

## Operator Checks

Use the operator cockpit:

- `GET /api/admin/persistence-health` verifies the configured Supabase/Postgres connection.
- `GET /api/admin/persistence-sync-status` compares SQLite execution-table counts with Supabase execution-table counts.
- `GET /api/admin/persistence/cutover-readiness` returns the go/no-go checklist for switching `EXECUTION_PERSISTENCE_MODE=supabase`.
- `POST /api/admin/persistence/backfill-execution` backfills local execution records into Supabase.

The backfill endpoint defaults to `dry_run: true`. It also skips append-only tables unless `include_append_only` is explicitly true. Keep that default unless you are intentionally copying a fresh environment, because append-only event/artifact/payment rows can duplicate history if replayed.

## Current Safe Mode

Read paths still come from SQLite. Do not switch to Supabase reads until:

1. `persistence-health` is healthy.
2. `persistence-sync-status` shows expected parity for the execution tables.
3. A dry-run filing job creates matching Supabase rows for job, run, and event.
4. Operators confirm the cockpit queue still reflects the expected local jobs.

## Cutover Gate

Run the new migration before relying on webhook idempotency or cutover readiness:

```text
supabase/migrations/20260509211500_execution_cutover_and_stripe_events.sql
```

That migration adds `public.stripe_webhook_events`, enables RLS, grants service-role access only, and adds an idempotency index to `execution_payment_ledger`. The Supabase CLI was not available in this workspace when the migration was created, so review and apply it through the Supabase SQL editor or `supabase db push` from an environment where the CLI is installed.

Treat `ready: true` from `/api/admin/persistence/cutover-readiness` as necessary but not sufficient. The actual cutover should still happen during a quiet filing window with rollback available:

1. Backfill core tables with `include_append_only=false`.
2. Verify cutover readiness with zero core deltas.
3. Set `EXECUTION_PERSISTENCE_MODE=supabase`.
4. Restart and confirm `/api/health/deep`, `/api/admin/persistence-health`, and `/api/admin/persistence/cutover-readiness`.
5. Keep the SQLite file unchanged until at least one payment authorization, filing job, and Slack ticket loop have succeeded.
