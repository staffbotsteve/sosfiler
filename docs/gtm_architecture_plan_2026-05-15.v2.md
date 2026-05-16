# SOSFiler — GTM + Architecture + Development Plan (v2)

Date: 2026-05-15
Author: Claude (Opus 4.7 1M)
Owner: Steven Swan
Status: DRAFT v2.6 — supersedes v2.5 (v2.4, v2.3, v2.2, v2.1, v2, v1). Awaiting `/codex review` PASS, then user approval before execution.

v2.6 changes (codex round-7 findings, 2026-05-15):
- Fixed PL/pgSQL function body terminator: `END; $$;` (an END for the BEGIN/END block, then close the dollar-quoted string).
- Added JSON type assertion to both SQLite and Postgres predicates so `{"value": 123}` fails (`json_type/jsonb_typeof` must be `'string'`).

v2.5 changes (codex round-6 findings, 2026-05-15):
- Mirrored the JSON `$.value` predicate exactly on the Postgres trigger (`{}`, raw string, `{"value":""}` all fail).
- Tightened §4.2.3 wording so app, SQLite trigger, and Postgres trigger all enforce the same JSON-shape predicate.

v2.4 changes (codex round-5 findings, 2026-05-15):
- Made the Postgres trigger explicitly action-aware to match the SQLite trigger (round-4 conjunction fix was only documented for SQLite by accident).
- Closed the `is_evidence` gap: `backend/server.py:9819` currently flags is_evidence=1 only for {submitted_receipt, approved_certificate, state_correspondence, rejection_notice, ein_confirmation_letter}. Plan now extends this set to include `state_acknowledgment`, `state_filed_document` (and where action-appropriate, `registered_agent_partner_order` stays non-evidence). Without this, uploads of the newly accepted terminal types would have is_evidence=0 and the invariant would still reject them.
- Widened the `annual-report/mark-complete` route accepted set (currently `{approved_certificate, state_correspondence}` at `backend/server.py:10031`) to `{approved_certificate, state_correspondence, state_acknowledgment, state_filed_document}` so the action-aware predicate is reachable from the route.
- Pinned `orders.filing_confirmation` format. Today the column is read with `json.loads(...)` at `backend/server.py:8539`. v2.4 standardizes the column as a JSON-serialized object `{"value": "<state-issued #>", "issued_at": "...", "source": "regex|operator|adapter"}` so the existing JSON reader keeps working and the trigger predicate becomes `json_valid(orders.filing_confirmation) AND length(json_extract(orders.filing_confirmation, '$.value')) > 0` instead of the raw-string check.

v2.3 changes (codex round-4 findings, 2026-05-15):
- Made the evidence predicate action-aware. Annual reports legitimately complete with `state_correspondence` (per `backend/server.py:10027`); formation completes with `approved_certificate`. The trigger and helpers now branch on `filing_jobs.action_type`.
- Fixed the formation-availability per-state shape to match the real projector (`state`, `state_name`, `sellable_now`, `customer_offer_status`, `fulfillment_lane`, `headline`, `message`, `timeline`, `operator_sla_hours`, `automation_scope`, `pricing`, `post_formation_workflows`).
- Called out that `FilingEvidenceRequest` (`backend/server.py:975`) needs its allowed-types pattern widened to include `registered_agent_partner_order`, `state_filed_document`, `state_acknowledgment` — added as an explicit API-schema change item.

v2.2 changes (codex round-3 findings, 2026-05-15):
- SQLite trigger for `approved/documents_collected/complete` now requires BOTH a prior `submitted_receipt` AND an `approved_certificate` (round-2 wording missed the conjunction).
- Added an explicit workstream for capturing `orders.filing_confirmation` (no current code path writes it; without this, the trigger would block all legitimate transitions). Includes `FilingTransitionRequest` payload extension, per-adapter regex capture, and a fallback for operator-keyed entry.
- Added `execution_filing_jobs.filing_confirmation TEXT` Postgres migration plus dual-write update in `SupabaseExecutionRepository.filing_job_payload()` / `upsert_filing_job()`.
- Expanded artifact-type vocabulary to include `registered_agent_partner_order`, `state_filed_document`, `state_acknowledgment` (non-evidence types; documented for completeness).
- Rewrote `/api/formation-availability` extension against the actual response shape (`{version, generated_at, summary, states: [...]}`), not the imagined top-level state-code map.

v2.1 changes (codex round-2 findings, 2026-05-15):
- Fixed evidence invariant to reference real artifact columns (`file_path` SQLite, `storage_path` Postgres) and real artifact-type vocabulary (`submitted_receipt`, `approved_certificate`, `state_correspondence`, …), not invented column/type names.
- Added explicit artifact-schema migration adding `sha256_hex` and `superseded_by_artifact_id`; documented as a precondition.
- Replaced "Postgres CHECK" with a Postgres trigger function on `public.execution_filing_jobs` (CHECK constraints cannot reference other tables).
- Added `needs_evidence_reverification` to the state machine alongside `closed_unfiled`; covered transition allowlist, UI copy, customer/operator handling.
- Corrected route name: `/api/admin/filing-jobs/{job_id}/transition` (not `operator`).
- Specified the exact additive shape of `/api/formation-availability` so the contract change is real, not vague.

v2 changes vs v1 (codex round-1 findings, 2026-05-15):
- Replaces invented `filings` table with the real schema (`orders`, `filing_jobs`, `filing_artifacts`).
- Replaces "rename CorpNet → FilePro" with a provider-abstraction workstream and a separate upstream-vendor table set.
- Recasts expedite data work: backfill 49 jurisdictions (not 16), using `filing_actions.generated.json` schema after promotion.
- Corrects Stripe economics: separate line items do NOT avoid card processing fees; pricing transparency is the only benefit.
- Defers the second browser-worker droplet; first step is Xvfb on the existing droplet.
- Aligns adapter contract to real ABC (`preflight / submit / check_status / collect_documents / requires_operator`) and treats additions as an explicit interface change.
- Reorders the calendar so each track's dependencies actually precede it.
- Marks every UI-touching item as blocking on Steven's approval. No "new pages" loophole.
- Tightens multi-agent execution to a concrete write-ownership model or removes it.
- Reconciles repo geography: app working tree is `<repo>/app/`; Supabase migrations live at `<repo>/supabase/migrations/`. The `app/` tree contains no `supabase/` directory.

The repo at `github.com/staffbotsteve/sosfiler` is authoritative. Anywhere this plan disagrees with the repo, the repo wins and this plan must be updated.

---

## 1. North Star (unchanged)

> Form and manage a U.S. entity for $29 + official state fee. Annual filings for $29 + official state fee. Evidence-backed status. Automation-first; operators handle protected gates. FilePro is the registered-agent backbone in all 51 jurisdictions.

Load-bearing claims:

