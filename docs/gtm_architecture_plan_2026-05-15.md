# SOSFiler — Go-to-Market + Architecture + Development Plan

Date: 2026-05-15
Author: Claude (Opus 4.7 1M)
Owner: Steven Swan
Status: DRAFT — awaiting `/codex review`, then user approval before execution.

This document is the single source of truth for what gets built, in what order, and how it ships to revenue. It supersedes any contradiction in older docs.

The repo at `github.com/staffbotsteve/sosfiler` (working tree `/Users/stevenswan/project-folders/sosfiler/app`) is the authoritative system. External research (Compass artifact 2026-05-15) is reference only; recommendations are reconciled with what already exists in `backend/`.

---

## 1. North Star

> Form and manage a U.S. entity for $29 + official state fee. Annual filings for $29 + official state fee. Evidence-backed status, automation-first fulfillment, operator-assisted only where a state portal mandates a protected gate. FilePro is the registered-agent backbone in all 51 jurisdictions.

Three load-bearing claims drive every architectural decision:

1. **Service fee is fixed.** $29 formation, $29 annual. No tiers, no expedite upsell.
2. **Evidence or it didn't happen.** A filing reaches `complete` only when the DB sees both a state-issued filing number AND a downloaded receipt PDF. Status cannot be promoted on a successful POST. This is enforced in the database, not in application code.
3. **Operator is exception, not norm.** The app drives Playwright; humans only break protected gates (CAPTCHA, MFA, identity attestation, terms).

## 2. Decisions locked in by the user (2026-05-15)

1. **FilePro is the sole partner.** No Northwest, no CorpNet, no MyCompanyWorks, no Filejet. Existing `backend/corpnet_client.py` is deprecated.
2. **Hosting is DigitalOcean.** App runs on the existing droplet at `146.190.140.164` (ops.sosfiler.com).
3. **Non-headless Playwright is required for some state portals.** Solution proposed below uses Xvfb on a worker droplet, not Browserbase / Bright Data managed browser.
4. **Multiple agents may be dispatched in parallel.**
5. **UI changes require user pre-approval.** No edits to `frontend/index.html`, `frontend/app.html`, `frontend/dashboard.html`, `frontend/operator.html`, `frontend/ra-rescue.html`, `frontend/partners.html`, or stylesheets without an explicit green-light per change.

## 3. Reconciliation with existing repo

The repo is far more developed than older session summaries imply. Already in place:

| Capability | Module | Status |
|---|---|---|
| FastAPI app + admin/operator console | `backend/server.py` (11,366 LOC) | Production at ops.sosfiler.com |
| Universal filing state machine | `backend/execution_platform.py` | Has `UNIVERSAL_FILING_STATES`, `ALLOWED_TRANSITIONS`, `EVIDENCE_REQUIRED_STATES` |
| Filing adapter registry | `backend/filing_adapters.py` | 712 LOC, lane-based (official_api / partner_api / browser_automation / operator_assisted) |
| State automation profiles | `backend/state_automation_profiles.py` | 409 LOC, declarative per-state manifests |
| Filing status listener | `backend/filing_status_listener.py` | Inbound state-portal email parser |
| Per-state workers | `ca_bizfile_worker.py`, `silverflume_filer.py` (NV), `tx_sosdirect_document_worker.py` | Real browser flows |
| EIN automation | `ein_filing.py`, `ein_queue_worker.py`, `ein_completion_ingest.py`, `irs_ein_worker.py` | Hours-gated IRS wizard |
| Regulatory research worker | `research_worker.py` (1,057 LOC) | Firecrawl ingest + Supabase pipeline |
| State review certifier | `state_review_certifier.py` (3,242 LOC) | Per-jurisdiction launch gate |
| Stripe events table | `supabase/migrations/20260509211500_execution_cutover_and_stripe_events.sql` | Idempotent webhook ledger |
| Partner API schema | `supabase/migrations/20260512170000_partner_api_schema.sql` | Outbound + inbound partner contracts |
| Launch readiness | `backend/launch_readiness.py` + `docs/launch_readiness.md` | All 51 jurisdictions marked sellable |
| Health check timer | `app/deploy/systemd/sosfiler-health-check.{service,timer}` | 5-min cadence |
| Deep health endpoint | `/api/health/deep` | Slack alert on failure |

