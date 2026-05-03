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