1. Service fee fixed at $29 formation, $29 annual report.
2. Evidence-or-it-didn't-happen — a state filing reaches a terminal "complete" state only after the app has captured a state-issued confirmation identifier AND a receipt artifact whose existence is verified at the time of transition.
3. Operator is exception, not norm.

## 2. Decisions locked in (2026-05-15)

1. FilePro is the sole registered-agent partner.
2. Hosting is DigitalOcean. Existing droplet `146.190.140.164` (`ops.sosfiler.com`) remains the app host. Second worker droplet is deferred behind a measured-need gate (see §4.6).
3. Non-headless Playwright is permitted via `Xvfb` on the existing droplet first. Move to a dedicated worker droplet only when the existing host shows CPU or memory pressure under real load.
4. Multiple agents may run in parallel under the rules in §8.
5. UI changes — including new public pages — require Steven's explicit pre-approval per change. There is no "new pages don't count" carve-out.

## 3. Repo reality (verified 2026-05-15)

Working tree paths referenced below are relative to `/Users/stevenswan/project-folders/sosfiler/`.

### 3.1 Tables present in `app/sosfiler.db` (production-shaped SQLite)

```
admin_audit_events           filing_artifacts          partner_orders
admin_sessions               filing_events             partner_webhook_endpoints
auth_mfa_challenges          filing_jobs               partner_webhook_events
auth_recovery_tokens         health_check_markers      partners
automation_runs              license_cache             payment_ledger
chat_sessions                license_filings           pii_access_events
compliance_deadlines         orders                    pii_vault
document_access_events       partner_api_keys          status_updates
documents                    partner_applications      stripe_webhook_events
execution_quotes                                       support_tickets
                                                       users
```

Key columns on `orders`: `id, status, entity_type, state, business_name, formation_data, stripe_session_id, stripe_payment_intent, state_fee_cents, gov_processing_fee_cents, platform_fee_cents, total_cents, filing_confirmation, ein, paid_at, filed_at, approved_at, documents_ready_at, product_type, service_code, parent_order_id`.

Key columns on `filing_jobs`: `id, order_id, action_type, state, entity_type, status, automation_level, filing_method, office, form_name, portal_name, portal_url, state_fee_cents, processing_fee_cents, total_government_cents, required_consents, required_evidence, evidence_summary, last_error, submitted_at, approved_at, automation_lane, automation_difficulty, adapter_key, customer_status, portal_blockers, route_metadata`.

There is no `filing_number` column and no `receipt_path` column. There is no `filings` table. Evidence artifacts live in a separate `filing_artifacts` table referenced by `filing_jobs.id`.

### 3.2 State machine reality

`backend/execution_platform.py` defines:

- `UNIVERSAL_FILING_STATES` — normalized vocabulary
- `LEGACY_STATUS_ALIASES` — maps `submitted_to_state → submitted`, `state_approved → approved`, etc.
- `ALLOWED_TRANSITIONS` — including `rejected_or_needs_correction → complete`
- `EVIDENCE_REQUIRED_STATES = {"submitted", "approved", "documents_collected", "complete"}`

The legacy aliases live alongside normalized statuses in production rows. Any constraint must consider both vocabularies.

### 3.3 Adapter ABC

`FilingAdapter` (in `backend/execution_platform.py:98`) requires:
- `async def preflight(filing_job) -> FilingActionResult`
- `async def submit(filing_job) -> FilingActionResult`
- `async def check_status(filing_job) -> FilingActionResult`
- `async def collect_documents(filing_job) -> FilingActionResult`
- `def requires_operator(filing_job) -> bool`

Most concrete adapters in `backend/filing_adapters.py` route through a `DryRunLaneAdapter` whose `submit()` refuses live submission. The fully-live adapters today are CA (`ca_bizfile_worker.py`), NV (`silverflume_filer.py`), and TX (`tx_sosdirect_document_worker.py`).

### 3.4 Partner tables — direction matters

`partners`, `partner_api_keys`, `partner_applications`, `partner_orders`, `partner_webhook_endpoints`, `partner_webhook_events` are all **SOSFiler-as-provider** tables: outside parties consume our API to file. They are not upstream-vendor records for SOSFiler's relationship with FilePro/CorpNet.

`backend/server.py:10368` `fulfill_registered_agent_assignment` rejects any provider not equal to `corpnet`. The CorpNet client is hardcoded throughout payload assembly, status parsing, and artifact ingestion.

### 3.5 Expedite data reality

- `data/filing_actions.generated.json` — generated from regulatory research worker. 35 jurisdictions present. **Zero** records contain populated `expedite_options`. Schema (in `filing_actions.schema.json`) supports the field; data does not.
- `data/filing_actions.json` — curated, ~2 records with `expedite_options` (CA Corp formation; TX). This is where the rich CA tiers cited in v1 came from. v1 conflated the two files.
- `data/state_fees.json` — single-tier legacy. 16/51 LLC + 16/51 Corp rows have `expedited` populated. Many are stale.

Promotion path for v2: `filing_actions.json` (curated) is authoritative for expedite tiers. The generated file is a research pipeline input. `state_fees.json.expedited` becomes a derived projection used only by older callers, marked deprecated, and not relied on for checkout.

### 3.6 Supabase migrations

Live at `<repo>/supabase/migrations/`, outside the `app/` working tree. Four files: regulatory research schema, execution platform schema, execution cutover + Stripe events, partner API schema. Postgres cutover requires Supabase project + service-role key in `.env` on the droplet; not yet wired for runtime use as far as v2 has verified.

---

## 4. Architecture (v2)

### 4.1 Topology — start simple

Phase A (default; no second droplet):

```
                 ┌──────────────────────────────┐
                 │ Customer Web (HTML/PWA)      │
                 │ frontend/* served by FastAPI │
                 └──────────────┬───────────────┘
                                │ HTTPS
┌───────────────────────────────▼────────────────────────────────────┐
│ DROPLET-APP  146.190.140.164  (existing)                           │
│   FastAPI / Uvicorn  (backend/server.py)                           │
│   SQLite WAL (production)                                          │
│   Workers (systemd): EIN queue, research, status listener,         │
│                       CA bizfile, NV silverflume, TX sosdirect     │
│   NEW: xvfb.service + sosfiler-browser-headed.service              │
│   NEW: FilePro client (provider abstraction)                       │
└────────────┬───────────────────────────────────────┬───────────────┘
             │                                       │
             ▼                                       ▼
   FilePro Partner API                    DigitalOcean Spaces
   (RA address, renewals,                 evidence/ (versioned + checksum)
    service-of-process webhooks)
```

Phase B (only if metrics warrant; gating rule in §4.6):

Add a second droplet running only the headed browser workers, pulling jobs from a Postgres-backed queue.

### 4.2 The evidence invariant (corrected to real schema)

The bug being prevented: an order or filing_job reaching a customer-visible "complete" or terminal-positive status without proof.