What the repo does NOT yet have (gap list driving this plan):

1. A separate non-headless browser-worker droplet for portals that fingerprint headless Chromium. Today all Playwright runs `headless=True` in-process on the main droplet.
2. A DB-enforced constraint that `status='complete' ⇒ filing_number IS NOT NULL AND receipt_path IS NOT NULL`. The state machine has `EVIDENCE_REQUIRED_STATES` but no CHECK trigger.
3. A FilePro client. CorpNet stub exists, must be replaced.
4. A drift watchdog / daily canary that walks each adapter to the final review screen.
5. Public state-specific landing pages for SEO acquisition (`/state/wy`, `/state/fl`, ...).
6. Postgres production migration. SQLite WAL is fine at launch volume but caps single-writer throughput and lacks online backups; Supabase is already wired for adjacent schemas.
7. Object Lock-equivalent evidence storage. Today receipts live on the droplet filesystem under `backend/receipts/`. Loss = unprovable filing.

This plan closes those gaps without rewriting what already works.

---

## 4. Architecture

### 4.1 Topology

```
                                  ┌──────────────────────────────┐
                                  │  Customer Web (HTML/PWA)     │
                                  │   frontend/* served by       │
                                  │   FastAPI on droplet-app     │
                                  └────────────┬─────────────────┘
                                               │ HTTPS
┌──────────────────────────────────────────────▼──────────────────────────────┐
│ DROPLET-APP  146.190.140.164  (existing)                                    │
│   - FastAPI / Uvicorn  (backend/server.py)                                  │
│   - SQLite WAL  →  Postgres (Supabase)  [migration in this plan]            │
│   - EIN queue worker, research worker, status listener (systemd units)      │
│   - Stripe webhooks, SendGrid, Slack notifier                               │
│   - Admin + operator cockpit                                                │
└────────────┬───────────────────────────────────────┬────────────────────────┘
             │                                       │
             │ HMAC-signed job push (HTTPS, mTLS opt)│
             ▼                                       ▼
┌────────────────────────────────┐      ┌────────────────────────────────────┐
│ DROPLET-BROWSER-POOL  (NEW)    │      │ FilePro Partner API                │
│   1-N workers, autoscalable    │      │   - RA address per state           │
│   Each worker:                 │      │   - Renewal + service-of-process    │
│     systemd Xvfb :99           │      │   - Webhook → status listener      │
│     systemd chromium under      │      └────────────────────────────────────┘
│       playwright-stealth        │
│     systemd worker-runner       │      ┌────────────────────────────────────┐
│       polls job queue, runs     │      │ DigitalOcean Spaces (S3-compatible)│
│       per-state adapter,        │      │   evidence/  versioned, ≥7y        │
│       captures DOM + PDF +      │      │   document_vault/  customer docs   │
│       confirmation number,      │      │   audit-anchor/   daily merkle root│
│       posts evidence back via   │      └────────────────────────────────────┘
│       HMAC-signed admin API     │
│                                │      ┌────────────────────────────────────┐
│   Persistent per-state          │      │ Stripe Identity (KYC)              │
│   user-data-dir on attached     │      │ Stripe Checkout (payments)         │
│   block volume                  │      │ SendGrid (outbound)                 │
│                                │      │ Postmark / Gmail Connector (inbound)│
└────────────────────────────────┘      └────────────────────────────────────┘
```

### 4.2 Why a second droplet (not Browserbase / Bright Data)

