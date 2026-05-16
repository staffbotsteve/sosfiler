# SOSFiler Cron Jobs

Production cron jobs should run on the SOSFiler server from `/root/.openclaw/workspace/builds/sosfiler`.

See `docs/formation_process.md` for the approval-evidence gate that should happen before any EIN cron job queues work.

## EIN Queue After State Approval

Purpose: once a state filing has approval evidence and the order is marked `state_approved`, queue the EIN application for operator/assisted IRS processing.

Command:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ein_queue_worker.py --limit 25 >> logs/ein_queue_worker.log 2>&1
```

Suggested schedule:

```cron
*/15 * * * * cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ein_queue_worker.py --limit 25 >> logs/ein_queue_worker.log 2>&1
```

Behavior:

- Selects orders with `status = 'state_approved'`, `approved_at` present, and no EIN.
- Requires captured approval evidence in `filing_artifacts` or `documents` before queueing.
- Skips orders that already have an EIN queue status or `generated_docs/<order_id>/ein_queue.json`.
- Creates `generated_docs/<order_id>/ein_queue.json`.
- Adds an `ein_pending` status update.
- Adds an `ein_queued` filing event.
- Does not submit anything to the IRS by itself; it prepares the order for the controlled IRS application step.
- Does not send customer emails.

Dry run:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ein_queue_worker.py --dry-run
```

## EIN Confirmation Document Ingest

Purpose: after the IRS EIN application is completed and the confirmation/CP575 document is captured, publish it into the customer's portal.

Command:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ein_completion_ingest.py --limit 50 >> logs/ein_completion_ingest.log 2>&1
```

Suggested schedule:

```cron
*/15 * * * * cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ein_completion_ingest.py --limit 50 >> logs/ein_completion_ingest.log 2>&1
```

Behavior:

- Looks for captured files in `generated_docs/<order_id>/` with names like `ein_confirmation*.pdf`, `irs_ein_confirmation*.pdf`, or `cp575*.pdf`.
- Adds the captured EIN confirmation document to the `documents` table with `doc_type = 'ein_confirmation_letter'`.
- Adds an `ein_received` status update and filing event exactly once.
- Updates the order to `ein_received`; if the EIN number is present in a `.txt` confirmation file, also stores it on the order.
- Does not email customers.

## State Filing Status Listener

Purpose: every 15 minutes, check official state filing status sources for every active submitted filing job, record source-specific statuses, and download official approval/rejection documents into the customer's portal when available.

Command:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/filing_status_listener.py --limit 100 >> logs/filing_status_listener.log 2>&1
```

Suggested schedule:

```cron
*/15 * * * * cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/filing_status_listener.py --limit 100 >> logs/filing_status_listener.log 2>&1
```

Behavior:

- Selects filing jobs in active state-submission statuses, including `submitted_to_state`, `awaiting_state`, `pending_state_review`, and `received_needs_sosdirect_check`.
- Runs the state adapter for each state represented in the active queue.
- Stores source-specific filing events instead of collapsing everything into one vague status.
- Downloads official state approval/rejection documents into `generated_docs/<order_id>/state_filings/` when the state exposes a document URL.
- Adds downloaded approval/rejection evidence to `filing_artifacts`.
- Adds downloaded customer-visible evidence to the `documents` table so it appears in the customer portal document vault.
- Does not email customers.
- Does not mark `state_approved` unless approval evidence is downloaded/captured.

Texas-specific rule:

- The public Business Filing Tracker is treated as `document_tracker_status` only.
- Texas status must be reconciled with SOSDirect entity inquiry and/or Briefcase when those sources are available.
- `Received` in the public tracker must not overwrite a stronger SOSDirect entity status such as `Pending Review`.