#### 4.2.1 Real schema, verified 2026-05-15

SQLite `filing_artifacts` columns: `id, filing_job_id, order_id, artifact_type, filename, file_path, is_evidence, created_at`. No `sha256_hex`, no `storage_url`, no `superseded_by_artifact_id`.

Postgres `public.execution_artifacts` columns: `id (uuid), order_id, filing_job_id (uuid), artifact_type, filename, storage_path, issuer, source_url, visibility, is_evidence, created_at`. Same omissions.

Real artifact-type vocabulary in use across `backend/`: `submitted_receipt`, `approved_certificate`, `state_correspondence`, `rejection_notice`, `registered_agent_consent`, `registered_agent_assignment`, `filing_authorization`, `annual_report_packet`, `ein_confirmation_letter`, `submitted_document`, `other`. The regex is hard-coded at `backend/server.py:975` and `backend/server.py:1837–1850` flags `submitted_receipt` and `approved_certificate` as `official_evidence`.

#### 4.2.2 Schema additions (precondition migration)

This migration ships before any invariant work:

```sql
-- SQLite
ALTER TABLE filing_artifacts ADD COLUMN sha256_hex TEXT;
ALTER TABLE filing_artifacts ADD COLUMN superseded_by_artifact_id INTEGER;
CREATE INDEX idx_filing_artifacts_job_type ON filing_artifacts(filing_job_id, artifact_type, is_evidence);

-- Postgres (separate migration file)
ALTER TABLE public.execution_artifacts ADD COLUMN IF NOT EXISTS sha256_hex TEXT;
ALTER TABLE public.execution_artifacts ADD COLUMN IF NOT EXISTS superseded_by_artifact_id UUID;
CREATE INDEX IF NOT EXISTS idx_execution_artifacts_job_type
  ON public.execution_artifacts(filing_job_id, artifact_type, is_evidence);
```

Code that writes artifacts is updated to populate `sha256_hex` at write time. Existing rows backfilled by a one-shot script that hashes their stored files; if a file is missing, `sha256_hex` stays NULL and the row is excluded from the evidence predicate.

#### 4.2.3 Definition of proof (action-aware)

A state machine transition into any status in `EVIDENCE_REQUIRED_STATES` (after legacy-alias normalization) requires:

- `orders.filing_confirmation` is a JSON-serialized object of the shape `{"value": "...", "issued_at": "...", "source": "regex|operator|adapter"}` whose `$.value` is a non-empty string (the state-issued identifier). Raw strings, empty JSON, and `{"value":""}` all fail. The same predicate is applied identically in the application layer, SQLite trigger, and Postgres trigger (see §4.2.4).
- For transitions targeting `submitted` (any `action_type`): at least one `filing_artifacts` row with `filing_job_id = <this job>` AND `artifact_type = 'submitted_receipt'` AND `is_evidence = 1` AND `sha256_hex IS NOT NULL` AND `file_path` resolves to a file whose magic bytes match its declared MIME (PDF: `%PDF`; PNG: `\x89PNG`).
- For transitions targeting `approved` / `documents_collected` / `complete`, the acceptable terminal-evidence artifact type depends on `filing_jobs.action_type`:

   | action_type | terminal-evidence artifact types accepted |
   |---|---|
   | `formation` | `approved_certificate` |
   | `annual_report` | `approved_certificate` OR `state_correspondence` OR `state_acknowledgment` OR `state_filed_document` (matches `backend/server.py:10027` behavior) |
   | `amendment`, `foreign_qualification`, `dissolution`, `reinstatement`, `certificate_of_good_standing` | `approved_certificate` OR `state_filed_document` |
   | anything else | `approved_certificate` (conservative default; new action types must add their entry here before launch) |

   The accepted-type set is named `TERMINAL_EVIDENCE_ARTIFACT_TYPES_BY_ACTION` and lives next to `EVIDENCE_REQUIRED_STATES` in `execution_platform.py` so app, repository, and migration code all read the same source of truth.
- A prior `submitted_receipt` row must still exist (no orphans), regardless of `action_type`.
- The transition request payload includes (or the route can extract) a state-specific `confirmation_number_regex` match against either the artifact's text-extracted content OR the captured confirmation-page DOM. The regex set is declared per adapter in §4.5.

Each condition is checked at the time the transition is requested, not at row create time.

#### 4.2.3.1 Three coordinated widenings (schema + is_evidence + route)

Three concrete changes ship together; widening any one in isolation leaves a gap.

1. **`FilingEvidenceRequest` schema** — `backend/server.py:975` currently restricts `artifact_type` to `submitted_receipt|submitted_document|approved_certificate|state_correspondence|rejection_notice|registered_agent_consent|registered_agent_assignment|filing_authorization|annual_report_packet|ein_confirmation_letter|other`. v2.4 widens the pattern to additionally accept `registered_agent_partner_order`, `state_filed_document`, `state_acknowledgment`.

2. **`is_evidence` flagging on upload** — `backend/server.py:9819` sets `is_evidence = 1` only for `{submitted_receipt, approved_certificate, state_correspondence, rejection_notice, ein_confirmation_letter}`. v2.4 extends this set to `{submitted_receipt, approved_certificate, state_correspondence, state_acknowledgment, state_filed_document, rejection_notice, ein_confirmation_letter}`. `registered_agent_partner_order` stays `is_evidence=0` (it is procurement, not filing evidence). Without this step, any newly accepted terminal artifact uploaded through the public route would write with `is_evidence=0` and the trigger predicate (`is_evidence = 1`) would still reject the transition.

3. **`annual-report/mark-complete` accepted types** — `backend/server.py:10031` currently rejects anything other than `{approved_certificate, state_correspondence}`. v2.4 widens this route's accepted set to `{approved_certificate, state_correspondence, state_acknowledgment, state_filed_document}` so the action-aware predicate is reachable from the route. The same is done for any other route that hardcodes an accepted-types set on a completion path; this plan also audits `mark-submitted` and `mark-approved` (`backend/server.py:9867`, `9949`) and widens or restricts them per the action-aware table in §4.2.3.

Test coverage:
- `qa/test_filing_evidence_request_schema.py` (new) — schema accepts the new types.
- `qa/test_evidence_is_evidence_flagging.py` (new) — uploads of each new terminal type write `is_evidence=1`.
- `qa/test_annual_report_completion_routes.py` (new) — completion accepts each terminal type per the action-aware table and rejects ineligible types.

#### 4.2.4 Layered enforcement