Cost: a $48/mo DigitalOcean s-4vcpu-8gb droplet hosts 4-8 concurrent Xvfb+Chromium sessions. Browserbase Startup is $99/mo for 50 hours of session time, then $0.10/min. At projected volume (200 filings/day average, 6 min per session) Browserbase costs ~$3,600/mo vs $48/mo self-hosted.

Control: portals that fingerprint headless mode (CA bizfile, MI MiBusiness, MD Maryland Express) need a non-headless Chromium with `playwright-stealth` and a real X server. Browserbase abstracts this but charges per session. Self-hosted gives us:

- Persistent Chromium profiles per state (cookies, fingerprint, accepted ToU)
- IP affinity per state via residential proxy (Bright Data Web Unlocker, pay-per-GB)
- Full control of the user-agent / screen / timezone matrix
- Ability to record full session video for evidence (saves to Spaces)

Trade-offs accepted: we own the infra. We are responsible for keeping Xvfb up, rotating proxies, and watching for Chromium updates. Existing systemd + healthcheck infrastructure handles this pattern already.

### 4.3 Adapter contract (no change to existing interface)

Existing `FilingAdapter` ABC in `execution_platform.py` already has `prepare / submit / check_status / capture_evidence`. Plan extends per-adapter:

- `selectors_contract: dict[str, str]` — declared DOM contract checked by canary
- `confirmation_number_regex: str` — must extract before `submitted` transition
- `receipt_pdf_required: bool` — adapter must produce a PDF before `submitted`
- `headed: bool` — whether this adapter must run on the browser-worker pool (non-headless) or can run in-process headless

### 4.4 Job queue

Current code uses FastAPI BackgroundTasks for some flows and dedicated systemd workers for EIN / research / TX SOSDirect. The plan keeps the systemd-worker pattern (it is already proven) but adds a `filing_jobs` queue table in Postgres so the browser-worker pool can pull work without coupling to in-process state.

Why not Redis + Arq: we already have Postgres for everything else, and `SKIP LOCKED` gives us a job queue with no extra dependency. Adopted only if Postgres-as-queue throughput becomes a bottleneck (>5,000 jobs/day).

Why not Temporal: too heavy for a one-founder ops surface. The existing state machine plus durable job table plus systemd-restart already gives durability and replay. Revisit at >20k filings/year.

### 4.5 Evidence storage

DO Spaces bucket layout:

```
sosfiler-evidence/
  filings/{filing_id}/
    receipt.pdf              # state-issued, sha256 in metadata
    confirmation.html        # final confirmation page DOM snapshot
    screenshot_final.png
    session.mp4              # full headed video (browser-worker only)
    har.json                 # network trace
  audit-anchor/
    YYYY-MM-DD.merkle.json   # daily merkle root of audit_events table
```

Versioning ON. Lifecycle: keep current + 7y of versions. Spaces does not have Object Lock; we mitigate with `audit-anchor` daily roots + hash-chained `audit_events` table.

### 4.6 DB invariant (THE bug fix)

Postgres migration adds:

```sql
ALTER TABLE filings
  ADD CONSTRAINT filings_complete_requires_evidence
  CHECK (
    status NOT IN ('submitted','approved','documents_collected','complete')
    OR (filing_number IS NOT NULL AND receipt_path IS NOT NULL)
  );
```

SQLite mirror via `CREATE TRIGGER` before-update / before-insert raising on violation. Existing rows in any of those states without both fields get flagged `needs_revalidation` and pulled from customer-facing display until evidence is reconciled.

### 4.7 Drift detection

`app/automations/canary_runner.py` (new) runs per adapter every 6h:

1. Drive portal to the final review screen, do not submit.
2. Assert declared `selectors_contract`.
3. Verify confirmation page regex still matches a known-good fixture.
4. Ping Healthchecks.io endpoint per state.
5. On fail: write `adapter_status.stale = true`, block new submissions for that state, page on Slack via `notifier.py`.

LLM-assisted change suggestion (optional, runs only on canary fail): post the prior schema + current DOM to OpenAI with a structured-output schema asking what moved. Draft a patch PR. Never auto-deploy.

