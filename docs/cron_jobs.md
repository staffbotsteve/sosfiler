# SOSFiler Cron Jobs

Production cron jobs should run on the SOSFiler server from `/root/.openclaw/workspace/builds/sosfiler`.

See `docs/formation_process.md` for the approval-evidence gate that should happen before any EIN cron job queues work.

## EIN Queue After State Approval

Purpose: once a state filing has approval evidence and the order is marked `state_approved`, queue the EIN application for operator processing.

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
- Skips orders that already have an EIN queue status or `generated_docs/<order_id>/ein_queue.json`.
- Creates `generated_docs/<order_id>/ein_queue.json`.
- Adds an `ein_pending` status update.
- Adds an `ein_queued` filing event.
- Does not submit anything to the IRS.
- Does not send customer emails.

Dry run:

```bash
cd /root/.openclaw/workspace/builds/sosfiler && /usr/bin/python3 backend/ein_queue_worker.py --dry-run
```

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