1. **Application layer** — `POST /api/admin/filing-jobs/{job_id}/transition` (route at `backend/server.py:8879`) is patched to call a new `enforce_evidence_for_transition(job, target_status, evidence)` helper that runs the §4.2.3 predicate after `LEGACY_STATUS_ALIASES` normalization. On fail: HTTP 409 with structured error code `EVIDENCE_INVARIANT_FAILED`. The route's existing `evidence` payload (`backend/server.py:8896–8898`) is extended so adapter callers also pass it through (already happens for the manual-evidence path; the automation path needs the same plumbing).
2. **Repository layer** — `execution_repository` gets a new `update_filing_status_with_evidence(job_id, target_status, evidence_refs)` that re-runs the §4.2.3 predicate before any UPDATE. The existing `update_filing_status` is deprecated and (after a grace period) deleted; the linter forbids new callers.
3. **SQLite BEFORE-UPDATE trigger on `filing_jobs`** — guards against any code path that bypasses the repository. For approved-class transitions the trigger requires BOTH a prior `submitted_receipt` AND an `approved_certificate`:

   ```sql
   CREATE TRIGGER filing_jobs_evidence_invariant
   BEFORE UPDATE ON filing_jobs
   FOR EACH ROW WHEN
     NEW.status IN ('submitted','submitted_to_state','approved','state_approved',
                    'documents_collected','complete')
   BEGIN
     SELECT CASE
       WHEN (SELECT filing_confirmation FROM orders WHERE id = NEW.order_id) IS NULL
            OR NOT json_valid((SELECT filing_confirmation FROM orders WHERE id = NEW.order_id))
            OR json_type((SELECT filing_confirmation FROM orders WHERE id = NEW.order_id), '$.value') != 'text'
            OR coalesce(length(json_extract((SELECT filing_confirmation FROM orders WHERE id = NEW.order_id), '$.value')), 0) = 0
         THEN RAISE(ABORT, 'evidence_invariant: missing or malformed filing_confirmation')
       WHEN NEW.status IN ('submitted','submitted_to_state')
            AND NOT EXISTS (SELECT 1 FROM filing_artifacts
                            WHERE filing_job_id = NEW.id
                              AND artifact_type = 'submitted_receipt'
                              AND is_evidence = 1
                              AND sha256_hex IS NOT NULL)
         THEN RAISE(ABORT, 'evidence_invariant: missing submitted_receipt')
       WHEN NEW.status IN ('approved','state_approved','documents_collected','complete')
            AND (NOT EXISTS (SELECT 1 FROM filing_artifacts
                             WHERE filing_job_id = NEW.id
                               AND artifact_type = 'submitted_receipt'
                               AND is_evidence = 1
                               AND sha256_hex IS NOT NULL)
                 OR NOT EXISTS (
                   SELECT 1 FROM filing_artifacts
                   WHERE filing_job_id = NEW.id
                     AND is_evidence = 1
                     AND sha256_hex IS NOT NULL
                     AND (
                       (NEW.action_type = 'formation' AND artifact_type = 'approved_certificate')
                       OR (NEW.action_type = 'annual_report'
                           AND artifact_type IN ('approved_certificate','state_correspondence',
                                                 'state_acknowledgment','state_filed_document'))
                       OR (NEW.action_type IN ('amendment','foreign_qualification','dissolution',
                                               'reinstatement','certificate_of_good_standing')
                           AND artifact_type IN ('approved_certificate','state_filed_document'))
                       OR (NEW.action_type NOT IN ('formation','annual_report','amendment',
                                                   'foreign_qualification','dissolution',
                                                   'reinstatement','certificate_of_good_standing')
                           AND artifact_type = 'approved_certificate')
                     )
                 ))
         THEN RAISE(ABORT, 'evidence_invariant: approved status requires submitted_receipt and an action_type-appropriate terminal artifact')
     END;
   END;
   ```

   The legacy aliases (`submitted_to_state`, `state_approved`) are included so the trigger catches old code that hasn't been normalized yet.

4. **Postgres trigger function on `public.execution_filing_jobs`** — `CHECK` constraints cannot reference other tables, so this is a `BEFORE UPDATE` trigger calling a `plpgsql` function. The function applies the SAME action-aware predicate as the SQLite trigger in §4.2.4 step 3, branching on `NEW.action_type`:

   ```sql
   CREATE OR REPLACE FUNCTION public.enforce_filing_evidence_invariant()
   RETURNS trigger LANGUAGE plpgsql AS $$
   DECLARE
     conf_value text;
     terminal_types text[];
   BEGIN
     IF NEW.status NOT IN ('submitted','submitted_to_state','approved','state_approved',
                           'documents_collected','complete') THEN
       RETURN NEW;
     END IF;

     -- filing_confirmation precondition (read from execution_filing_jobs.filing_confirmation)
     -- Must be a JSON object whose $.value is a non-empty STRING.
     -- Raw strings, {}, {"value":""}, and {"value": 123} all fail.
     BEGIN
       IF jsonb_typeof(NEW.filing_confirmation::jsonb -> 'value') <> 'string' THEN
         RAISE EXCEPTION 'evidence_invariant: filing_confirmation $.value is not a string' USING ERRCODE = '23514';
       END IF;
       conf_value := NEW.filing_confirmation::jsonb ->> 'value';
     EXCEPTION
       WHEN invalid_text_representation THEN
         RAISE EXCEPTION 'evidence_invariant: filing_confirmation is not valid JSON' USING ERRCODE = '23514';
       WHEN others THEN
         RAISE EXCEPTION 'evidence_invariant: malformed filing_confirmation' USING ERRCODE = '23514';
     END;
     IF conf_value IS NULL OR length(conf_value) = 0 THEN
       RAISE EXCEPTION 'evidence_invariant: filing_confirmation $.value is empty' USING ERRCODE = '23514';
     END IF;

     -- submitted: any action_type requires submitted_receipt
     IF NEW.status IN ('submitted','submitted_to_state') THEN
       IF NOT EXISTS (
         SELECT 1 FROM public.execution_artifacts
         WHERE filing_job_id = NEW.id AND artifact_type = 'submitted_receipt'
           AND is_evidence = true AND sha256_hex IS NOT NULL
       ) THEN
         RAISE EXCEPTION 'evidence_invariant: missing submitted_receipt' USING ERRCODE = '23514';
       END IF;
       RETURN NEW;
     END IF;

     -- approved-class: terminal artifact set depends on action_type
     terminal_types := CASE
       WHEN NEW.action_type = 'formation'      THEN ARRAY['approved_certificate']
       WHEN NEW.action_type = 'annual_report'  THEN ARRAY['approved_certificate','state_correspondence',
                                                          'state_acknowledgment','state_filed_document']
       WHEN NEW.action_type IN ('amendment','foreign_qualification','dissolution',
                                'reinstatement','certificate_of_good_standing')
         THEN ARRAY['approved_certificate','state_filed_document']
       ELSE ARRAY['approved_certificate']
     END;

     IF NOT EXISTS (
       SELECT 1 FROM public.execution_artifacts
       WHERE filing_job_id = NEW.id AND artifact_type = 'submitted_receipt'
         AND is_evidence = true AND sha256_hex IS NOT NULL
     ) OR NOT EXISTS (
       SELECT 1 FROM public.execution_artifacts
       WHERE filing_job_id = NEW.id AND artifact_type = ANY(terminal_types)
         AND is_evidence = true AND sha256_hex IS NOT NULL
     ) THEN
       RAISE EXCEPTION 'evidence_invariant: approved status requires submitted_receipt and an action_type-appropriate terminal artifact'
         USING ERRCODE = '23514';
     END IF;

     RETURN NEW;
   END;
   $$;

   CREATE TRIGGER trg_execution_filing_jobs_evidence
     BEFORE UPDATE ON public.execution_filing_jobs
     FOR EACH ROW EXECUTE FUNCTION public.enforce_filing_evidence_invariant();
   ```

   A prerequisite migration adds `execution_filing_jobs.filing_confirmation TEXT` and `idx_execution_filing_jobs_filing_confirmation`. `SupabaseExecutionRepository.filing_job_payload()` and `upsert_filing_job()` (`backend/execution_repository.py:97`) are updated to carry the new column on every write so the dual-write stays in sync. The Postgres column stores the same JSON shape as the SQLite column (`{"value":"...","issued_at":"...","source":"..."}`); the trigger's emptiness check examines that shape.

   The artifact-type vocabulary used by the predicate matches `TERMINAL_EVIDENCE_ARTIFACT_TYPES_BY_ACTION` declared in `execution_platform.py`. Other artifact types in the codebase (`rejection_notice`, `registered_agent_consent`, `registered_agent_assignment`, `registered_agent_partner_order`, `filing_authorization`, `annual_report_packet`, `ein_confirmation_letter`, `submitted_document`, `other`) are catalogued but not load-bearing for terminal evidence.