### 4.8 LLM scope (hard boundary)

LLMs may:
- Generate operating agreements, resolutions, meeting minutes
- Draft adapter selector patches when canary fails
- Summarize customer-support inbound

LLMs MUST NOT:
- Adjudicate whether a state accepted a filing
- Decide whether a filing reached `submitted` or `complete`
- Choose entity type for the customer (UPL)

This is the single largest false-completion risk vector and is enforced by code review.

---

## 5. Hosting + deployment

### 5.1 Droplet inventory

| Role | Hostname (proposed) | Size | Cost / mo |
|---|---|---|---|
| App + admin + operator | ops.sosfiler.com (existing) | s-2vcpu-4gb | $24 |
| Browser worker pool (1) | browser-1.sosfiler.com | s-4vcpu-8gb | $48 |
| Postgres (Supabase managed) | n/a | Free tier → Pro $25 | $0-25 |
| Spaces (evidence) | n/a | 250GB included | $5 |
| Total at launch | | | **~$80/mo** |

Scale plan: each additional browser worker is $48/mo and adds ~120 concurrent filings/day capacity.

### 5.2 Browser-worker systemd contract

```
/etc/systemd/system/xvfb.service             # Xvfb :99 -screen 0 1920x1080x24
/etc/systemd/system/chromium-pool@.service   # template, one per slot
/etc/systemd/system/sosfiler-browser-worker.service
```

Worker loop (simplified):

```python
while True:
    job = pg.fetch_one("""
        UPDATE filing_jobs SET locked_by=$1, locked_at=now()
        WHERE id = (
          SELECT id FROM filing_jobs
          WHERE status='ready_to_file' AND locked_by IS NULL
                AND headed = true
          ORDER BY priority DESC, created_at
          FOR UPDATE SKIP LOCKED LIMIT 1
        )
        RETURNING *
    """, worker_id)
    if not job: sleep(2); continue
    result = run_adapter(job)              # captures evidence locally
    post_evidence_to_admin_api(result)     # HMAC-signed
```

HMAC: shared secret in droplet env, header `X-Sosfiler-Signature: sha256=...` over canonical payload. Admin API rejects mismatch with 403 and Slack-pages.

### 5.3 Configuration tasks I will need from Steven

I cannot complete these without user action; flagged here, not blocking the rest of the plan:

1. Provision the new browser-worker droplet (or grant me sudo on existing droplet to do it). I will hand a one-liner.
2. Create `sosfiler-evidence` DO Spaces bucket; paste the access key into the existing `.env` on the droplet.
3. FilePro API credentials (sandbox + prod). Until provided, the FilePro client runs in dry-run / operator-assisted fallback.
4. Bright Data Web Unlocker (or Smartproxy) account for residential IPs on the 5-10 hardest states. ~$15/GB, projected $20-40/mo. Skip if launching headless-only first.
5. Healthchecks.io account (free) — one check per state adapter.

I will deploy code and configs via the existing `scripts/deploy_ops.sh` pattern.

---

## 6. Go-to-market

### 6.1 Positioning

> SOSFiler routes every filing through the fastest compliant lane available. $29 + state fee. Evidence-backed. National coverage. No upsells. If we miss our posted SOSFiler-controlled fulfillment SLA, the $29 service fee is refundable.

This matches `docs/sosfiler_world_class_redesign.md`. Do not claim full automation for protected portals.

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
| **State expedite (selectable)** | **$0 SOSFiler markup** | **passthrough** | **state's posted fee** |
| BOI filing (only when CTA returns) | $29 | n/a | $29 |

State fees and state-posted expedite fees are charged as separate line items on the Stripe checkout to avoid eating 2.9% of high-fee states (MA $500, TX $300, NV $425, CA $350 expedite) as Stripe processing.

### 6.2.1 Expedite-fee pass-through (NEW, per 2026-05-15 user direction)