Dry run:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/filing_status_listener.py --dry-run --json
```

## California BizFile Worker

Purpose: run California formation status checks and, after live certification, California LLC-1 submission from a trusted SOSFiler BizFile browser profile. This is the non-operator path for California: the worker owns navigation, status polling, evidence capture, and dashboard updates. It escalates only if the official portal presents a login/MFA/access checkpoint, raw card-entry screen, portal layout break, rejection, or missing approval document.

Protocol-first migration:

- Run `backend/ca_bizfile_worker.py --operation discover` from the trusted profile after any BizFile portal change or after a successful live filing.
- The discover operation is read-only. It observes official BizFile network calls, redacts OAuth/session-shaped query values, and writes `data/portal_maps/ca_bizfile_protocol_manifest.json`.
- Promote stable API/report/document endpoints from that manifest only after legal/provider approval that the use is permitted by California's terms; otherwise keep it as a diagnostic for portal changes.
- Keep browser-profile navigation as the fallback for protected submission/payment checkpoints until California exposes a documented filing API or SOSFiler contracts a partner API.
- See `docs/ca_llc_no_playwright_automation.md`.

Required worker environment:

```bash
CA_BIZFILE_EMAIL=admin@sosfiler.com
CA_BIZFILE_PASSWORD=...
CA_BIZFILE_PROFILE_DIR=/var/lib/sosfiler/ca-bizfile-profile
CA_BIZFILE_PAYMENT_MODE=saved_portal_method
```

Do not configure raw card variables such as `CA_BIZFILE_CARD_NUMBER` or `CA_BIZFILE_CARD_CVV`. The worker deliberately blocks those. Use a saved BizFile portal payment method or a PCI-compliant vault/token integration.

Status polling command:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ca_bizfile_worker.py --operation status --limit 25 --json >> logs/ca_bizfile_worker.log 2>&1
```

Suggested schedule:

```cron
*/15 * * * * cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ca_bizfile_worker.py --operation status --limit 25 --json >> logs/ca_bizfile_worker.log 2>&1
```

Behavior:

- Selects active California filings in `submitted_to_state`, `submitted`, `awaiting_state`, `pending_state_review`, or `received_needs_sosdirect_check`.
- Opens BizFile using the persistent trusted browser profile.
- Clicks `My Work Queue`, matches each order by legal entity name, and stores the raw displayed portal status.
- Captures screenshots under `data/filing_listener_runs/ca_bizfile/<order_id>/`.
- Updates jobs to `pending_state_review` when BizFile shows `Pending Review`.
- Moves jobs to `operator_review` when BizFile shows rejection/correction language.
- Marks `state_approved` only after approval evidence is captured; if BizFile appears approved but no certificate/document is captured, it records `ca_bizfile_approved_needs_document` and leaves EIN blocked.

Dry run:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ca_bizfile_worker.py --operation status --dry-run --json
```

Live California submission is intentionally off until the trusted worker profile and payment method are certified:

```bash
CA_BIZFILE_ENABLE_LIVE_SUBMIT=true \
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ca_bizfile_worker.py --operation submit --order-id CA-... --json
```

The submit operation fills the LLC-1 flow, clicks `File Online`, captures the payment cart, and only submits payment if BizFile exposes a compliant saved portal payment method. If BizFile asks for raw card details, the worker blocks and records evidence instead of handling card data in `.env`.

## Texas SOSDirect Authenticated Document Retriever

Purpose: log into the SOSFiler Texas SOSDirect account, inspect Briefcase for active Texas filings, download matching filed/approval documents, attach them to the customer portal, and mark the order `state_approved` when approval evidence is captured.

Required secrets:

```bash
TX_SOSDIRECT_USER_ID=...
TX_SOSDIRECT_PASSWORD=...
```

Command:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/tx_sosdirect_document_worker.py --limit 25 >> logs/tx_sosdirect_document_worker.log 2>&1
```

Suggested schedule:

```cron
*/15 * * * * cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/tx_sosdirect_document_worker.py --limit 25 >> logs/tx_sosdirect_document_worker.log 2>&1
```

Behavior:

- Uses the company SOSDirect subscriber account instead of creating customer-specific Texas portal accounts.
- Checks only active Texas filing jobs.
- Matches Briefcase rows by business name, order ID, and known Texas document/session identifiers.
- Downloads matching approval/certificate/formation documents into `generated_docs/<order_id>/state_filings/`.
- Adds downloaded evidence to `filing_artifacts` and `documents`, making it visible in the customer portal.
- Marks the filing job and order `state_approved` only after a matching approval document is captured.
- Writes diagnostic Briefcase HTML snapshots under `data/filing_listener_runs/tx_sosdirect/` when no document is found, so portal layout changes can be debugged without guessing.
- Does not submit filings, make payments, order new copies, or email customers.

Dry run:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/tx_sosdirect_document_worker.py --dry-run --order-id IL-825E3CBCCCC3
```