5. **Transition allowlist tightened** — `rejected_or_needs_correction → complete` is removed from `ALLOWED_TRANSITIONS`. The legitimate terminal for a job that was rejected and abandoned is a new status `closed_unfiled` (no evidence required; non-revenue-recognizing; customer-visible copy: "Filing closed; not submitted"). The path that today writes `complete` from a rejection is updated to write `closed_unfiled`.

6. **Backfill** — rows currently in `EVIDENCE_REQUIRED_STATES` (or their legacy aliases) that fail the §4.2.3 predicate are flipped to a new status `needs_evidence_reverification` by a one-shot migration script. Both `needs_evidence_reverification` and `closed_unfiled` are added to `UNIVERSAL_FILING_STATES`, `LEGACY_STATUS_ALIASES` (no aliases needed; both are normalized terms), `ALLOWED_TRANSITIONS` (allowed targets from `needs_evidence_reverification`: `{submitted, approved, complete, closed_unfiled, operator_required}`), customer dashboard copy ("Reverifying filing evidence"), and operator-cockpit work queue.

#### 4.2.5 Filing-confirmation capture (precondition; today no path writes `orders.filing_confirmation`)

The trigger requires a non-empty `orders.filing_confirmation`, but the column is presently unused by any write path in the repo. Without a capture story, every legitimate transition would be blocked by the trigger. This workstream lands before the trigger:

1. **`FilingTransitionRequest` payload** (`backend/server.py` around line 881) gains an optional `filing_confirmation: str | None` field. When the target status is in `EVIDENCE_REQUIRED_STATES` after normalization, the request must include either a non-empty `filing_confirmation` OR the route must be able to derive one from the most recent `submitted_receipt` artifact's parsed content using the per-adapter regex from §4.5. Missing both → HTTP 409 with `code=MISSING_FILING_CONFIRMATION`.
2. **Per-adapter regex extraction** — every live adapter (`ca_bizfile_worker.py`, `silverflume_filer.py`, `tx_sosdirect_document_worker.py`, and new lane adapters) declares a `confirmation_number_regex` and is required to run it against the confirmation-page DOM at the moment it writes its `submitted_receipt` artifact. The extracted value is passed to `mark-submitted` / the transition route, which writes it to `orders.filing_confirmation` in the same transaction as the status update.
3. **Operator manual entry** — the operator cockpit transition form exposes a `Filing confirmation #` input that is required when promoting a job to a state with no extracted value. Existing UI; the field is admin-only so this is not a customer-facing UI change.
4. **Backfill** — for the (small) set of historical orders already in `approved`/`complete` without `filing_confirmation`, a one-shot script reads the `approved_certificate` artifact's filename / parsed text and best-effort populates the column. Rows that cannot be backfilled are flagged `needs_evidence_reverification` per §4.2.4 step 6.

### 4.3 FilePro as a provider abstraction (not a rename)

Step 1 — Define `RegisteredAgentProvider` Protocol:

```python
class RegisteredAgentProvider(Protocol):
    code: str  # "corpnet", "filepro"
    def quote(self, payload: dict) -> dict: ...
    def create_order(self, payload: dict) -> dict: ...
    def get_order_status(self, external_id: str) -> dict: ...
    def get_ra_address(self, state: str) -> dict: ...
    def verify_webhook(self, headers: dict, body: bytes) -> bool: ...
    def parse_status_webhook(self, body: dict) -> dict: ...
```

Step 2 — Two concrete implementations: keep `CorpNetClient` (deprecated, dry-run unless creds configured), add `FileProClient` (also dry-run until creds). Both surface through a `get_active_ra_provider()` resolver reading `RA_PROVIDER_CODE` env var (default `filepro`).

Step 3 — Refactor `fulfill_registered_agent_assignment` and all `corpnet`-string call sites to use the abstraction. `provider == "corpnet"` checks become `provider == get_active_ra_provider().code`. Tests assert both providers can be selected.

Step 4 — New upstream-vendor tables (separate from `partners/*` which are downstream):

```sql
ra_vendors                -- one row per upstream provider
  id, code, display_name, dry_run BOOLEAN, created_at, updated_at, secrets_ref

ra_vendor_orders          -- our orders placed with the vendor
  id, vendor_code, our_order_id, external_id, state, status,
  raw_request, raw_response, last_error, created_at, updated_at

ra_vendor_webhook_events  -- inbound from vendor
  id, vendor_code, external_id, event_type, signature, payload,
  received_at, processed_at, processing_error
```

These tables ship as a new Supabase migration plus an SQLite mirror. Existing `partner_*` tables are untouched. The plan does NOT reuse them for upstream vendor flows.

Step 5 — FilePro credentials gating: until `FILEPRO_API_BASE_URL` and `FILEPRO_API_KEY` are configured, every FilePro call returns a `dry_run=True` shape and the legacy CorpNet path is still selectable by setting `RA_PROVIDER_CODE=corpnet`. Switchover to live FilePro happens by env var only.

### 4.4 Expedite-fee pass-through (corrected scope)

#### Customer promise