Many states publish optional expedite tiers for both formations and post-formation management actions (amendments, certificates of good standing, dissolutions, foreign qualifications). SOSFiler offers these tiers as customer-selectable add-ons and charges exactly the state's posted fee — no SOSFiler markup. Examples already captured in `data/filing_actions.generated.json`:

- **CA** — 24-Hour ($350) / Same Day ($750) / 4-Hour ($500, drop-off only, not customer-selectable)
- **DE** — 24-hour / Same-day / 2-hour / 1-hour (tiered, ~$50 → $1,000)
- **TX** — Expedite handled differently (per `data/filing_actions.generated.json`)
- **WY** — Standard processing is already instant (no expedite SKU needed)

**Data coverage gap (must close before launching expedite UI):** today only 35/51 jurisdictions are represented in `filing_actions.generated.json` and only 16/51 jurisdictions have a populated `expedited` value in `state_fees.json`. The remaining states must be researched and added. Track A includes this backfill.

**Schema unification:** the canonical expedite source is `filing_actions.generated.json` (rich tiers, channels, processing time, source URL). `state_fees.json.expedited` is single-tier legacy. Plan promotes `filing_actions.generated.json` to authoritative; `state_fees.json` becomes a derived projection.

**Data fields per expedite tier (already standard in the rich schema):** `label`, `fee_cents`, `processing_time`, `channel`, `customer_selectable`, `combinable`, `requires_preclearance` (where applicable), `source_url`. Tiers with `customer_selectable=false` (e.g. CA's 4-hour Sacramento drop-off) are hidden from the customer wizard but visible to operators.

**Wizard impact (UI work — UI approval required before publish):**
1. Formation wizard: add an "Expedite" step that lists the state's customer-selectable tiers with label + processing-time copy + fee. Default "Standard (no extra cost)" is preselected.
2. Customer dashboard: surface picked tier on the timeline. Operator cockpit shows ineligible tiers and the reason.
3. Dashboard "post-formation services" surface: every supported management action (amendment, change of agent, foreign qual, certificate of good standing, reinstatement, dissolution) exposes the state's expedite tiers on the same model.

**API impact (no UI dependency — can ship first):**
- `/api/formation-availability` extends per-state payload with `expedite_options[]` filtered to `customer_selectable=true`.
- `/api/management-actions/{action_type}/availability` (new) returns the same shape per post-formation action.
- Stripe checkout adds a line item `state_expedite_<state>_<tier_id>` with amount = `fee_cents`. Refund policy mirrors the underlying state fee (non-refundable once submitted).
- `state_review_certifier.py` blocks a state from advertising an expedite tier until the source URL has been re-verified within the last 90 days.

### 6.3 Unit economics

Per-formation COGS (target, headless lane):

- DO browser session amortized: $0.04
- Residential proxy (only ~20% of filings): $0.08 weighted
- Stripe on $29 service fee: $1.14
- KYC (Stripe Identity, 1 per customer not per filing): $0.50 amortized
- SendGrid + Spaces + Postgres: $0.05
- Refund / retry reserve: $0.75
- **Total COGS: ~$2.55. Contribution margin: $26.45 (≈91%).**

Customer LTV (3-year horizon, blended assumptions):

| Year | Revenue per customer |
|---|---|
| Y1 formation | $29 |
| Y1 RA | $99 |
| Y2 annual report + RA | $29 + $99 |
| Y3 annual report + RA | $29 + $99 |
| 30% take foreign qual | $99 × 0.30 |
| 10% amendment | $49 × 0.10 |
| **3-year LTV** | **~$420** |

CAC ceiling at 3:1 LTV ratio: $140. Google "Wyoming LLC" CPC ~$8-12; allowable.

### 6.4 v1 launch states (already sellable; production push order)

Existing `docs/launch_readiness.md` marks all 51 jurisdictions sellable today via operator-assisted lanes. This plan picks the eight that get full automation-first investment first, ordered by demand × ease:

1. **WY** — instant approval, no CAPTCHA, $102. Magnet for non-resident founders.
2. **FL** — stable HTML 15+ yrs, $125, no account.
3. **TX** — `tx_sosdirect_document_worker.py` already exists; finish it.
4. **CO** — instant, $50, online-only.
5. **NM** — $50, no LLC annual report, popular privacy state.
6. **OH** — $99, no annual report.
7. **MO** — $50, no annual report.
8. **NV** — `silverflume_filer.py` already exists; $425 bundle, high revenue per filing.

Deferred-but-sellable (operator-assisted only) in v1: remaining 43 jurisdictions. CA gets v1.5 once `ca_bizfile_worker.py` clears canary 14 days.

### 6.5 Acquisition channels

1. **SEO** — one landing page per jurisdiction: `/state/{code}`, `/{state-name}-llc-formation`. Content sourced from `data/state_fees.json` + `state_automation_profiles.py` + `launch_readiness`. Schema markup, FAQ block, structured pricing. Target: 51 pages live within 2 weeks. NEW pages, no existing-page redesign — no UI approval required.
2. **Paid search** — Google Ads on "[state] LLC formation", "form LLC in [state]". Start with WY, FL, TX. $50/day per state, scale on CAC.
3. **Affiliate / partner referrals** — FilePro upstream referrals from law firms / CPAs.
4. **Programmatic content** — How-to blog seeded from regulatory research worker output.

### 6.6 Retention engine

The $29 formation is acquisition. Recurring revenue comes from:

- FilePro RA renewal at $99/yr (FilePro wholesale ≤ $50, margin ≥ $49)
- Annual report at $29 + state, automated via existing annual-report queue
- Compliance reminders (CRM-style email cadence) lift annual-report attach to 70%+

---

## 7. Build calendar (8 weeks, parallelizable)

Each row is a deliverable, not a sprint. Multiple rows run in parallel by dispatching agents to independent worktrees.

| Wk | Track A — Platform | Track B — Adapters | Track C — GTM | Track D — Expedite |
|---|---|---|---|---|
| 1 | Remove CorpNet, stub FilePro client | Audit ca_bizfile_worker.py | Plan + brand audit | Audit existing expedite data; gap list |
| 1 | DB invariant migration (SQLite trigger + Postgres CHECK) | Audit silverflume_filer.py | SEO landing template scaffold | Research backfill: WY/FL/CO/NM/OH/MO/TX/NV (v1 8) |
| 2 | Postgres cutover (Supabase managed) | Adapter contract: add selectors_contract + regex per state | Generate 8 state landings (v1 set) | Promote `filing_actions.generated.json` to authoritative |
| 2 | Spaces bucket + lifecycle + presigned URLs | Drift canary runner | Stripe Identity wiring | `/api/formation-availability` exposes customer-selectable tiers |
| 3 | Browser-worker droplet provisioned + Xvfb + stealth | Promote WY/FL/CO/NM/OH/MO to fully_automated_sellable | Paid-search account setup | Stripe checkout supports `state_expedite_*` line items |
| 3 | filing_jobs queue (Postgres SKIP LOCKED) | Headed lane for CA, NV | Compliance reminder cadence | Adapters pick selected tier at portal time + capture evidence |
| 4 | HMAC-signed evidence intake API | EIN window enforcement + retry | Generate remaining 43 landings | Expedite UI step in wizard (UI APPROVAL REQUIRED) |
| 4 | Daily merkle anchor cron | Notifier: completed only on DB trigger | Affiliate program scaffold | Research backfill: remaining 43 jurisdictions |
| 5 | Operator cockpit: STALE-adapter view (admin-only) | NY publication coordinator | Launch Ads on WY | Management actions: amendment/COGS/foreign qual expedite tiers |
| 6 | Postmark inbound for state acceptance emails | LA notarization-via-RA loop via FilePro | Launch Ads on FL, TX | Dashboard expedite picker for post-formation actions (UI APPROVAL) |
| 7 | Cost dashboard, per-state COGS | First v2 adapter (GA Experian KBA, attended) | Content engine: blog posts | 90-day source-URL re-verification cron |
| 8 | Hardening pass + threat model review | Foreign qualifications via FilePro | Public PR + Product Hunt prep | Expedite-take metrics in admin dashboard |

Items needing UI work (deferred until UI approval): operator cockpit STALE view, dashboard polish, landing-page visual treatment beyond template. The landings themselves are NEW pages and ship without approval; visual chrome shared with existing site stays current.

---

## 8. Multi-agent execution model

Agents I will dispatch in parallel:

| Agent | Role | Output |
|---|---|---|
| `Explore` | Code search across `backend/`, find every status-promotion path | Audit doc → input to Track A |
| `general-purpose` | Codex-style adversarial review of plan | Findings list (this round = `/codex review` per user request) |
| `general-purpose` | Generate 51 SEO landings from template | HTML pages under `frontend/state/` |
| `general-purpose` | Adapter selector audit per state | Per-state contracts file |
| `codex:codex-rescue` | Independent diagnosis when an adapter regresses | Patch suggestions |

Coordination: one parent agent (me) owns the task list. Each child agent receives a self-contained prompt and returns a structured artifact. No agent commits to main; PRs only.

---

## 9. Legal + compliance posture

Pulled from research artifact, confirmed against existing repo posture:

- "Not a law firm" disclaimers already present on `frontend/terms.html`; verify on every page.
- Clickwrap agency: `terms.html` already grants SOSFiler authority to file. Audit before launch.
- No entity-type recommendation engine. Customer chooses; UI explains differences neutrally.
- UPL exposure low post-2016. Arbitration + class-action waiver in TOS — verify present.
- BOI: paused per FinCEN IFR 2025-03-21. NY LLC Transparency Act applies only to foreign-formed LLCs registered in NY (effective 2026-01-01). Build the flow, don't ship until regulatory_research_worker confirms scope expansion.
- North Carolina SoS prohibits automated searches in ToS — use paid Data Subscription Service for NC name pre-checks (existing `state_automation_profiles.py` should reflect; verify).
- Money transmission: agent-of-payee exemption applies (remit state fees within 24h). Stripe Connect not needed at v1 scale.

Insurance to-do (Steven): $2-5M E&O + $1-3M cyber. Quote sources Hiscox / Coalition / Embroker. ~$5-15k/yr combined.

---

## 10. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| FilePro API unavailable / wholesale RA pricing renegotiated | M | H | Dry-run FilePro client; keep operator-assisted RA fallback path until contract signed; sourcing Steven on FilePro PRO Service Agreement status |
| State portal redesign (CA bizfile, MI MiBusiness redux) | H | M | Drift canary + version-pinned adapters; STALE flag halts that state only |
| Headless detection escalates across states | M | H | Headed lane on browser-worker droplet; per-state stealth profile |
| Stripe dispute spike on misfiled orders | L | H | DB invariant + evidence requirements make misfiles structurally impossible to mark complete |
| Single-droplet outage | M | M | DO snapshot daily; Postgres on managed Supabase; restore < 30 min |
| LLM hallucination in success determination | L (after fix) | Critical | Bounded to doc generation only; code review gates |
| BOI rule changes | M | L | Reg-research worker monitors FinCEN + state portals |

---

## 11. Definition of done for this plan

Plan is "done" when:

1. `/codex review` returns PASS (no [P1] findings).
2. Steven approves the doc.
3. Task list (#1–#7 in TaskList) executed in repo with PRs landed on `main`.
4. ops.sosfiler.com health-deep still GREEN after each rollout.
5. v1 8-state set transitions from operator-assisted to fully_automated_sellable in `launch_readiness.md`, certified by `state_review_certifier.py`.

This document gets updated, not replaced, as scope shifts.