For every supported filing (formation; amendment; certificate of good standing; foreign qualification; reinstatement; dissolution), if the state publishes optional expedite tiers, SOSFiler lists those tiers in the wizard at the exact state-posted fee. **SOSFiler adds $0 markup.** Default selection is the no-extra-cost standard tier.

#### Data reality and the actual backfill

- Authoritative file: `data/filing_actions.json` (curated). Today: 2 records (CA, TX) with `expedite_options` populated.
- Coverage gap: 49 jurisdictions × N action types each. Each row needs source-URL verification.
- Backfill plan: prioritize the v1 launch eight (WY/FL/TX/CO/NM/OH/MO/NV), then top-revenue/heavy-volume states (CA, DE, IL, MA, NY, PA, FL Corp, WA), then long tail.
- For each tier: `label`, `fee_cents`, `processing_time`, `channel`, `customer_selectable` (bool), `combinable` (bool), `requires_preclearance` (bool, optional), `source_url`, `verified_at`.
- `state_review_certifier.py` is extended with a rule: a state may not display an expedite tier whose `verified_at` is older than 90 days.

#### Stripe economics — correction

Stripe charges processing on the total transaction. Splitting state fee and expedite into separate line items is a transparency feature (the customer sees what's SOSFiler vs. what's state), not a processing-fee dodge. v1's claim that line-item separation reduces Stripe cost was wrong. To actually reduce processing-fee exposure on high-fee states would require ACH (Stripe ACH or Plaid) on the state-fee portion, which is out of scope for v1 but on the roadmap.

#### Schema changes for expedite

- `orders` — add columns `expedite_tier_id TEXT NULL`, `expedite_fee_cents INTEGER NOT NULL DEFAULT 0`. Migration backfills 0/NULL for existing rows.
- `filing_jobs` — same two columns to keep operator cockpit accurate.
- `FormationRequest` Pydantic model — add optional `expedite_tier_id: str | None`.
- `build_formation_checkout_quote` — read selected tier from request, validate it exists in `filing_actions.json` for `(state, entity_type, action_type)` and is `customer_selectable=true`, add `expedite_fee_cents` to total. Reject mismatch.
- Cart checkout (`server.py:7203`) — line items become `[platform_fee, state_fee, state_expedite (if any)]` so the receipt is auditable. Stripe processing applies to the sum; no savings claimed.

#### API surface

`/api/formation-availability` (existing endpoint, additive extension):

Actual response shape today, served by `build_public_launch_readiness_report()` in `backend/launch_readiness.py:395`:

```json
{
  "version": 1,
  "generated_at": "2026-05-15T18:00:00Z",
  "summary": {
    "total_jurisdictions": 51,
    "sellable_now": 51,
    "available_automated": 1,
    "available_operator_assisted": 50,
    "not_available": 0
  },
  "states": [
    {
      "state": "WY",
      "state_name": "Wyoming",
      "sellable_now": true,
      "customer_offer_status": "available_operator_assisted",
      "fulfillment_lane": "…",
      "headline": "…",
      "message": "…",
      "timeline": "…",
      "operator_sla_hours": 4,
      "automation_scope": { "application_handles": [ … ], "operator_only_for": "…" },
      "pricing": {
        "currency": "usd",
        "official_government_fee_cents": 10200,
        "sosfiler_fee_cents": 2900,
        "estimated_total_cents": 13100,
        "pricing_status": "published"
      },
      "post_formation_workflows": [ … ]
    }
  ]
}
```

v2.3 additive extension — `public_launch_readiness_state()` (the per-state projector at `backend/launch_readiness.py:360`) gains an `expedite_options[]` field on each `states[i]` object when the (state, entity_type, action_type) tuple has customer-selectable tiers. Default `entity_type=LLC`, `action_type=formation`; both may be passed as query params. Each entry mirrors `data/filing_actions.json`:

```json
{
  "state": "WY",
  "state_name": "Wyoming",
  "sellable_now": true,
  "customer_offer_status": "available_operator_assisted",
  "fulfillment_lane": "…",
  "headline": "…",
  "message": "…",
  "timeline": "…",
  "operator_sla_hours": 4,
  "automation_scope": { "…": "…" },
  "pricing": { "…": "…" },
  "post_formation_workflows": [ … ],
  "expedite_options": [
    {
      "tier_id": "wy_24h",
      "label": "24-Hour Filing Service",
      "fee_cents": 5000,
      "processing_time": "Filed within 24 hours",
      "channel": "online",
      "customer_selectable": true,
      "combinable": false,
      "source_url": "https://…",
      "verified_at": "2026-05-14T22:13:00Z"
    }
  ]
}
```

Existing consumers ignore the new field. New consumers (formation wizard, dashboard expedite picker) consume it. Tests in `qa/test_formation_availability_expedite.py` (new) cover the shape, the omission rule (no tiers ⇒ no field), the 90-day re-verification gate, and the `customer_selectable=false` filter.

`/api/management-actions/{action_type}/availability` (NEW) returns the same shape per action type (e.g., `amendment`, `certificate_of_good_standing`, `foreign_qualification`, `reinstatement`, `dissolution`). Test fixture in `qa/test_management_actions_availability.py`.

#### UI work (BLOCKED on Steven approval)

- Formation wizard: add an "Expedite" step that lists customer-selectable tiers. UI change. Approval required.
- Customer dashboard: post-formation services surface expedite picker. UI change. Approval required.
- Operator cockpit: STALE-adapter view and expedite-tier picker for operator-driven flows. UI change. Approval required.

### 4.5 Drift detection (refined)

`app/automations/canary_runner.py` runs per adapter, but only for adapters whose `submit()` is not the dry-run shim. Initial coverage: CA, NV, TX. Adds WY/FL/CO/NM/OH/MO as their adapters become live (Track B).

Canary loop:
1. Drive portal to the final review screen using a synthetic shell entity.
2. Assert declared `selectors_contract` (new adapter attribute).
3. Verify confirmation-page regex still matches a known fixture.
4. Ping Healthchecks.io.
5. On fail: set `automation_runs.adapter_status = 'stale'` row, block new submissions for that state, page Slack via `notifier.py`.

LLM-assisted patch drafting on canary fail is optional and post-MVP. v2 does not depend on it.

### 4.6 Phase B (second droplet) — measured gate

Phase B starts only when **all three** are true for two consecutive weeks:

1. Mean droplet CPU > 60% during 9–17 PT.
2. P95 filing latency > 5 minutes from `ready_to_file` to `submitted`.
3. Headed browser RAM usage > 4 GB sustained.

Until then, Xvfb on the existing droplet hosts headed sessions. The browser worker becomes a separate systemd unit on the same host with its own user-data-dirs under `/var/lib/sosfiler/profiles/<state>/`.

### 4.7 LLM scope (unchanged)

LLMs may generate operating agreements, resolutions, minutes; draft selector patches when canary fails; summarize support inbound.

LLMs must not adjudicate filing success, decide status promotions, or recommend entity type.

### 4.8 Evidence storage

DO Spaces is the primary store; it lacks Object Lock. v2 mitigates with:

- Append-only `filing_artifacts` rows (no DELETE; UPDATE only allowed to set `superseded_by_artifact_id`).
- SHA-256 stored at ingest. Re-verified on every read used in evidence transitions.
- Daily Merkle root anchored to a separate AWS S3 bucket with Object Lock (Compliance mode). The S3 bucket is the only paid-AWS dependency the plan adds and is justified by the tamper-evident requirement.

---

## 5. Hosting + deployment

### 5.1 Inventory

| Role | Where | Size | Cost / mo |
|---|---|---|---|
| App + workers + headed browser pool (Xvfb) | ops.sosfiler.com (existing) | s-2vcpu-4gb → s-4vcpu-8gb upgrade | $24 → $48 |
| Postgres (Supabase) | Supabase Pro | Pro tier when going live | $25 |
| DO Spaces (evidence) | DO | 250GB included | $5 |
| AWS S3 (Merkle anchor only) | AWS | <1 GB | <$1 |
| Total at launch | | | **~$60–80/mo** |

Phase B adds $48/mo per worker droplet.

### 5.2 Required Steven actions

These block parts of the rollout; not blocking the plan itself:

1. Provision an upgrade to s-4vcpu-8gb if current droplet sizes show pressure during testing. One-liner from me.
2. Supabase project: confirm existing project or create; service-role key into `.env`.
3. FilePro PRO Service Agreement status: any signed contract from `FileForms_PRO_Service_Agreement_SOSFILER_Redline_Verified.docx`? Need partner sandbox URL + API key.
4. DO Spaces bucket `sosfiler-evidence` + AWS S3 bucket `sosfiler-merkle-anchor` (Object Lock Compliance) — access keys to `.env`.
5. Healthchecks.io account.
6. UI approvals for each UI-bound item (§4.4, §6 SEO landings, operator cockpit views).

### 5.3 Deploy mechanics

Existing `scripts/deploy_ops.sh` already handles syncing backend, restarting systemd, running QA tests, and verifying health endpoints. v2 adds two units (xvfb.service + sosfiler-browser-headed.service) to the deploy roster.

---

## 6. Go-to-market

### 6.1 Positioning (unchanged from `docs/sosfiler_world_class_redesign.md`)

> SOSFiler routes every filing through the fastest compliant lane available. $29 + state fee. Evidence-backed. National coverage. No upsells. If we miss our posted SOSFiler-controlled fulfillment SLA, the $29 service fee is refundable.

### 6.2 Price card

| Service | SOSFiler fee | State fee | Total customer pays |
|---|---|---|---|
| LLC formation | $29 | passthrough | $29 + state |
| Corp formation | $29 | passthrough | $29 + state |
| Annual report | $29 | passthrough | $29 + state |
| Registered Agent (FilePro) | $99 / year | n/a | $99 / year |
| EIN | $0 (included in formation) | $0 | included |
| Operating Agreement | $0 (included) | n/a | included |
| Foreign qualification | $99 | passthrough | $99 + state |
| Amendment | $49 | passthrough | $49 + state |
| State expedite (customer-selectable) | **$0 markup** | passthrough | exact state fee |
| BOI filing (when CTA returns) | $29 | n/a | $29 |

Stripe processing is paid on the total transaction. Line-item separation is for customer-visible transparency, not fee avoidance.

### 6.3 Unit economics (corrected)

Stripe assumption: 2.9% + $0.30 on the total transaction. v1 understated this for high-fee states. Three illustrative formations:

| Scenario | State fee | Service fee | Total | Stripe (2.9% + $0.30) | Other COGS | Contribution |
|---|---|---|---|---|---|---|
| WY LLC (cheap state) | $102 | $29 | $131 | $4.10 | $1.40 | $23.50 |
| FL LLC (mid) | $125 | $29 | $154 | $4.77 | $1.40 | $22.83 |
| TX LLC (high) | $300 | $29 | $329 | $9.84 | $1.40 | $17.76 |
| MA LLC + expedite ($500 + $20) | $520 | $29 | $549 | $16.22 | $1.40 | $11.38 |
| CA LLC + 24h expedite ($90 + $350) | $440 | $29 | $469 | $13.90 | $1.40 | $13.70 |

Contribution margin per formation on the SOSFiler $29 fee ranges 39% (MA expedite) to 81% (WY). Average across realistic state mix ≈ 65%.

Recurring revenue lift:
- RA renewal $99/yr (FilePro wholesale TBD; assume $40 → $59 margin)
- Annual report $29/yr + state passthrough
- 3-year LTV target ~$300–$400 per customer

### 6.4 v1 launch states (already sellable; production-push order)

`docs/launch_readiness.md` already markets all 51 jurisdictions as sellable via operator-assisted lanes. v2 invests automation work first in:

1. **WY** — already live conceptually; promote `state_filing.py` flow into `filing_adapters.py` lane
2. **FL** — stable, no account
3. **TX** — finish `tx_sosdirect_document_worker.py`
4. **CO** — instant, online-only; but blocked on FilePro RA evidence (see §6.4.1)
5. **NM** — $50, no annual report
6. **OH** — $99, no annual report
7. **MO** — $50, no annual report
8. **NV** — `silverflume_filer.py` already exists; finish hardening

#### 6.4.1 CO dependency

Colorado portal expects registered-agent verification before formation submission. The matrix fixture used today assumes a SOSFiler-controlled RA. With FilePro as sole RA, CO automation is gated on: FilePro returning a per-state RA name+address contract, AND `state_automation_profiles.py` having the FilePro CO RA values, AND a passing canary that proves CO accepts the FilePro RA on a synthetic entity. CO moves into the launch eight only after those three.

### 6.5 Acquisition (UI-bound items flagged)

1. **SEO landings** — one page per jurisdiction at `/state/{code}`. These are public web pages and therefore UI work. **BLOCKED on Steven approval per page or per template.** Plan does not ship landings without explicit approval.
2. **Paid search** — Google Ads on `[state] LLC formation`. Ads creative is not a UI change but the landing pages they drop into are.
3. **Affiliate referrals** — FilePro upstream referrals from law firms/CPAs.
4. **Programmatic content** — seeded from regulatory research worker.

### 6.6 Retention

- FilePro RA renewal $99/yr
- Annual report $29 + state
- Compliance reminders to push annual-report attach to 70%+
- Foreign qual + amendment cross-sell

---

## 7. Build calendar (8 weeks, dependency-aware)

Each row's dependencies are noted. Where a UI step is required, the entire row is **BLOCKED on approval**.

| Wk | Track A — Platform | Track B — Adapters | Track C — Vendor + GTM | Track D — Expedite |
|---|---|---|---|---|
| 1 | Add `closed_unfiled` to state machine; alias-aware status normalizer in repository | Audit live adapters (CA, NV, TX) for evidence-capture gaps | FilePro credentials request; FilePro client scaffold (dry-run); `ra_vendors` migration | Promote `filing_actions.json` to authoritative; deprecate `state_fees.json.expedited` |
| 1 | Migration: `orders.expedite_tier_id`, `orders.expedite_fee_cents` (and on filing_jobs) | Selectors-contract schema added to base adapter (interface change documented) | `RegisteredAgentProvider` Protocol; resolver | Backfill v1 eight states' expedite tiers from primary sources |
| 2 | Layered evidence enforcement: app + repo + SQLite trigger | Promote WY adapter from generic flow to lane (still headless) | Refactor `fulfill_registered_agent_assignment` to provider abstraction; keep CorpNet selectable | `FormationRequest.expedite_tier_id`; `build_formation_checkout_quote` validates + adds tier |
| 2 | Backfill migration for non-compliant evidence rows → `needs_evidence_reverification` | Xvfb on existing droplet; persistent profiles dir | `ra_vendor_orders`, `ra_vendor_webhook_events` tables | `/api/formation-availability` exposes `expedite_options[]` (additive) |
| 3 | DO Spaces bucket + presigned URLs; SHA-256 at write; magic-bytes check | Promote FL adapter to lane (still headless) | FilePro live wiring blocked on creds; behind feature flag | Cart checkout: line items include expedite (no fee-avoidance claim) |
| 3 | AWS S3 Merkle anchor bucket with Object Lock | Promote TX (finish worker) | FilePro CO RA evidence + canary | Backfill next 8–10 jurisdictions |
| 4 | Daily Merkle anchor cron | Promote NV (already live; harden + lane) | CorpNet path moved to deprecated lane; default `RA_PROVIDER_CODE=filepro` | UI: Expedite step in wizard (**APPROVAL REQUIRED**) |
| 4 | Notifier driven by repository transition events (no DB-trigger claim) | Promote MO + NM (headless) | Operator cockpit RA-vendor switcher (admin-only; no UI redesign yet) | `/api/management-actions/{action_type}/availability` (NEW) |
| 5 | Postgres cutover: connect existing Supabase migrations to droplet runtime | Promote OH (headless) | Annual-report renewal flow with FilePro hooks | Dashboard expedite picker for post-formation actions (**APPROVAL REQUIRED**) |
| 6 | Drift canary runner v1 (CA/NV/TX/WY/FL only) | Promote CO once FilePro RA canary passes | Inbound state-acceptance email parser (`filing_status_listener.py` extension) | Backfill remaining jurisdictions |
| 7 | Cost dashboard, per-state COGS (admin-only; no public UI yet) | First v2 adapter: GA Experian KBA attended | Launch Ads on WY (no landing pages without approval) | 90-day source-URL re-verification cron |
| 8 | Hardening + threat model review | First v2 adapter: MI MiBusiness (headed via Xvfb) | Land FilePro live behind feature flag | Expedite take-rate metrics in admin dashboard |

UI-bound items appear only in weeks 4–5 and are explicitly approval-gated. No UI ships before approval.

---

## 8. Multi-agent execution rules

The plan only dispatches parallel agents when there is clean write-ownership and a real dependency graph. Each dispatched agent gets a single ownership scope and an output contract.

| Scope | Owner | Output | Merge target | Conflict rule |
|---|---|---|---|---|
| Schema migrations | one agent | SQL migration file in `<repo>/supabase/migrations/` + SQLite mirror in `app/backend/server.py` `_apply_sqlite_migrations()` | dedicated branch `mig/2026-05-XX-...` | no two migration agents at once |
| FilePro provider abstraction | one agent | `backend/ra_provider.py`, `backend/filepro_client.py`, refactor of `fulfill_registered_agent_assignment` | branch `ra-provider-abstraction` | blocks all other backend touches in that file until merged |
| Expedite data backfill | one agent per 5-state batch | edits to `data/filing_actions.json` only | branch `expedite-batch-N` | each batch is its own PR; no two agents edit the same state |
| Adapter lane promotion | one agent per state | edits to a single state worker file plus `filing_adapters.py` registration | branch `adapter-promote-{state}` | sequential, not parallel, on `filing_adapters.py` |
| Canary runner | one agent | new file `app/automations/canary_runner.py` plus systemd unit | branch `canary-runner` | independent |
| Read-only research / audits | unlimited Explore agents | structured report; no writes | n/a | n/a |

No agent commits to `main`. Every change is a PR. Steven (or me, with Steven's explicit approval) merges. Each PR requires `/codex review` PASS as a CI step before merge.

When the rules can't be satisfied (e.g., two agents would touch the same file), the work serializes. No parallelism theater.

---

## 9. Legal + compliance (unchanged from v1)

Disclaimers, clickwrap, arbitration, no entity-type recommendation, UPL guardrails, BOI paused per FinCEN IFR 2025-03-21. NC ToS still blocks automated searches. Agent-of-payee exemption for state-fee remittance.

E&O + cyber insurance still to-do for Steven.

---

## 10. Risk register (refreshed)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| FilePro creds delayed | M | H | Provider abstraction lets CorpNet stay selectable; FilePro behind feature flag |
| Evidence backfill flips many rows to `needs_evidence_reverification` | H | M | Operator cockpit queue; staged rollout; cap to recent windows first |
| Stripe trigger from a misfiled order before invariant lands | M | H | Land invariant in week 2; before then, manual ops review of every "complete" transition |
| Xvfb stability on shared droplet | M | M | Phase B gate; existing droplet upsize to s-4vcpu-8gb first |
| Adapter promotion takes longer than calendar implies | H | M | Each state has its own PR; calendar can slip per-state without blocking others |
| Expedite source URLs going stale | M | M | 90-day re-verification cron + certifier rule |
| SQLite trigger semantics differ from Postgres CHECK after cutover | M | M | Mirror the same predicate in app code; trust app code over either DB layer |
| Multi-agent merge conflict on `filing_adapters.py` | M | L | §8 rule: sequential on that file |

---

## 11. Definition of done (v2)

This plan ships when:

1. `/codex review` returns PASS (no [P1] findings).
2. Steven approves the doc.
3. Tasks #1–#8 in TaskList executed via PRs to `main`, each gated by `/codex review` PASS.
4. ops.sosfiler.com `health-deep` GREEN after every rollout.
5. v1 eight-state set transitions from operator-assisted to fully-automated, certified by `state_review_certifier.py`.
6. Evidence invariant blocks at least one synthetic "fake-complete" attempt in the QA suite.
7. FilePro feature flag flipped on and at least one live RA order placed end-to-end.
8. At least one customer-selectable expedite tier paid through Stripe end-to-end on a v1 state.

This document gets updated, not replaced, as scope shifts. v3 is allowed only when a P1-grade finding requires it.
